#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
signing_secret="${SLACK_TWIN_SIGNING_SECRET:-}"
team_id="${SLACK_TWIN_TEAM_ID:-}"
# Entries must be workspace-qualified: T123:U456. Empty means binding required.
allowed_user_ids="${SLACK_TWIN_ALLOWED_USER_IDS:-}"
visitor_profile="${SLACK_TWIN_VISITOR_PROFILE:-lab_member}"
request_url="${SLACK_TWIN_REQUEST_URL:-https://twin.sage.org.ai/slack/commands/twin}"

if [[ $# -ne 0 || -z "$signing_secret" || ! "$team_id" =~ ^[A-Za-z0-9]+$ ]]; then
    echo "Set SLACK_TWIN_SIGNING_SECRET and SLACK_TWIN_TEAM_ID in the secure environment, then run ./manage.sh configure-slack-twin without arguments." >&2
    exit 2
fi

env_file="$repo_root/.env"
if [[ ! -f "$env_file" ]]; then
    echo "ERROR: .env not found at $env_file" >&2
    exit 1
fi

"${PYTHON_BIN:-python3}" - "$env_file" "$team_id" "$allowed_user_ids" "$visitor_profile" <<'PY'
from __future__ import annotations

import sys
import os
from pathlib import Path

env_path = Path(sys.argv[1])
updates = {
    "SLACK_TWIN_SIGNING_SECRET": os.environ["SLACK_TWIN_SIGNING_SECRET"],
    "SLACK_TWIN_TEAM_ID": sys.argv[2],
    "SLACK_TWIN_ALLOWED_USER_IDS": sys.argv[3],
    "SLACK_TWIN_VISITOR_PROFILE": sys.argv[4],
}

lines = env_path.read_text().splitlines()
seen: set[str] = set()
out: list[str] = []
for line in lines:
    if line.lstrip().startswith("#") or "=" not in line:
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
env_path.chmod(0o600)
PY

echo "[configure-slack-twin] Slack /twin config written."
echo "[configure-slack-twin] Request URL: $request_url"
echo "[configure-slack-twin] Allowed Slack users: $allowed_user_ids"
echo "[configure-slack-twin] Visitor profile: $visitor_profile"

if [[ "${SLACK_TWIN_RESTART_APP:-true}" == "true" ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl --user restart sage-mate-app.service
    echo "[configure-slack-twin] Restarted sage-mate-app.service"
fi
