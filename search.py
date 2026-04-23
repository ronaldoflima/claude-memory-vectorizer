#!/usr/bin/env python3
"""Quick search tool for testing the vectorized conversations."""

import sys
import requests

QDRANT_URL = "http://localhost:6333"
OLLAMA_URL = "http://localhost:11434"
COLLECTION = "agent_sessions"
EMBEDDING_MODEL = "nomic-embed-text"
SEMANTIC_THRESHOLD = 0.6


def get_embedding(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBEDDING_MODEL, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def fulltext_search(query: str, limit: int = 5, project: str = None, date: str = None) -> list[dict]:
    filters = [{"key": "text", "match": {"text": query}}]
    if project:
        filters.append({"key": "project", "match": {"value": project}})
    if date:
        filters.append({"key": "date", "match": {"value": date}})

    resp = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
        json={
            "filter": {"must": filters},
            "limit": limit,
            "with_payload": True,
        },
    )
    resp.raise_for_status()
    points = resp.json().get("result", {}).get("points", [])
    return [{"id": p["id"], "score": 1.0, "payload": p["payload"], "match": "text"} for p in points]


def semantic_search(query: str, limit: int = 5, project: str = None, date: str = None) -> list[dict]:
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
        f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
        json=body,
    )
    resp.raise_for_status()
    results = resp.json()["result"]
    return [{"id": r["id"], "score": r["score"], "payload": r["payload"], "match": "semantic"} for r in results]


def search(query: str, limit: int = 5, project: str = None, date: str = None):
    results = semantic_search(query, limit=limit, project=project, date=date)

    best_score = results[0]["score"] if results else 0
    if best_score < SEMANTIC_THRESHOLD:
        text_results = fulltext_search(query, limit=limit, project=project, date=date)
        if text_results:
            seen_ids = {r["id"] for r in results}
            for tr in text_results:
                if tr["id"] not in seen_ids:
                    results.append(tr)
            results.sort(key=lambda r: (r["match"] == "text", r["score"]), reverse=True)
            results = results[:limit]

    for i, r in enumerate(results):
        score = r["score"]
        payload = r["payload"]
        date_info = payload.get("date", "?")
        match_type = r["match"]
        label = f"Score: {score:.4f}" if match_type == "semantic" else "FULLTEXT"
        print(f"\n{'='*60}")
        print(f"[{i+1}] {label} | {date_info} | {payload['project']} | {payload['session_id'][:8]}")
        print(f"{'='*60}")
        text = payload["text"]
        if len(text) > 500:
            text = text[:500] + "..."
        print(text)

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python search.py <query> [--project <name>] [--date <YYYY-MM-DD>] [--limit <n>]")
        sys.exit(1)

    args = sys.argv[1:]
    project = None
    date = None
    limit = 5
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
        else:
            query_parts.append(args[i])
            i += 1

    query = " ".join(query_parts)
    info = f"Searching: '{query}'"
    if project:
        info += f" (project: {project})"
    if date:
        info += f" (date: {date})"
    print(info)
    search(query, limit=limit, project=project, date=date)
