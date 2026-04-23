#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

[[ -f "$ROOT_DIR/.env" ]] && set -a && source "$ROOT_DIR/.env" && set +a

VPS_HOST="${VPS_HOST:-}"
VPS_SOURCE_HOST="${VPS_SOURCE_HOST:-}"
VPS_SOURCE_USER="${VPS_SOURCE_USER:-}"
QDRANT_VPS_PORT="${QDRANT_VPS_PORT:-6333}"
GITHUB_ORG="${GITHUB_ORG:-}"
LOCAL_TUNNEL_PORT=16333
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
ssh -f -N -L "$LOCAL_TUNNEL_PORT:localhost:$QDRANT_VPS_PORT" "$VPS_HOST"
TUNNEL_PID=$(lsof -ti "tcp:$LOCAL_TUNNEL_PORT" -sTCP:LISTEN 2>/dev/null | head -1)
log "Tunnel PID: $TUNNEL_PID"

sleep 1
QDRANT_URL="http://localhost:$LOCAL_TUNNEL_PORT"

# 3. ETL - local Claude sessions
log "Indexing local sessions..."
PYTHONUNBUFFERED=1 python3 "$ROOT_DIR/etl/claude/conversations.py" \
    --qdrant-url "$QDRANT_URL" \
    --source-label local \
    2>&1 | tee -a "$LOG_FILE"

# 4. ETL - VPS sessions (optional)
if [[ -n "$VPS_SOURCE_HOST" && -n "$VPS_SOURCE_USER" ]]; then
    log "Indexing VPS sessions..."
    PYTHONUNBUFFERED=1 python3 "$ROOT_DIR/etl/claude/conversations.py" \
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
    PYTHONUNBUFFERED=1 python3 "$ROOT_DIR/etl/github/prs.py" \
        --qdrant-url "$QDRANT_URL" \
        --org "$GITHUB_ORG" \
        --since "$SINCE" \
        2>&1 | tee -a "$LOG_FILE"
else
    log "Skipping GitHub PRs (GITHUB_ORG not set)"
fi

log "=== Done ==="
