#!/usr/bin/env python3
"""Quick search tool for testing the vectorized conversations."""

import os
import sys
from pathlib import Path

import requests

# Load .env from repo root if present
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
COLLECTION = os.environ.get("COLLECTION", "agent_sessions")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
SEMANTIC_THRESHOLD = float(os.environ.get("SEMANTIC_THRESHOLD", "0.45"))


def list_collections() -> list[dict]:
    resp = requests.get(f"{QDRANT_URL}/collections")
    resp.raise_for_status()
    return resp.json().get("result", {}).get("collections", [])


def collection_info(name: str) -> dict | None:
    resp = requests.get(f"{QDRANT_URL}/collections/{name}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json().get("result", {})


def print_collections(default: str) -> None:
    collections = list_collections()
    if not collections:
        print(f"No collections found at {QDRANT_URL}")
        return
    print(f"Available collections at {QDRANT_URL}:")
    for c in collections:
        name = c.get("name", "?")
        info = collection_info(name) or {}
        count = info.get("points_count", "?")
        marker = " (default)" if name == default else ""
        print(f"  - {name}{marker}  [{count} points]")


def get_embedding(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBEDDING_MODEL, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def fulltext_search(query: str, collection: str, limit: int = 5, project: str = None, date: str = None) -> list[dict]:
    filters = [{"key": "text", "match": {"text": query}}]
    if project:
        filters.append({"key": "project", "match": {"value": project}})
    if date:
        filters.append({"key": "date", "match": {"value": date}})

    resp = requests.post(
        f"{QDRANT_URL}/collections/{collection}/points/scroll",
        json={
            "filter": {"must": filters},
            "limit": limit,
            "with_payload": True,
        },
    )
    resp.raise_for_status()
    points = resp.json().get("result", {}).get("points", [])
    return [
        {"id": p["id"], "score": 0.0, "payload": p["payload"], "match": "text", "rank": idx}
        for idx, p in enumerate(points)
    ]


def semantic_search(query: str, collection: str, limit: int = 5, project: str = None, date: str = None) -> list[dict]:
    embedding = get_embedding(query)

    body = {
        "vector": embedding,
        "limit": limit,
        "with_payload": True,
    }

    filters = []
    if project:
        filters.append({"key": "project", "match": {"value": project}})
    if date:
        filters.append({"key": "date", "match": {"value": date}})
    if filters:
        body["filter"] = {"must": filters}

    resp = requests.post(
        f"{QDRANT_URL}/collections/{collection}/points/search",
        json=body,
    )
    resp.raise_for_status()
    results = resp.json()["result"]
    return [{"id": r["id"], "score": r["score"], "payload": r["payload"], "match": "semantic"} for r in results]


def search(query: str, collection: str, limit: int = 5, project: str = None, date: str = None):
    results = semantic_search(query, collection, limit=limit, project=project, date=date)

    best_score = results[0]["score"] if results else 0
    if best_score < SEMANTIC_THRESHOLD:
        text_results = fulltext_search(query, collection, limit=limit, project=project, date=date)
        if text_results:
            seen_ids = {r["id"] for r in results}
            results.sort(key=lambda r: r["score"], reverse=True)
            for tr in text_results:
                if tr["id"] not in seen_ids:
                    results.append(tr)
            results = results[:limit]

    for i, r in enumerate(results):
        score = r["score"]
        payload = r["payload"]
        date_info = payload.get("date", "?")
        match_type = r["match"]
        label = f"Score: {score:.4f}" if match_type == "semantic" else f"FULLTEXT#{r.get('rank', 0)+1}"
        print(f"\n{'='*60}")
        print(f"[{i+1}] {label} | {date_info} | {payload.get('project', '?')} | {payload.get('session_id', '?')[:8]}")
        print(f"{'='*60}")
        text = payload["text"]
        if len(text) > 2000:
            text = text[:2000] + "..."
        print(text)

    return results


def usage() -> None:
    print(
        "Usage: python search.py <query> [--project <name>] [--date <YYYY-MM-DD>] "
        "[--limit <n>] [--collection <name>]\n"
        "       python search.py --list-collections"
    )


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        usage()
        sys.exit(1)

    if "--list-collections" in args or "--list" in args:
        print_collections(default=COLLECTION)
        sys.exit(0)

    project = None
    date = None
    limit = 5
    collection = COLLECTION
    query_parts = []

    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]
            i += 2
        elif args[i] == "--date" and i + 1 < len(args):
            date = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--collection" and i + 1 < len(args):
            collection = args[i + 1]
            i += 2
        elif args[i] in ("-h", "--help"):
            usage()
            sys.exit(0)
        else:
            query_parts.append(args[i])
            i += 1

    query = " ".join(query_parts).strip()
    if not query:
        usage()
        sys.exit(1)

    if collection_info(collection) is None:
        print(f"Collection '{collection}' not found at {QDRANT_URL}.")
        print_collections(default=COLLECTION)
        sys.exit(1)

    info = f"Searching '{query}' in collection '{collection}'"
    if project:
        info += f" (project: {project})"
    if date:
        info += f" (date: {date})"
    print(info)
    search(query, collection, limit=limit, project=project, date=date)
