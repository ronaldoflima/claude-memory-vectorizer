---
name: memory-search
description: Search vectorized Claude Code conversation history using semantic + fulltext hybrid search via local Qdrant + Ollama
---

Use this skill to search past Claude Code conversation history indexed in Qdrant.

## When to use

- User asks "how did we do X", "what did we decide about Y", "find where we discussed Z"
- You need context from previous sessions not available in the current conversation
- You need to find commands, decisions, or solutions from past work

## How to search

Run the search script directly:

```bash
python3 ~/pessoal/projects/claude-memory-vectorizer/search.py "<query>"
python3 ~/pessoal/projects/claude-memory-vectorizer/search.py "<query>" --project <name>
python3 ~/pessoal/projects/claude-memory-vectorizer/search.py "<query>" --date 2026-03-15
python3 ~/pessoal/projects/claude-memory-vectorizer/search.py "<query>" --limit 10
```

## Requirements

- Qdrant must be accessible at `http://localhost:6333` (run locally or via SSH tunnel)
- Ollama must be running with `nomic-embed-text` model

## Starting Qdrant tunnel (if remote)

```bash
ssh -fNL 6333:localhost:6333 vps-gateway
```

## Interpreting results

Each result shows:
- Score (semantic similarity 0–1) or `FULLTEXT` (exact match fallback)
- Date, project name, session ID
- Excerpt of the conversation chunk (up to 500 chars)

Results with score ≥ 0.6 are semantic matches. Below that threshold, fulltext results are mixed in.
