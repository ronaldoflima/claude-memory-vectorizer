#!/usr/bin/env python3
"""
ETL: Microsoft Teams (teams-crawler data/) → Qdrant vector store
Indexa janelas conversa+tempo das mensagens capturadas pelo teams-crawler.
"""

import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Permite importar a lib teams_extract sem instalar (PYTHONPATH ou TEAMS_CRAWLER_DIR)
_TEAMS_DIR = os.environ.get("TEAMS_CRAWLER_DIR")
if _TEAMS_DIR and _TEAMS_DIR not in sys.path:
    sys.path.insert(0, _TEAMS_DIR)

from teams_extract import iter_documents  # noqa: E402

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
COLLECTION = os.environ.get("COLLECTION_TEAMS", "agent_teams")
COLLECTION_UNIFIED = os.environ.get("COLLECTION_UNIFIED", "agent_memory")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
VECTOR_SIZE = int(os.environ.get("VECTOR_SIZE", "1024"))
CHUNK_MAX_CHARS = int(os.environ.get("CHUNK_MAX_CHARS_TEAMS", "2000"))
TIME_GAP_MIN = int(os.environ.get("TEAMS_TIME_GAP_MIN", "30"))

STATE_FILE = Path(__file__).parent / ".etl_teams_state.json"
SOURCE_LABEL = "teams"

KEYWORD_FIELDS = ("source", "type", "project", "session_id", "date", "author", "tags")


def ensure_collection():
    resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION}")
    if resp.status_code == 200:
        return
    requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION}",
        json={"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}, "on_disk_payload": True},
    ).raise_for_status()
    for field in KEYWORD_FIELDS:
        requests.put(
            f"{QDRANT_URL}/collections/{COLLECTION}/index",
            json={"field_name": field, "field_schema": "keyword"},
        )
    requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION}/index",
        json={"field_name": "text", "field_schema": {
            "type": "text", "tokenizer": "multilingual",
            "min_token_len": 2, "max_token_len": 40, "lowercase": True}},
    ).raise_for_status()
    print(f"Collection '{COLLECTION}' created (size={VECTOR_SIZE}).")


DEFAULT_UNIFIED_KEYWORDS = (
    "source", "project", "date", "session_id", "repo", "author", "state", "type", "tags",
)
UNIFIED_KEYWORD_FIELDS = DEFAULT_UNIFIED_KEYWORDS + tuple(
    f.strip() for f in os.environ.get("UNIFIED_EXTRA_KEYWORDS", "").split(",") if f.strip()
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
        json={"field_name": "text", "field_schema": {
            "type": "text", "tokenizer": "multilingual",
            "min_token_len": 2, "max_token_len": 40, "lowercase": True}},
    )
    print(f"Unified collection '{COLLECTION_UNIFIED}' created (size={VECTOR_SIZE}).")


def get_embedding(text: str):
    text = (text or "").strip() or "empty"
    if len(text) > 8000:
        text = text[:8000]
    resp = requests.post(f"{OLLAMA_URL}/api/embed",
                         json={"model": EMBEDDING_MODEL, "input": text})
    if resp.status_code != 200:
        return None
    return resp.json()["embeddings"][0]


def text_to_id(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:16], 16)


def upsert_batch(collection: str, points: list):
    if not collection or not points:
        return
    for i in range(0, len(points), 50):
        requests.put(f"{QDRANT_URL}/collections/{collection}/points",
                     json={"points": points[i:i + 50]}).raise_for_status()


def existing_ids(collection: str, ids: list[int]) -> set[int]:
    """IDs já presentes na coleção (para pular re-embedding)."""
    if not ids:
        return set()
    resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points",
                         json={"ids": ids, "with_payload": False, "with_vector": False})
    if resp.status_code != 200:
        return set()
    return {p["id"] for p in resp.json().get("result", [])}


