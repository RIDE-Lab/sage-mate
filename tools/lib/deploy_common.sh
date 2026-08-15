#!/usr/bin/env bash

# Shared deployment helpers for shell entrypoints.
# Keep this file side-effect light: functions only, no command execution on source.

set -euo pipefail

deploy_log() {
    if declare -F log >/dev/null 2>&1; then
        log "$@"
    else
        printf '[deploy] %s\n' "$*"
    fi
}

deploy_warn() {
    if declare -F warn >/dev/null 2>&1; then
        warn "$@"
    else
        printf '[deploy] %s\n' "$*" >&2
    fi
}

deploy_fail() {
    if declare -F fail >/dev/null 2>&1; then
        fail "$@"
    else
        printf '[deploy] %s\n' "$*" >&2
        exit 1
    fi
}

python_meets_min_version() {
    local candidate="$1"
    local min_major="${2:-3}"
    local min_minor="${3:-11}"
    "$candidate" - "$min_major" "$min_minor" <<'PY'
import sys

major = int(sys.argv[1])
minor = int(sys.argv[2])
raise SystemExit(0 if sys.version_info >= (major, minor) else 1)
PY
}

project_optional_requirement() {
    local repo_root="$1" python_bin="$2" extra_name="$3" package_prefix="$4"
    "$python_bin" - "$repo_root/pyproject.toml" "$extra_name" "$package_prefix" <<'PY'
import sys
import tomllib

project_path, extra_name, package_prefix = sys.argv[1:]
with open(project_path, "rb") as handle:
    extras = tomllib.load(handle)["project"]["optional-dependencies"]
for requirement in extras[extra_name]:
    if requirement.lower().startswith(package_prefix.lower()):
        print(requirement)
        break
else:
    raise SystemExit(
        f"project extra {extra_name!r} does not declare {package_prefix!r}"
    )
PY
}

ensure_neuromem_collection_runtime() {
    local repo_root="$1" python_bin="$2"
    if "$python_bin" -c "from importlib.metadata import version; version('isage-neuromem'); from sage.neuromem import UnifiedCollection" 2>/dev/null; then
        return 0
    fi

    local requirement=""
    requirement=$(project_optional_requirement \
        "$repo_root" "$python_bin" "neuromem" "isage-neuromem")
    deploy_log "Installing lightweight NeuroMem collection runtime"
    # The published package currently declares the optional SAGE service
    # kernel as mandatory. Sage Mate uses only the collection API, and the
    # service-kernel native wheel is not available on Ascend ARM64.
    if "$python_bin" -m pip --version >/dev/null 2>&1; then
        "$python_bin" -m pip install --quiet --no-deps "$requirement"
    else
        local uv_bin=""
        uv_bin=$(resolve_uv_bin)
        [[ -n "$uv_bin" ]] || deploy_fail \
            "Neither pip nor uv is available to repair the NeuroMem runtime"
        "$uv_bin" pip install --python "$python_bin" --no-deps "$requirement"
    fi
    "$python_bin" -c "from importlib.metadata import version; version('isage-neuromem'); from sage.neuromem import UnifiedCollection"
}

resolve_uv_bin() {
    local uv_bin=""
    uv_bin=$(command -v uv 2>/dev/null || true)
    if [[ -z "$uv_bin" && -x "$HOME/.local/bin/uv" ]]; then
        uv_bin="$HOME/.local/bin/uv"
    fi
    printf '%s\n' "$uv_bin"
}

version_ge() {
    local actual="$1" required="$2"
    if command -v dpkg >/dev/null 2>&1; then
        dpkg --compare-versions "$actual" ge "$required"
    else
        [[ "$(printf '%s\n%s\n' "$required" "$actual" | sort -V | head -n1)" == "$required" ]]
    fi
}

