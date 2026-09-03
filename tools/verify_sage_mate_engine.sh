#!/usr/bin/env bash
# Read-only verification for the canonical Sage Mate engine lock.

set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
env_file="$repo_root/.env"
unit="${SAGE_MATE_ENGINE_UNIT:-sage-mate-vllm-engine.service}"
host="${VLLM_ENGINE_CONNECT_HOST:-127.0.0.1}"
port="${VLLM_ENGINE_CONNECT_PORT:-${VLLM_ENGINE_PORT:-8000}}"
container="${VLLM_ENGINE_CONTAINER:-}"
import_origins=""
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

# An empty machine-local override means "use the portable deployment-role name";
# verification must resolve the same identity as the launcher rather than silently
# skipping container, NPU, argv, and import-origin gates.
# shellcheck source=tools/lib/vllm_container_identity.sh
source "$repo_root/tools/lib/vllm_container_identity.sh"
normalize_vllm_engine_container_name "$repo_root"
container="$VLLM_ENGINE_CONTAINER"

echo "[sage-mate-verify] unit=$unit status=$(systemctl --user is-active "$unit" 2>/dev/null || true)"
systemctl --user is-active --quiet "$unit" || { systemctl --user status "$unit" --no-pager || true; exit 1; }

health_url="http://$host:$port/health"
models_url="http://$host:$port/v1/models"
curl_engine() {
  # Keep credentials out of curl's command line (and process listings).
  if [[ -n "$api_key" ]]; then
    printf 'Authorization: Bearer %s\n' "$api_key" | curl --header @- "$@"
  else
    curl "$@"
  fi
}
curl_engine --fail --silent --show-error --max-time "${SAGE_MATE_VERIFY_TIMEOUT_SECONDS:-20}" "$health_url" >/dev/null
models="$(curl_engine --fail --silent --show-error --max-time "${SAGE_MATE_VERIFY_TIMEOUT_SECONDS:-20}" "$models_url")"
echo "[sage-mate-verify] health=OK models=$models"

if [[ "${SAGE_MATE_VERIFY_CHAT:-1}" != "0" ]]; then
  expected_model="${VLLM_ENGINE_SERVED_MODEL_NAME:-${VLLM_ENGINE_ACTUAL_MODEL_ID:-${DIGITAL_TWIN_MODEL_NAME:-}}}"
  model_id="$(python3 "$repo_root/tools/validate_engine_chat.py" \
    --list-models --model "$expected_model" <<< "$models")"
  chat_payload="$(MODEL_ID="$model_id" python3 - <<'PY'
import json
import os
print(json.dumps({"model": os.environ["MODEL_ID"], "messages": [{"role": "user", "content": "Reply with exactly OK."}], "max_tokens": 64, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}))
PY
)"
  chat_response="$(curl_engine --fail --silent --show-error --max-time "${SAGE_MATE_VERIFY_CHAT_TIMEOUT_SECONDS:-90}" \
    -H 'Content-Type: application/json' -d "$chat_payload" \
    "http://$host:$port/v1/chat/completions")"
  python3 "$repo_root/tools/validate_engine_chat.py" --model "$model_id" <<< "$chat_response"
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
  runtime_pythonpath="$(sed -n 's/^PYTHONPATH=//p' <<< "$runtime_env" | head -n1)"
  declared_pythonpath="${VLLM_ENGINE_PYTHONPATH:-${VLLM_ENGINE_BASE_PYTHONPATH:-}}"
  installed_modules_json="${VLLM_ENGINE_INSTALLED_MODULES_JSON:-}"
  if [[ -z "$installed_modules_json" ]]; then
    installed_modules_json='{}'
  fi
  [[ -n "$runtime_pythonpath" ]] || {
    echo "ERROR: runtime PYTHONPATH is empty" >&2
    exit 1
  }
  [[ -n "$declared_pythonpath" || "$installed_modules_json" != '{}' ]] || {
    echo "ERROR: neither declared source roots nor installed distributions are configured" >&2
    exit 1
  }
  IFS=: read -r -a declared_roots <<< "$declared_pythonpath"
  IFS=: read -r -a runtime_roots <<< "$runtime_pythonpath"
  for root in "${runtime_roots[@]}"; do
    [[ -n "$root" ]] || continue
    declared=0
    for expected_root in "${declared_roots[@]}"; do
      if [[ "$root" == "$expected_root" ]]; then
        declared=1
        break
      fi
    done
    if [[ "$declared" == "0" ]] && {
      "${docker_cmd[@]}" exec "$container" test -f "$root/vllm/__init__.py" >/dev/null 2>&1 \
        || "${docker_cmd[@]}" exec "$container" test -f "$root/vllm_ascend/__init__.py" >/dev/null 2>&1;
    }; then
      echo "ERROR: undeclared engine/plugin source found in runtime PYTHONPATH: $root" >&2
      exit 1
    fi
  done
  import_origin_output="$("${docker_cmd[@]}" exec \
    --env "PYTHONPATH=$runtime_pythonpath" \
    --env "SAGE_MATE_DECLARED_PYTHONPATH=$declared_pythonpath" \
    --env "VLLM_ENGINE_INSTALLED_MODULES_JSON=$installed_modules_json" \
    "$container" sh -c '
    pid=$(ps -eo pid=,args= | awk "/vllm serve/ {print \$1; exit}")
    exe=$(readlink "/proc/$pid/exe")
    "$exe" - <<'"'"'PY'"'"'
