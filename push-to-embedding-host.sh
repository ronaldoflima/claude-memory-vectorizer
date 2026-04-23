#!/bin/bash
set -euo pipefail

# Host que roda o ETL. Pode ser sobrescrito:
#   EMBEDDING_HOST=mymac ./push-to-embedding-host.sh
#   ./push-to-embedding-host.sh mymac
EMBEDDING_HOST="${1:-${EMBEDDING_HOST:-desktop}}"
REMOTE_DIR="${REMOTE_DIR:-/home/ronaldo/claude-vectorizer-sources/mac}"

echo "Syncing to $EMBEDDING_HOST:$REMOTE_DIR"

rsync -az --delete ~/.claude/projects/ "$EMBEDDING_HOST:$REMOTE_DIR/projects/"
rsync -az ~/.claude/history.jsonl "$EMBEDDING_HOST:$REMOTE_DIR/history.jsonl" 2>/dev/null || true
rsync -az --delete --exclude='.obsidian/' --exclude='.trash/' ~/pessoal/obsidian/ "$EMBEDDING_HOST:$REMOTE_DIR/obsidian/"
