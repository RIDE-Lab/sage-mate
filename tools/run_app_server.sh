#!/usr/bin/env bash
# run_app_server.sh — Start the sage-mate uvicorn server.

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$repo_root/tools/lib/runtime_env.sh"
source "$repo_root/tools/lib/deploy_common.sh"

# Load installer-written paths before applying shared runtime defaults.
load_repo_env_if_unset "$repo_root"
export_repo_runtime_env "$repo_root"
python_exec="$PYTHON_BIN"
app_host="$(require_runtime_setting APP_HOST)"
app_port="$(require_runtime_setting APP_PORT)"

# --- HuggingFace cache setup (always use writable local cache) ---
hf_home="$HOME/.cache/hf-models"
mkdir -p "$hf_home/hub"
export HF_HOME="$hf_home"
export HUGGINGFACE_HUB_CACHE="$hf_home/hub"
export HF_HUB_CACHE="$hf_home/hub"
export TRANSFORMERS_CACHE="$hf_home/hub"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

# --- Validate and auto-install runtime stack dependencies ---
_ensure_base_runtime_deps() {
    local py="$1"
    if ! "$py" -c "from importlib.metadata import version; version('isage'); version('isage-anns'); import sage_anns" 2>/dev/null; then
        echo "[runtime] Core SAGE/SageANNS stack is incomplete." >&2
        echo "[runtime] Repairing from the repository dependency contract." >&2
        "$py" -m pip install --quiet -e "$repo_root"
    fi

    ensure_neuromem_collection_runtime "$repo_root" "$py"

    "$py" -c "from importlib.metadata import version; version('isage'); version('isage-neuromem'); version('isage-anns'); from sage.neuromem import UnifiedCollection; import sage_anns"
}

# sagevdb (C extension) is required only when a SageVDB-backed index is
# selected. Install through the declared project extra so this script cannot
# drift away from pyproject.toml package names or versions.
_ensure_knowledge_deps() {
    local py="$1"
    if ! "$py" -c "from sagevdb import DatabaseConfig; from sage_anns import create_index" 2>/dev/null; then
        echo "[runtime] SageVDB knowledge stack is incomplete; repairing." >&2
        "$py" -m pip install --quiet -e "$repo_root[vdb-anns]"
        "$py" -c "from sagevdb import DatabaseConfig; from sage_anns import create_index"
    fi
}
_ensure_base_runtime_deps "$python_exec"
conversation_index="${DIGITAL_TWIN_CONVERSATION_MEMORY_INDEX_TYPE:-segment}"
if [[ "${DIGITAL_TWIN_KNOWLEDGE_BACKEND:-neuromem}" == "sagevdb" \
    || "$conversation_index" == "sage_vdb_ann" \
    || "$conversation_index" == "sagedb_ann" ]]; then
    _ensure_knowledge_deps "$python_exec"
fi

# --- Start server ---
cd "$repo_root"
exec "$python_exec" -m uvicorn sage_faculty_twin.api:app \
    --host "$app_host" --port "$app_port"
