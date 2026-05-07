#!/usr/bin/env python3
"""
ETL: Claude Code conversations → Qdrant vector store
Extracts user/assistant messages, generates embeddings via Ollama, indexes in Qdrant.
"""

import json
import hashlib
import datetime
import os
import sys
from pathlib import Path

import requests

# Load .env from repo root if present
_env_file = Path(__file__).parent.parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
COLLECTION = os.environ.get("COLLECTION", "agent_sessions")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
VECTOR_SIZE = int(os.environ.get("VECTOR_SIZE", "768"))
CHUNK_MAX_CHARS = int(os.environ.get("CHUNK_MAX_CHARS", "3500"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", "400"))

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
HISTORY_FILE = Path.home() / ".claude" / "history.jsonl"
STATE_FILE = Path(__file__).parent / ".etl_state.json"
SOURCE_LABEL = "local"

_strip_raw = os.environ.get("PROJECT_PATH_STRIP", "")
PROJECT_PATH_STRIPS = [s for s in _strip_raw.split(":") if s] if _strip_raw else []


def get_embedding(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBEDDING_MODEL, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def ensure_collection():
    resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION}")
    if resp.status_code == 200:
        return
    requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION}",
        json={
            "vectors": {"size": VECTOR_SIZE, "distance": "Cosine"},
            "on_disk_payload": True,
        },
    ).raise_for_status()
    for field in ("project", "date", "source", "session_id"):
        requests.put(
            f"{QDRANT_URL}/collections/{COLLECTION}/index",
            json={"field_name": field, "field_schema": "keyword"},
        )
    requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION}/index",
        json={
            "field_name": "text",
            "field_schema": {
                "type": "text",
                "tokenizer": "multilingual",
                "min_token_len": 2,
                "max_token_len": 40,
                "lowercase": True,
            },
        },
    )
    print(f"Collection '{COLLECTION}' created (size={VECTOR_SIZE}).")


def load_session_dates() -> dict:
    dates = {}
    if not HISTORY_FILE.exists():
        return dates
    with open(HISTORY_FILE) as f:
        for line in f:
            try:
                obj = json.loads(line)
                sid = obj.get("sessionId", "")
                ts = obj.get("timestamp", 0)
                if sid and ts:
                    dt = datetime.datetime.fromtimestamp(ts / 1000)
                    date_str = dt.strftime("%Y-%m-%d")
                    if sid not in dates:
                        dates[sid] = {"date": date_str, "first_ts": dt.isoformat(), "last_ts": dt.isoformat()}
                    else:
                        dates[sid]["last_ts"] = dt.isoformat()
                        if date_str < dates[sid]["date"]:
                            dates[sid]["date"] = date_str
            except (json.JSONDecodeError, ValueError):
                continue
    return dates


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"indexed_files": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def extract_messages(jsonl_path: Path) -> list[dict]:
    messages = []
    try:
        with open(jsonl_path) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = obj.get("type")
                if msg_type not in ("user", "assistant"):
                    continue
                message = obj.get("message", obj.get("data", {}))
                if isinstance(message, dict):
                    role = message.get("role", msg_type)
                    content = message.get("content", "")
                    if isinstance(content, list):
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    text_parts.append(block["text"])
                                elif block.get("type") == "tool_result":
                                    pass
                            elif isinstance(block, str):
                                text_parts.append(block)
                        content = "\n".join(text_parts)
                    if content and len(content.strip()) > 10:
                        messages.append({"role": role, "text": content.strip()})
    except Exception as e:
        print(f"  Error reading {jsonl_path}: {e}")
    return messages


