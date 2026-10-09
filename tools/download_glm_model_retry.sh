#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_root="/data/shared_models/modelscope_cache"
default_runtime_dir="$(dirname "$repo_root")/sage-mate-runtime-private"
log_file="${DIGITAL_TWIN_RUNTIME_DIR:-$default_runtime_dir}/glm-4-32b-download.log"
mkdir -p "$(dirname "$log_file")" "$cache_root"

# This is intentionally a host-side direct-download job.  Do not inherit a
# desktop/client VPN or proxy socket into the model transfer.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY="*"
export HF_HUB_DOWNLOAD_TIMEOUT=60
export HF_HUB_ETAG_TIMEOUT=20
export VLLM_ENGINE_REQUIRED_FAMILIES=glm
export VLLM_ENGINE_MODEL_PREFERENCE=GLM-4-32B-0414
export VLLM_ENGINE_FAMILY_REMOTE_GLM_CANDIDATES=zai-org/GLM-4-32B-0414
export VLLM_ENGINE_AUTO_DOWNLOAD=1
export VLLM_ENGINE_MODEL_ROOTS="$cache_root"

for attempt in $(seq 1 12); do
  for endpoint in https://huggingface.co https://hf-mirror.com; do
    {
      echo "[$(date -Is)] attempt=$attempt endpoint=$endpoint"
      HF_ENDPOINT="$endpoint" python3 "$repo_root/tools/resolve_vllm_hust_model.py" --print-env
      echo "[$(date -Is)] download complete"
      exit 0
    } >>"$log_file" 2>&1 && exit 0
    echo "[$(date -Is)] endpoint failed; retrying" >>"$log_file"
  done
  sleep $((attempt * 10))
done
echo "[$(date -Is)] exhausted retries" >>"$log_file"
exit 1
