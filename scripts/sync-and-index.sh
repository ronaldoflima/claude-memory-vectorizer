#!/bin/bash
set -euo pipefail

# Ensure Homebrew binaries (gh, etc.) are reachable when run from cron.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

[[ -f "$ROOT_DIR/.env" ]] && set -a && source "$ROOT_DIR/.env" && set +a

VPS_HOST="${VPS_HOST:-}"
VPS_SOURCE_HOST="${VPS_SOURCE_HOST:-}"
VPS_SOURCE_USER="${VPS_SOURCE_USER:-}"
QDRANT_VPS_PORT="${QDRANT_VPS_PORT:-6333}"
GITHUB_ORG="${GITHUB_ORG:-}"
LOCAL_TUNNEL_PORT=16333
PYTHON_BIN="${PYTHON_BIN:-/usr/local/bin/python3}"
LOG_FILE="$SCRIPT_DIR/sync.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

cleanup() {
    if [[ -n "${TUNNEL_PID:-}" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        kill "$TUNNEL_PID" 2>/dev/null
        log "SSH tunnel closed"
    fi
}
trap cleanup EXIT

log "=== Starting sync-and-index ==="

# 1. Rsync VPS sessions to local /tmp (optional)
if [[ -n "$VPS_SOURCE_HOST" && -n "$VPS_SOURCE_USER" ]]; then
    log "Syncing VPS sessions ($VPS_SOURCE_HOST)..."
    rsync -az --delete "$VPS_SOURCE_HOST:/home/$VPS_SOURCE_USER/.claude/projects/" /tmp/vps-source-claude-projects/
    rsync -az "$VPS_SOURCE_HOST:/home/$VPS_SOURCE_USER/.claude/history.jsonl" /tmp/vps-source-claude-history.jsonl 2>/dev/null || true
    log "VPS sync done"
else
    log "Skipping VPS sync (VPS_SOURCE_HOST/VPS_SOURCE_USER not set)"
fi

# 2. Open SSH tunnel to VPS Qdrant
if [[ -z "$VPS_HOST" ]]; then
    log "Error: VPS_HOST not set in .env" >&2
    exit 1
fi

log "Opening SSH tunnel (localhost:$LOCAL_TUNNEL_PORT -> VPS:$QDRANT_VPS_PORT)..."
EXISTING_PID=$(lsof -ti "tcp:$LOCAL_TUNNEL_PORT" -sTCP:LISTEN 2>/dev/null | head -1 || true)
if [[ -n "$EXISTING_PID" ]]; then
    log "Killing stale tunnel (PID $EXISTING_PID) on port $LOCAL_TUNNEL_PORT..."
    kill "$EXISTING_PID" 2>/dev/null || true
    sleep 1
fi
ssh -f -N -L "$LOCAL_TUNNEL_PORT:localhost:$QDRANT_VPS_PORT" "$VPS_HOST"
TUNNEL_PID=$(lsof -ti "tcp:$LOCAL_TUNNEL_PORT" -sTCP:LISTEN 2>/dev/null | head -1 || true)
log "Tunnel PID: $TUNNEL_PID"

sleep 1
QDRANT_URL="http://localhost:$LOCAL_TUNNEL_PORT"

# 3. ETL - local Claude sessions
log "Indexing local sessions..."
PYTHONUNBUFFERED=1 "$PYTHON_BIN" "$ROOT_DIR/etl/claude/conversations.py" \
    --qdrant-url "$QDRANT_URL" \
    --source-label local \
    2>&1 | tee -a "$LOG_FILE"

# 4. ETL - VPS sessions (optional)
if [[ -n "$VPS_SOURCE_HOST" && -n "$VPS_SOURCE_USER" ]]; then
    log "Indexing VPS sessions..."
    PYTHONUNBUFFERED=1 "$PYTHON_BIN" "$ROOT_DIR/etl/claude/conversations.py" \
        --qdrant-url "$QDRANT_URL" \
        --source-dir /tmp/vps-source-claude-projects \
        --history /tmp/vps-source-claude-history.jsonl \
        --source-label vps-mcpgw \
        --state-file "$SCRIPT_DIR/.etl_state_vps.json" \
        2>&1 | tee -a "$LOG_FILE"
fi

# 5. ETL - GitHub PRs (optional, last 7 days)
if [[ -n "$GITHUB_ORG" ]]; then
    log "Indexing GitHub PRs..."
    SINCE=$(date -v-7d '+%Y-%m-%d')
    PYTHONUNBUFFERED=1 "$PYTHON_BIN" "$ROOT_DIR/etl/github/prs.py" \
        --qdrant-url "$QDRANT_URL" \
        --org "$GITHUB_ORG" \
        --since "$SINCE" \
        2>&1 | tee -a "$LOG_FILE"
else
    log "Skipping GitHub PRs (GITHUB_ORG not set)"
fi

# 6. ETL - Obsidian notes (optional)
if [[ -n "${OBSIDIAN_DIR:-}" ]]; then
    log "Indexing Obsidian notes ($OBSIDIAN_DIR)..."
    PYTHONUNBUFFERED=1 "$PYTHON_BIN" "$ROOT_DIR/etl/obsidian/notes.py" \
        --qdrant-url "$QDRANT_URL" \
        --vault "$OBSIDIAN_DIR" \
        2>&1 | tee -a "$LOG_FILE"
else
    log "Skipping Obsidian notes (OBSIDIAN_DIR not set)"
fi

# ETL - Teams messages (optional)
if [[ -n "${TEAMS_CRAWLER_DIR:-}" ]]; then
    TEAMS_DATA="${TEAMS_DATA_DIR:-$TEAMS_CRAWLER_DIR/data}"
    log "Indexing Teams messages ($TEAMS_DATA)..."
    PYTHONUNBUFFERED=1 TEAMS_CRAWLER_DIR="$TEAMS_CRAWLER_DIR" "$PYTHON_BIN" "$ROOT_DIR/etl/teams/messages.py" \
        --qdrant-url "$QDRANT_URL" \
        --data-dir "$TEAMS_DATA" \
        2>&1 | tee -a "$LOG_FILE"
else
    log "Skipping Teams messages (TEAMS_CRAWLER_DIR not set)"
fi

log "=== Done ==="
