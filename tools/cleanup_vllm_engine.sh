#!/usr/bin/env bash
# Best-effort cleanup for Faculty Twin's dedicated vLLM-HUST engine runtime.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dev_hub_root="${VLLM_HUST_DEV_HUB_ROOT:-$repo_root/deps/vllm-hust-dev-hub}"
container_cleanup="$dev_hub_root/scripts/cleanup_vllm_hust_engine.sh"

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
source "$repo_root/tools/lib/instance_control.sh"
sage_mate_route_instance_operation cleanup

export VLLM_ENGINE_CONTAINER="${VLLM_ENGINE_CONTAINER:-faculty_twin_vllm_hust}"
export VLLM_ENGINE_PORT="${VLLM_ENGINE_PORT:-8000}"
export VLLM_ENGINE_CONTAINER_LOG_FILE="${VLLM_ENGINE_CONTAINER_LOG_FILE:-/tmp/sage-mate-vllm-engine.redacted.log}"
export VLLM_ENGINE_AGGRESSIVE_CLEANUP="${VLLM_ENGINE_AGGRESSIVE_CLEANUP:-1}"

run_container_cleanup() {
    [[ -x "$container_cleanup" ]] || return 0
    "$container_cleanup" || true
}

kill_process_group() {
    local signal="$1"
    local pgid="$2"
    [[ "$pgid" =~ ^[0-9]+$ ]] || return 0
    kill "-$signal" -- "-$pgid" 2>/dev/null || sudo kill "-$signal" -- "-$pgid" 2>/dev/null || true
}

cleanup_host_process_groups() {
    [[ "${VLLM_ENGINE_HOST_CLEANUP:-1}" == "1" || "${VLLM_ENGINE_HOST_CLEANUP:-true}" == "true" ]] || return 0

    local groups=""
    local own_pgid=""
    own_pgid="$(ps -o pgid= -p "$$" | tr -d ' ')"
    groups=$(ps -eo pgid=,args= | awk \
        -v log_file="$VLLM_ENGINE_CONTAINER_LOG_FILE" \
        -v container="$VLLM_ENGINE_CONTAINER" \
        -v own_pgid="$own_pgid" '
        $1 == own_pgid {
            next
        }
        index($0, "tee -a " log_file) ||
        (index($0, container) && index($0, "docker exec") && index($0, "/tmp/vllm-hust-engine.")) {
            print $1
        }
    ' | sort -u)

    [[ -n "$groups" ]] || return 0

    local pgid=""
    for pgid in $groups; do
        kill_process_group TERM "$pgid"
    done
    sleep 2
    for pgid in $groups; do
        kill_process_group KILL "$pgid"
    done
    echo "[faculty-twin] cleaned residual vLLM host process group(s): $groups"
}

run_container_cleanup
cleanup_host_process_groups
