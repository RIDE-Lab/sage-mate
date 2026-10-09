#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
runtime_dir="$repo_root/.runtime"
source "$repo_root/tools/lib/runtime_env.sh"

load_repo_env_if_unset "$repo_root"
export_repo_runtime_env "$repo_root"
python_bin="$PYTHON_BIN"

mkdir -p "$runtime_dir"
cd "$repo_root"

if [[ ! -x "$python_bin" ]]; then
    echo "Python interpreter not found or not executable: $python_bin" >&2
    exit 1
fi

proxy_host="$(require_runtime_setting VLLM_PROXY_HOST)"
proxy_port="$(require_runtime_setting VLLM_PROXY_PORT)"
proxy_upstream_base_url="${VLLM_PROXY_UPSTREAM_BASE_URL-}"

if [[ -z "$proxy_upstream_base_url" ]]; then
    proxy_connect_host="${VLLM_ENGINE_CONNECT_HOST:-${VLLM_ENGINE_HOST:-127.0.0.1}}"
    proxy_connect_port="${VLLM_ENGINE_CONNECT_PORT:-${VLLM_ENGINE_PORT:-8000}}"
    proxy_upstream_base_url="http://$proxy_connect_host:$proxy_connect_port/v1"
fi

if ! "$python_bin" - "$proxy_host" "$proxy_port" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
family = socket.AF_INET6 if ":" in host and host != "0.0.0.0" else socket.AF_INET
sock = socket.socket(family, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind((host, port))
except OSError:
    raise SystemExit(1)
else:
    sock.close()
    raise SystemExit(0)
PY
then
    echo "VLLM proxy listen address ${proxy_host}:${proxy_port} is already in use. Stop the conflicting process or choose a different VLLM_PROXY_PORT before enabling sage-mate-vllm-openai-proxy.service." >&2
    exit 1
fi

if "$python_bin" - <<'PY' >/dev/null 2>&1
import importlib.util
raise SystemExit(0 if importlib.util.find_spec("uvicorn") else 1)
PY
then
    export VLLM_PROXY_UPSTREAM_BASE_URL="$proxy_upstream_base_url"
    exec "$python_bin" -m uvicorn sage_faculty_twin.vllm_openai_proxy:app --host "$proxy_host" --port "$proxy_port"
fi

export VLLM_PROXY_UPSTREAM_BASE_URL="$proxy_upstream_base_url"
exec "$python_bin" "$repo_root/tools/openai_key_proxy.py"
