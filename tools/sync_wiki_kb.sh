#!/usr/bin/env bash
# Keep the canonical Wiki repository synchronized with Twin's live KB.

set -euo pipefail

SCRIPT_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WIKI_DIR="${SAGE_WIKI_DIR:-/home/shuhao/sage-wiki}"
WIKI_REPO_URL="${SAGE_WIKI_REPO_URL:-https://github.com/intellistream/sage-wiki.git}"
WIKI_BRANCH="${SAGE_WIKI_BRANCH:-main}"

load_dotenv() {
    if [[ -f "$SCRIPT_REPO_ROOT/.env" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
            key="${line%%=*}"
            key="${key// /}"
            [[ -z "$key" || -n "${!key:-}" ]] && continue
            export "$line"
        done < "$SCRIPT_REPO_ROOT/.env"
    fi
}

load_dotenv
RUNTIME_ROOT="${DIGITAL_TWIN_RUNTIME_DIR:-$SCRIPT_REPO_ROOT/../sage-mate-runtime-private}"
LOG_DIR="$RUNTIME_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/wiki_sync_$(date +%Y%m%d_%H%M%S).log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

fail() {
    log "ERROR: $*" >&2
    exit 1
}

log "=== Wiki KB Sync Start ==="
log "Wiki dir: $WIKI_DIR"

if [[ ! -e "$WIKI_DIR" ]]; then
    log "Cloning $WIKI_REPO_URL ($WIKI_BRANCH)"
    git clone --depth 1 --branch "$WIKI_BRANCH" "$WIKI_REPO_URL" "$WIKI_DIR" \
        2>&1 | tee -a "$LOG_FILE" || fail "Wiki clone failed"
fi
[[ -d "$WIKI_DIR/.git" ]] || fail "Wiki directory is not a Git checkout: $WIKI_DIR"
[[ -d "$WIKI_DIR/docs" ]] || fail "Wiki docs directory is missing: $WIKI_DIR/docs"
[[ "$(git -C "$WIKI_DIR" branch --show-current)" == "$WIKI_BRANCH" ]] || \
    fail "Wiki checkout is not on $WIKI_BRANCH"
[[ -z "$(git -C "$WIKI_DIR" status --porcelain --untracked-files=no)" ]] || \
    fail "Wiki checkout has local tracked edits; refusing to ingest an unverified revision"

before="$(git -C "$WIKI_DIR" rev-parse HEAD)"
source_update_status="current"
if command -v timeout >/dev/null 2>&1; then
    pull_command=(timeout 45s git -C "$WIKI_DIR" pull --ff-only origin "$WIKI_BRANCH")
else
    pull_command=(git -C "$WIKI_DIR" pull --ff-only origin "$WIKI_BRANCH")
fi
if ! GIT_TERMINAL_PROMPT=0 "${pull_command[@]}" 2>&1 | tee -a "$LOG_FILE"; then
    source_update_status="stale-unverified"
    log "WARN: Wiki pull failed; ingesting existing local revision and retrying at next timer run"
fi
after="$(git -C "$WIKI_DIR" rev-parse HEAD)"
log "Wiki revision: ${before:0:8} → ${after:0:8} (source update: $source_update_status)"

ACTIVE_REPO_ROOT="${SAGE_MATE_APP_REPO_ROOT:-}"
if [[ -z "$ACTIVE_REPO_ROOT" ]]; then
    ACTIVE_REPO_ROOT="$(systemctl --user show sage-mate-app.service -p WorkingDirectory --value)"
fi
[[ -f "$ACTIVE_REPO_ROOT/tools/ingest_wiki.py" ]] || \
    fail "Cannot find ingest_wiki.py in the active Twin release: $ACTIVE_REPO_ROOT"

PYTHON_BIN="${SAGE_MATE_PYTHON_BIN:-${PYTHON_BIN:-}}"
if [[ -z "$PYTHON_BIN" && -x "$ACTIVE_REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ACTIVE_REPO_ROOT/.venv/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || fail "No usable Python interpreter configured"

# A shared source tree is not enough: the timer must use the same retrieval
# environment as the app. Refuse drift before opening or mutating the index.
(
    cd "$ACTIVE_REPO_ROOT"
    PYTHONPATH="$ACTIVE_REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" tools/check_retrieval_config.py
) 2>&1 | tee -a "$LOG_FILE" || fail "Retrieval configuration preflight failed"

changed_marker="$(mktemp "$LOG_DIR/.wiki-changed.XXXXXX")"
trap 'rm -f -- "$changed_marker"' EXIT
log "Ingesting with active release: $ACTIVE_REPO_ROOT"
(
    cd "$ACTIVE_REPO_ROOT"
    PYTHONPATH="$ACTIVE_REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
        "$PYTHON_BIN" tools/ingest_wiki.py \
        --wiki-dir "$WIKI_DIR" --changed-marker "$changed_marker"
) 2>&1 | tee -a "$LOG_FILE" || fail "Wiki ingest failed"

if [[ -s "$changed_marker" ]]; then
    log "Wiki documents changed: $(tr -d '\n' < "$changed_marker")"
    if [[ "${SAGE_MATE_RESTART_AFTER_WIKI_SYNC:-0}" == "1" ]]; then
        systemctl --user try-restart sage-mate-app.service || fail "Twin restart failed"
        if [[ -n "${SAGE_MATE_HEALTH_URL:-}" ]]; then
            ready=0
            health_wait_seconds="${SAGE_MATE_HEALTH_WAIT_SECONDS:-420}"
            [[ "$health_wait_seconds" =~ ^[0-9]+$ ]] || fail "Invalid health wait: $health_wait_seconds"
            deadline=$((SECONDS + health_wait_seconds))
            while (( SECONDS <= deadline )); do
                if curl -fsS --max-time 3 "$SAGE_MATE_HEALTH_URL" >/dev/null 2>&1; then
                    ready=1
                    break
                fi
                sleep 5
            done
            [[ "$ready" == "1" ]] || fail "Twin did not become healthy after restart"
        fi
        log "Twin refreshed after Wiki changes"
    else
        log "Wiki changed; online Twin requires a refresh"
    fi
else
    log "Wiki documents unchanged; no Twin restart needed"
fi

log "=== Wiki KB Sync Complete ==="
