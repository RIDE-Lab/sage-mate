#!/usr/bin/env bash
# run_vllm_engine.sh — Sage Mate wrapper for vLLM-HUST.
#
# The real vLLM-HUST launch path lives in vllm-hust-dev-hub:
#   host -> docker exec -> conda activation -> Ascend/CANN env -> vLLM-HUST.
# Keep this file thin so the Sage Mate systemd unit can stay stable without
# maintaining a second, drifting copy of the engine launcher.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
default_dev_hub_root="$repo_root/deps/vllm-hust-dev-hub"
dev_hub_root="${VLLM_HUST_DEV_HUB_ROOT:-$default_dev_hub_root}"
launcher="$dev_hub_root/scripts/run_vllm_hust_engine.sh"

if [[ ! -x "$launcher" ]]; then
    echo "ERROR: vLLM-HUST dev-hub submodule launcher not found or not executable: $launcher" >&2
    echo "Run: git submodule update --init --recursive deps/vllm-hust-dev-hub" >&2
    exit 1
fi

load_dotenv() {
    local env_file="$1"
    [[ -f "$env_file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        local key="${line%%=*}"
        key="${key// /}"
        [[ -z "$key" || -n "${!key:-}" ]] && continue
        export "$line"
    done < "$env_file"
}

load_dotenv "$repo_root/.env"
selector_python="${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"

# Map Sage Mate's engine variables onto dev-hub's canonical launcher variables.
# Machine-specific model/device values are mandatory: silently choosing a model
# path or NPU IDs can disrupt unrelated workloads on a different host.
resolve_engine_model() {
    local resolver_script="$repo_root/tools/resolve_vllm_hust_model.py"
    local resolve_output
    local resolve_err
    local resolve_exit=0

    if [[ ! -x "$resolver_script" ]]; then
        echo "ERROR: model resolver script is not executable: $resolver_script" >&2
        exit 2
    fi
    resolve_err="$(mktemp)"
    if ! resolve_output="$("$selector_python" "$resolver_script" 2> "$resolve_err")"; then
        resolve_exit=$?
        sed "s/^/[sage-mate] /" "$resolve_err" >&2 || true
        rm -f "$resolve_err"
        echo "ERROR: automatic model resolver failed. Set VLLM_ENGINE_MODEL_PATH in .env to continue." >&2
        exit "$resolve_exit"
    fi
    if [[ -n "$resolve_output" ]]; then
        sed "s/^/[sage-mate] /" "$resolve_err" >&2 || true
        # shellcheck disable=SC2086
        eval "$resolve_output"
    fi
    rm -f "$resolve_err"
}

is_port_free() {
    local host="$1"
    local port="$2"
    "$selector_python" - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    raise SystemExit(1)
else:
    raise SystemExit(0)
finally:
    try:
        sock.close()
    except OSError:
        pass
PY
}

resolve_engine_port() {
    local requested_port="$1"
    local host="$2"
    local max_retries="${3:-64}"
    local candidate="$requested_port"
    local tries=0
    local remap="${VLLM_ENGINE_AUTO_REMAP_PORT:-1}"

    if ! [[ "$requested_port" =~ ^[0-9]+$ ]] || ((requested_port < 1 || requested_port > 65535)); then
        echo "ERROR: invalid VLLM_ENGINE_PORT=$requested_port" >&2
        return 1
    fi

    if is_port_free "$host" "$candidate"; then
        echo "$candidate"
        return 0
    fi

    if [[ "$remap" == "0" || "$remap" == "false" ]]; then
        echo "ERROR: ${host}:${candidate} is already in use and auto-remap is disabled." >&2
        return 1
    fi

    echo "[sage-mate] ${host}:${candidate} is in use, auto-searching for a free port." >&2
    while ((tries < max_retries)) && ((candidate < 65535)); do
        ((candidate += 1))
        if is_port_free "$host" "$candidate"; then
            echo "$candidate"
            return 0
        fi
        ((tries += 1))
    done
    echo "ERROR: unable to find a free port near ${requested_port}." >&2
    return 1
}

auto_resolve_model="${VLLM_ENGINE_AUTO_RESOLVE_MODEL:-true}"
auto_resolve_model="${auto_resolve_model,,}"
if [[ -z "${VLLM_ENGINE_MODEL_PATH:-}" && ("$auto_resolve_model" == "true" || "$auto_resolve_model" == "1" || "$auto_resolve_model" == "yes") ]]; then
    resolve_engine_model
fi

if [[ -z "${VLLM_ENGINE_MODEL_PATH:-}" ]]; then
    echo "ERROR: VLLM_ENGINE_MODEL_PATH is required. Set it in the machine-local .env." >&2
    exit 2
fi

engine_devices="${VLLM_ENGINE_NPU_DEVICES:-${ASCEND_RT_VISIBLE_DEVICES:-}}"
allowed_devices="${VLLM_ENGINE_ALLOWED_NPU_IDS:-}"
if [[ -z "$engine_devices" && -n "$allowed_devices" ]]; then
    engine_devices="$allowed_devices"
    echo "[sage-mate] VLLM_ENGINE_NPU_DEVICES not set; using VLLM_ENGINE_ALLOWED_NPU_IDS=$engine_devices"
fi
engine_devices="${engine_devices//[[:space:]]/}"
if [[ -z "$engine_devices" ]]; then
    echo "ERROR: VLLM_ENGINE_NPU_DEVICES (or ASCEND_RT_VISIBLE_DEVICES) is required." >&2
    if [[ -n "$allowed_devices" ]]; then
        echo "Set it explicitly, or set VLLM_ENGINE_ALLOWED_NPU_IDS to a specific pool first." >&2
    else
        echo "Choose verified idle devices explicitly, or set VLLM_ENGINE_ALLOWED_NPU_IDS." >&2
    fi
    exit 2
fi

IFS=',' read -r -a engine_device_ids <<< "$engine_devices"
declare -A seen_device_ids=()
for device_id in "${engine_device_ids[@]}"; do
    if [[ ! "$device_id" =~ ^[0-9]+$ ]]; then
        echo "ERROR: invalid Ascend device ID in '$engine_devices': '$device_id'" >&2
        exit 2
    fi
    if [[ -n "${seen_device_ids[$device_id]:-}" ]]; then
        echo "ERROR: duplicate Ascend device ID in '$engine_devices': '$device_id'" >&2
        exit 2
    fi
    seen_device_ids[$device_id]=1
done

if [[ -n "${allowed_devices:-}" ]]; then
    allowed_devices_csv=",${allowed_devices//[[:space:]]/},"
    for device_id in "${engine_device_ids[@]}"; do
        if [[ "$allowed_devices_csv" != *",${device_id},"* ]]; then
            echo "ERROR: device $device_id is not in VLLM_ENGINE_ALLOWED_NPU_IDS=$allowed_devices" >&2
            exit 2
        fi
    done
fi

device_count="${#engine_device_ids[@]}"
engine_tp_size="${VLLM_ENGINE_TP_SIZE:-$device_count}"
if [[ ! "$engine_tp_size" =~ ^[1-9][0-9]*$ || "$engine_tp_size" -ne "$device_count" ]]; then
    echo "ERROR: VLLM_ENGINE_TP_SIZE=$engine_tp_size must equal the configured device count ($device_count)." >&2
    exit 2
fi

npu_selector="$repo_root/tools/select_idle_npus.py"
if [[ -z "$selector_python" || ! -x "$selector_python" ]]; then
    echo "ERROR: Python is required to validate Ascend device ownership." >&2
    exit 2
fi
if ! "$selector_python" "$npu_selector" --devices "$engine_devices" >/dev/null; then
    echo "ERROR: configured Ascend devices are not all idle; refusing to launch." >&2
    exit 2
fi

default_served_model="${VLLM_ENGINE_MODEL_PATH%/}"
if [[ -n "${VLLM_ENGINE_ACTUAL_MODEL_ID:-}" ]]; then
    default_served_model="${VLLM_ENGINE_ACTUAL_MODEL_ID%/}"
elif [[ -n "${VLLM_ENGINE_MODEL_FAMILY:-}" ]]; then
    default_served_model="${VLLM_ENGINE_MODEL_FAMILY}/$default_served_model"
else
    default_served_model="${default_served_model##*/}"
fi
container_suffix="${USER:-user}-$(basename "$repo_root")"
container_suffix="$(printf '%s' "$container_suffix" | tr -cs '[:alnum:]_.-' '-')"

export VLLM_ENGINE_CONTAINER="${VLLM_ENGINE_CONTAINER:-sage-mate-vllm-${container_suffix}}"
export VLLM_ENGINE_MODEL_PATH
resolved_served_name="${VLLM_ENGINE_SERVED_MODEL_NAME:-${VLLM_ENGINE_ACTUAL_MODEL_ID:-$default_served_model}}"
export VLLM_ENGINE_SERVED_MODEL_NAME="$resolved_served_name"
export DIGITAL_TWIN_MODEL_NAME="$resolved_served_name"
export VLLM_ENGINE_MODEL_SOURCE="${VLLM_ENGINE_MODEL_SOURCE:-auto}"
export VLLM_ENGINE_MODEL_FAMILY="${VLLM_ENGINE_MODEL_FAMILY:-unknown}"
export VLLM_ENGINE_HOST="${VLLM_ENGINE_HOST:-0.0.0.0}"
export VLLM_ENGINE_CONNECT_HOST="${VLLM_ENGINE_CONNECT_HOST:-${VLLM_ENGINE_HOST:-127.0.0.1}}"
resolved_engine_port="$(resolve_engine_port "${VLLM_ENGINE_PORT:-8000}" "$VLLM_ENGINE_CONNECT_HOST")" || exit 1
export VLLM_ENGINE_PORT="$resolved_engine_port"
export VLLM_ENGINE_CONNECT_PORT="${VLLM_ENGINE_CONNECT_PORT:-$VLLM_ENGINE_PORT}"
export VLLM_PROXY_UPSTREAM_BASE_URL="${VLLM_PROXY_UPSTREAM_BASE_URL:-http://$VLLM_ENGINE_CONNECT_HOST:$VLLM_ENGINE_CONNECT_PORT/v1}"
unset resolved_engine_port
export VLLM_ENGINE_TP_SIZE="$engine_tp_size"
export VLLM_ENGINE_MAX_MODEL_LEN="${VLLM_ENGINE_MAX_MODEL_LEN:-32768}"
export VLLM_ENGINE_MAX_NUM_BATCHED_TOKENS="${VLLM_ENGINE_MAX_NUM_BATCHED_TOKENS:-$VLLM_ENGINE_MAX_MODEL_LEN}"
export VLLM_ENGINE_GPU_MEM_UTIL="${VLLM_ENGINE_GPU_MEM_UTIL:-0.9}"
export VLLM_ENGINE_MAX_NUM_SEQS="${VLLM_ENGINE_MAX_NUM_SEQS:-16}"
export VLLM_ENGINE_DTYPE="${VLLM_ENGINE_DTYPE:-bfloat16}"
export VLLM_ENGINE_NPU_DEVICES="$engine_devices"
export ASCEND_RT_VISIBLE_DEVICES="$engine_devices"
export ASCEND_VISIBLE_DEVICES="$engine_devices"
export VLLM_ENGINE_CONDA_ENV="${VLLM_ENGINE_CONDA_ENV:-vllm-hust-dev}"
export VLLM_ENGINE_BIN="${VLLM_ENGINE_BIN:-vllm-hust}"
export VLLM_ENGINE_BASE_PYTHONPATH="${VLLM_ENGINE_BASE_PYTHONPATH:-/workspace/vllm-hust:/workspace/vllm-ascend-hust}"
export VLLM_ENGINE_CONTAINER_LOG_FILE="${VLLM_ENGINE_CONTAINER_LOG_FILE:-/tmp/sage-mate-vllm-engine.redacted.log}"
export VLLM_ENGINE_AUTO_CREATE_CONTAINER="${VLLM_ENGINE_AUTO_CREATE_CONTAINER:-true}"
export VLLM_ENGINE_REPLACE_EXISTING="${VLLM_ENGINE_REPLACE_EXISTING:-true}"
export VLLM_ENGINE_CONTAINER_NON_INTERACTIVE="${VLLM_ENGINE_CONTAINER_NON_INTERACTIVE:-1}"
export VLLM_ENGINE_AUTO_PREPARE_ENV="${VLLM_ENGINE_AUTO_PREPARE_ENV:-0}"
export VLLM_ENGINE_LOAD_REPO_ENV=false
export VLLM_HUST_AUTO_ENABLE_CONTAINER_SSH="${VLLM_HUST_AUTO_ENABLE_CONTAINER_SSH:-0}"

# The vLLM-HUST runtime must use Sage Mate's pinned submodules, not an
# operator's shared /home checkout. dev-hub's container launcher maps
# HOST_WORKSPACE_ROOT to /workspace, so /workspace/vllm-hust resolves to this
# repo's deps/vllm-hust.
export HOST_WORKSPACE_ROOT="${HOST_WORKSPACE_ROOT:-$repo_root/deps}"
if [[ "$HOST_WORKSPACE_ROOT" != /* ]]; then
    HOST_WORKSPACE_ROOT="$repo_root/${HOST_WORKSPACE_ROOT#./}"
    export HOST_WORKSPACE_ROOT
fi
export CONTAINER_WORKSPACE_ROOT="${CONTAINER_WORKSPACE_ROOT:-/workspace}"
export CONTAINER_WORKDIR="${CONTAINER_WORKDIR:-/workspace/vllm-hust-dev-hub}"

prepare_host_model_mount() {
    local model_path="${VLLM_ENGINE_MODEL_PATH:-}"
    local workspace_root="${HOST_WORKSPACE_ROOT%/}"
    local container_root="${CONTAINER_WORKSPACE_ROOT%/}"

    # 1) Keep container-visible model paths in sync with workspace mapping.
    if [[ "$model_path" == "$workspace_root/"* || "$model_path" == "$workspace_root" ]]; then
        local relative_path="${model_path#"$workspace_root"}"
        VLLM_ENGINE_MODEL_PATH="$container_root${relative_path}"
        return 0
    fi

    # 2) For models outside the workspace root, create a sibling symlink under
    #    HOST_WORKSPACE_ROOT so the container runtime can mirror the mount into
    #    the same absolute path.
    [[ "$model_path" == /* ]] || return 0
    [[ -e "$model_path" ]] || return 0
    [[ "$model_path" != "$workspace_root"* ]] || return 0

    local link_path="$workspace_root/.sage-mate-primary-model"
    if [[ -L "$link_path" || -e "$link_path" ]]; then
        local current_target
        current_target="$(readlink "$link_path" 2>/dev/null || true)"
        if [[ "$current_target" != "$model_path" ]]; then
            rm -f "$link_path"
        fi
    fi
    if [[ ! -e "$link_path" ]]; then
        ln -s "$model_path" "$link_path"
    fi
}

resolve_docker_cmd() {
    local -a cmd=(docker)
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        printf '%s\n' "docker"
        return 0
    fi
    if command -v docker >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
        printf '%s\n' "sudo -n docker"
        return 0
    fi
    return 1
}


container_is_running() {
    local -a cmd=("${docker_cmd[@]}")
    local container_name="${VLLM_ENGINE_CONTAINER:?}"
    [[ "$("${cmd[@]}" inspect -f '{{.State.Running}}' "$container_name" 2>/dev/null || true)" == "true" ]]
}

model_dir_visible_in_container() {
    local model_path="$1"
    local -a cmd=("${docker_cmd[@]}")
    local container_name="${VLLM_ENGINE_CONTAINER:?}"
    "${cmd[@]}" exec "$container_name" test -e "$model_path"
}

ensure_container_model_visibility() {
    local container_name="${VLLM_ENGINE_CONTAINER:?}"
    local model_path="${VLLM_ENGINE_MODEL_PATH}"
    local -a cmd=("${docker_cmd[@]}")

    [[ -n "$model_path" ]] || return 0
    [[ "$model_path" == /* ]] || return 0

    if ! container_is_running; then
        return 0
    fi

    if model_dir_visible_in_container "$model_path"; then
        return 0
    fi

    echo "[sage-mate] container '$container_name' is running but cannot see model path '$model_path'; recreating."
    "${cmd[@]}" stop "$container_name" >/dev/null 2>&1 || true
    "${cmd[@]}" rm -f "$container_name" >/dev/null 2>&1 || true
}

prepare_host_model_mount

if [[ -z "${VLLM_HUST_API_KEY:-}" && -n "${DIGITAL_TWIN_API_KEY:-}" ]]; then
    export VLLM_ENGINE_API_KEY="$DIGITAL_TWIN_API_KEY"
fi

if ! docker_cmd="$(resolve_docker_cmd)"; then
    echo "ERROR: docker is unavailable. Start Docker or configure passwordless sudo access." >&2
    exit 1
fi
read -r -a docker_cmd <<< "$docker_cmd"
ensure_container_model_visibility

echo "[sage-mate] delegating vLLM-HUST launch to $launcher"
exec "$launcher"
