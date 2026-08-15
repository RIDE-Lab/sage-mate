#!/usr/bin/env bash
# quickstart.sh — single entry point for Sage Mate installation.
#
# Usage:
#   ./quickstart.sh                                # macOS: Sage Mate local app runtime; Linux: hosted web
#   ./quickstart.sh --target hosted-web --start    # server/web install + start systemd
#   ./quickstart.sh --local-mac-app --start        # scripted local Sage Mate service install
#   ./quickstart.sh --mac-dmg                      # build dist/sage-mate-macos.dmg
#   ./quickstart.sh --check                        # preflight only — diagnose, do not change
#   ./quickstart.sh --systemd-only                 # refresh systemd units only; no pip, no start
#   ./quickstart.sh --skip-python-install          # skip editable pip install steps
#   ./quickstart.sh --with-vllm                    # also install vllm-hust (editable)
#   ./quickstart.sh --with-vllm-engine             # enable vLLM engine systemd service
#   ./quickstart.sh --with-nvidia-vllm-engine      # enable local NVIDIA/CUDA vLLM engine
#   ./quickstart.sh --with-vllm-proxy              # enable vLLM OpenAI auth proxy service
#   ./quickstart.sh --with-site-proxy              # enable local nginx/python proxy service
#   ./quickstart.sh --with-tunnel                  # enable Cloudflare tunnel service
#   ./quickstart.sh --no-systemd                   # skip systemd unit install
#   ./quickstart.sh --yes                          # non-interactive (assume yes)
#
# Install targets:
#   hosted-web       Linux/server browser deployment. Default on Linux. Never enables code tools.
#   local-mac-app    Scripted local Sage Mate service install; delegates to tools/install_local_code_mode.sh.
#   mac-dmg          Build the macOS DMG package; delegates to tools/build_macos_local_code_package.sh.
#
# Environment overrides:
#   PYTHON_BIN        Path to interpreter (default: python3 in PATH)
#   CONDA_ENV_NAME    Conda env to use/create (default: vllm-hust-dev)
#   APP_PORT          App listen port (default: 55601)
#
# This script will NOT:
#   - Download or launch any LLM model — use manage.sh for runtime operations.
#   - Touch git history.
#   - Enable local code tools on hosted-web installs.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
parent_dir="${FACULTY_TWIN_PARENT_DIR:-$(dirname "$repo_root")}"

# ── CLI flags ────────────────────────────────────────────────────────────────
mode_check=false
mode_systemd_only=false
mode_skip_python_install=false
mode_skip_vdb_extras=false
pip_timeout_seconds="${PIP_INSTALL_TIMEOUT_SECONDS:-0}"
mode_with_vllm=false
mode_with_nvidia_vllm=false
mode_no_systemd=false
mode_no_siblings=false
mode_start=false
mode_yes=false
svc_engine=false
svc_nvidia_engine=false
svc_proxy=false
svc_site=false
svc_tunnel=false
if [[ "$(uname -s)" == "Darwin" ]]; then
	install_target="local-mac-app"
else
	install_target="hosted-web"
fi
local_install_args=()
mac_dmg_args=()
local_target_hint=false
local_claude_hust_dir_explicit=false

