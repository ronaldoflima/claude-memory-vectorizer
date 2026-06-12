# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ETL pipeline that indexes Claude Code conversation histories (and optionally Obsidian notes, GitHub PRs, Teams messages) into Qdrant for semantic + fulltext search. Embeddings are generated locally via Ollama (`bge-m3`, 1024 dims, cosine).

**Collections** — each source writes to its own per-source collection **and** to a shared unified collection (`agent_memory`), so a single MCP tool can search across everything. All collections share the same vector config (1024 dims, cosine).

| ETL | per-source collection | env override |
|---|---|---|
| `etl/claude/conversations.py` | `agent_sessions` | `COLLECTION` |
| `etl/obsidian/notes.py` | `agent_notes` | `COLLECTION_NOTES` |
| `etl/github/prs.py` | `agent_work_artifacts` | `COLLECTION_ARTIFACTS` |
| `etl/teams/messages.py` | `agent_teams` | `COLLECTION_TEAMS` |
| _all of the above_ | `agent_memory` (unified) | `COLLECTION_UNIFIED` |

## Common commands

```bash
# Start local Qdrant
docker compose up -d

# Incremental index of local Claude sessions
python3 etl/claude/conversations.py

# Dry-run / force reindex
python3 etl/claude/conversations.py --dry-run
python3 etl/claude/conversations.py --force

# Full pipeline (VPS rsync + SSH tunnel to remote Qdrant + local/VPS/PR ETLs)
./scripts/sync-and-index.sh

# CLI search
python3 search.py "query text" [--project NAME] [--date YYYY-MM-DD] [--limit N]
```

No test suite, linter, or build step. Config is loaded from `.env` at repo root (see `.env.example`).

## Architecture

**ETL pattern** — each source lives in `etl/<source>/` as a standalone script with its own `.etl_state*.json` file tracking indexed file mtimes for incremental runs. All ETLs share:
- The same embedding model (`bge-m3`) and vector config (1024 dims, cosine)
- **Dual-write**: every ETL calls `upsert_batch()` twice per batch — once into its per-source collection, once into the unified `agent_memory` collection (same point IDs in both). `ensure_collection()` creates both on first run.
- `--qdrant-url`, `--source-label`, `--state-file` CLI overrides so the same script can be run multiple times against different source sets (local vs `vps-mcpgw`) while keeping separate state
- Deterministic point IDs via `md5(text)[:16]` — re-indexing the same chunk is idempotent

**Chunking** — sessions are split at `CHUNK_MAX_CHARS` (2000) boundaries joining user/assistant turns; session date comes from `~/.claude/history.jsonl` (keyed by `sessionId`), falling back to file mtime. Project names are derived from Claude's dir-encoded paths and cleaned by stripping `PROJECT_PATH_STRIP` fragments (colon-separated env var).

**Memory files** — `etl/claude/conversations.py` also scans `<project>/memory/*.md` (skipping `MEMORY.md` index), parses frontmatter, and indexes them as `Memory [type] name: body` chunks with `session_id=memory-<stem>`.

**Search** — hybrid: semantic first via Qdrant `/points/search`; if top score < `SEMANTIC_THRESHOLD` (0.6), fulltext fallback via `/points/scroll` with `match.text` filter, merged and deduped. Implemented identically in `search.py` (sync) and the `mcp/` plugins (async `httpx`). Each searcher targets a specific collection: `search.py` defaults to `agent_sessions` (overridable via `--collection` / `--list-collections`), `mcp/conversation_history_search.py` → `agent_sessions`, `mcp/work_artifacts_search.py` → `agent_work_artifacts`. Search the unified `agent_memory` to span all sources at once.

**MCP plugins** — files under `mcp/` target an external gateway that expects `services.base.ServicePlugin` / `ToolDefinition`. They aren't runnable from this repo alone; they're deployed into the gateway host.

**Multi-host setup** — Qdrant + Ollama typically run on a VPS. `sync-and-index.sh` opens an SSH tunnel (`localhost:16333 -> VPS:6333`) and pipes `--qdrant-url http://localhost:16333` into each ETL. `push-to-embedding-host.sh` is the inverse: rsync local sources to a remote host that runs the ETL.

## Conventions

- New ETLs should follow the pattern in `etl/claude/conversations.py`: env loading from repo-root `.env`, `ensure_collection()` (creates both per-source + unified), mtime-based state file, `--dry-run`/`--force`/`--qdrant-url` flags, batch size 50 on Qdrant upserts, and dual-write (per-source collection + `agent_memory`).
- Never change the vector size (1024) or distance (cosine) without reindexing everything — point IDs and payloads are shared across the per-source and unified collections. Collection names are env-configurable per ETL (see the table in Architecture), but renaming one means reindexing it.
- `PROJECT_PATH_STRIP` is host-specific (strips things like the user's home prefix from Claude's mangled dir names) — keep it in `.env`, not code.
