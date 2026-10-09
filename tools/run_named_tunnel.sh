#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$repo_root/tools/lib/runtime_env.sh"
load_repo_env_if_unset "$repo_root"
export_repo_runtime_env "$repo_root" >/dev/null

runtime_dir="${DIGITAL_TWIN_RUNTIME_DIR:-$repo_root/.runtime}"
config_path="${TUNNEL_CONFIG_PATH:-$runtime_dir/cloudflared/config.yml}"
repo_config_path="$repo_root/.runtime/cloudflared/config.yml"
token_file="${TUNNEL_TOKEN_FILE:-$runtime_dir/cloudflared/token}"
protocol="${TUNNEL_PROTOCOL:-http2}"
startup_timeout_seconds="${TUNNEL_SITE_TIMEOUT_SECONDS:-30}"
site_health_url="$(require_runtime_setting TUNNEL_ORIGIN_HEALTH_URL)"
cloudflared_bin="${CLOUDFLARED_BIN:-}"

if [[ -z "$cloudflared_bin" ]]; then
    for candidate in \
        "$HOME/.local/bin/cloudflared" \
        "/usr/local/bin/cloudflared" \
        "/usr/bin/cloudflared"; do
        if [[ -x "$candidate" ]]; then
            cloudflared_bin="$candidate"
            break
        fi
    done
fi

if [[ -z "$cloudflared_bin" ]] && command -v cloudflared >/dev/null 2>&1; then
    cloudflared_bin="$(command -v cloudflared)"
fi

if [[ -z "$cloudflared_bin" || ! -x "$cloudflared_bin" ]]; then
    echo "cloudflared binary not found. Set CLOUDFLARED_BIN or install cloudflared." >&2
    exit 1
fi

if [[ ! -f "$config_path" && -f "$repo_config_path" ]]; then
    config_path="$repo_config_path"
fi

if [[ ! -f "$config_path" && ! -f "$token_file" && -z "${TUNNEL_TOKEN:-}" ]]; then
    echo "Missing tunnel config/token:" >&2
    echo "  config: $config_path" >&2
    echo "  token:  $token_file" >&2
    echo "Create either a named tunnel config or a private runtime token file." >&2
    exit 1
fi

deadline=$((SECONDS + startup_timeout_seconds))
until curl --noproxy '*' -fsS "$site_health_url" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
        echo "Local proxy is not reachable at ${site_health_url} after ${startup_timeout_seconds}s." >&2
        exit 1
    fi
    sleep 1
done

mkdir -p "$runtime_dir/cloudflared"

if [[ -f "$config_path" ]]; then
    exec "$cloudflared_bin" tunnel --protocol "$protocol" --config "$config_path" run
fi

if [[ -f "$token_file" ]]; then
    exec "$cloudflared_bin" tunnel --protocol "$protocol" run --token-file "$token_file"
fi

# Avoid putting tunnel secrets in argv where ps/pgrep can expose them.
env_token_file="$runtime_dir/cloudflared/token.from-env"
umask 077
printf '%s\n' "$TUNNEL_TOKEN" >"$env_token_file"
exec "$cloudflared_bin" tunnel --protocol "$protocol" run --token-file "$env_token_file"
