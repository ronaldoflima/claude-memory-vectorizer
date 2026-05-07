#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SKILLS_DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
SKILL_DIR="$SKILLS_DEST/memory-search"
SKILL_SRC="$PROJECT_ROOT/platforms/claude-code/skills/memory-search"

# Se já existe como symlink antigo, remove para criar diretório real
[[ -L "$SKILL_DIR" ]] && rm "$SKILL_DIR"
mkdir -p "$SKILL_DIR"

echo "Installing memory-search skill → $SKILL_DIR"

# Symlink do projeto inteiro para acesso fácil a search.py e outros scripts
ln -sfn "$PROJECT_ROOT" "$SKILL_DIR/claude-memory-vectorizer"
echo "  linked: claude-memory-vectorizer/ → $PROJECT_ROOT"

# SKILL.md e search.py como symlinks individuais
ln -sfn "$SKILL_SRC/SKILL.md" "$SKILL_DIR/SKILL.md"
ln -sfn "$SKILL_DIR/claude-memory-vectorizer/search.py" "$SKILL_DIR/search.py"
echo "  linked: SKILL.md, search.py"

echo "Done."