def doc_to_payload(doc: dict) -> dict:
    date = doc["end"].date().isoformat()
    return {
        "text": doc["text"],
        "source": SOURCE_LABEL,
        "type": doc["source_type"],
        "project": doc["label"],
        "session_id": doc["conversation_id"],
        "date": date,
        "author": "",
        "tags": doc["participants"],
        "conversation_id": doc["conversation_id"],
        "team_id": doc["team_id"] or "",
        "msg_ids": doc["msg_ids"],
        "msg_count": len(doc["msg_ids"]),
        "start": doc["start"].isoformat(),
        "end": doc["end"].isoformat(),
        "has_attachments": doc["has_attachments"],
        "indexed_at": datetime.datetime.now().isoformat(),
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"indexed_files": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def index_file_docs(docs: list[dict]) -> int:
    """Embeda+upserta os docs de UM arquivo, pulando point_ids já existentes."""
    if not docs:
        return 0
    payloads = [doc_to_payload(d) for d in docs]
    ids = [text_to_id(p["text"]) for p in payloads]
    skip = existing_ids(COLLECTION, ids)

    points = []
    for pid, payload in zip(ids, payloads):
        if pid in skip:
            continue
        emb = get_embedding(payload["text"])
        if emb is None:
            continue
        points.append({"id": pid, "vector": emb, "payload": payload})

    upsert_batch(COLLECTION, points)
    upsert_batch(COLLECTION_UNIFIED, points)
    return len(points)


def parse_arg(flag: str):
    try:
        return sys.argv[sys.argv.index(flag) + 1]
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

    default_data = os.environ.get("TEAMS_DATA_DIR") or str(
        Path(_TEAMS_DIR or ".") / "data"
    )
    data_dir = Path(parse_arg("--data-dir") or default_data)
    if not data_dir.exists():
        print(f"data/ não encontrado: {data_dir}")
        print("Passe --data-dir <path> ou defina TEAMS_DATA_DIR no .env")
        sys.exit(1)

    print("Teams → Qdrant ETL")
    print(f"  Data: {data_dir}")
    print(f"  Qdrant: {QDRANT_URL}")
    print(f"  Collection: {COLLECTION}  Model: {EMBEDDING_MODEL} ({VECTOR_SIZE}d)")
    print()

    if not dry_run:
        ensure_collection()
        ensure_unified_collection()

    state = load_state()
    indexed = state["indexed_files"]

    from teams_extract.reader import conversation_files
    files = conversation_files(data_dir)

    # a unidade é o ARQUIVO (path), nunca o conversation_id — o mesmo conv_id
    # pode existir em chats/ e channels/ e não deve ser fundido nem sobrescrever
    # o tracking de mtime um do outro
    pending = [
        cf for cf in files
        if force or indexed.get(str(cf.path), 0) < cf.path.stat().st_mtime
    ]
    pending_paths = {str(cf.path) for cf in pending}
    print(f"Arquivos: {len(files)} | novos/modificados: {len(pending)}")

    # UMA passada por todos os documentos, bucketizada por arquivo
    docs_by_file: dict[str, list[dict]] = {}
    for doc in iter_documents(data_dir, max_chars=CHUNK_MAX_CHARS,
                              time_gap_minutes=TIME_GAP_MIN):
        if doc["file"] in pending_paths:
            docs_by_file.setdefault(doc["file"], []).append(doc)

    if dry_run:
        for cf in pending[:20]:
            print(f"  {cf.path.name}: {len(docs_by_file.get(str(cf.path), []))} janelas")
        if len(pending) > 20:
            print(f"  ... +{len(pending) - 20} arquivos")
        return

    total = 0
    for i, cf in enumerate(pending):
        n = index_file_docs(docs_by_file.get(str(cf.path), []))
        total += n
        indexed[str(cf.path)] = cf.path.stat().st_mtime
        if (i + 1) % 5 == 0 or i == len(pending) - 1:
            save_state(state)
            print(f"  [{i+1}/{len(pending)}] {cf.path.name[:32]}…: {n} pts")

    save_state(state)
    print(f"\nDone! Indexados {total} pontos de {len(pending)} arquivos.")


if __name__ == "__main__":
    main()