load_dotenv_file() {
    local env_file="$1"
    [[ -f "$env_file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *"="* ]] || continue
        local key="${line%%=*}"
        key="${key// /}"
        [[ -z "$key" || -n "${!key:-}" ]] && continue
        export "$line"
    done < "$env_file"
}

env_file_has_key() {
    local env_file="$1" key="$2"
    [[ -f "$env_file" ]] || return 1
    awk -F= -v key="$key" '
        $0 ~ "^[[:space:]]*#" { next }
        $1 == key { found=1; exit }
        END { exit(found ? 0 : 1) }
    ' "$env_file"
}

env_file_get() {
    local env_file="$1" key="$2"
    [[ -f "$env_file" ]] || return 0
    awk -F= -v key="$key" '
        $0 ~ "^[[:space:]]*#" { next }
        $1 == key { sub(/^[^=]*=/, ""); print; exit }
    ' "$env_file"
}

env_file_ensure() {
    local env_file="$1" key="$2" default="$3"
    env_file_has_key "$env_file" "$key" && return 0
    printf '%s=%s\n' "$key" "$default" >> "$env_file"
}

env_file_set() {
    local env_file="$1" key="$2" value="$3" python_bin="${4:-python3}"
    if [[ -f "$env_file" ]] && env_file_has_key "$env_file" "$key"; then
        "$python_bin" - "$env_file" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
prefix = f"{key}="
out = []
updated = False
for line in lines:
    if line.strip().startswith(prefix):
        out.append(f"{key}={value}")
        updated = True
    else:
        out.append(line)
if not updated:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

nvidia_driver_version() {
    nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null \
        | head -n1 \
        | tr -d '[:space:]' \
        || true
}

check_nvidia_driver_for_vllm_hust() {
    local min_driver="${VLLM_NVIDIA_MIN_DRIVER_VERSION:-575.51.03}"
    local torch_backend="${VLLM_NVIDIA_TORCH_BACKEND:-cu129}"
    local driver_version=""
    driver_version=$(nvidia_driver_version)
    [[ -n "$driver_version" ]] || deploy_fail "could not detect NVIDIA driver version with nvidia-smi"

    deploy_log "  NVIDIA driver: $driver_version (minimum for vllm-hust $torch_backend: $min_driver)"
    if ! version_ge "$driver_version" "$min_driver"; then
        cat >&2 <<EOF
[deploy] NVIDIA driver $driver_version is too old for pinned vllm-hust $torch_backend wheels.
[deploy] Upgrade the driver first, then reboot and rerun quickstart:
[deploy]   tools/upgrade_nvidia_driver_for_vllm.sh --yes
[deploy]   sudo reboot
[deploy] After reboot, verify:
[deploy]   nvidia-smi
EOF
        exit 1
    fi
}

assert_nvidia_vllm_host() {
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        deploy_fail "nvidia-smi not found; NVIDIA vLLM engine requires a CUDA GPU host."
    fi
    if command -v npu-smi >/dev/null 2>&1 && npu-smi info >/dev/null 2>&1; then
        deploy_fail "npu-smi is present; refusing to use the NVIDIA launcher on a mixed/Ascend host without explicit isolation."
    fi
}

assert_pinned_vllm_hust_checkout() {
    local vllm_hust_root="$1"
    if [[ ! -f "$vllm_hust_root/pyproject.toml" || ! -d "$vllm_hust_root/vllm" ]]; then
        deploy_fail "pinned vllm-hust checkout not found at $vllm_hust_root. Run: git submodule update --init --recursive deps/vllm-hust"
    fi
}

assert_python_imports_pinned_vllm_hust() {
    local python_bin="$1"
    local vllm_hust_root="$2"
    if ! "$python_bin" - "$vllm_hust_root" <<'PY' >/dev/null 2>&1
import importlib.util
import pathlib
import sys

expected = pathlib.Path(sys.argv[1]).resolve()
spec = importlib.util.find_spec("vllm")
if spec is None or spec.origin is None:
    raise SystemExit(1)
origin = pathlib.Path(spec.origin).resolve()
raise SystemExit(0 if expected in origin.parents else 1)
PY
    then
        deploy_fail "Python cannot import pinned vllm-hust from $vllm_hust_root. Run: ./quickstart.sh --target hosted-web --with-nvidia-vllm-engine --with-vllm-proxy"
    fi
}

setup_systemd_user_env() {
    local user_runtime_dir="/run/user/$(id -u)"
    local user_bus_path="$user_runtime_dir/bus"

    if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "$user_runtime_dir" ]]; then
        export XDG_RUNTIME_DIR="$user_runtime_dir"
    fi
    if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "$user_bus_path" ]]; then
        export DBUS_SESSION_BUS_ADDRESS="unix:path=$user_bus_path"
    fi
}

resolve_systemd_python_bin() {
    local fallback_python="$1"

    if [[ -n "${PYTHON_BIN:-}" && -x "$PYTHON_BIN" ]]; then
        printf '%s\n' "$PYTHON_BIN"
        return 0
    fi

    local conda_env_name="${CONDA_ENV_NAME:-vllm-hust-dev}"
    local candidate=""
    for candidate in \
        "$HOME/miniconda3/envs/$conda_env_name/bin/python3" \
        "$HOME/anaconda3/envs/$conda_env_name/bin/python3"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    printf '%s\n' "$fallback_python"
}

install_systemd_user_units() {
    local repo_root="$1" render_python_bin="$2"
    local source_dir="$repo_root/deploy/systemd/user"
    local target_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    local unit="" rendered=""

    mkdir -p "$target_dir"
    for unit in "$source_dir"/*.service "$source_dir"/*.timer; do
        [[ -f "$unit" ]] || continue
        rendered="$target_dir/$(basename "$unit")"
        sed \
            -e "s|__REPO_ROOT__|$repo_root|g" \
            -e "s|__PYTHON_BIN__|$render_python_bin|g" \
            "$unit" > "$rendered"
        chmod 0644 "$rendered"
        deploy_log "  installed: $(basename "$unit")"
    done
}

cleanup_legacy_python_override() {
    local target_dir="${1:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
    local override_dir="" override_file=""

    for override_dir in \
        "$target_dir/sage-mate-app.service.d" \
        "$target_dir/sage-faculty-twin-app.service.d"; do
        override_file="$override_dir/override.conf"
        if [[ -f "$override_file" ]] && awk '
        /^[[:space:]]*$/ { next }
        /^\[Service\]$/ { next }
        /^Environment=PYTHON_BIN=/ { next }
        { exit 1 }
    ' "$override_file"; then
            rm -f "$override_file"
            rmdir "$override_dir" 2>/dev/null || true
            deploy_log "  removed legacy PYTHON_BIN override"
        fi
    done
}
