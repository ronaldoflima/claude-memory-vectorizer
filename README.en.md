# claude-memory-vectorizer

> 🌐 **English** · [Português](README.md)

Indexes Claude Code conversation history into a vector store (Qdrant) for semantic search across past sessions. Enables AI agents and tools to query what was discussed, decided, or built in previous conversations.

## How it works

1. Reads `.jsonl` session files from `~/.claude/projects/`
2. Extracts user/assistant messages and chunks them (~2000 chars)
3. Generates embeddings via Ollama (`bge-m3`, 1024 dims, runs locally — or `nomic-embed-text`/768 dims as a lighter alternative)
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

### Software

- **Python** 3.11+
- **Docker** (to run Qdrant locally) — or access to a remote Qdrant
- **[Ollama](https://ollama.com)** with `bge-m3` (default, 1024 dims) or `nomic-embed-text` (768 dims, lighter)
- **[Qdrant](https://qdrant.tech)** local via Docker or remote

### Operating system

- macOS, Linux, or WSL2 on Windows
- `bash` (scripts under `scripts/` rely on bash features; `sync-and-index.sh` uses BSD-style `date -v` — on Linux you may need to swap it for `date -d`)

### Resource usage

RAM costs are **peaks during indexing**, not constant usage. When idle (no indexing running) Qdrant sits around ~100-200 MB and Ollama unloads the model after its `keep_alive` window (default 5 min).

| Component | Idle | During indexing | Disk |
|-----------|------|-----------------|------|
| Ollama (`bge-m3`) | ~0 MB (model unloaded) | ~2 GB while running | ~1.2 GB (model) |
| Qdrant | ~100-200 MB | ~500 MB | grows with history (typically hundreds of MB) |
| Recommended total | — | 8 GB RAM min, 16 GB comfortable | 3-10 GB free |

CPU/GPU: indexing runs fine on CPU; a GPU or Apple Silicon noticeably speeds up the first full pass. After that it's incremental — only new/modified files are processed.

### Data source

- Claude Code history at `~/.claude/projects/` (created automatically by using the CLI)
- Optional: Obsidian vault and GitHub repos for PR/commit indexing

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
# Install Ollama (Linux/macOS):
curl -fsSL https://ollama.com/install.sh | sh

# Default (recommended): bge-m3 — 1024 dims, multilingual, larger context
ollama pull bge-m3

# Lighter alternative: nomic-embed-text — 768 dims
# ollama pull nomic-embed-text
```

### 3. Python dependencies

If you don't have `pip` yet:

```bash
# Ubuntu / Debian
sudo apt install -y python3-pip python3-venv

# macOS / Windows / other distros
python3 -m ensurepip --upgrade
```

Install dependencies:

```bash
pip install -r requirements.txt
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
| `EMBEDDING_MODEL` | Ollama model (default: `bge-m3`; alternative: `nomic-embed-text`) |
| `VECTOR_SIZE` | Vector dim — must match the model (`1024` for `bge-m3`, `768` for `nomic-embed-text`). Changing this requires reindexing. |
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

## Automation with crontab

To index automatically in the background, add a crontab entry.

### Hourly local indexing

```bash
crontab -e
```

Add:

```cron
# Index local Claude sessions every hour
0 * * * * cd /path/to/claude-memory-vectorizer && python3 etl/claude/conversations.py >> /tmp/claude-vectorizer.log 2>&1
```

### Hourly full sync (VPS + local)

If you use `sync-and-index.sh` (which opens the SSH tunnel, pulls VPS sources and indexes everything):

```cron
# Full sync + indexing every hour
0 * * * * cd /path/to/claude-memory-vectorizer && ./scripts/sync-and-index.sh >> /tmp/claude-vectorizer-sync.log 2>&1
```

### Tips

- Replace `/path/to/claude-memory-vectorizer` with the absolute repo path.
- Use `crontab -l` to list existing entries.
- Check logs at `/tmp/claude-vectorizer.log` if something is off.
- The script is incremental: it only processes new/modified files since the last run.
- To run only on weekdays: `0 * * * 1-5 cd /path/... && python3 etl/claude/conversations.py`

---

## Claude Code skill

Install the memory search skill into Claude Code:

```bash
./platforms/claude-code/install.sh
```

This symlinks `platforms/claude-code/skills/memory-search.md` into `~/.claude/skills/`, enabling Claude to search your conversation history directly.
