#!/usr/bin/env bash
# Canonical Sage Mate Ascend deployment lock.
#
# This is the only supported entrypoint for applying the machine-local engine
# contract. It deliberately reads .env as the source of truth, clears stale
# systemd-user values left by older retry scripts, validates physical NPU
# ownership, and then restarts the managed service.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
env_file="$repo_root/.env"
unit="${SAGE_MATE_ENGINE_UNIT:-sage-mate-vllm-engine.service}"
runtime_root="${DIGITAL_TWIN_RUNTIME_DIR:-$repo_root/../sage-mate-runtime-private}"
python_bin="${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"

[[ -f "$env_file" ]] || { echo "ERROR: missing machine-local $env_file" >&2; exit 2; }
[[ -n "$python_bin" && -x "$python_bin" ]] || { echo "ERROR: python3 is required" >&2; exit 2; }

# .env is an operator-owned shell-compatible file. Load it explicitly so a
# stale caller environment cannot override the machine contract.
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# || "$line" != *=* ]] && continue
  key="${line%%=*}"; key="${key// /}"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  export "$line"
done < "$env_file"

runtime_root="${DIGITAL_TWIN_RUNTIME_DIR:-$repo_root/../sage-mate-runtime-private}"
lock_file="$runtime_root/engine-deployment.lock.env"
mkdir -p "$runtime_root"
exec 9>"$runtime_root/deployment.lock"
if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
  echo "ERROR: another Sage Mate deployment lock is already running: $runtime_root/deployment.lock" >&2
  exit 4
fi

# Reconcile orphaned legacy retry loops before applying the contract. These are
# operator scripts from this repository, never arbitrary processes.
if [[ "${SAGE_MATE_AUTOKILL_LEGACY_RETRY:-1}" == "1" ]]; then
  while read -r pid args; do
    [[ -n "${pid:-}" && "$pid" != "$$" ]] || continue
    [[ "$args" == *"retry_deploy_vllm_ascend_until_success.sh"* ]] || continue
    echo "[sage-mate-lock] stopping orphaned legacy retry pid=$pid"
    kill "$pid" 2>/dev/null || true
  done < <(ps -eo pid=,args=)
fi

physical_devices="${VLLM_ENGINE_NPU_DEVICES:-${VLLM_ENGINE_ALLOWED_NPU_IDS:-}}"
physical_devices="${physical_devices//[[:space:]]/}"
[[ -n "$physical_devices" ]] || { echo "ERROR: set VLLM_ENGINE_NPU_DEVICES in $env_file" >&2; exit 2; }

