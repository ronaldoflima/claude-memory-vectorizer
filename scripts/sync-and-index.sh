#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VPS_HOST="vps-mesh-root"
VPS_MCPGW_HOST="vps-mesh-root"
QDRANT_VPS_PORT=6333
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

# 1. Rsync VPS mcpgw sessions to local /tmp
log "Syncing VPS mcpgw sessions..."
rsync -az --delete "$VPS_MCPGW_HOST:/home/mcpgw/.claude/projects/" /tmp/vps-mcpgw-claude-projects/
rsync -az "$VPS_MCPGW_HOST:/home/mcpgw/.claude/history.jsonl" /tmp/vps-mcpgw-claude-history.jsonl 2>/dev/null || true
log "VPS sync done"

# 2. Open SSH tunnel to VPS Qdrant
log "Opening SSH tunnel (localhost:$LOCAL_TUNNEL_PORT -> VPS:$QDRANT_VPS_PORT)..."
ssh -f -N -L "$LOCAL_TUNNEL_PORT:localhost:$QDRANT_VPS_PORT" "$VPS_HOST"
TUNNEL_PID=$(lsof -ti "tcp:$LOCAL_TUNNEL_PORT" -sTCP:LISTEN 2>/dev/null | head -1)
log "Tunnel PID: $TUNNEL_PID"

sleep 1
QDRANT_URL="http://localhost:$LOCAL_TUNNEL_PORT"

# 3. ETL - Mac local sessions
log "Indexing Mac local sessions..."
PYTHONUNBUFFERED=1 python3 "$ROOT_DIR/etl/claude/conversations.py" \
    --qdrant-url "$QDRANT_URL" \
    --source-label local \
    2>&1 | tee -a "$LOG_FILE"

# 4. ETL - VPS mcpgw sessions
log "Indexing VPS mcpgw sessions..."
PYTHONUNBUFFERED=1 python3 "$ROOT_DIR/etl/claude/conversations.py" \
    --qdrant-url "$QDRANT_URL" \
    --source-dir /tmp/vps-mcpgw-claude-projects \
    --history /tmp/vps-mcpgw-claude-history.jsonl \
    --source-label vps-mcpgw \
    --state-file "$SCRIPT_DIR/.etl_state_vps.json" \
    2>&1 | tee -a "$LOG_FILE"

# 5. ETL - GitHub PRs (last 7 days)
log "Indexing GitHub PRs..."
SINCE=$(date -v-7d '+%Y-%m-%d')
PYTHONUNBUFFERED=1 python3 "$ROOT_DIR/etl/github/prs.py" \
    --qdrant-url "$QDRANT_URL" \
    --org px-center \
    --since "$SINCE" \
    2>&1 | tee -a "$LOG_FILE"

log "=== Done ==="
