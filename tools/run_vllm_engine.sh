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

# Map Sage Mate's engine variables onto dev-hub's canonical launcher variables.
# Machine-specific model/device values are mandatory: silently choosing a model
# path or NPU IDs can disrupt unrelated workloads on a different host.
if [[ -z "${VLLM_ENGINE_MODEL_PATH:-}" ]]; then
    echo "ERROR: VLLM_ENGINE_MODEL_PATH is required. Set it in the machine-local .env." >&2
    exit 2
fi

engine_devices="${VLLM_ENGINE_NPU_DEVICES:-${ASCEND_RT_VISIBLE_DEVICES:-}}"
engine_devices="${engine_devices//[[:space:]]/}"
if [[ -z "$engine_devices" ]]; then
    echo "ERROR: VLLM_ENGINE_NPU_DEVICES (or ASCEND_RT_VISIBLE_DEVICES) is required." >&2
    echo "Choose verified idle devices explicitly, or use the hosted installer to auto-select them." >&2
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

device_count="${#engine_device_ids[@]}"
engine_tp_size="${VLLM_ENGINE_TP_SIZE:-$device_count}"
if [[ ! "$engine_tp_size" =~ ^[1-9][0-9]*$ || "$engine_tp_size" -ne "$device_count" ]]; then
    echo "ERROR: VLLM_ENGINE_TP_SIZE=$engine_tp_size must equal the configured device count ($device_count)." >&2
    exit 2
fi

npu_selector="$repo_root/tools/select_idle_npus.py"
selector_python="${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"
if [[ -z "$selector_python" || ! -x "$selector_python" ]]; then
    echo "ERROR: Python is required to validate Ascend device ownership." >&2
    exit 2
fi
if ! "$selector_python" "$npu_selector" --devices "$engine_devices" >/dev/null; then
    echo "ERROR: configured Ascend devices are not all idle; refusing to launch." >&2
    exit 2
fi

default_served_model="${VLLM_ENGINE_MODEL_PATH%/}"
default_served_model="${default_served_model##*/}"
container_suffix="${USER:-user}-$(basename "$repo_root")"
container_suffix="$(printf '%s' "$container_suffix" | tr -cs '[:alnum:]_.-' '-')"

export VLLM_ENGINE_CONTAINER="${VLLM_ENGINE_CONTAINER:-sage-mate-vllm-${container_suffix}}"
export VLLM_ENGINE_MODEL_PATH
export VLLM_ENGINE_SERVED_MODEL_NAME="${VLLM_ENGINE_SERVED_MODEL_NAME:-${DIGITAL_TWIN_MODEL_NAME:-$default_served_model}}"
export VLLM_ENGINE_HOST="${VLLM_ENGINE_HOST:-0.0.0.0}"
export VLLM_ENGINE_PORT="${VLLM_ENGINE_PORT:-8000}"
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
    [[ "$model_path" == /* ]] || return 0
    [[ -e "$model_path" ]] || return 0
    [[ "$model_path" != "$HOST_WORKSPACE_ROOT"* ]] || return 0

    local link_path="$HOST_WORKSPACE_ROOT/.sage-mate-primary-model"
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

prepare_host_model_mount

if [[ -z "${VLLM_HUST_API_KEY:-}" && -n "${DIGITAL_TWIN_API_KEY:-}" ]]; then
    export VLLM_ENGINE_API_KEY="$DIGITAL_TWIN_API_KEY"
fi

echo "[sage-mate] delegating vLLM-HUST launch to $launcher"
exec "$launcher"