usage() {
	cat <<'EOF'
quickstart.sh — single entry point for Sage Mate installation.

Usage:
  ./quickstart.sh                                # macOS: Sage Mate local app runtime; Linux: hosted web
  ./quickstart.sh --target hosted-web --start    # server/web install + start systemd
  ./quickstart.sh --local-mac-app --start        # scripted local Sage Mate service install
  ./quickstart.sh --mac-dmg                      # build dist/sage-mate-macos.dmg
  ./quickstart.sh --check                        # static/preflight checks only
  ./quickstart.sh --systemd-only                 # refresh systemd user units only

Install targets:
  hosted-web       Linux/server browser deployment. Default on Linux. Never enables code tools.
  local-mac-app    Scripted local Sage Mate service install; delegates to tools/install_local_code_mode.sh.
  mac-dmg          Build the macOS DMG package; delegates to tools/build_macos_local_code_package.sh.

Hosted-web options:
  --with-vllm, --with-vllm-engine, --with-nvidia-vllm-engine, --with-vllm-proxy, --with-site-proxy, --with-tunnel
  --no-systemd, --no-siblings, --skip-python-install, --skip-vdb-extras, --pip-timeout SECONDS, --start, --yes

Local Sage Mate options:
  --app-profile faculty_twin|code_assistant|auto_scientist
  --workspace PATH              Repeatable local repository allowlist.
  --workspace-roots CSV         Comma-separated local repository allowlist.
  --runtime-dir PATH
  --llm-base-url URL
  --api-key KEY
  --model-name NAME
  --local-model-backend auto|none|vllm_metal
  --skip-local-model-runtime
  --vllm-metal-dir PATH
  --vllm-metal-model NAME
  --code-backend auto|internal|claude_hust
  --claude-hust-repo URL
  --claude-hust-dir PATH
  --skip-claude-hust
  --prefill-env PATH
  --port PORT
  --python PATH
  --venv PATH
  --start

DMG options:
  --zip                         Also build a zip fallback.
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--target)
		[[ $# -ge 2 ]] || { echo "--target requires a value" >&2; exit 2; }
		install_target="$2"
		shift 2
		;;
	--target=*)
		install_target="${1#*=}"
		shift
		;;
	--hosted-web)
		install_target="hosted-web"
		shift
		;;
	--local-mac-app)
		install_target="local-mac-app"
		shift
		;;
	--mac-dmg | --build-mac-dmg)
		install_target="mac-dmg"
		shift
		;;
	--check)          mode_check=true; shift ;;
	--systemd-only)
		mode_systemd_only=true
		mode_no_siblings=true
		mode_skip_python_install=true
		shift
		;;
	--skip-python-install) mode_skip_python_install=true; shift ;;
	--skip-vdb-extras) mode_skip_vdb_extras=true; shift ;;
	--pip-timeout)
		[[ $# -ge 2 ]] || { echo "--pip-timeout requires a value" >&2; exit 2; }
		pip_timeout_seconds="$2"
		shift 2
		;;
	--pip-timeout=*)
		pip_timeout_seconds="${1#*=}"
		shift
		;;
	--with-vllm)      mode_with_vllm=true; shift ;;
	--with-vllm-engine) svc_engine=true; shift ;;
	--with-nvidia-vllm-engine) svc_nvidia_engine=true; mode_with_nvidia_vllm=true; shift ;;
	--with-vllm-proxy)  svc_proxy=true; shift ;;
	--with-site-proxy)  svc_site=true; shift ;;
	--with-tunnel)      svc_tunnel=true; shift ;;
	--no-systemd)     mode_no_systemd=true; shift ;;
	--no-siblings)    mode_no_siblings=true; shift ;;
	--start)
		mode_start=true
		local_install_args+=(--start)
		shift
		;;
	--yes | -y)       mode_yes=true; shift ;;
	--zip)
		mac_dmg_args+=(--zip)
		shift
		;;
	--workspace | --workspace-roots | --runtime-dir | --llm-base-url | --api-key | --model-name | --local-model-backend | --vllm-metal-dir | --vllm-metal-model | --prefill-env | --port | --python | --venv | --app-profile | --code-backend | --claude-hust-repo | --claude-hust-dir)
		[[ $# -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
		local_target_hint=true
		[[ "$1" == "--claude-hust-dir" ]] && local_claude_hust_dir_explicit=true
		[[ "$1" == "--claude-hust-dir" ]] && mac_dmg_args+=(--claude-hust-dir "$2")
		[[ "$1" == "--vllm-metal-dir" ]] && mac_dmg_args+=(--vllm-metal-dir "$2")
		if [[ "$1" == "--app-profile" ]]; then
			local_install_args+=(--profile "$2")
		else
			local_install_args+=("$1" "$2")
		fi
		shift 2
		;;
	--skip-local-model-runtime)
		local_target_hint=true
		local_install_args+=(--skip-local-model-runtime)
		shift
		;;
	--skip-claude-hust)
		local_target_hint=true
		local_install_args+=(--skip-claude-hust)
		shift
		;;
	--profile)
		[[ $# -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
		case "$2" in
			hosted-web|local-mac-app|mac-dmg) install_target="$2" ;;
			faculty_twin|code_assistant|auto_scientist)
				local_target_hint=true
				local_install_args+=(--profile "$2")
				;;
			*) echo "--profile must be a deployment target or app profile" >&2; exit 2 ;;
		esac
		shift 2
		;;
	-h | --help)      usage; exit 0 ;;
	*) echo "Unknown argument: $1" >&2; exit 2 ;;
	esac
done

if $local_target_hint && [[ "$install_target" == "hosted-web" ]]; then
	install_target="local-mac-app"
fi

case "$install_target" in
	hosted-web|local-mac-app|mac-dmg) ;;
	*) echo "--target must be one of: hosted-web, local-mac-app, mac-dmg" >&2; exit 2 ;;
esac

log()  { printf '\033[1;36m[quickstart]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[quickstart]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[quickstart]\033[0m %s\n' "$*" >&2; exit 1; }
confirm() { $mode_yes && return 0; read -r -p "$1 [y/N] " ans; [[ "$ans" =~ ^[Yy]$ ]]; }

source "$repo_root/tools/lib/deploy_common.sh"
source "$repo_root/tools/lib/runtime_seed.sh"

run_with_optional_timeout() {
	if [[ "$pip_timeout_seconds" =~ ^[0-9]+$ ]] && (( pip_timeout_seconds > 0 )) && command -v timeout >/dev/null 2>&1; then
		timeout "$pip_timeout_seconds" "$@"
	else
		"$@"
	fi
}

bootstrap_python311_venv() {
	local venv_dir="${FACULTY_TWIN_VENV:-$repo_root/.venv}"
	local venv_python="$venv_dir/bin/python"

	if [[ -x "$venv_python" ]] && python_meets_min_version "$venv_python"; then
		printf '%s\n' "$venv_python"
		return 0
	fi

	local uv_bin=""
	uv_bin=$(command -v uv 2>/dev/null || true)
	if [[ -z "$uv_bin" && -x "$HOME/.local/bin/uv" ]]; then
		uv_bin="$HOME/.local/bin/uv"
	fi
	if [[ -z "$uv_bin" ]]; then
		log "Installing uv to bootstrap a user-space Python 3.11 runtime" >&2
		if command -v curl >/dev/null 2>&1; then
			curl -LsSf https://astral.sh/uv/install.sh | sh >&2 || true
			uv_bin=$(command -v uv 2>/dev/null || true)
			if [[ -z "$uv_bin" && -x "$HOME/.local/bin/uv" ]]; then
				uv_bin="$HOME/.local/bin/uv"
			fi
		fi
	fi
	if [[ -z "$uv_bin" ]]; then
		if command -v timeout >/dev/null 2>&1; then
			timeout 300 "$python_bin" -m pip install --user uv >&2
		else
			"$python_bin" -m pip install --user uv >&2
		fi
		uv_bin="$HOME/.local/bin/uv"
	fi
	[[ -x "$uv_bin" ]] || fail "uv was not installed at $uv_bin"

	log "Installing user-space Python 3.11 and virtualenv" >&2
	"$uv_bin" python install 3.11 >&2
	"$uv_bin" venv "$venv_dir" --python 3.11 >&2
	"$venv_python" -m ensurepip --upgrade >/dev/null 2>&1 || true
	printf '%s\n' "$venv_python"
}

