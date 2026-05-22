#!/usr/bin/env python3
"""
ETL: Obsidian notes → Qdrant vector store
Indexes markdown notes with frontmatter metadata.
"""

import json
import hashlib
import datetime
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
COLLECTION = os.environ.get("COLLECTION_NOTES", "agent_notes")
COLLECTION_UNIFIED = os.environ.get("COLLECTION_UNIFIED", "agent_memory")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
VECTOR_SIZE = int(os.environ.get("VECTOR_SIZE", "1024"))
CHUNK_MAX_CHARS = int(os.environ.get("CHUNK_MAX_CHARS_NOTES", "2000"))

STATE_FILE = Path(__file__).parent / ".etl_obsidian_state.json"
SOURCE_LABEL = "obsidian"


def ensure_collection():
    resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION}")
    if resp.status_code == 200:
        return
    requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION}",
        json={"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}, "on_disk_payload": True},
    ).raise_for_status()
    for field in ("project", "source", "date", "status", "prioridade", "responsavel", "origem", "tags"):
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
    ).raise_for_status()
    print(f"Collection '{COLLECTION}' created (size={VECTOR_SIZE}).")


DEFAULT_UNIFIED_KEYWORDS = (
    "source", "project", "date", "session_id",
    "repo", "author", "state", "type", "tags",
)
UNIFIED_KEYWORD_FIELDS = DEFAULT_UNIFIED_KEYWORDS + tuple(
    f.strip()
    for f in os.environ.get("UNIFIED_EXTRA_KEYWORDS", "").split(",")
    if f.strip()
)


def ensure_unified_collection():
    if not COLLECTION_UNIFIED:
        return
    resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION_UNIFIED}")
    if resp.status_code == 200:
        return
    requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION_UNIFIED}",
        json={"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}, "on_disk_payload": True},
    ).raise_for_status()
    for field in UNIFIED_KEYWORD_FIELDS:
        requests.put(
            f"{QDRANT_URL}/collections/{COLLECTION_UNIFIED}/index",
            json={"field_name": field, "field_schema": "keyword"},
        )
    requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION_UNIFIED}/index",
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
    print(f"Unified collection '{COLLECTION_UNIFIED}' created (size={VECTOR_SIZE}).")


def upsert_batch(collection: str, points: list):
    if not collection or not points:
        return
    batch_size = 50
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        requests.put(
            f"{QDRANT_URL}/collections/{collection}/points",
            json={"points": batch},
        ).raise_for_status()


def get_embedding(text: str) -> list[float]:
    text = text.strip()
    if not text:
        text = "empty"
    if len(text) > 8000:
        text = text[:8000]
    resp = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBEDDING_MODEL, "input": text},
    )
    if resp.status_code != 200:
        return None
    return resp.json()["embeddings"][0]


def text_to_id(text: str) -> int:
    h = hashlib.md5(text.encode()).hexdigest()
    return int(h[:16], 16)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"indexed_files": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    fm = {}
    current_key = None
    current_list = None

    for line in parts[1].strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("  - ") or line.startswith("    - "):
            if current_key and current_list is not None:
                current_list.append(stripped.lstrip("- ").strip())
        elif ":" in stripped:
            if current_key and current_list is not None:
                fm[current_key] = current_list
            k, v = stripped.split(":", 1)
            current_key = k.strip()
            v = v.strip()
            if v:
                fm[current_key] = v
                current_list = None
            else:
                current_list = []

    if current_key and current_list is not None:
        fm[current_key] = current_list

    return fm, parts[2].strip()


def extract_inline_tags(text: str) -> list[str]:
    import re
    return list(set(re.findall(r'(?:^|\s)#([a-zA-Z\u00C0-\u024F][\w\-/]*)', text)))