import importlib
import importlib.metadata
import json
import os
import pathlib
import warnings

declared_roots = [
    pathlib.Path(entry).resolve()
    for entry in os.environ.get("SAGE_MATE_DECLARED_PYTHONPATH", "").split(":")
    if entry
]
try:
    installed_contract = json.loads(
        os.environ.get("VLLM_ENGINE_INSTALLED_MODULES_JSON", "{}")
    )
except json.JSONDecodeError as exc:
    raise SystemExit(
        f"ERROR: invalid VLLM_ENGINE_INSTALLED_MODULES_JSON: {exc}"
    ) from exc
if not isinstance(installed_contract, dict):
    raise SystemExit("ERROR: VLLM_ENGINE_INSTALLED_MODULES_JSON must be an object")

for module_name in ("vllm", "vllm_ascend"):
    with warnings.catch_warnings():
        # Source checkouts intentionally lack the generated vllm._version module.
        # This probe validates import ownership, not package-build metadata.
        if module_name == "vllm":
            warnings.filterwarnings(
                "ignore",
                message=r"Failed to read commit hash:.*",
                category=RuntimeWarning,
            )
        module = importlib.import_module(module_name)
    origin = pathlib.Path(module.__file__).resolve()
    source_root = next(
        (
            root
            for root in declared_roots
            if (root / module_name / "__init__.py").is_file()
        ),
        None,
    )
    if source_root is not None:
        if not origin.is_relative_to(source_root):
            raise SystemExit(
                f"ERROR: {module_name} imported from {origin}, expected {source_root}"
            )
        print(f"{module_name}={origin} (declared source)")
        continue

    contract = installed_contract.get(module_name)
    if not isinstance(contract, dict):
        raise SystemExit(
            f"ERROR: {module_name} has no declared source root and no "
            "installed-distribution contract"
        )
    distribution_name = contract.get("distribution")
    expected_version = contract.get("version")
    if not isinstance(distribution_name, str) or not distribution_name:
        raise SystemExit(
            f"ERROR: installed contract for {module_name} needs distribution"
        )
    if not isinstance(expected_version, str) or not expected_version:
        raise SystemExit(
            f"ERROR: installed contract for {module_name} needs exact version"
        )
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise SystemExit(
            f"ERROR: installed distribution {distribution_name} not found"
        ) from exc
    if distribution.version != expected_version:
        raise SystemExit(
            f"ERROR: installed distribution {distribution_name} version "
            f"{distribution.version}, expected {expected_version}"
        )
    distribution_root = pathlib.Path(distribution.locate_file("")).resolve()
    try:
        relative_origin = origin.relative_to(distribution_root)
    except ValueError as exc:
        raise SystemExit(
            f"ERROR: {module_name} imported from {origin}, outside installed "
            f"distribution root {distribution_root}"
        ) from exc
    distribution_files = {
        pathlib.PurePosixPath(str(item)) for item in (distribution.files or ())
    }
    if pathlib.PurePosixPath(relative_origin.as_posix()) not in distribution_files:
        raise SystemExit(
            f"ERROR: {module_name} origin {relative_origin} is not owned by "
            f"installed distribution {distribution_name}"
        )
    print(
        f"{module_name}={origin} "
        f"(installed {distribution_name}=={distribution.version})"
    )
PY
  ')"
  import_origins="$(printf '%s\n' "$import_origin_output" \
    | awk '/^(vllm|vllm_ascend)=/')"
  [[ "$(wc -l <<< "$import_origins")" == "2" ]] || {
    echo "ERROR: could not verify vllm and vllm_ascend import origins" >&2
    exit 1
  }
  echo "[sage-mate-verify] import_origins=$(tr '\n' ';' <<< "$import_origins" | sed 's/;$//')"
  echo "[sage-mate-verify] graph_mode=ON command_verified"
fi

if [[ "${SAGE_MATE_WRITE_DEPLOYMENT_RECEIPT:-1}" != "0" ]]; then
  [[ "${SAGE_MATE_VERIFY_CHAT:-1}" != "0" ]] || {
    echo "ERROR: cannot publish a deployment receipt without a real chat probe" >&2
    exit 1
  }
  [[ -n "$import_origins" ]] || {
    echo "ERROR: cannot publish a deployment receipt without verified import origins" >&2
    exit 1
  }
  runtime_dir="${DIGITAL_TWIN_RUNTIME_DIR:-$repo_root/../sage-mate-runtime-private}"
  receipt_path="$(python3 "$repo_root/tools/write_verified_deployment_receipt.py" \
    --models-json "$models" \
    --import-origins "$import_origins" \
    --runtime-dir "$runtime_dir")"
  echo "[sage-mate-verify] deployment_receipt=$receipt_path"
fi

echo "[sage-mate-verify] PASS"
