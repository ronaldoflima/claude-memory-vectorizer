#!/usr/bin/env python3
"""Popula agent_sessions_bgem3 com amostra de agent_sessions re-embedada via bge-m3.

Uso: python3 scripts/bgem3_sample.py [--limit N] [--batch B]
"""
import sys
import time
import requests

QDRANT = "http://localhost:6333"
OLLAMA = "http://localhost:11434"
SRC = "agent_sessions"
DST = "agent_sessions_bgem3"
MODEL = "bge-m3"


def embed(texts: list[str]) -> list[list[float]]:
    r = requests.post(f"{OLLAMA}/api/embed", json={"model": MODEL, "input": texts}, timeout=120)
    r.raise_for_status()
    return r.json()["embeddings"]


def scroll_all(limit: int):
    offset = None
    got = 0
    while got < limit:
        body = {"limit": min(256, limit - got), "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        r = requests.post(f"{QDRANT}/collections/{SRC}/points/scroll", json=body, timeout=30)
        r.raise_for_status()
        res = r.json()["result"]
        pts = res["points"]
        if not pts:
            return
        for p in pts:
            yield p
            got += 1
            if got >= limit:
                return
        offset = res.get("next_page_offset")
        if offset is None:
            return


def main():
    limit = 500
    batch = 16
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--limit":
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--batch":
            batch = int(args[i + 1]); i += 2
        else:
            i += 1

    print(f"[bgem3] sampling up to {limit} points, batch={batch}")
    buf_ids, buf_texts, buf_payloads = [], [], []
    total = 0
    t0 = time.time()

    def flush():
        nonlocal total
        if not buf_texts:
            return
        prefixed = buf_texts  # bge-m3 não exige prefixo
        vecs = embed(prefixed)
        points = [
            {"id": pid, "vector": v, "payload": pl}
            for pid, v, pl in zip(buf_ids, vecs, buf_payloads)
        ]
        r = requests.put(
            f"{QDRANT}/collections/{DST}/points?wait=true",
            json={"points": points}, timeout=60,
        )
        r.raise_for_status()
        total += len(points)
        elapsed = time.time() - t0
        print(f"  upserted={total} rate={total/elapsed:.1f}/s")
        buf_ids.clear(); buf_texts.clear(); buf_payloads.clear()

    for p in scroll_all(limit):
        text = p["payload"].get("text", "")
        if not text:
            continue
        buf_ids.append(p["id"])
        buf_texts.append(text)
        buf_payloads.append(p["payload"])
        if len(buf_texts) >= batch:
            flush()
    flush()

    print(f"[bgem3] done: {total} points in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