def _tail_overlap(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    nl = tail.find("\n")
    if 0 <= nl < max_chars // 2:
        tail = tail[nl + 1 :]
    return tail


def chunk_session(messages: list[dict], session_id: str, project: str, file_path: str, session_date: str = "", session_first_ts: str = "", session_last_ts: str = "") -> list[dict]:
    chunks: list[dict] = []
    current_parts: list[str] = []
    current_len = 0

    def base_payload(text: str) -> dict:
        return {
            "text": text,
            "session_id": session_id,
            "project": project,
            "source": SOURCE_LABEL,
            "file": file_path,
            "date": session_date,
            "first_ts": session_first_ts,
            "last_ts": session_last_ts,
        }

    def flush():
        nonlocal current_parts, current_len
        if not current_parts:
            return
        text = "\n\n".join(current_parts)
        chunks.append(base_payload(text))
        if CHUNK_OVERLAP_CHARS > 0:
            tail = _tail_overlap(text, CHUNK_OVERLAP_CHARS)
            current_parts = [tail] if tail else []
            current_len = len(tail)
        else:
            current_parts = []
            current_len = 0

    for msg in messages:
        prefix = "User" if msg["role"] == "user" else "Assistant"
        line = f"{prefix}: {msg['text']}"

        if current_len + len(line) + 2 > CHUNK_MAX_CHARS and current_parts:
            flush()

        if len(line) > CHUNK_MAX_CHARS:
            start = 0
            while start < len(line):
                end = min(start + CHUNK_MAX_CHARS, len(line))
                piece = line[start:end]
                if current_parts and current_len + len(piece) + 2 > CHUNK_MAX_CHARS:
                    flush()
                current_parts.append(piece)
                current_len += len(piece) + 2
                if end < len(line):
                    flush()
                start = end
            continue

        current_parts.append(line)
        current_len += len(line) + 2

    if current_parts:
        text = "\n\n".join(current_parts)
        chunks.append(base_payload(text))

    return chunks


def text_to_id(text: str) -> int:
    h = hashlib.md5(text.encode()).hexdigest()
    return int(h[:16], 16)


def index_chunks(chunks: list[dict]):
    if not chunks:
        return 0

    points = []
    for chunk in chunks:
        embedding = get_embedding(chunk["text"])
        point_id = text_to_id(chunk["text"])
        points.append({
            "id": point_id,
            "vector": embedding,
            "payload": {
                "text": chunk["text"],
                "session_id": chunk["session_id"],
                "project": chunk["project"],
                "source": chunk.get("source", SOURCE_LABEL),
                "file": chunk["file"],
                "date": chunk.get("date", ""),
                "first_ts": chunk.get("first_ts", ""),
                "last_ts": chunk.get("last_ts", ""),
                "indexed_at": datetime.datetime.now().isoformat(),
            },
        })

    batch_size = 50
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        requests.put(
            f"{QDRANT_URL}/collections/{COLLECTION}/points",
            json={"points": batch},
        ).raise_for_status()

    return len(points)


def clean_project_name(dir_name: str) -> str:
    name = dir_name
    for fragment in PROJECT_PATH_STRIPS:
        name = name.replace(fragment, "")
    return name.strip("-") or dir_name


def find_memory_files() -> list[tuple[Path, str]]:
    results = []
    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        memory_dir = project_dir / "memory"
        if not memory_dir.is_dir():
            continue
        project_name = clean_project_name(project_dir.name)
        for f in memory_dir.glob("*.md"):
            if f.name == "MEMORY.md":
                continue
            results.append((f, project_name))
    return results


def parse_memory_file(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    frontmatter = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    frontmatter[k.strip()] = v.strip()
            body = parts[2].strip()
    if not body or len(body) < 10:
        return None
    mem_type = frontmatter.get("type", "unknown")
    mem_name = frontmatter.get("name", path.stem)
    return {
        "name": mem_name,
        "type": mem_type,
        "description": frontmatter.get("description", ""),
        "body": body,
    }


def find_session_files() -> list[tuple[Path, str, str]]:
    results = []
    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        project_name = clean_project_name(project_dir.name)
        for f in project_dir.rglob("*.jsonl"):
            session_id = f.stem
            results.append((f, session_id, project_name))

    return results


def parse_arg(flag: str) -> str | None:
    try:
        idx = sys.argv.index(flag)
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        return None


def main():
    global QDRANT_URL, CLAUDE_PROJECTS_DIR, HISTORY_FILE, STATE_FILE, SOURCE_LABEL

    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    if v := parse_arg("--qdrant-url"):
        QDRANT_URL = v
    if v := parse_arg("--source-dir"):
        CLAUDE_PROJECTS_DIR = Path(v)
    if v := parse_arg("--history"):
        HISTORY_FILE = Path(v)
    if v := parse_arg("--source-label"):
        SOURCE_LABEL = v
    if v := parse_arg("--state-file"):
        STATE_FILE = Path(v)

    print("Claude Conversations → Qdrant ETL")
    print(f"  Qdrant: {QDRANT_URL}")
    print(f"  Ollama: {OLLAMA_URL} ({EMBEDDING_MODEL})")
    print()

    if not dry_run:
        ensure_collection()

    state = load_state()
    indexed = state["indexed_files"]

    session_dates = load_session_dates()
    print(f"Loaded dates for {len(session_dates)} sessions from history.jsonl")

    files = find_session_files()
    print(f"Found {len(files)} session files")

    new_files = []
    for f, sid, proj in files:
        file_key = str(f)
        file_mtime = f.stat().st_mtime
        if not force and file_key in indexed and indexed[file_key] >= file_mtime:
            continue
        new_files.append((f, sid, proj))

    print(f"New/modified: {len(new_files)} files")

    if dry_run:
        for f, sid, proj in new_files[:10]:
            msgs = extract_messages(f)
            sd = session_dates.get(sid, {})
            chunks = chunk_session(msgs, sid, proj, str(f), sd.get("date", ""), sd.get("first_ts", ""), sd.get("last_ts", ""))
            print(f"  {proj}/{sid[:8]}: {len(msgs)} msgs → {len(chunks)} chunks")
        if len(new_files) > 10:
            print(f"  ... +{len(new_files) - 10} more")
        return

    total_chunks = 0
    for i, (f, sid, proj) in enumerate(new_files):
        msgs = extract_messages(f)
        if not msgs:
            indexed[str(f)] = f.stat().st_mtime
            continue

        sd = session_dates.get(sid, {})
        file_date = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
        chunks = chunk_session(msgs, sid, proj, str(f), sd.get("date", file_date), sd.get("first_ts", ""), sd.get("last_ts", ""))
        n = index_chunks(chunks)
        total_chunks += n
        indexed[str(f)] = f.stat().st_mtime

        if (i + 1) % 10 == 0 or i == len(new_files) - 1:
            save_state(state)
            print(f"  [{i+1}/{len(new_files)}] {proj}/{sid[:8]}: {n} chunks indexed")

    save_state(state)
    print(f"Indexed {total_chunks} conversation chunks from {len(new_files)} files.")

    # --- Memory files ---
    memory_files = find_memory_files()
    print(f"\nFound {len(memory_files)} memory files")

    new_memories = []
    for f, proj in memory_files:
        file_key = str(f)
        file_mtime = f.stat().st_mtime
        if not force and file_key in indexed and indexed[file_key] >= file_mtime:
            continue
        new_memories.append((f, proj))

    print(f"New/modified: {len(new_memories)} memory files")

    mem_chunks = 0
    for i, (f, proj) in enumerate(new_memories):
        parsed = parse_memory_file(f)
        if not parsed:
            indexed[str(f)] = f.stat().st_mtime
            continue

        file_date = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
        text = f"Memory [{parsed['type']}] {parsed['name']}: {parsed['body']}"
        chunk = {
            "text": text,
            "session_id": f"memory-{f.stem}",
            "project": proj,
            "source": SOURCE_LABEL,
            "file": str(f),
            "date": file_date,
        }
        n = index_chunks([chunk])
        mem_chunks += n
        indexed[str(f)] = f.stat().st_mtime

    save_state(state)
    print(f"Indexed {mem_chunks} memory chunks from {len(new_memories)} files.")
    print(f"\nDone! Total: {total_chunks + mem_chunks} chunks.")


if __name__ == "__main__":
    main()