install_nvidia_vllm_hust_runtime() {
	local uv_bin=""
	uv_bin=$(resolve_uv_bin)
	local attempt=1
	local max_attempts="${VLLM_NVIDIA_INSTALL_ATTEMPTS:-3}"
	local vllm_hust_root="${VLLM_HUST_ROOT:-$repo_root/deps/vllm-hust}"
	local uv_http_timeout="${UV_HTTP_TIMEOUT:-300}"
	local torch_backend="${VLLM_NVIDIA_TORCH_BACKEND:-cu129}"
	local installer="${VLLM_NVIDIA_INSTALLER:-auto}"
	local precompiled_commit="${VLLM_PRECOMPILED_WHEEL_COMMIT:-}"
	local precompiled_wheel_location="${VLLM_PRECOMPILED_WHEEL_LOCATION:-}"
	assert_pinned_vllm_hust_checkout "$vllm_hust_root"
	check_nvidia_driver_for_vllm_hust
	if [[ -z "$precompiled_commit" && -f "$vllm_hust_root/upstream_version.json" ]]; then
		precompiled_commit=$("$python_bin" - "$vllm_hust_root/upstream_version.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("upstream_commit", ""))
PY
)
	fi
	if [[ -z "$precompiled_wheel_location" && -n "$precompiled_commit" ]] && command -v curl >/dev/null 2>&1; then
		local wheel_dir metadata_path wheel_url wheel_name
		wheel_dir="$repo_root/.runtime/vllm-wheels"
		metadata_path="$wheel_dir/metadata-${precompiled_commit}-${torch_backend}.json"
		mkdir -p "$wheel_dir"
		if [[ ! -s "$metadata_path" ]]; then
			curl -4 -fsSL --retry 5 --retry-delay 3 --retry-all-errors \
				"https://wheels.vllm.ai/${precompiled_commit}/${torch_backend}/vllm/metadata.json" \
				-o "$metadata_path" || true
		fi
		if [[ -s "$metadata_path" ]]; then
			wheel_url=$("$python_bin" - "$metadata_path" "$precompiled_commit" <<'PY'
import json
import platform
import sys
from urllib.parse import urljoin

metadata_path, commit = sys.argv[1:3]
arch = platform.machine()
base = f"https://wheels.vllm.ai/{commit}/cu129/vllm/metadata.json"
for item in json.load(open(metadata_path, encoding="utf-8")):
    if item.get("package_name") == "vllm" and arch in item.get("platform_tag", ""):
        print(urljoin(base, item["path"]))
        break
PY
)
			if [[ -n "$wheel_url" ]]; then
				wheel_name="${wheel_url##*/}"
				wheel_name="${wheel_name//%2B/+}"
				precompiled_wheel_location="$wheel_dir/$wheel_name"
				if [[ ! -s "$precompiled_wheel_location" ]]; then
					log "Downloading pinned vllm-hust precompiled wheel with IPv4 curl"
					curl -4 -fL --retry 5 --retry-delay 3 --retry-all-errors \
						"$wheel_url" -o "$precompiled_wheel_location" || precompiled_wheel_location=""
				fi
			fi
		fi
	fi

	while (( attempt <= max_attempts )); do
		log "Installing pinned vllm-hust runtime for NVIDIA (attempt $attempt/$max_attempts; may take several minutes)"
		if [[ "$installer" != "pip" && -n "$uv_bin" ]]; then
			if UV_HTTP_TIMEOUT="$uv_http_timeout" VLLM_USE_PRECOMPILED="${VLLM_USE_PRECOMPILED:-1}" VLLM_PRECOMPILED_WHEEL_COMMIT="$precompiled_commit" VLLM_PRECOMPILED_WHEEL_VARIANT="${VLLM_PRECOMPILED_WHEEL_VARIANT:-$torch_backend}" VLLM_PRECOMPILED_WHEEL_LOCATION="$precompiled_wheel_location" run_with_optional_timeout "$uv_bin" pip install --python "$python_bin" -e "$vllm_hust_root" --torch-backend="$torch_backend"; then
				return 0
			fi
		else
			run_with_optional_timeout "$python_bin" -m pip install --retries 10 --timeout 120 \
				cmake "ninja" "packaging>=24.2" "setuptools>=77.0.3,<81.0.0" \
				"setuptools-scm>=8.0" "setuptools-rust>=1.9.0" "torch==2.11.0" wheel jinja2
			if VLLM_USE_PRECOMPILED="${VLLM_USE_PRECOMPILED:-1}" VLLM_PRECOMPILED_WHEEL_COMMIT="$precompiled_commit" VLLM_PRECOMPILED_WHEEL_VARIANT="${VLLM_PRECOMPILED_WHEEL_VARIANT:-$torch_backend}" VLLM_PRECOMPILED_WHEEL_LOCATION="$precompiled_wheel_location" run_with_optional_timeout "$python_bin" -m pip install --retries 10 --timeout 120 --no-build-isolation -e "$vllm_hust_root"; then
				return 0
			fi
		fi
		(( attempt++ ))
		sleep 5
	done

	return 1
}

