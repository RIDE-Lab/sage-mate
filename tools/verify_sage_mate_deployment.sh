#!/usr/bin/env bash
# Canonical read-only deployment verification entrypoint.
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec "$repo_root/tools/verify_sage_mate_engine.sh" "$@"
