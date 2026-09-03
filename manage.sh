#!/usr/bin/env bash
# manage.sh — single entry point for sage-mate runtime management.
#
# Actions:
#   status    Show service states
#   start     Start services
#   stop      Stop services
#   restart   Restart services
#   logs      Follow journal for a service (usage: manage.sh logs <name>)
#   repair-sagevdb  Repair sageVDB native extension wiring
#   check-inference  Run one inference health check
#   verify-hosted-web  Verify hosted/web safety boundaries and model wiring
#   reserve-vllm-devices  Pin vllm-hust to specific Ascend device IDs
#   configure-slack-twin  Configure Slack /twin command secret and access
#
# Flags (combine with any action):
#   --all                Include all optional services
#   --with-vllm-engine   Ascend vLLM-HUST inference engine
#   --with-nvidia-vllm-engine  NVIDIA/CUDA vLLM inference engine
#   --with-vllm-metal-engine  Apple Silicon vLLM Metal inference engine
#   --with-vllm-proxy    OpenAI-compatible auth proxy
#   --with-site-proxy    Local nginx/python site proxy
#   --with-tunnel        Cloudflare tunnel
#   --with-model         (start/stop only) launch model engine in foreground
#   --json               Output machine-readable JSON (status only)
#   --foreground         Run the action in the foreground (model engine)
#
# Examples:
#   ./manage.sh status --all
#   ./manage.sh start  --all
#   ./manage.sh restart --with-nvidia-vllm-engine --with-vllm-proxy
#   ./manage.sh restart --with-vllm-engine
#   ./manage.sh logs   app
#   ./manage.sh logs   engine
#   ./manage.sh stop   --all

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$repo_root/tools/lib/runtime_env.sh"
load_repo_env_if_unset "$repo_root"
user_runtime_dir="/run/user/$(id -u)"
user_bus_path="$user_runtime_dir/bus"

if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "$user_runtime_dir" ]]; then
    export XDG_RUNTIME_DIR="$user_runtime_dir"
fi
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "$user_bus_path" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$user_bus_path"
fi

export_repo_runtime_env "$repo_root"
python_bin="$PYTHON_BIN"

usage() {
    cat <<EOF
Usage: $0 <status|start|stop|restart|logs|install|repair-sagevdb|check-inference|verify-hosted-web|reserve-vllm-devices|configure-slack-twin> [flags]
  Flags: --all --with-app --with-vllm-engine --with-nvidia-vllm-engine --with-vllm-metal-engine --with-vllm-proxy --with-site-proxy --with-tunnel --json
  Logs:  $0 logs <app|engine|nvidia-engine|metal-engine|proxy|site|tunnel|model>
EOF
}

# ── Parse arguments ──────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    usage >&2
    exit 1
fi

action="$1"
shift

if [[ "$action" == "-h" || "$action" == "--help" || "$action" == "help" ]]; then
    usage
    exit 0
fi

# Delegate install to quickstart.sh
if [[ "$action" == "install" ]]; then
    exec "$repo_root/quickstart.sh" "$@"
fi

if [[ "$action" == "repair-sagevdb" ]]; then
    SAGEVDB_REPAIR_IGNORE_PYTHON_BIN=1 exec "$repo_root/tools/repair_sagevdb.sh" "$@"
fi

if [[ "$action" == "check-inference" ]]; then
    exec "$repo_root/tools/monitor_twin_inference.sh" "$@"
fi

if [[ "$action" == "verify-hosted-web" ]]; then
    exec "$python_bin" "$repo_root/tools/verify_hosted_web_deploy.py" "$@"
fi

if [[ "$action" == "reserve-vllm-devices" ]]; then
    exec "$repo_root/tools/reserve_vllm_devices.sh" "$@"
fi

if [[ "$action" == "configure-slack-twin" ]]; then
    exec "$repo_root/tools/configure_slack_twin.sh" "$@"
fi

json_output="false"
foreground="false"
include_engine=false
include_nvidia_engine=false
include_metal_engine=false
include_proxy=false
include_site=false
include_tunnel=false
include_model=false
include_app=false
explicit_service_selection=false

