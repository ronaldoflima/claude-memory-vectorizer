#!/usr/bin/env python3
"""Compara top-k entre nomic-embed-text e bge-m3 para queries PT-BR.

Restringe ambas as buscas aos IDs presentes em agent_sessions_bgem3
para garantir comparação sobre o mesmo universo de documentos.
"""
import sys
import requests

QDRANT = "http://localhost:6333"
OLLAMA = "http://localhost:11434"


def embed(model: str, text: str) -> list[float]:
    if model == "nomic-embed-text":
        text = f"search_query: {text}"
    r = requests.post(f"{OLLAMA}/api/embed", json={"model": model, "input": text}, timeout=60)
    r.raise_for_status()
    return r.json()["embeddings"][0]


def get_sample_ids(limit: int = 500) -> set:
    ids = set()
    offset = None
    while True:
        body = {"limit": 256, "with_payload": False, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        r = requests.post(f"{QDRANT}/collections/agent_sessions_bgem3/points/scroll", json=body)
        r.raise_for_status()
        res = r.json()["result"]
        for p in res["points"]:
            ids.add(p["id"])
        offset = res.get("next_page_offset")
        if offset is None or len(ids) >= limit:
            break
    return ids


def search(collection: str, model: str, query: str, ids: set, k: int = 5):
    vec = embed(model, query)
    body = {"vector": vec, "limit": k * 4, "with_payload": True}
    r = requests.post(f"{QDRANT}/collections/{collection}/points/search", json=body, timeout=30)
    r.raise_for_status()
    hits = [h for h in r.json()["result"] if h["id"] in ids][:k]
    return hits


def fmt(h):
    pl = h["payload"]
    text = pl.get("text", "")[:120].replace("\n", " ")
    return f"  {h['score']:.4f} [{pl.get('project','?')[:30]}] {text}..."


def compare(query: str, ids: set, k: int = 5):
    print(f"\n{'='*80}\nQUERY: {query}\n{'='*80}")
    print(f"\n-- nomic-embed-text (768d) --")
    for h in search("agent_sessions", "nomic-embed-text", query, ids, k):
        print(fmt(h))
    print(f"\n-- bge-m3 (1024d) --")
    for h in search("agent_sessions_bgem3", "bge-m3", query, ids, k):
        print(fmt(h))


if __name__ == "__main__":
    queries = sys.argv[1:] or [
        "como está o recalculo do diversity score",
        "problema de autenticação no superset",
        "pagamento de freight order não sincronizou com netsuite",
        "índice de payload no qdrant",
        "chart do superset está com dataset errado",
    ]
    print("Loading sample IDs...")
    ids = get_sample_ids(500)
    print(f"Comparing over {len(ids)} shared docs")
    for q in queries:
        compare(q, ids, k=3)
