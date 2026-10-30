#!/bin/bash
set -euo pipefail
script_dir=$(cd "$(dirname "$0")" && pwd)
# Restore routing before removing the temporary connector. The old school
# egress must be healthy for the original path to provide service again.
python3 "$script_dir/cloudflare_bridge.py" rollback
ssh -o BatchMode=yes a100-dev 'python3 /home/shuhao/twin-alternate-search-auth.py rollback && python3 /home/shuhao/twin-alternate-egress.py rollback'
for label in org.sage.twin.emergency-ingress org.sage.twin.emergency-bridge; do
  launchctl bootout "gui/$(id -u)/$label"
  mv "$HOME/Library/LaunchAgents/$label.plist" "$HOME/.local/share/sage-twin-emergency/$label.plist.disabled"
done