prepare_hosted_runtime_data() {
	local runtime_dir
	runtime_dir=$(env_get DIGITAL_TWIN_RUNTIME_DIR)
	if [[ -z "$runtime_dir" ]]; then
		runtime_dir="$parent_dir/sage-mate-runtime-private"
	fi
	runtime_dir="${runtime_dir/#\~/$HOME}"

	if ! mkdir -p "$runtime_dir" 2>/dev/null; then
		local fallback_runtime="$parent_dir/sage-mate-runtime-private"
		warn "DIGITAL_TWIN_RUNTIME_DIR is not writable; using $fallback_runtime"
		runtime_dir="$fallback_runtime"
		mkdir -p "$runtime_dir"
		set_env_kv DIGITAL_TWIN_RUNTIME_DIR "$runtime_dir"
	fi
	runtime_dir=$(cd "$runtime_dir" && pwd -P)

	local runtime_repo_url runtime_repo_branch runtime_repo_required
	runtime_repo_url=$(env_get FACULTY_TWIN_RUNTIME_REPO_URL)
	runtime_repo_branch=$(env_get FACULTY_TWIN_RUNTIME_REPO_BRANCH)
	runtime_repo_required=$(env_get FACULTY_TWIN_RUNTIME_REPO_REQUIRED)
	if [[ -n "$runtime_repo_url" ]]; then
		local runtime_repo_args=(
			--runtime-dir "$runtime_dir"
			--repo-url "$runtime_repo_url"
			--branch "${runtime_repo_branch:-main}"
			--env-file "$repo_root/.env"
		)
		[[ "${runtime_repo_required,,}" =~ ^(1|true|yes|on)$ ]] && runtime_repo_args+=(--required)
		"$repo_root/tools/sync_runtime_repo.sh" "${runtime_repo_args[@]}"
		runtime_dir=$(cd "$runtime_dir" && pwd -P)
	fi

	log "Preparing hosted runtime data folder: $runtime_dir"
	mkdir -p \
		"$runtime_dir/.runtime/online_presence" \
		"$runtime_dir/data/alerts" \
		"$runtime_dir/data/artifact_memory_drafts" \
		"$runtime_dir/data/availability/history" \
		"$runtime_dir/data/capability_plugins" \
		"$runtime_dir/data/code_sessions" \
		"$runtime_dir/data/conversation_memory/digests" \
		"$runtime_dir/data/escalations" \
		"$runtime_dir/data/follow_up_actions" \
		"$runtime_dir/data/homepage" \
		"$runtime_dir/data/installed_skills" \
		"$runtime_dir/data/knowledge_base" \
		"$runtime_dir/data/knowledge_gap_drafts" \
		"$runtime_dir/data/operations_task_state" \
		"$runtime_dir/data/persona" \
		"$runtime_dir/data/skills" \
		"$runtime_dir/data/slack_user_links" \
		"$runtime_dir/data/suggestions" \
		"$runtime_dir/data/user_accounts" \
		"$runtime_dir/data/workflow_policies" \
		"$runtime_dir/data/workflow_scenarios"

	seed_runtime_data "$repo_root" "$runtime_dir"
	[[ -f "$runtime_dir/data/persona/style_profile.md" ]] || printf '%s\n' "# Faculty Twin style profile" > "$runtime_dir/data/persona/style_profile.md"
	[[ -f "$runtime_dir/data/installed_skills/fixed_prompt_skills.md" ]] || printf '%s\n' "# Installed skills prompt" > "$runtime_dir/data/installed_skills/fixed_prompt_skills.md"
	[[ -f "$runtime_dir/data/availability/current_week.json" ]] || printf '%s\n' '{"timezone":"Asia/Shanghai","slots":[]}' > "$runtime_dir/data/availability/current_week.json"
	[[ -f "$runtime_dir/data/changelog.json" ]] || printf '%s\n' '[]' > "$runtime_dir/data/changelog.json"
	[[ -f "$runtime_dir/data/workflow_policies/faculty-default-2026-05.json" ]] || printf '%s\n' '{"policy_version":"faculty-default-2026-05"}' > "$runtime_dir/data/workflow_policies/faculty-default-2026-05.json"
	[[ -f "$runtime_dir/data/workflow_scenarios/v3_preview_scenarios.json" ]] || printf '%s\n' '[]' > "$runtime_dir/data/workflow_scenarios/v3_preview_scenarios.json"
}

