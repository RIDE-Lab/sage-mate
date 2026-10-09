#!/usr/bin/env bash
# Canonical one-click Sage Mate deployment entrypoint.
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec "$repo_root/tools/lock_sage_mate_engine.sh" "$@"
