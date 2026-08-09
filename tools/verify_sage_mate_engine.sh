#!/usr/bin/env bash
# Read-only verification for the canonical Sage Mate engine lock.

set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
env_file="$repo_root/.env"
unit="${SAGE_MATE_ENGINE_UNIT:-sage-mate-vllm-engine.service}"
host="${VLLM_ENGINE_CONNECT_HOST:-127.0.0.1}"
port="${VLLM_ENGINE_CONNECT_PORT:-${VLLM_ENGINE_PORT:-8000}}"
container="${VLLM_ENGINE_CONTAINER:-}"
api_key="${VLLM_HUST_API_KEY:-${VLLM_ENGINE_API_KEY:-${DIGITAL_TWIN_API_KEY:-}}}"
if [[ -f "$env_file" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# || "$line" != *=* ]] && continue
    key="${line%%=*}"; key="${key// /}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$line"
  done < "$env_file"
  host="${VLLM_ENGINE_CONNECT_HOST:-${VLLM_ENGINE_HOST:-127.0.0.1}}"
  port="${VLLM_ENGINE_CONNECT_PORT:-${VLLM_ENGINE_PORT:-8000}}"
  container="${VLLM_ENGINE_CONTAINER:-$container}"
  api_key="${VLLM_HUST_API_KEY:-${VLLM_ENGINE_API_KEY:-${DIGITAL_TWIN_API_KEY:-$api_key}}}"
fi

echo "[sage-mate-verify] unit=$unit status=$(systemctl --user is-active "$unit" 2>/dev/null || true)"
systemctl --user is-active --quiet "$unit" || { systemctl --user status "$unit" --no-pager || true; exit 1; }

health_url="http://$host:$port/health"
models_url="http://$host:$port/v1/models"
curl_auth=()
if [[ -n "$api_key" ]]; then
  curl_auth+=(--header "Authorization: Bearer $api_key")
fi
curl --fail --silent --show-error --max-time "${SAGE_MATE_VERIFY_TIMEOUT_SECONDS:-20}" "${curl_auth[@]}" "$health_url" >/dev/null
models="$(curl --fail --silent --show-error --max-time "${SAGE_MATE_VERIFY_TIMEOUT_SECONDS:-20}" "${curl_auth[@]}" "$models_url")"
echo "[sage-mate-verify] health=OK models=$models"

if [[ "${SAGE_MATE_VERIFY_CHAT:-1}" != "0" ]]; then
  chat_payload='{"model":"__MODEL__","messages":[{"role":"user","content":"Reply with exactly OK."}],"max_tokens":8,"temperature":0}'
  model_id="$(MODEL_JSON="$models" python3 - <<'PY'
import json
import os
data = json.loads(os.environ["MODEL_JSON"])
items = data.get("data", [])
print(items[0].get("id", "") if items else "")
PY
)"
  [[ -n "$model_id" ]] || { echo "ERROR: /v1/models returned no model id" >&2; exit 1; }
  chat_payload="${chat_payload/__MODEL__/$model_id}"
  chat_response="$(curl --fail --silent --show-error --max-time "${SAGE_MATE_VERIFY_CHAT_TIMEOUT_SECONDS:-90}" \
    "${curl_auth[@]}" -H 'Content-Type: application/json' -d "$chat_payload" \
    "http://$host:$port/v1/chat/completions")"
  [[ "$chat_response" == *'"content"'* ]] || { echo "ERROR: chat completion returned no content" >&2; exit 1; }
  echo "[sage-mate-verify] chat=OK model=$model_id"
fi

if [[ -n "$container" ]] && command -v docker >/dev/null 2>&1; then
  docker_cmd=(docker)
  if ! docker info >/dev/null 2>&1; then
    docker_cmd=(sudo -n docker)
  fi
  expected_devices="${VLLM_ENGINE_NPU_DEVICES:-${VLLM_ENGINE_ALLOWED_NPU_IDS:-}}"
  mounted_devices="$("${docker_cmd[@]}" inspect -f '{{range .HostConfig.Devices}}{{.PathOnHost}} {{end}}' "$container" 2>/dev/null || true)"
  for id in ${expected_devices//,/ }; do
    [[ "$mounted_devices" == *"/dev/davinci${id}"* ]] || {
      echo "ERROR: container $container is missing physical NPU /dev/davinci$id (mounted=$mounted_devices)" >&2
      exit 1
    }
  done
  echo "[sage-mate-verify] physical_npu_mounts=$expected_devices"
  cmd="$("${docker_cmd[@]}" exec "$container" ps -eo args 2>/dev/null | rg 'vllm serve' | head -n1 || true)"
  [[ -n "$cmd" ]] || { echo "ERROR: serving process not found in container $container" >&2; exit 1; }
  if [[ "$cmd" == *"--enforce-eager"* ]]; then
    echo "ERROR: serving command contains --enforce-eager: $cmd" >&2
    exit 1
  fi
  runtime_env="$("${docker_cmd[@]}" exec "$container" sh -c 'pid=$(ps -eo pid=,args= | awk "/vllm serve/ {print \$1; exit}"); if [ -n "$pid" ]; then tr "\\0" "\\n" </proc/$pid/environ; fi' 2>/dev/null || true)"
  [[ "$runtime_env" == *$'COMPILE_CUSTOM_KERNELS=1\n'* || "$runtime_env" == *'COMPILE_CUSTOM_KERNELS=1'* ]] || {
    echo "ERROR: COMPILE_CUSTOM_KERNELS is not enabled in container runtime" >&2
    exit 1
  }
  echo "[sage-mate-verify] graph_mode=ON command_verified"
fi

echo "[sage-mate-verify] PASS"