ensure_hosted_knowledge_defaults() {
	if "$python_bin" - <<'PY' >/dev/null 2>&1
from sagevdb import DatabaseConfig  # noqa: F401
import sage_anns  # noqa: F401
PY
	then
		return 0
	fi

	warn "sageVDB/SageANNS is not fully available; using neuromem/segment hosted defaults"
	set_env_kv DIGITAL_TWIN_KNOWLEDGE_BACKEND neuromem
	set_env_kv DIGITAL_TWIN_NEUROMEM_INDEX_TYPE segment
	set_env_kv DIGITAL_TWIN_CONVERSATION_MEMORY_COLLECTION_TYPE unified
	set_env_kv DIGITAL_TWIN_CONVERSATION_MEMORY_INDEX_TYPE segment
}

run_static_checks() {
	log "Static checks: shell entrypoints"
	local shell_scripts=(
		manage.sh
		quickstart.sh
		tools/run_app_server.sh
		tools/run_vllm_engine.sh
		tools/run_vllm_nvidia_engine.sh
		tools/run_vllm_openai_proxy.sh
		tools/run_local_proxy.sh
		tools/run_named_tunnel.sh
		tools/monitor_twin_inference.sh
		tools/reserve_vllm_devices.sh
	)
	local script=""
	for script in "${shell_scripts[@]}"; do
		[[ -f "$repo_root/$script" ]] || continue
		bash -n "$repo_root/$script"
		log "  ok: $script"
	done

	log "Static checks: Python entrypoints"
	local py_files=(
		src/sage_faculty_twin/vllm_openai_proxy.py
		tools/openai_key_proxy.py
		tools/repair_sagevdb.py
		tools/verify_hosted_web_deploy.py
	)
	local py_file=""
	for py_file in "${py_files[@]}"; do
		[[ -f "$repo_root/$py_file" ]] || continue
		"$python_bin" -m py_compile "$repo_root/$py_file"
		log "  ok: $py_file"
	done

	log "Static checks: vLLM-HUST dev-hub launcher"
	local dev_hub_launcher="$repo_root/deps/vllm-hust-dev-hub/scripts/run_vllm_hust_engine.sh"
	[[ -x "$dev_hub_launcher" ]] || fail "missing executable dev-hub launcher: $dev_hub_launcher"
	bash -n "$dev_hub_launcher"
	log "  ok: deps/vllm-hust-dev-hub/scripts/run_vllm_hust_engine.sh"

	log "Static checks: systemd templates"
	local unit=""
	for unit in "$repo_root"/deploy/systemd/user/*.service "$repo_root"/deploy/systemd/user/*.timer; do
		[[ -f "$unit" ]] || continue
		grep -q "__REPO_ROOT__" "$unit" || [[ "$(basename "$unit")" == *.timer ]] || warn "  $(basename "$unit") has no __REPO_ROOT__ placeholder"
		log "  ok: deploy/systemd/user/$(basename "$unit")"
	done
}

log "Install target: $install_target"

# ── 1. Preflight ─────────────────────────────────────────────────────────────
log "Preflight: $repo_root"
command -v git >/dev/null 2>&1 || fail "git not found"
[[ -f "$repo_root/pyproject.toml" ]] || fail "pyproject.toml missing — run quickstart from the repo root"

if [[ -n "${PYTHON_BIN:-}" ]]; then
	python_bin="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
	python_bin=$(command -v python3)
else
	fail "python3 not found; set PYTHON_BIN or install Python 3.10+"
fi

py_ver=$("$python_bin" -c 'import sys; print("%d.%d"%sys.version_info[:2])')
log "  python: $python_bin ($py_ver)"
if ! python_meets_min_version "$python_bin"; then
	if $mode_check; then
		warn "  expected Python >=3.11, got $py_ver"
	else
		warn "  expected Python >=3.11, got $py_ver; bootstrapping a local runtime"
		python_bin=$(bootstrap_python311_venv)
		export PYTHON_BIN="$python_bin"
		py_ver=$("$python_bin" -c 'import sys; print("%d.%d"%sys.version_info[:2])')
		log "  python: $python_bin ($py_ver)"
	fi
fi

has_npu=false
has_nvidia=false
if command -v npu-smi >/dev/null 2>&1; then
	npu_count=$(npu-smi info 2>/dev/null | awk '/^[|][[:space:]]*[0-9]+[[:space:]]+910/ { count++ } END { print count+0 }')
	log "  npu-smi: $npu_count Ascend NPU(s)"
	if (( npu_count > 0 )); then
		has_npu=true
	fi
fi
if command -v nvidia-smi >/dev/null 2>&1; then
	gpu_count=$(nvidia-smi -L 2>/dev/null | wc -l)
	log "  nvidia-smi: $gpu_count GPU(s)"
	if (( gpu_count > 0 )); then
		has_nvidia=true
	fi
fi
if ! $has_npu && ! $has_nvidia; then
	warn "  neither npu-smi nor nvidia-smi found — local vLLM serving needs Ascend/NVIDIA drivers"
fi

if $svc_engine && ! $has_npu && $has_nvidia; then
	fail "--with-vllm-engine is the Ascend vLLM-HUST engine and this host only has NVIDIA GPUs. Use --with-nvidia-vllm-engine for A6000/CUDA hosts."
fi

if ($mode_with_nvidia_vllm || $svc_nvidia_engine) && $has_nvidia; then
	check_nvidia_driver_for_vllm_hust
fi

if $mode_check; then
	run_static_checks
	log "Preflight/static checks only (--check). Done."
	exit 0
fi

if [[ "$install_target" == "mac-dmg" ]]; then
	log "Building Sage Mate macOS DMG"
	if (( ${#mac_dmg_args[@]} > 0 )); then
		exec "$repo_root/tools/build_macos_local_code_package.sh" "${mac_dmg_args[@]}"
	else
		exec "$repo_root/tools/build_macos_local_code_package.sh"
	fi
fi

if [[ "$install_target" == "local-mac-app" ]]; then
	if ! $local_claude_hust_dir_explicit; then
		local_install_args+=(--claude-hust-dir "$parent_dir/claude-code-hust")
	fi
	log "Installing local Sage Mate app runtime"
	exec "$repo_root/tools/install_local_code_mode.sh" "${local_install_args[@]}"
fi

# ── 3. Sibling repositories ─────────────────────────────────────────────────
mkdir -p "$parent_dir"
clone_if_missing() {
	local name="$1" url="$2"
	local tmp_askpass="" github_token=""
	if [[ ! -d "$parent_dir/$name" ]]; then
		log "Cloning $name into $parent_dir"
		github_token="${GITHUB_TOKEN:-}"
		if [[ -z "$github_token" && -f "$repo_root/.env" ]]; then
			github_token="$(env_file_get "$repo_root/.env" GITHUB_TOKEN || true)"
		fi
		if [[ -n "$github_token" && "$url" =~ ^https://github.com/ ]]; then
			tmp_askpass=$(mktemp)
			chmod 0700 "$tmp_askpass"
			cat >"$tmp_askpass" <<'EOF'
#!/usr/bin/env bash
case "$1" in
	*Username*) printf '%s\n' x-access-token ;;
	*) printf '%s\n' "${GITHUB_TOKEN:-}" ;;
esac
EOF
		fi
		if ! GITHUB_TOKEN="$github_token" GIT_ASKPASS="${tmp_askpass:-}" GIT_TERMINAL_PROMPT=0 git clone --depth=1 "$url" "$parent_dir/$name" 2>/dev/null; then
			warn "  could not clone $name (may need GITHUB_TOKEN for private repos)"
		fi
		[[ -z "$tmp_askpass" ]] || rm -f "$tmp_askpass"
	else
		log "  sibling repo present: $parent_dir/$name"
	fi
}

if $mode_no_siblings; then
	log "Skipping sibling repo cloning (--no-siblings)"
else
	clone_if_missing SAGE      https://github.com/intellistream/SAGE.git
	clone_if_missing neuromem  https://github.com/intellistream/neuromem.git
	clone_if_missing sageVDB   https://github.com/intellistream/sageVDB.git
	$mode_with_vllm && clone_if_missing vllm-hust https://github.com/intellistream/vllm-hust.git
fi

# ── 4. Python dependencies ───────────────────────────────────────────────────
if $mode_skip_python_install; then
	log "Skipping Python dependency installation (--skip-python-install)"
else
	if $mode_skip_vdb_extras; then
		log "Installing sage-mate (editable, base dependencies)"
	else
		log "Installing sage-mate (editable, with vdb-anns extras)"
	fi
	run_with_optional_timeout "$python_bin" -m pip install --quiet --upgrade pip "setuptools>=77.0.3,<81.0.0" wheel scikit-build-core pybind11
	if $mode_skip_vdb_extras; then
		run_with_optional_timeout "$python_bin" -m pip install --quiet --no-build-isolation -e "$repo_root"
	elif ! run_with_optional_timeout "$python_bin" -m pip install --quiet --no-build-isolation -e "$repo_root[vdb-anns]"; then
		warn "vdb-anns extras failed (C extensions may need CANN/C++ toolchain)"
		warn "Falling back to base install"
		run_with_optional_timeout "$python_bin" -m pip install --quiet --no-build-isolation -e "$repo_root"
	fi
	ensure_neuromem_collection_runtime "$repo_root" "$python_bin"

	if [[ -d "$parent_dir/sageVDB" ]]; then
		log "Repairing sageVDB native extension wiring"
		if ! "$python_bin" "$repo_root/tools/repair_sagevdb.py" --sagevdb-root "$parent_dir/sageVDB"; then
			warn "sageVDB repair failed; sagevdb backend may be unavailable for this Python"
			warn "  Retry with: ./manage.sh repair-sagevdb"
		fi
	fi

	if $mode_with_vllm && [[ -d "$parent_dir/vllm-hust" ]]; then
		log "Installing vllm-hust (editable, may take several minutes)"
		run_with_optional_timeout "$python_bin" -m pip install --quiet -e "$parent_dir/vllm-hust" \
			|| warn "vllm-hust install failed; build deps (CUDA/ninja, Ascend toolkit) may be missing"
	fi

	if $mode_with_nvidia_vllm || $svc_nvidia_engine; then
		if ! install_nvidia_vllm_hust_runtime; then
			fail "vllm-hust install failed; check CUDA/PyTorch/driver compatibility and retry"
		fi
	fi
fi

# ── 5. .env bootstrap ────────────────────────────────────────────────────────
env_file="$repo_root/.env"
template="$repo_root/.env.example"
if [[ ! -f "$env_file" ]]; then
	log "Creating .env from .env.example"
	cp "$template" "$env_file"
	warn "  Edit $env_file before serving real users:"
	warn "    DIGITAL_TWIN_OWNER_NAME / OWNER_ROLE"
	warn "    DIGITAL_TWIN_LLM_BASE_URL / API_KEY  (point at vllm-hust DIRECTLY)"
	warn "    DIGITAL_TWIN_ADMIN_PASSWORD"
fi

# Ensure required keys exist (append only if absent — never overwrite).
env_get() {
	local key="$1"
	if [[ -n "${!key:-}" ]]; then
		printf '%s\n' "${!key}"
		return 0
	fi
	env_file_get "$env_file" "$key"
}

require_env_value() {
	local key="$1" value
	value="$(env_get "$key")"
	[[ -n "$value" ]] || fail "$key must be set in $env_file"
	printf '%s\n' "$value"
}

ensure_env_kv() {
	local key="$1" default="$2"
	if ! env_file_has_key "$env_file" "$key"; then
		env_file_ensure "$env_file" "$key" "$default"
		log "  appended $key"
	fi
}
ensure_env_kv DIGITAL_TWIN_STREAM_CHAT_ANSWER       false
ensure_env_kv DIGITAL_TWIN_CHAT_REQUEST_TIMEOUT_SECONDS 80
ensure_env_kv DIGITAL_TWIN_LLM_TIMEOUT_SECONDS       60
ensure_env_kv DIGITAL_TWIN_CHAT_SSE_KEEPALIVE_SECONDS 15
ensure_env_kv DIGITAL_TWIN_CHAT_PROMPT_SOFT_CAP_CHARS 12000

set_env_kv() {
	local key="$1" value="$2"
	env_file_set "$env_file" "$key" "$value" "$python_bin"
}

if [[ "$install_target" == "hosted-web" ]]; then
	log "Applying hosted web safety defaults: local code tools disabled"
	set_env_kv DIGITAL_TWIN_DEPLOYMENT_MODE hosted
	set_env_kv DIGITAL_TWIN_APP_PROFILE faculty_twin
	set_env_kv DIGITAL_TWIN_CODE_WORKBENCH_ENABLED false
	set_env_kv DIGITAL_TWIN_CODE_WORKSPACE_ROOTS ""
	proxy_host="$(require_env_value VLLM_PROXY_CONNECT_HOST)"
	[[ "$proxy_host" != "0.0.0.0" && "$proxy_host" != "::" ]] || proxy_host="127.0.0.1"
	proxy_port="$(require_env_value VLLM_PROXY_PORT)"
	if $svc_nvidia_engine; then
		nvidia_host="$(require_env_value VLLM_NVIDIA_CONNECT_HOST)"
		nvidia_port="$(env_get VLLM_NVIDIA_PORT)"
		nvidia_port="${nvidia_port:-18000}"
		set_env_kv DIGITAL_TWIN_LLM_BASE_URL "http://${proxy_host}:${proxy_port}/v1"
		set_env_kv VLLM_PROXY_UPSTREAM_BASE_URL "http://${nvidia_host}:${nvidia_port}/v1"
		ensure_env_kv VLLM_NVIDIA_MODEL "Qwen/Qwen2.5-14B-Instruct-AWQ"
		ensure_env_kv DIGITAL_TWIN_MODEL_NAME "Qwen/Qwen2.5-14B-Instruct-AWQ"
		ensure_env_kv VLLM_NVIDIA_SERVED_MODEL_NAME "\${DIGITAL_TWIN_MODEL_NAME}"
		ensure_env_kv VLLM_NVIDIA_HOST "127.0.0.1"
		ensure_env_kv VLLM_NVIDIA_PORT "18000"
		ensure_env_kv VLLM_NVIDIA_GPU_MEMORY_UTILIZATION "0.88"
		ensure_env_kv VLLM_NVIDIA_MAX_MODEL_LEN "16384"
		ensure_env_kv VLLM_NVIDIA_MAX_NUM_SEQS "8"
		set_env_kv TWIN_MONITOR_ENGINE_FLAG "--with-nvidia-vllm-engine"
		set_env_kv TWIN_MONITOR_ENGINE_UNIT "sage-mate-vllm-nvidia-engine.service"
	fi
	if $svc_engine; then
		engine_model_path="$(env_get VLLM_ENGINE_MODEL_PATH)"
		engine_devices="$(env_get VLLM_ENGINE_NPU_DEVICES)"
		engine_devices="${engine_devices:-$(env_get ASCEND_RT_VISIBLE_DEVICES)}"
		[[ -n "$engine_model_path" ]] || fail "--with-vllm-engine requires VLLM_ENGINE_MODEL_PATH in .env"
		[[ -n "$engine_devices" ]] || fail "--with-vllm-engine requires VLLM_ENGINE_NPU_DEVICES in .env (the release installer can auto-select idle devices)"
		engine_host="$(require_env_value VLLM_ENGINE_CONNECT_HOST)"
		engine_port="$(require_env_value VLLM_ENGINE_PORT)"
		set_env_kv DIGITAL_TWIN_LLM_BASE_URL "http://${proxy_host}:${proxy_port}/v1"
		set_env_kv VLLM_PROXY_UPSTREAM_BASE_URL "http://${engine_host}:${engine_port}/v1"
		ensure_env_kv VLLM_ENGINE_SERVED_MODEL_NAME "\${DIGITAL_TWIN_MODEL_NAME}"
		ensure_env_kv VLLM_ENGINE_PORT "$engine_port"
		ensure_env_kv VLLM_ENGINE_MAX_MODEL_LEN "32768"
		ensure_env_kv VLLM_ENGINE_MAX_NUM_SEQS "16"
		set_env_kv TWIN_MONITOR_ENGINE_FLAG "--with-vllm-engine"
		set_env_kv TWIN_MONITOR_ENGINE_UNIT "sage-mate-vllm-engine.service"
	fi
	prepare_hosted_runtime_data

	vllm_hust_api_key=$(env_get VLLM_HUST_API_KEY)
	digital_twin_api_key=$(env_get DIGITAL_TWIN_API_KEY)
	if [[ -n "$vllm_hust_api_key" && ( -z "$digital_twin_api_key" || "$digital_twin_api_key" == "EMPTY" ) ]]; then
		set_env_kv DIGITAL_TWIN_API_KEY "$vllm_hust_api_key"
	fi
	vllm_proxy_upstream_key=$(env_get VLLM_PROXY_UPSTREAM_API_KEY)
	if [[ -n "$vllm_hust_api_key" && -z "$vllm_proxy_upstream_key" ]]; then
		set_env_kv VLLM_PROXY_UPSTREAM_API_KEY "$vllm_hust_api_key"
	fi
	ensure_hosted_knowledge_defaults
fi

# ── 6. Systemd service units ─────────────────────────────────────────────────
# Renders templates from deploy/systemd/user/ and installs them.
# This was previously tools/install_user_services.sh — now inlined.

if $mode_no_systemd; then
	log "Skipping systemd install (--no-systemd)"
else
	target_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
	setup_systemd_user_env
	render_python_bin=$(resolve_systemd_python_bin "$python_bin")
	log "Installing systemd --user units (python=$render_python_bin)"
	install_systemd_user_units "$repo_root" "$render_python_bin"
	cleanup_legacy_python_override "$target_dir"

	systemctl --user daemon-reload

	# Build service list
	service_units=(sage-mate-app.service)
	$svc_site   && service_units+=(sage-mate-site.service)
	$svc_tunnel && service_units+=(sage-mate-tunnel.service)
	$svc_proxy  && service_units+=(sage-mate-vllm-openai-proxy.service)
	$svc_engine && service_units+=(sage-mate-vllm-engine.service)
	$svc_nvidia_engine && service_units+=(sage-mate-vllm-nvidia-engine.service)

	systemctl --user enable "${service_units[@]}"
	log "  enabled: ${service_units[*]}"

	# Enable and start timers
	timer_units=(sage-mate-wiki-sync.timer sage-mate-inference-monitor.timer)
	systemctl --user enable "${timer_units[@]}"
	log "  enabled: ${timer_units[*]}"

	if $mode_start; then
		systemctl --user restart "${service_units[@]}" "${timer_units[@]}"
		systemctl --user --no-pager --full status "${service_units[@]}"
	fi
fi

# ── 7. Smoke test ────────────────────────────────────────────────────────────
app_health_host="$(require_env_value APP_HEALTH_HOST)"
app_port="$(require_env_value APP_PORT)"
if curl --noproxy '*' -sS --max-time 3 -o /dev/null -w '%{http_code}' "http://${app_health_host}:$app_port/" 2>/dev/null | grep -q '^200$'; then
	log "Smoke test: GET http://${app_health_host}:$app_port/ -> 200 OK"
else
	warn "App not yet listening on :$app_port — run: ./manage.sh restart, or ./quickstart.sh --start"
fi

if $mode_start && [[ "$install_target" == "hosted-web" ]]; then
	log "Hosted/web verification: safety boundaries and model wiring"
	verify_args=(--app-url "http://${app_health_host}:$app_port")
	if $svc_nvidia_engine; then
		verify_vllm_port=$(env_get VLLM_NVIDIA_PORT)
		verify_vllm_port="${verify_vllm_port:-8000}"
		verify_vllm_host="$(require_env_value VLLM_NVIDIA_CONNECT_HOST)"
		verify_args+=(--vllm-url "http://${verify_vllm_host}:$verify_vllm_port/v1")
	fi
	if ! "$python_bin" "$repo_root/tools/verify_hosted_web_deploy.py" "${verify_args[@]}"; then
		fail "hosted/web verification failed; fix the issues above before exposing the service"
	fi
fi

# ── 8. Next steps ────────────────────────────────────────────────────────────
cat <<EOM

────────────────────────────────────────────────────────────
Next steps
────────────────────────────────────────────────────────────

1. Edit $env_file and set at minimum:
     DIGITAL_TWIN_OWNER_NAME, DIGITAL_TWIN_OWNER_ROLE
     DIGITAL_TWIN_LLM_BASE_URL  e.g. http://<vllm-host>:8080/v1
     DIGITAL_TWIN_API_KEY       e.g. change-me-please
     DIGITAL_TWIN_MODEL_NAME    e.g. Qwen/Qwen2.5-14B-Instruct-AWQ
     DIGITAL_TWIN_ADMIN_PASSWORD

2. Local inference:
   - NVIDIA/CUDA hosts:
       ./manage.sh start --with-nvidia-vllm-engine --with-vllm-proxy
   - Ascend/NPU hosts:
       ./manage.sh start --with-vllm-engine --with-vllm-proxy

3. Manage the stack:
     ./manage.sh status --all
     ./manage.sh start  --all
     ./manage.sh logs   app
     ./manage.sh logs   engine

4. Open http://127.0.0.1:$app_port/ in a browser to verify.

EOM

log "Done."