for arg in "$@"; do
    case "$arg" in
        --json)              json_output="true" ;;
        --foreground)        foreground="true" ;;
        --all)               explicit_service_selection=true; include_app=true; include_engine=true; include_proxy=true; include_site=true; include_tunnel=true ;;
        --with-app)          explicit_service_selection=true; include_app=true ;;
        --with-vllm-engine)  explicit_service_selection=true; include_engine=true ;;
        --with-nvidia-vllm-engine) explicit_service_selection=true; include_nvidia_engine=true ;;
        --with-vllm-metal-engine) explicit_service_selection=true; include_metal_engine=true; include_model=true ;;
        --with-vllm-proxy)   explicit_service_selection=true; include_proxy=true ;;
        --with-site-proxy)   explicit_service_selection=true; include_site=true ;;
        --with-tunnel)       explicit_service_selection=true; include_tunnel=true ;;
        --with-model)        explicit_service_selection=true; include_model=true ;;
    esac
done

if ! $explicit_service_selection; then
    include_app=true
fi

# ── Service registry ─────────────────────────────────────────────────────────
# Format: "display_name:systemd_unit"
# Order: model/engine → proxy → app → site → tunnel
services=()
$include_engine && services+=("推理引擎:sage-mate-vllm-engine.service")
$include_nvidia_engine && services+=("NVIDIA推理引擎:sage-mate-vllm-nvidia-engine.service")
$include_proxy  && services+=("模型代理:sage-mate-vllm-openai-proxy.service")

$include_app    && services+=("应用服务:sage-mate-app.service")

$include_site   && services+=("本地代理:sage-mate-site.service")
$include_tunnel && services+=("公网隧道:sage-mate-tunnel.service")

# ── Action: logs ─────────────────────────────────────────────────────────────
if [[ "$action" == "logs" ]]; then
    target="${1:-app}"
    case "$target" in
        app)     unit="sage-mate-app.service" ;;
        engine)  unit="sage-mate-vllm-engine.service" ;;
        nvidia-engine|nvidia) unit="sage-mate-vllm-nvidia-engine.service" ;;
        metal-engine|metal) echo "Metal engine runs outside systemd - logs are in the terminal running tools/run_vllm_metal_engine.sh"; exit 0 ;;
        proxy)   unit="sage-mate-vllm-openai-proxy.service" ;;
        site)    unit="sage-mate-site.service" ;;
        tunnel)  unit="sage-mate-tunnel.service" ;;
        model)   echo "Model runs outside systemd — use: journalctl or docker logs"; exit 0 ;;
        *)       echo "Unknown service: $target (app|engine|nvidia-engine|proxy|site|tunnel|model)" >&2; exit 1 ;;
    esac
    exec journalctl --user -u "$unit" -f
fi

# ── Action: start model in foreground ────────────────────────────────────────
if [[ "$action" == "start" ]] && $include_model; then
    if $foreground || true; then
        echo "[manage] Launching vLLM engine in foreground..."
        if $include_nvidia_engine; then
            exec "$repo_root/tools/run_vllm_nvidia_engine.sh"
        fi
        if $include_metal_engine; then
            exec "$repo_root/tools/run_vllm_metal_engine.sh"
        fi
        exec "$repo_root/tools/run_vllm_engine.sh"
    fi
fi

# ── Validate action ──────────────────────────────────────────────────────────
case "$action" in
    status|start|stop|restart) ;;
    *) echo "Unsupported action: $action" >&2; exit 1 ;;
esac

# ── Extract systemd unit names ───────────────────────────────────────────────
service_units=()
for entry in "${services[@]}"; do
    service_units+=("${entry#*:}")
done

cleanup_vllm_engine_residuals() {
    $include_engine || return 0
    [[ -x "$repo_root/tools/cleanup_vllm_engine.sh" ]] || return 0
    "$repo_root/tools/cleanup_vllm_engine.sh" || true
}

