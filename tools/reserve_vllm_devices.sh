#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
devices="${1:-}"

if [[ -z "$devices" ]]; then
    echo "Usage: ./manage.sh reserve-vllm-devices <comma-separated-device-ids>" >&2
    echo "Example: ./manage.sh reserve-vllm-devices 0,1,2,3" >&2
    exit 2
fi

if [[ "$devices" == *" "* || ! "$devices" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    echo "ERROR: device list must look like 0,1,2,3" >&2
    exit 2
fi

device_count=$(awk -F, '{print NF}' <<<"$devices")
if [[ "$device_count" -lt 1 ]]; then
    echo "ERROR: at least one device is required." >&2
    exit 2
fi

env_file="$repo_root/.env"
if [[ ! -f "$env_file" ]]; then
    echo "ERROR: .env not found at $env_file" >&2
    exit 1
fi

selector_python="${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"
if [[ -z "$selector_python" || ! -x "$selector_python" ]]; then
    echo "ERROR: Python is required to validate Ascend device ownership." >&2
    exit 1
fi
"$selector_python" "$repo_root/tools/select_idle_npus.py" --devices "$devices" >/dev/null

"${PYTHON_BIN:-python3}" - "$env_file" "$devices" "$device_count" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

env_path = Path(sys.argv[1])
devices = sys.argv[2]
device_count = sys.argv[3]
updates = {
    "ASCEND_RT_VISIBLE_DEVICES": devices,
    "VLLM_ENGINE_NPU_DEVICES": devices,
    "VLLM_ENGINE_TP_SIZE": device_count,
}

lines = env_path.read_text().splitlines()
seen: set[str] = set()
out: list[str] = []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        out.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)

for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")

env_path.write_text("\n".join(out) + "\n")
PY

echo "[reserve-vllm-devices] ASCEND_RT_VISIBLE_DEVICES=$devices"
echo "[reserve-vllm-devices] VLLM_ENGINE_NPU_DEVICES=$devices"
echo "[reserve-vllm-devices] VLLM_ENGINE_TP_SIZE=$device_count"
echo "[reserve-vllm-devices] future vllm-hust restarts will be pinned to these Ascend devices"
