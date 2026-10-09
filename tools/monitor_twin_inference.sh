#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$repo_root/tools/lib/runtime_env.sh"
export_repo_runtime_env "$repo_root"

load_dotenv() {
    if [[ -f "$repo_root/.env" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
            key="${line%%=*}"
            key="${key// /}"
            [[ -z "$key" || -n "${!key:-}" ]] && continue
            export "$line"
        done < "$repo_root/.env"
    fi
}

load_dotenv

source "$repo_root/tools/lib/instance_control.sh"
sage_mate_route_instance_operation monitor

runtime_root="${DIGITAL_TWIN_RUNTIME_DIR:-$repo_root/../sage-mate-runtime-private}"
mkdir -p "$runtime_root/logs" "$runtime_root/data/alerts"
log_file="$runtime_root/logs/twin_inference_monitor.log"
state_file="$runtime_root/data/alerts/twin_inference_monitor_state.json"
boot_marker_file="$runtime_root/data/alerts/twin_inference_engine_boot_started_at"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$log_file"
}

notify() {
    local title="$1"
    local body="$2"
    local payload
    payload="$title"$'\n'"$body"
    if [[ -n "${TWIN_MONITOR_NOTIFY_COMMAND:-}" ]]; then
        TWIN_MONITOR_TITLE="$title" TWIN_MONITOR_BODY="$body" bash -lc "$TWIN_MONITOR_NOTIFY_COMMAND" || true
        return 0
    fi
    local slack_target="${TWIN_MONITOR_SLACK_USER_ID:-${TWIN_MONITOR_SLACK_CHANNEL_ID:-}}"
    if [[ -n "${TWIN_MONITOR_SLACK_BOT_TOKEN:-}" && -n "$slack_target" ]]; then
        "$PYTHON_BIN" - "$TWIN_MONITOR_SLACK_BOT_TOKEN" "$slack_target" "$payload" <<'PY' || true
import json, sys, urllib.request
token, target, text = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request(
    "https://slack.com/api/chat.postMessage",
    data=json.dumps({"channel": target, "text": text}, ensure_ascii=False).encode(),
    headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {token}"},
)
try:
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except Exception as exc:
    print(f"slack_notify_failed: {exc.__class__.__name__}", file=sys.stderr)
else:
    if not data.get("ok"):
        print(f"slack_notify_failed: {data.get('error', 'slack_post_failed')}", file=sys.stderr)
PY
        return 0
    fi
    if [[ -n "${TWIN_MONITOR_SLACK_WEBHOOK_URL:-}" ]]; then
        "$PYTHON_BIN" - "$TWIN_MONITOR_SLACK_WEBHOOK_URL" "$payload" <<'PY' || true
import json, sys, urllib.request
url, text = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, data=json.dumps({"text": text}, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=8).read()
except Exception as exc:
    print(f"slack_webhook_notify_failed: {exc.__class__.__name__}", file=sys.stderr)
PY
        return 0
    fi
    printf '%s\n%s\n' "$title" "$body" > "$runtime_root/data/alerts/latest_twin_inference_alert.txt"
}

engine_is_active() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl --user is-active --quiet "${TWIN_MONITOR_ENGINE_UNIT:-sage-mate-vllm-engine.service}"
}

looks_like_engine_booting() {
    [[
        "$failure" == *"Connection refused"* ||
        "$failure" == *"connection refused"* ||
        "$failure" == *"RemoteDisconnected"* ||
        "$failure" == *"upstream_unavailable"* ||
        "$failure" == *'"status": 503'*
    ]] || return 1
    engine_is_active
}

within_engine_boot_grace() {
    local now
    local started_at
    local grace

    now=$(date +%s)
    grace="${TWIN_MONITOR_ENGINE_BOOT_GRACE_SECONDS:-600}"
    if [[ -s "$boot_marker_file" ]]; then
        started_at=$(cat "$boot_marker_file" 2>/dev/null || true)
        [[ "$started_at" =~ ^[0-9]+$ ]] || started_at="$now"
    else
        started_at="$now"
        printf '%s\n' "$started_at" > "$boot_marker_file"
    fi

    (( now - started_at < grace ))
}

if "$PYTHON_BIN" "$repo_root/tools/check_twin_inference.py" --repo-root "$repo_root" --mode completion --timeout "${TWIN_MONITOR_TIMEOUT_SECONDS:-25}" --json >"$state_file.tmp" 2>&1; then
    mv "$state_file.tmp" "$state_file"
    rm -f "$boot_marker_file"
    log "inference healthy"
    exit 0
fi

failure="$(cat "$state_file.tmp" 2>/dev/null || true)"
if [[ -f "$state_file.tmp" ]]; then
    mv "$state_file.tmp" "$state_file"
fi
if [[ -z "$failure" && -f "$state_file" ]]; then
    failure="$(cat "$state_file" 2>/dev/null || true)"
fi
log "inference failed: $failure"

if looks_like_engine_booting && within_engine_boot_grace; then
    log "engine is active but not listening yet; treating as startup grace window"
    exit 0
fi

log "restarting via manage.sh"
"$repo_root/manage.sh" restart "${TWIN_MONITOR_ENGINE_FLAG:---with-vllm-engine}" --with-vllm-proxy >>"$log_file" 2>&1 || true
sleep "${TWIN_MONITOR_RECHECK_DELAY_SECONDS:-20}"

if "$PYTHON_BIN" "$repo_root/tools/check_twin_inference.py" --repo-root "$repo_root" --mode completion --timeout "${TWIN_MONITOR_TIMEOUT_SECONDS:-25}" --json >"$state_file.tmp" 2>&1; then
    mv "$state_file.tmp" "$state_file"
    rm -f "$boot_marker_file"
    log "inference recovered after restart"
    notify "[Faculty Twin] 推理服务已自动恢复" "首次探测失败，已通过 manage.sh restart 恢复。原始失败：$failure"
    exit 0
fi

second_failure="$(cat "$state_file.tmp" 2>/dev/null || true)"
if [[ -f "$state_file.tmp" ]]; then
    mv "$state_file.tmp" "$state_file"
fi
if [[ -z "$second_failure" && -f "$state_file" ]]; then
    second_failure="$(cat "$state_file" 2>/dev/null || true)"
fi
log "inference still failing: $second_failure"
notify "[Faculty Twin] 推理服务异常，自动恢复失败" "已通过 manage.sh restart 重启 app/proxy/engine，但推理仍失败。首次失败：$failure"$'\n'"复测失败：$second_failure"
exit 1
