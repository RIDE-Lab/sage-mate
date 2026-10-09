#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
runtime_dir="$repo_root/.runtime"
nginx_prefix="$runtime_dir/nginx"
nginx_template="$repo_root/tools/nginx-local.conf"
nginx_conf="$nginx_prefix/nginx.conf"
nginx_conf_tmp="$nginx_prefix/nginx.conf.tmp"
source "$repo_root/tools/lib/runtime_env.sh"
load_repo_env_if_unset "$repo_root"
app_host="$(require_runtime_setting APP_HOST)"
app_port="$(require_runtime_setting APP_PORT)"
site_host="$(require_runtime_setting SITE_HOST)"
site_port="$(require_runtime_setting SITE_PORT)"
startup_timeout_seconds="${APP_STARTUP_TIMEOUT_SECONDS:-30}"

format_host_port() {
    local host="$1" port="$2"
    if [[ "$host" == *:* && "$host" != \[*\] ]]; then
        printf '[%s]:%s\n' "$host" "$port"
    else
        printf '%s:%s\n' "$host" "$port"
    fi
}

app_address="$(format_host_port "$app_host" "$app_port")"
site_address="$(format_host_port "$site_host" "$site_port")"
app_upstream_host="$app_host"
[[ "$app_upstream_host" != *:* || "$app_upstream_host" == \[*\] ]] || app_upstream_host="[$app_upstream_host]"

mkdir -p "$runtime_dir" "$nginx_prefix/logs" "$nginx_prefix/client_body_temp" "$nginx_prefix/proxy_temp" "$nginx_prefix/cache/home_proxy"

deadline=$((SECONDS + startup_timeout_seconds))
until curl --noproxy '*' -fsS "http://${app_address}/" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
        echo "App is not reachable on ${app_address} after ${startup_timeout_seconds}s." >&2
        exit 1
    fi
    sleep 1
done

sed \
    -e "s|__SITE_ADDRESS__|$site_address|g" \
    -e "s|__APP_HOST__|$app_upstream_host|g" \
    -e "s|__APP_PORT__|$app_port|g" \
    "$nginx_template" >"$nginx_conf_tmp"

nginx \
    -p "$nginx_prefix" \
    -c "$nginx_conf_tmp" \
    -g "error_log logs/error.log notice;" \
    -t

mv "$nginx_conf_tmp" "$nginx_conf"

exec nginx \
    -p "$nginx_prefix" \
    -c "$nginx_conf" \
    -g "error_log logs/error.log notice;" \
    -s reload