def extract_links(text: str) -> list[str]:
    import re
    return list(set(re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', text)))


def parse_note(path: Path, vault_root: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return []

    if not text or len(text) < 20:
        return []

    rel_path = str(path.relative_to(vault_root))
    folder = str(path.parent.relative_to(vault_root))
    title = path.stem
    file_date = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")

    fm, body = parse_frontmatter(text)

    if not body or len(body) < 20:
        return []

    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip().strip("[]") for t in tags.split(",") if t.strip()]
    inline_tags = extract_inline_tags(body)
    all_tags = list(set(tags + inline_tags))

    links = extract_links(body)

    status = fm.get("status", "")
    prioridade = fm.get("prioridade", "")
    responsavel = fm.get("responsavel", "")
    origem = fm.get("origem", "")
    contexto = fm.get("contexto", "")
    date = fm.get("data", file_date)
    notion_url = fm.get("notion_url", "")

    header_parts = [f"Note: {title}", f"Folder: {folder}"]
    if all_tags:
        header_parts.append(f"Tags: {', '.join(all_tags)}")
    meta_parts = []
    if status:
        meta_parts.append(f"Status: {status}")
    if prioridade:
        meta_parts.append(f"Prioridade: {prioridade}")
    if responsavel:
        meta_parts.append(f"Responsável: {responsavel}")
    if origem:
        meta_parts.append(f"Origem: {origem}")
    if meta_parts:
        header_parts.append(" | ".join(meta_parts))
    if contexto:
        header_parts.append(f"Contexto: {contexto}")
    if links:
        header_parts.append(f"Links: {', '.join(links)}")

    header = "\n".join(header_parts)
    full_text = f"{header}\n\n{body}"

    chunks = []
    if len(full_text) <= CHUNK_MAX_CHARS:
        chunks.append(full_text)
    else:
        pos = 0
        while pos < len(full_text):
            end = min(pos + CHUNK_MAX_CHARS, len(full_text))
            chunk = full_text[pos:end]
            if pos > 0:
                chunk = f"{header}\n\n{chunk}"
            chunks.append(chunk)
            pos = end

    return [
        {
            "text": chunk,
            "session_id": f"obsidian-{hashlib.md5(rel_path.encode()).hexdigest()[:8]}",
            "project": folder.split("/")[0] if "/" in folder else "obsidian",
            "source": SOURCE_LABEL,
            "file": rel_path,
            "date": date,
            "tags": all_tags,
            "status": status,
            "prioridade": prioridade,
            "responsavel": responsavel,
            "origem": origem,
            "notion_url": notion_url,
            "links": links,
        }
        for chunk in chunks
    ]


def index_chunks(chunks: list[dict]) -> int:
    if not chunks:
        return 0

    points = []
    for chunk in chunks:
        embedding = get_embedding(chunk["text"])
        if embedding is None:
            continue
        point_id = text_to_id(chunk["text"])
        payload = {
            "text": chunk["text"],
            "session_id": chunk["session_id"],
            "project": chunk["project"],
            "source": chunk["source"],
            "file": chunk["file"],
            "date": chunk["date"],
            "indexed_at": datetime.datetime.now().isoformat(),
        }
        for field in ("tags", "status", "prioridade", "responsavel", "origem", "notion_url", "links"):
            if chunk.get(field):
                payload[field] = chunk[field]
        points.append({"id": point_id, "vector": embedding, "payload": payload})

    if not points:
        return 0

    upsert_batch(COLLECTION, points)
    upsert_batch(COLLECTION_UNIFIED, points)

    return len(points)


def find_notes(vault_path: Path) -> list[Path]:
    return [
        f for f in vault_path.rglob("*.md")
        if ".obsidian" not in f.parts and ".trash" not in f.parts
    ]


def parse_arg(flag: str) -> str | None:
    try:
        idx = sys.argv.index(flag)
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        return None


def main():
    global QDRANT_URL, STATE_FILE, COLLECTION

    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    if v := parse_arg("--qdrant-url"):
        QDRANT_URL = v
    if v := parse_arg("--state-file"):
        STATE_FILE = Path(v)
    if v := parse_arg("--collection"):
        COLLECTION = v

    default_vault = os.environ.get("OBSIDIAN_DIR") or str(Path.home() / "obsidian")
    vault_path = Path(parse_arg("--vault") or default_vault)
    if not vault_path.exists():
        print(f"Vault not found: {vault_path}")
        print("Set OBSIDIAN_DIR in .env or pass --vault <path>")
        sys.exit(1)

    print(f"Obsidian → Qdrant ETL")
    print(f"  Vault: {vault_path}")
    print(f"  Qdrant: {QDRANT_URL}")
    print(f"  Collection: {COLLECTION}")
    print(f"  Model: {EMBEDDING_MODEL} ({VECTOR_SIZE}d)")
    print()

    if not dry_run:
        ensure_collection()
        ensure_unified_collection()

    state = load_state()
    indexed = state["indexed_files"]

    notes = find_notes(vault_path)
    print(f"Found {len(notes)} notes")

    new_notes = []
    for f in notes:
        file_key = str(f)
        file_mtime = f.stat().st_mtime
        if not force and file_key in indexed and indexed[file_key] >= file_mtime:
            continue
        new_notes.append(f)

    print(f"New/modified: {len(new_notes)} notes")

    if dry_run:
        for f in new_notes[:20]:
            chunks = parse_note(f, vault_path)
            print(f"  {f.relative_to(vault_path)}: {len(chunks)} chunks")
        if len(new_notes) > 20:
            print(f"  ... +{len(new_notes) - 20} more")
        return

    total = 0
    for i, f in enumerate(new_notes):
        chunks = parse_note(f, vault_path)
        n = index_chunks(chunks)
        total += n
        indexed[str(f)] = f.stat().st_mtime

        if (i + 1) % 10 == 0 or i == len(new_notes) - 1:
            save_state(state)
            print(f"  [{i+1}/{len(new_notes)}] {f.stem}: {n} chunks")

    save_state(state)
    print(f"\nDone! Indexed {total} chunks from {len(new_notes)} notes.")


if __name__ == "__main__":
    main()
