# claude-memory-vectorizer

Indexes Claude Code conversation history into a vector store (Qdrant) for semantic search across past sessions. Enables AI agents and tools to query what was discussed, decided, or built in previous conversations.

## How it works

1. Reads `.jsonl` session files from `~/.claude/projects/`
2. Extracts user/assistant messages and chunks them (~2000 chars)
3. Generates embeddings via Ollama (`nomic-embed-text`, 768 dims, runs locally)
4. Upserts into Qdrant with metadata (project, date, session ID, source)
5. Tracks processed files in a state file for incremental runs

Search is hybrid: semantic first, fulltext fallback when best score < 0.6.

## Project structure

```
etl/
  claude/conversations.py   # ETL for Claude Code sessions
  obsidian/notes.py         # ETL for Obsidian vault
  github/prs.py             # ETL for GitHub PRs and commits
mcp/
  conversation_history_search.py  # MCP plugin for agent search
  work_artifacts_search.py        # MCP plugin for PR/commit search
scripts/
  sync-and-index.sh         # Full sync: pull VPS sources + tunnel + index
  pull-from-vps.sh          # Pull Claude sessions (and optionally Obsidian) from VPS
  push-to-embedding-host.sh # Push local sources to a remote embedding host
  check-health.sh           # Health check for Qdrant + Ollama
search.py                   # CLI search tool
platforms/
  claude-code/              # Claude Code skill + install script
```

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) with `nomic-embed-text` model
- [Qdrant](https://qdrant.tech) (local via Docker or remote)

## Setup

### 1. Qdrant

**Local** (with persistent volume):

```bash
docker compose up -d
```

**Remote Tunnel** (optional - if running in VPS):

```bash
ssh -fNL 6333:localhost:6333 your-vps-host
```

### 2. Ollama

```bash
# Install Ollama: https://ollama.com
ollama pull nomic-embed-text
```

### 3. Python dependencies

```bash
pip install requests
```

### 4. Configuration

```bash
cp .env.example .env
# edit .env with your values
```

Key variables:

| Variable | Description |
|----------|-------------|
| `QDRANT_URL` | Qdrant endpoint (default: `http://localhost:6333`) |
| `OLLAMA_URL` | Ollama endpoint (default: `http://localhost:11434`) |
| `VPS_HOST` | SSH host where Qdrant runs (for tunnel in `sync-and-index.sh`) |
| `VPS_SOURCE_HOST` | SSH host to pull Claude sessions from (optional) |
| `VPS_SOURCE_USER` | User on the VPS source host |
| `VPS_SOURCE_OBSIDIAN_DIR` | Obsidian vault path on the VPS (optional) |
| `GITHUB_ORG` | GitHub org/user for PR indexing (optional) |
| `EMBEDDING_HOST` | Host that runs the ETL, for `push-to-embedding-host.sh` |
| `REMOTE_DIR` | Remote path for synced sources |
| `OBSIDIAN_DIR` | Local Obsidian vault path |
| `PROJECT_PATH_STRIP` | Colon-separated path fragments to strip from Claude's internal project directory names |

## Usage

### Index conversations (incremental)

```bash
python3 etl/claude/conversations.py
```

Only processes new or modified session files since the last run. State is saved in `etl/claude/.etl_state.json`.

### Index options

```bash
python3 etl/claude/conversations.py --dry-run
python3 etl/claude/conversations.py --force
python3 etl/claude/conversations.py --qdrant-url http://localhost:6333
python3 etl/claude/conversations.py --source-dir /path/to/.claude/projects
python3 etl/claude/conversations.py --history /path/to/history.jsonl
python3 etl/claude/conversations.py --source-label my-machine
python3 etl/claude/conversations.py --state-file /path/to/state.json
```

### Search

```bash
python3 search.py "how did we implement authentication"
python3 search.py "qdrant setup" --project my-project
python3 search.py "deploy pipeline" --date 2026-03-15
python3 search.py "bug fix" --limit 10
```

## Multi-source setup (optional)

Pull sessions from a VPS and index alongside local sessions:

```bash
# Pull VPS sources locally
./scripts/pull-from-vps.sh

# Index local sessions
python3 etl/claude/conversations.py --source-label local

# Index VPS sessions
python3 etl/claude/conversations.py \
  --source-dir /tmp/vps-source-claude-projects \
  --history /tmp/vps-source-claude-history.jsonl \
  --source-label vps \
  --state-file .etl_state_vps.json
```

`scripts/sync-and-index.sh` automates this full flow including the SSH tunnel to Qdrant.

## Sync sources to a remote embedding host

`scripts/push-to-embedding-host.sh` rsyncs your Claude projects, history, and Obsidian vault to a remote host that runs the ETL:

```bash
./scripts/push-to-embedding-host.sh
# or override host
EMBEDDING_HOST=my-host ./scripts/push-to-embedding-host.sh
```

## Claude Code skill

Install the memory search skill into Claude Code:

```bash
./platforms/claude-code/install.sh
```

This symlinks `platforms/claude-code/skills/memory-search.md` into `~/.claude/skills/`, enabling Claude to search your conversation history directly.
