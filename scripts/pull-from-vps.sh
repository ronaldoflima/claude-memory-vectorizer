#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT_DIR/.env" ]] && set -a && source "$ROOT_DIR/.env" && set +a

VPS_MCPGW_HOST="${VPS_MCPGW_HOST:-}"
VPS_MCPGW_USER="${VPS_MCPGW_USER:-}"

if [[ -z "$VPS_MCPGW_HOST" || -z "$VPS_MCPGW_USER" ]]; then
    echo "Error: VPS_MCPGW_HOST and VPS_MCPGW_USER must be set in .env" >&2
    exit 1
fi

echo "Pulling from $VPS_MCPGW_HOST ($VPS_MCPGW_USER)..."

mkdir -p /tmp/vps-mcpgw-claude-projects

rsync -az --delete "$VPS_MCPGW_HOST:/home/$VPS_MCPGW_USER/.claude/projects/" /tmp/vps-mcpgw-claude-projects/
rsync -az "$VPS_MCPGW_HOST:/home/$VPS_MCPGW_USER/.claude/history.jsonl" /tmp/vps-mcpgw-claude-history.jsonl 2>/dev/null || true

echo "Done. Sources available at:"
echo "  /tmp/vps-mcpgw-claude-projects/"
echo "  /tmp/vps-mcpgw-claude-history.jsonl"
