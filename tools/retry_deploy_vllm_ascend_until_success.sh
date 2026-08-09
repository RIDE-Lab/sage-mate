#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# Compatibility shim: retries used to mutate systemd-user environment and
# introduce conflicting physical/logical NPU mappings. Keep the old filename
# usable, but route normal invocations through the canonical lock workflow.
if [[ "${SAGE_MATE_ALLOW_LEGACY_RETRY:-0}" != "1" ]]; then
  exec "$repo_root/tools/lock_sage_mate_deployment.sh" "$@"
fi
PYTHON_BIN_DEFAULT="${PYTHON_BIN:-python3}"

load_dotenv() {
    [[ -f "$repo_root/.env" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        key="${key// /}"
        [[ -z "$key" ]] && continue
        if [[ "${!key+x}" != "x" ]]; then
            export "$line"
        fi
    done < "$repo_root/.env"
}

load_dotenv

log_dir="${DIGITAL_TWIN_RUNTIME_DIR:-$repo_root/../sage-mate-runtime-private}/logs"
mkdir -p "$log_dir"
retry_log_file="$log_dir/vllm-ascend-auto-retry.log"
engine_log_file="/tmp/sage-mate-vllm-engine.redacted.log"

max_attempts="${SAGE_MATE_RETRY_MAX_ATTEMPTS:-0}" # 0 = retry forever
base_delay="${SAGE_MATE_RETRY_BASE_DELAY_SECONDS:-15}"
check_timeout="${SAGE_MATE_RETRY_CHECK_TIMEOUT_SECONDS:-90}"
resolver_timeout="${SAGE_MATE_RETRY_RESOLVER_TIMEOUT_SECONDS:-120}"

engine_host="${VLLM_ENGINE_CONNECT_HOST:-${VLLM_ENGINE_HOST:-127.0.0.1}}"
engine_port="${VLLM_ENGINE_CONNECT_PORT:-${VLLM_ENGINE_PORT:-8001}}"
proxy_base="${DIGITAL_TWIN_LLM_BASE_URL:-http://127.0.0.1:18001/v1}"
proxy_api_key="${DIGITAL_TWIN_API_KEY:-${VLLM_HUST_API_KEY:-EMPTY}}"
model_to_test="${DIGITAL_TWIN_MODEL_NAME:-${VLLM_ENGINE_SERVED_MODEL_NAME:-${VLLM_ENGINE_ACTUAL_MODEL_ID:-deepseek}}}"
startup_wait_seconds="${SAGE_MATE_RETRY_STARTUP_WAIT_SECONDS:-180}"
# Split endpoint list as a whitespace-separated list by default, but honor custom
# env input if provided (keeps backwards compatibility for single-token URLs).
if [[ -n "${SAGE_MATE_HF_ENDPOINTS:-}" ]]; then
  IFS=$' \t\n,' read -r -a hf_endpoints <<< "${SAGE_MATE_HF_ENDPOINTS//,/ }"
  cleaned_endpoints=()
  for endpoint in "${hf_endpoints[@]}"; do
    endpoint="$(echo "$endpoint" | xargs)"
    [[ -n "$endpoint" ]] && cleaned_endpoints+=("$endpoint")
  done
  hf_endpoints=("${cleaned_endpoints[@]}")
  if (( ${#hf_endpoints[@]} == 0 )); then
    hf_endpoints=("https://huggingface.co" "https://hf-mirror.com")
  fi
else
  hf_endpoints=("https://huggingface.co" "https://hf-mirror.com")
fi

base_strategy_quantization="${VLLM_ENGINE_QUANTIZATION:-}"
base_strategy_use_v1="${VLLM_USE_V1:-0}"
base_strategy_npu_devices="${VLLM_ENGINE_RUNTIME_VISIBLE_DEVICES:-${VLLM_ENGINE_NPU_DEVICES:-${ASCEND_RT_VISIBLE_DEVICES:-0,1,2,3}}}"
base_strategy_required_families="${VLLM_ENGINE_REQUIRED_FAMILIES:-deepseek,glm,minimax}"
base_strategy_tp_size="${VLLM_ENGINE_TP_SIZE:-4}"
base_strategy_enforce_eager="${VLLM_ENGINE_ENFORCE_EAGER:-0}"
base_strategy_extra_args="${VLLM_ENGINE_EXTRA_ARGS_JSON:-}"
base_strategy_compilation_config="${VLLM_ENGINE_COMPILATION_CONFIG:-}"
allow_qwen_fallback="${VLLM_ENGINE_ALLOW_QWEN_FALLBACK:-0}"
auto_download_default="${VLLM_ENGINE_AUTO_DOWNLOAD:-1}"

# Keep these in sync with current .env, but we will temporarily override with retry
# strategies below.
declare -a strategy_names
declare -a strategy_quantizations
declare -a strategy_use_v1
declare -a strategy_required_families
declare -a strategy_npu_devices
declare -a strategy_tp_sizes

add_strategy() {
  strategy_names+=("$1")
  strategy_quantizations+=("${2:-$base_strategy_quantization}")
  strategy_use_v1+=("${3:-$base_strategy_use_v1}")
  strategy_required_families+=("${4:-$base_strategy_required_families}")
  strategy_npu_devices+=("${5:-$base_strategy_npu_devices}")
  strategy_tp_sizes+=("${6:-$base_strategy_tp_size}")
}

# Baseline strategy: keep current project config as-is.
add_strategy "baseline" \
  "$base_strategy_quantization" "$base_strategy_use_v1" "$base_strategy_required_families" \
  "$base_strategy_npu_devices" "$base_strategy_tp_size"

# Fallback 1: keep model family, disable ascended quantization to avoid crashes in quant path.
if [[ "${base_strategy_quantization,,}" == "ascend" ]]; then
  add_strategy "no-quantization" "" "$base_strategy_use_v1" "$base_strategy_required_families" \
    "$base_strategy_npu_devices" "$base_strategy_tp_size"
  # If v1 is currently disabled, also try toggling it on when quantization is already
  # disabled (some Ascend builds are sensitive to v1/v0 scheduling assumptions).
  if [[ "$base_strategy_use_v1" != "1" ]]; then
    add_strategy "no-quantization-use-v1" "" "1" "$base_strategy_required_families" \
      "$base_strategy_npu_devices" "$base_strategy_tp_size"
  fi
fi

# Family fallback strategies: deepthink-first, then GLM/Minimax/Qwen (if allowed by policy).
IFS=',' read -r -a configured_families <<< "$base_strategy_required_families"
declare -A seen_family=()
for configured_family in "${configured_families[@]}"; do
  configured_family="$(echo "$configured_family" | xargs)"
  [[ -z "$configured_family" ]] && continue
  configured_family="${configured_family,,}"
  seen_family["$configured_family"]=1
done

# If qwen is not allowed, skip it in fallback.
if [[ "$allow_qwen_fallback" != "1" ]]; then
  seen_family["qwen"]=0
fi

for fallback_family in glm minimax qwen; do
  if [[ "${seen_family[$fallback_family]:-1}" == "0" ]]; then
    continue
  fi
  add_strategy "fallback-$fallback_family" "" "$base_strategy_use_v1" "$fallback_family" \
    "$base_strategy_npu_devices" "$base_strategy_tp_size"
done

# Probe a single-card fallback (for runtime breakpoints only). This helps determine
# whether a crash is tied to TP=4 scheduling instead of model compatibility.
add_strategy "fallback-single-card-tp1" \
  "" "$base_strategy_use_v1" "$base_strategy_required_families" \
  "4" "1"

resolve_model_for_strategy() {
    local strategy_idx="$1"
    local endpoint="$2"

    local required_families="${strategy_required_families[$strategy_idx]:-}"
  local strategy_name="${strategy_names[$strategy_idx]:-unknown}"
  local strategy_npu="${strategy_npu_devices[$strategy_idx]:-$base_strategy_npu_devices}"
  local strategy_tp="${strategy_tp_sizes[$strategy_idx]:-$base_strategy_tp_size}"
    local resolver_out resolver_err
    resolver_out="$(mktemp)"
    resolver_err="$(mktemp)"

    if ! HF_ENDPOINT="$endpoint" \
      VLLM_ENGINE_REQUIRED_FAMILIES="$required_families" \
      VLLM_ENGINE_AUTO_RESOLVE_MODEL=1 \
      VLLM_ENGINE_AUTO_DOWNLOAD="$auto_download_default" \
      "$PYTHON_BIN_DEFAULT" "$repo_root/tools/resolve_vllm_hust_model.py" >"$resolver_out" 2>"$resolver_err"; then
      log "strategy=$strategy_name resolver failed (endpoint=$endpoint)."
      if [[ -s "$resolver_err" ]]; then
        sed "s/^/[retry-resolver] /" "$resolver_err" | tee -a "$retry_log_file"
      fi
      rm -f "$resolver_out" "$resolver_err"
      return 1
    fi

    # Clear any previous resolved model env values to avoid stale reuse across
    # attempts (important when strategy flips families).
    systemctl --user unset-environment \
      VLLM_ENGINE_MODEL_PATH \
      VLLM_ENGINE_SERVED_MODEL_NAME \
      VLLM_ENGINE_ACTUAL_MODEL_ID \
      VLLM_ENGINE_MODEL_SOURCE \
      VLLM_ENGINE_MODEL_FAMILY \
      VLLM_ENGINE_REPO_ROOT \
      > /dev/null 2>&1 || true

    local line key value normalized_value
    while IFS= read -r line; do
      [[ "$line" == VLLM_ENGINE_*'='* ]] || continue
      key="${line%%=*}"
      value="${line#*=}"
      # remove matching shell quotes from resolver output; this output is shell-safe by design.
      if [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]] \
         || [[ "${value:0:1}" == "\"" && "${value: -1}" == "\"" ]]; then
        value="${value:1:${#value}-2}"
      fi
      systemctl --user set-environment "$key=$value" >/dev/null
    done <"$resolver_out"
    rm -f "$resolver_out" "$resolver_err"
    return 0
}

detect_runtime_port() {
  local fallback_port="$1"
  local discovered_port
  discovered_port="$(tail -n 60 "$engine_log_file" 2>/dev/null \
    | awk -F'port=' '/host=/{gsub(/[^0-9].*$/,"",$2); if ($2 ~ /^[0-9]+$/) print $2; exit}')"
  if [[ -n "$discovered_port" ]]; then
    echo "$discovered_port"
    return
  fi
  echo "$fallback_port"
}

normalize_json_model() {
  "$PYTHON_BIN_DEFAULT" - "$1" <<'PY'
import json
import sys

path = sys.argv[1]
raw = open(path, "r", encoding="utf-8", errors="replace").read()
data = json.loads(raw)
models = data.get("data") or []
if not models:
    # Some implementations return {"object":"list","data":[...]}
    data = data.get("models") or data.get("result") or []
    models = data if isinstance(data, list) else models
if not isinstance(models, list) or not models:
    sys.exit(1)
for item in models:
    if isinstance(item, str):
        print(item)
        sys.exit(0)
    if isinstance(item, dict) and item.get("id"):
        print(item["id"])
        sys.exit(0)
sys.exit(1)
PY
}

# Avoid accidentally routing downloads through local VPN/proxy settings.
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy 2>/dev/null || true
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-127.0.0.1,localhost}"

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$retry_log_file"
}

check_upstream() {
    local engine_health="http://$engine_host:$engine_port/health"
    local engine_models="http://$engine_host:$engine_port/v1/models"
    local proxy_chat="$proxy_base/chat/completions"

    if ! curl -fsS --max-time "$check_timeout" "$engine_health" >/dev/null; then
        return 1
    fi
    if ! curl -fsS --max-time "$check_timeout" "$engine_models" >/dev/null; then
        return 1
    fi

    local response_file
    response_file=$(mktemp)
    if ! curl -fsS --max-time "$check_timeout" \
        -H "Authorization: Bearer $proxy_api_key" \
        -H "Content-Type: application/json" \
        -d '{"model":"'$model_to_test'","messages":[{"role":"user","content":"健康检查"}],"max_tokens":8,"stream":false}' \
        "$proxy_chat" >"$response_file"; then
        rm -f "$response_file"
        return 1
    fi

    "$PYTHON_BIN_DEFAULT" - "$response_file" <<'PY'
import json
import sys
path = sys.argv[1]
raw = open(path, "r", encoding="utf-8", errors="replace").read()
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    raise SystemExit(1)
if not isinstance(data, dict):
    raise SystemExit(1)
choices = data.get("choices")
if not isinstance(choices, list) or not choices:
    raise SystemExit(1)
first = choices[0]
if not isinstance(first, dict):
    raise SystemExit(1)
message = first.get("message", {})
if not isinstance(message, dict) or not message.get("content"):
    raise SystemExit(1)
PY
    local status=$?
    rm -f "$response_file"
    if [[ $status -ne 0 ]]; then
        return 1
    fi
}

restart_engine_once() {
    local endpoint="$1"
    local strategy_idx="$2"
    local strategy_name
    local strategy_npu
    local strategy_tp
    strategy_name="${strategy_names[$strategy_idx]:-fallback}"
    strategy_npu="${strategy_npu_devices[$strategy_idx]:-$base_strategy_npu_devices}"
    strategy_tp="${strategy_tp_sizes[$strategy_idx]:-$base_strategy_tp_size}"

    log "setting HF_ENDPOINT=$endpoint for fallback-ready model download"
    systemctl --user unset-environment HF_ENDPOINT HF_HUB_OFFLINE HF_ENDPOINTS >/dev/null 2>&1 || true
    systemctl --user set-environment HF_ENDPOINT="$endpoint" HF_HUB_OFFLINE="0" >/dev/null 2>&1 || true
    systemctl --user unset-environment \
      VLLM_ENGINE_REQUIRED_FAMILIES \
      VLLM_ENGINE_QUANTIZATION \
      VLLM_ENGINE_ENFORCE_EAGER \
      VLLM_ENGINE_EXTRA_ARGS_JSON \
      VLLM_ENGINE_COMPILATION_CONFIG \
      VLLM_ENGINE_RUNTIME_VISIBLE_DEVICES \
      VLLM_USE_V1 \
      VLLM_ENGINE_AUTO_RESOLVE_MODEL \
      VLLM_ENGINE_AUTO_DOWNLOAD \
      ASCEND_VISIBLE_DEVICES \
      ASCEND_RT_VISIBLE_DEVICES \
      >/dev/null 2>&1 || true

    systemctl --user set-environment \
      VLLM_ENGINE_REQUIRED_FAMILIES="${strategy_required_families[$strategy_idx]:-}" \
      VLLM_ENGINE_QUANTIZATION="${strategy_quantizations[$strategy_idx]:-}" \
      VLLM_ENGINE_ENFORCE_EAGER="${base_strategy_enforce_eager}" \
      VLLM_ENGINE_EXTRA_ARGS_JSON="${base_strategy_extra_args}" \
      VLLM_ENGINE_COMPILATION_CONFIG="${base_strategy_compilation_config}" \
      VLLM_USE_V1="${strategy_use_v1[$strategy_idx]:-$base_strategy_use_v1}" \
      VLLM_ENGINE_NPU_DEVICES="$strategy_npu" \
      VLLM_ENGINE_RUNTIME_VISIBLE_DEVICES="$strategy_npu" \
      ASCEND_RT_VISIBLE_DEVICES="$strategy_npu" \
      ASCEND_VISIBLE_DEVICES="$strategy_npu" \
      VLLM_ENGINE_TP_SIZE="$strategy_tp" \
      VLLM_ENGINE_AUTO_RESOLVE_MODEL=1 \
      VLLM_ENGINE_AUTO_DOWNLOAD="$auto_download_default" \
      >/dev/null 2>&1 || true

    # Resolve model explicitly for this strategy; this allows on-the-fly family fallback.
    if ! resolve_model_for_strategy "$strategy_idx" "$endpoint"; then
      return 2
    fi

    if ! systemctl --user restart sage-mate-vllm-engine.service; then
        return 1
    fi
    log "restart invoked for strategy=${strategy_name} endpoint=$endpoint"
    return 0
}

attempt=1
while true; do
    strategy_count="${#strategy_names[@]}"
    strategy_index=$(( (attempt - 1) % strategy_count ))
    endpoint_index=$(( ((attempt - 1) / strategy_count) % ${#hf_endpoints[@]} ))
    endpoint="${hf_endpoints[$endpoint_index]}"
    strategy_name="${strategy_names[$strategy_index]:-baseline}"

    log "--- attempt #$attempt start strategy=$strategy_name endpoint=$endpoint ---"

    if ! restart_engine_once "$endpoint" "$strategy_index"; then
        log "restart failed for endpoint=$endpoint; waiting ${base_delay}s before retry"
        if [[ "$max_attempts" != "0" ]] && ((attempt >= max_attempts)); then
            log "reached max attempts=${max_attempts}; stopping"
            exit 1
        fi
        endpoint_index=$(((endpoint_index + 1) % ${#hf_endpoints[@]}))
        sleep_seconds=$((base_delay + (attempt - 1) * 8))
        if ((sleep_seconds > 120)); then
            sleep_seconds=120
        fi
        log "sleeping ${sleep_seconds}s before next attempt"
        sleep "$sleep_seconds"
        ((attempt++))
        continue
    fi

    # Give the service a brief grace period after restart before probing.
    active_port="$(detect_runtime_port "$engine_port")"
    waited=0
    while (( waited < startup_wait_seconds )); do
      if check_upstream "$engine_host" "$active_port"; then
        break
      fi
      sleep 10
      ((waited += 10))
      status="$(systemctl --user is-active sage-mate-vllm-engine.service 2>/dev/null || true)"
      if [[ "$status" != "active" ]]; then
        log "service lost active state during startup wait (status=${status:-unknown}); abandoning attempt."
        break
      fi
    done

    if (( waited < startup_wait_seconds )); then
        log "inference health + chat completion probe passed"
        model_line="$(grep -m 1 'selected .* model' "$engine_log_file" || true)"
        log "resolved model: ${model_line:-unknown}"
        log "active strategy: ${strategy_name}"
        log "active endpoint: ${endpoint}"
        log "active port: ${active_port}"
        exit 0
    fi

    log "probe failed on attempt #$attempt; tail runtime log"
    tail -n 120 "$engine_log_file" >>"$retry_log_file" || true
    tail -n 120 /tmp/sage-mate-runtime-private/logs/* 2>/dev/null | tail -n 80 >>"$retry_log_file" || true

    if [[ "$max_attempts" != "0" ]] && ((attempt >= max_attempts)); then
        log "reached max attempts=${max_attempts}; stopping"
        exit 1
    fi

    endpoint_index=$(((endpoint_index + 1) % ${#hf_endpoints[@]}))
    sleep_seconds=$((base_delay + (attempt - 1) * 8))
    if ((sleep_seconds > 120)); then
        sleep_seconds=120
    fi
    log "sleeping ${sleep_seconds}s before next attempt"
    sleep "$sleep_seconds"
    ((attempt++))
done
