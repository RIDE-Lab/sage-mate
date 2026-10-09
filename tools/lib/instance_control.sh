#!/usr/bin/env bash
# Default-off owner routing. No supervisor/approval logic belongs in this file.

sage_mate_route_instance_operation() {
    local operation="$1"
    local binding_key binding_line
    # Only these operator-owned values are authoritative. Never source .env as
    # shell code or let an inherited false value bypass persistent enrollment.
    if [[ -f "$repo_root/.env" ]]; then
        while IFS= read -r binding_line || [[ -n "$binding_line" ]]; do
            [[ "$binding_line" == *=* && ! "$binding_line" =~ ^[[:space:]]*# ]] || continue
            binding_key="${binding_line%%=*}"
            case "$binding_key" in
                SAGE_MATE_INSTANCE_CONTROL_ENABLED|SAGE_MATE_INSTANCE_REGISTRATION)
                    export "$binding_line" ;;
            esac
        done < "$repo_root/.env"
    fi
    case "${SAGE_MATE_INSTANCE_CONTROL_ENABLED:-0}" in
        0|false)
            # An enrolled instance stays fenced when new operations are disabled.
            [[ -n "${SAGE_MATE_INSTANCE_REGISTRATION:-}" ]] || return 0 ;;
        1|true) ;;
        *) echo 'ERROR: invalid SAGE_MATE_INSTANCE_CONTROL_ENABLED; refusing operation.' >&2; exit 2 ;;
    esac
    local owner_python="${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"
    [[ -n "$owner_python" && -x "$owner_python" ]] || {
        echo 'ERROR: Python is required for enrolled instance control.' >&2; exit 2;
    }
    # Producer failure must never fall through to legacy cleanup/restart.
    # Signals and lifetime remain attached to the systemd-managed process.
    exec "$owner_python" -I "$repo_root/tools/sage_mate_instance_control.py" \
        --action "$operation"
}