stop_legacy_conflicting_services() {
    [[ "${FACULTY_TWIN_STOP_LEGACY_CONFLICTS:-1}" == "0" ]] && return 0
    local legacy_units=()
    $include_app && legacy_units+=(sage-faculty-twin-app.service)
    $include_proxy && legacy_units+=(sage-faculty-twin-vllm-openai-proxy.service)
    $include_site && legacy_units+=(sage-faculty-twin-site.service)
    $include_tunnel && legacy_units+=(sage-faculty-twin-tunnel.service)
    [[ ${#legacy_units[@]} -gt 0 ]] || return 0

    local existing_units=() unit
    for unit in "${legacy_units[@]}"; do
        if systemctl --user show "$unit" --property=LoadState --value 2>/dev/null | grep -qx 'loaded'; then
            existing_units+=("$unit")
        fi
    done
    [[ ${#existing_units[@]} -gt 0 ]] || return 0

    echo "[manage] Stopping legacy sage-faculty-twin services that use the same hosted/web ports: ${existing_units[*]}"
    systemctl --user stop "${existing_units[@]}" || true
    systemctl --user disable "${existing_units[@]}" >/dev/null 2>&1 || true
}

# ── Execute action ───────────────────────────────────────────────────────────
if $include_engine && [[ "$action" != "status" ]]; then
    source "$repo_root/tools/lib/instance_control.sh"
    # Enrolled operations cannot silently include a proxy/app/tunnel restart.
    if (( ${#service_units[@]} != 1 )); then
        sage_mate_route_instance_operation mixed-services
    else
        sage_mate_route_instance_operation "$action"
    fi
fi
if [[ "$action" != "status" ]]; then
    if [[ "$action" == "start" || "$action" == "restart" ]]; then
        stop_legacy_conflicting_services
    fi
    if [[ "$action" == "restart" ]]; then
        cleanup_vllm_engine_residuals
    fi
    systemctl --user "$action" "${service_units[@]}"
    if [[ "$action" == "stop" ]]; then
        cleanup_vllm_engine_residuals
    fi
fi

# ── Collect status for each service ──────────────────────────────────────────
service_payload="[]"
for entry in "${services[@]}"; do
    name="${entry%%:*}"
    unit="${entry#*:}"
    properties=$(systemctl --user show "$unit" \
        --property=Id \
        --property=ActiveState \
        --property=SubState \
        --property=Description)

    id_value=""
    active_state=""
    sub_state=""
    description=""
    while IFS='=' read -r key value; do
        case "$key" in
            Id) id_value="$value" ;;
            ActiveState) active_state="$value" ;;
            SubState) sub_state="$value" ;;
            Description) description="$value" ;;
        esac
    done <<< "$properties"

    service_payload=$(SERVICE_PAYLOAD="$service_payload" "$python_bin" - "$name" "$id_value" "$active_state" "$sub_state" "$description" <<'PY'
import json, os, sys
payload = json.loads(os.environ["SERVICE_PAYLOAD"])
payload.append({
    "name": sys.argv[1],
    "unit": sys.argv[2],
    "active_state": sys.argv[3],
    "sub_state": sys.argv[4],
    "description": sys.argv[5] or None,
})
print(json.dumps(payload, ensure_ascii=False))
PY
    )
done

# ── Output ───────────────────────────────────────────────────────────────────
message="Service action '${action}' completed."

if [[ "$json_output" == "true" ]]; then
    SERVICES_JSON="$service_payload" "$python_bin" - "$action" "$message" <<'PY'
import json, os, sys
print(json.dumps({
    "action": sys.argv[1],
    "success": True,
    "message": sys.argv[2],
    "services": json.loads(os.environ["SERVICES_JSON"]),
}, ensure_ascii=False))
PY
    exit 0
fi

echo "$message"
SERVICES_JSON="$service_payload" "$python_bin" - <<'PY'
import json, os
for item in json.loads(os.environ["SERVICES_JSON"]):
    state = item['active_state']
    icon = "✓" if state == "active" else "✗" if state == "failed" else "·"
    print(f"  {icon} {item['name']}: {state} ({item['sub_state']}) [{item['unit']}]")
PY
