#!/bin/bash
set -euo pipefail

SKILLS_SRC="$(cd "$(dirname "$0")/skills" && pwd)"
SKILLS_DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

mkdir -p "$SKILLS_DEST"

echo "Installing skills from $SKILLS_SRC → $SKILLS_DEST"

for skill in "$SKILLS_SRC"/*/; do
    name="$(basename "$skill")"
    dest="$SKILLS_DEST/$name"

    if [[ -L "$dest" ]]; then
        echo "  update symlink: $name"
    elif [[ -d "$dest" ]]; then
        echo "  replace dir:    $name"
    else
        echo "  install:        $name"
    fi

    ln -sfn "$skill" "$dest"
done

echo "Done."
