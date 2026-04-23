# claude-memory-vectorizer

Indexes Claude Code conversation history into a vector store (Qdrant) for semantic search across past sessions. Enables AI agents and tools to query what was discussed, decided, or built in previous conversations.

## How it works

1. Reads `.jsonl` session files from `~/.claude/projects/`
2. Extracts user/assistant messages and chunks them (~2000 chars)
3. Generates embeddings via Ollama (`nomic-embed-text`, 768 dims, runs locally)
4. Upserts into Qdrant with metadata (project, date, session ID, source)
5. Tracks processed files in a state file for incremental runs

Search is hybrid: semantic first, fulltext fallback when best score < 0.6.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) with `nomic-embed-text` model
- [Qdrant](https://qdrant.tech) (local via Docker or remote)

## Setup

### 1. Qdrant

```bash
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant
```

Or connect to a remote Qdrant via SSH tunnel:

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

## Usage

### Index conversations (incremental)

```bash
python3 etl.py
```

Only processes new or modified session files since the last run. State is saved in `.etl_state.json`.

### Index options

```bash
python3 etl.py --dry-run              # show what would be indexed, no writes
python3 etl.py --force                # re-index everything
python3 etl.py --qdrant-url http://localhost:6333   # custom Qdrant URL
python3 etl.py --source-dir /path/to/.claude/projects  # custom source dir
python3 etl.py --history /path/to/history.jsonl    # custom history file
python3 etl.py --source-label my-machine           # tag chunks by origin
python3 etl.py --state-file /path/to/state.json    # custom state file
```

### Search

```bash
python3 search.py "how did we implement authentication"
python3 search.py "qdrant setup" --project torrepx
python3 search.py "deploy pipeline" --date 2026-03-15
python3 search.py "bug fix" --limit 10
```

## Multi-source setup (optional)

To index sessions from multiple machines, run the ETL separately for each source with different `--source-label` and `--state-file` values pointing to the same Qdrant instance.

Example: indexing a VPS alongside your local machine:

```bash
# Local sessions
python3 etl.py --source-label local

# VPS sessions (after rsyncing them locally)
rsync -az vps-host:/home/user/.claude/projects/ /tmp/vps-projects/
rsync -az vps-host:/home/user/.claude/history.jsonl /tmp/vps-history.jsonl

python3 etl.py \
  --source-dir /tmp/vps-projects \
  --history /tmp/vps-history.jsonl \
  --source-label vps \
  --state-file .etl_state_vps.json
```

The `sync-and-index.sh` script automates this full flow including the SSH tunnel.

## Sync sources to another machine

`push-to-embedding-host.sh` rsyncs your Claude projects, history, and Obsidian vault to a remote host that runs the ETL. Configure the target host:

```bash
# Default host is "desktop" (from SSH config)
./push-to-embedding-host.sh

# Custom host via argument or env var
./push-to-embedding-host.sh my-mac
EMBEDDING_HOST=my-mac ./push-to-embedding-host.sh
```

## Additional ETLs

| Script | Description |
|--------|-------------|
| `etl_obsidian.py` | Indexes Obsidian vault notes into a separate `obsidian_notes` collection |
| `etl_prs.py` | Indexes GitHub PRs and commits via GitHub API |
| `conversation_history_search.py` | MCP-compatible search server used by Claude agents |
| `work_artifacts_search.py` | MCP-compatible search for PRs/commits |
