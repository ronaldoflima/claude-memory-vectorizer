#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT_DIR/.env" ]] && set -a && source "$ROOT_DIR/.env" && set +a

VPS_SOURCE_HOST="${VPS_SOURCE_HOST:-}"
VPS_SOURCE_USER="${VPS_SOURCE_USER:-}"
VPS_SOURCE_OBSIDIAN_DIR="${VPS_SOURCE_OBSIDIAN_DIR:-}"

if [[ -z "$VPS_SOURCE_HOST" || -z "$VPS_SOURCE_USER" ]]; then
    echo "Error: VPS_SOURCE_HOST and VPS_SOURCE_USER must be set in .env" >&2
    exit 1
fi

echo "Pulling from $VPS_SOURCE_HOST ($VPS_SOURCE_USER)..."

mkdir -p /tmp/vps-source-claude-projects

rsync -az --delete "$VPS_SOURCE_HOST:/home/$VPS_SOURCE_USER/.claude/projects/" /tmp/vps-source-claude-projects/
rsync -az "$VPS_SOURCE_HOST:/home/$VPS_SOURCE_USER/.claude/history.jsonl" /tmp/vps-source-claude-history.jsonl 2>/dev/null || true

if [[ -n "$VPS_SOURCE_OBSIDIAN_DIR" ]]; then
    mkdir -p /tmp/vps-source-obsidian
    rsync -az --delete --exclude='.obsidian/' --exclude='.trash/' \
        "$VPS_SOURCE_HOST:$VPS_SOURCE_OBSIDIAN_DIR/" /tmp/vps-source-obsidian/
    echo "  /tmp/vps-source-obsidian/"
fi

echo "Done. Sources available at:"
echo "  /tmp/vps-source-claude-projects/"
echo "  /tmp/vps-source-claude-history.jsonl"
