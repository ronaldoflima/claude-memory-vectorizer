#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT_DIR/.env" ]] && set -a && source "$ROOT_DIR/.env" && set +a

EMBEDDING_HOST="${1:-${EMBEDDING_HOST:-}}"
REMOTE_DIR="${REMOTE_DIR:-}"
OBSIDIAN_DIR="${OBSIDIAN_DIR:-}"

if [[ -z "$EMBEDDING_HOST" ]]; then
    echo "Error: EMBEDDING_HOST not set. Set it in .env or pass as argument." >&2
    exit 1
fi
if [[ -z "$REMOTE_DIR" ]]; then
    echo "Error: REMOTE_DIR not set. Set it in .env." >&2
    exit 1
fi

echo "Syncing to $EMBEDDING_HOST:$REMOTE_DIR"

rsync -az --delete ~/.claude/projects/ "$EMBEDDING_HOST:$REMOTE_DIR/projects/"
rsync -az ~/.claude/history.jsonl "$EMBEDDING_HOST:$REMOTE_DIR/history.jsonl" 2>/dev/null || true

if [[ -n "$OBSIDIAN_DIR" ]]; then
    eval OBSIDIAN_DIR="$OBSIDIAN_DIR"
    rsync -az --delete --exclude='.obsidian/' --exclude='.trash/' "$OBSIDIAN_DIR/" "$EMBEDDING_HOST:$REMOTE_DIR/obsidian/"
fi