IFS=',' read -r -a device_ids <<< "$physical_devices"
device_count="${#device_ids[@]}"
[[ "$device_count" -gt 0 ]] || { echo "ERROR: invalid NPU device set: $physical_devices" >&2; exit 2; }
for id in "${device_ids[@]}"; do
  [[ "$id" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid NPU id '$id'" >&2; exit 2; }
done
tp_size="${VLLM_ENGINE_TP_SIZE:-$device_count}"
[[ "$tp_size" =~ ^[1-9][0-9]*$ && "$tp_size" -eq "$device_count" ]] || {
  echo "ERROR: VLLM_ENGINE_TP_SIZE=$tp_size must equal physical NPU count=$device_count" >&2; exit 2;
}

# The lock contract keeps the target engine in graph mode. Clear the explicit
# target flag, but retain an operator-declared extra-args JSON from the
# machine-local .env (for example, a model's documented MTP configuration).
# This prevents stale retry-loop values from leaking in while still allowing
# model-specific, reproducible vLLM flags to be part of the locked contract.
VLLM_ENGINE_ENFORCE_EAGER=0
VLLM_ENGINE_EXTRA_ARGS_JSON="${VLLM_ENGINE_EXTRA_ARGS_JSON:-}"
COMPILE_CUSTOM_KERNELS=1
export VLLM_ENGINE_ENFORCE_EAGER VLLM_ENGINE_EXTRA_ARGS_JSON COMPILE_CUSTOM_KERNELS

docker_command() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    printf 'docker\n'
  elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    printf 'sudo -n docker\n'
  fi
}

find_engine_container_on_port() {
  local port="${VLLM_ENGINE_PORT:-8000}"
  local docker_bin
  local name
  local args
  docker_bin="$(docker_command)"
  [[ -n "$docker_bin" ]] || return 0
  local -a docker_cmd
  read -r -a docker_cmd <<< "$docker_bin"
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    args="$("${docker_cmd[@]}" exec "$name" ps -eo args= 2>/dev/null || true)"
    if awk -v port="$port" '
      /vllm/ && / serve / && ($0 ~ ("--port " port) || $0 ~ ("--port=" port)) { found=1 }
      END { exit !found }
    ' <<< "$args"; then
      printf '%s\n' "$name"
      return 0
    fi
  done < <("${docker_cmd[@]}" ps --format '{{.Names}}')
}

previous_engine_container="$(find_engine_container_on_port)"

cleanup_managed_container() {
  local container_name="$1"
  [[ -n "$container_name" ]] || return 0
  local docker_bin
  docker_bin="$(docker_command)"
  [[ -n "$docker_bin" ]] || return 0
  read -r -a docker_cmd <<< "$docker_bin"
  if "${docker_cmd[@]}" inspect "$container_name" >/dev/null 2>&1; then
    echo "[sage-mate-lock] removing managed container '$container_name' to release NPUs"
    "${docker_cmd[@]}" rm -f "$container_name" >/dev/null 2>&1 || true
  fi
}

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user stop "$unit" >/dev/null 2>&1 || true
  systemctl --user unset-environment \
    VLLM_ENGINE_RUNTIME_VISIBLE_DEVICES ASCEND_RT_VISIBLE_DEVICES ASCEND_VISIBLE_DEVICES \
    VLLM_ENGINE_ENFORCE_EAGER VLLM_ENGINE_EXTRA_ARGS_JSON \
    VLLM_ENGINE_COMPILATION_CONFIG VLLM_ENGINE_REQUIRED_FAMILIES \
    COMPILE_CUSTOM_KERNELS \
    VLLM_ENGINE_MODEL_PATH VLLM_ENGINE_SERVED_MODEL_NAME VLLM_ENGINE_ACTUAL_MODEL_ID \
    VLLM_ENGINE_MODEL_SOURCE VLLM_ENGINE_MODEL_FAMILY VLLM_ENGINE_AUTO_RESOLVE_MODEL \
    VLLM_ENGINE_AUTO_DOWNLOAD VLLM_USE_V1 \
    VLLM_ENGINE_MAX_MODEL_LEN VLLM_ENGINE_MAX_NUM_BATCHED_TOKENS VLLM_ENGINE_MAX_NUM_SEQS \
    VLLM_ENGINE_GPU_MEM_UTIL VLLM_ENGINE_DTYPE VLLM_ENGINE_QUANTIZATION \
    VLLM_ENGINE_KV_CACHE_DTYPE VLLM_ENGINE_KV_CACHE_MEMORY_BYTES \
    VLLM_ENGINE_CONTAINER_HOME VLLM_ASCEND_ENABLE_MLAPO \
    VLLM_ASCEND_KV_CACHE_FREE_MEMORY_FRACTION \
    VLLM_ENGINE_CONTAINER_SHM_SIZE VLLM_ENGINE_CONTAINER_LD_PRELOAD SHM_SIZE VLLM_HUST_CONTAINER_PRIVILEGED \
    VLLM_ENGINE_ENABLE_PREFIX_CACHING VLLM_ENGINE_ENABLE_CHUNKED_PREFILL \
    VLLM_ENGINE_ENABLE_EXPERT_PARALLEL VLLM_ENGINE_VLLM_VERSION \
    VLLM_ENGINE_CANN_VERSION VLLM_ENGINE_TORCH_VERSION VLLM_ENGINE_TORCH_NPU_VERSION \
    VLLM_ENGINE_IMAGE VLLM_ENGINE_CONTAINER VLLM_ASCEND_ENABLE_FLASHCOMM1 \
    HCCL_BUFFSIZE HCCL_OP_EXPANSION_MODE OMP_NUM_THREADS OMP_PROC_BIND \
    PYTORCH_NPU_ALLOC_CONF TASK_QUEUE_ENABLE LD_PRELOAD \
    >/dev/null 2>&1 || true
  # Keep physical IDs in the manager; run_vllm_engine.sh derives 0..N-1 for
  # the container and exports the physical IDs only for host ownership checks.
  systemctl --user set-environment \
    VLLM_ENGINE_NPU_DEVICES="$physical_devices" \
    VLLM_ENGINE_TP_SIZE="$tp_size" \
    VLLM_ENGINE_ENFORCE_EAGER=0 \
    VLLM_ENGINE_EXTRA_ARGS_JSON="$VLLM_ENGINE_EXTRA_ARGS_JSON" \
    COMPILE_CUSTOM_KERNELS=1 \
    VLLM_ENGINE_AUTO_RESOLVE_MODEL="${VLLM_ENGINE_AUTO_RESOLVE_MODEL:-true}" \
    VLLM_ENGINE_AUTO_DOWNLOAD="${VLLM_ENGINE_AUTO_DOWNLOAD:-1}" \
    >/dev/null
fi

if [[ -n "$previous_engine_container" ]]; then
  cleanup_managed_container "$previous_engine_container"
fi
if [[ "${VLLM_ENGINE_CONTAINER:-}" != "$previous_engine_container" ]]; then
  cleanup_managed_container "${VLLM_ENGINE_CONTAINER:-}"
fi

# Validate ownership after stopping the old service. Ascend's UDA driver can
# retain the previous container namespace briefly after its last worker exits;
# launching a replacement during that window surfaces as device_count=0 plus
# "Conflict open udevid" even though all /dev nodes are mounted correctly.
# Wait for real process/HBM release, with a finite operator-configurable bound.
release_timeout="${VLLM_ENGINE_DEVICE_RELEASE_TIMEOUT_SECONDS:-60}"
release_poll_interval="${VLLM_ENGINE_DEVICE_RELEASE_POLL_SECONDS:-2}"
[[ "$release_timeout" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: VLLM_ENGINE_DEVICE_RELEASE_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
}
[[ "$release_poll_interval" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: VLLM_ENGINE_DEVICE_RELEASE_POLL_SECONDS must be a positive integer" >&2
  exit 2
}
release_probe="$(mktemp)"
trap 'rm -f "$release_probe"' EXIT
release_deadline=$((SECONDS + release_timeout))
release_attempt=0
while ! "$python_bin" "$repo_root/tools/select_idle_npus.py" \
  --devices "$physical_devices" >/dev/null 2>"$release_probe"; do
  if (( SECONDS >= release_deadline )); then
    cat "$release_probe" >&2
    echo "ERROR: configured NPU devices did not become idle within ${release_timeout}s: $physical_devices" >&2
    exit 3
  fi
  release_attempt=$((release_attempt + 1))
  echo "[sage-mate-lock] waiting for Ascend device namespace release (attempt=$release_attempt devices=$physical_devices)"
  sleep "$release_poll_interval"
done
rm -f "$release_probe"
trap - EXIT

umask 077
{
  printf 'SAGE_MATE_ENGINE_UNIT=%q\n' "$unit"
  printf 'VLLM_ENGINE_NPU_DEVICES=%q\n' "$physical_devices"
  printf 'VLLM_ENGINE_TP_SIZE=%q\n' "$tp_size"
  printf 'VLLM_ENGINE_ENFORCE_EAGER=0\n'
  printf 'VLLM_ENGINE_EXTRA_ARGS_JSON=%q\n' "$VLLM_ENGINE_EXTRA_ARGS_JSON"
  printf 'VLLM_ENGINE_CONTAINER_SHM_SIZE=%q\n' "${VLLM_ENGINE_CONTAINER_SHM_SIZE:-}"
  printf 'VLLM_ENGINE_CONTAINER=%q\n' "${VLLM_ENGINE_CONTAINER:-}"
  printf 'COMPILE_CUSTOM_KERNELS=1\n'
  printf 'REPO_ROOT=%q\n' "$repo_root"
  printf 'LOCKED_AT_UTC=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$lock_file"

echo "[sage-mate-lock] physical NPU=$physical_devices tp=$tp_size graph_mode=ON"
echo "[sage-mate-lock] restarting $unit via tools/run_vllm_engine.sh"
systemctl --user restart "$unit"

echo "[sage-mate-lock] service restart requested; run tools/verify_sage_mate_engine.sh for verification"
