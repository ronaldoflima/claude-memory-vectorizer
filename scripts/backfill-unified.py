#!/usr/bin/env python3
"""
Backfill the unified Qdrant collection (COLLECTION_UNIFIED) by copying points
from the three legacy collections without re-running embeddings.

Each point keeps its existing vector + payload. A `source` field is injected
when the legacy collection didn't already record one.

Usage:
    python3 scripts/backfill-unified.py [--qdrant-url URL] [--dry-run]
"""

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION_UNIFIED = os.environ.get("COLLECTION_UNIFIED", "agent_memory")
VECTOR_SIZE = int(os.environ.get("VECTOR_SIZE", "1024"))

# (collection_name, default_source_if_missing)
LEGACY_COLLECTIONS = [
    (os.environ.get("COLLECTION", "agent_sessions"), None),
    (os.environ.get("COLLECTION_ARTIFACTS", "agent_work_artifacts"), "github"),
    (os.environ.get("COLLECTION_NOTES", "agent_notes"), "obsidian"),
]

DEFAULT_UNIFIED_KEYWORDS = (
    "source", "project", "date", "session_id",
    "repo", "author", "state", "type", "tags",
)
UNIFIED_KEYWORD_FIELDS = DEFAULT_UNIFIED_KEYWORDS + tuple(
    f.strip()
    for f in os.environ.get("UNIFIED_EXTRA_KEYWORDS", "").split(",")
    if f.strip()
)

SCROLL_BATCH = 256
UPSERT_BATCH = 100


def parse_arg(flag: str, default=None):
    try:
        idx = sys.argv.index(flag)
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        return default


def collection_exists(name: str) -> bool:
    return requests.get(f"{QDRANT_URL}/collections/{name}").status_code == 200


def ensure_unified():
    if collection_exists(COLLECTION_UNIFIED):
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
    print(f"Created unified collection '{COLLECTION_UNIFIED}' (size={VECTOR_SIZE})")


def count_points(collection: str) -> int:
    resp = requests.post(
        f"{QDRANT_URL}/collections/{collection}/points/count",
        json={"exact": True},
    )
    if resp.status_code != 200:
        return 0
    return resp.json()["result"]["count"]


def scroll_all(collection: str):
    offset = None
    while True:
        body = {"limit": SCROLL_BATCH, "with_payload": True, "with_vector": True}
        if offset is not None:
            body["offset"] = offset
        resp = requests.post(
            f"{QDRANT_URL}/collections/{collection}/points/scroll",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()["result"]
        points = data.get("points", [])
        if not points:
            return
        yield points
        offset = data.get("next_page_offset")
        if offset is None:
            return


def upsert(points: list):
    for i in range(0, len(points), UPSERT_BATCH):
        batch = points[i : i + UPSERT_BATCH]
        requests.put(
            f"{QDRANT_URL}/collections/{COLLECTION_UNIFIED}/points",
            json={"points": batch},
        ).raise_for_status()


def backfill(collection: str, default_source: str | None, dry_run: bool) -> int:
    if not collection_exists(collection):
        print(f"  [{collection}] skipped (does not exist)")
        return 0

    total = count_points(collection)
    print(f"  [{collection}] {total} points to copy" + (f" — injecting source='{default_source}' when missing" if default_source else ""))

    if dry_run or total == 0:
        return total

    copied = 0
    for batch in scroll_all(collection):
        out = []
        for pt in batch:
            payload = pt.get("payload") or {}
            if default_source and not payload.get("source"):
                payload = {**payload, "source": default_source}
            out.append({
                "id": pt["id"],
                "vector": pt["vector"],
                "payload": payload,
            })
        upsert(out)
        copied += len(out)
        print(f"    {copied}/{total}")
    return copied


def main():
    global QDRANT_URL
    if v := parse_arg("--qdrant-url"):
        QDRANT_URL = v
    dry_run = "--dry-run" in sys.argv

    print(f"Backfill → {COLLECTION_UNIFIED}")
    print(f"  Qdrant: {QDRANT_URL}")
    print(f"  Dry-run: {dry_run}")
    print()

    if not dry_run:
        ensure_unified()

    grand_total = 0
    for collection, default_source in LEGACY_COLLECTIONS:
        n = backfill(collection, default_source, dry_run)
        grand_total += n

    print()
    print(f"Done. {'Would copy' if dry_run else 'Copied'} {grand_total} points into '{COLLECTION_UNIFIED}'.")


if __name__ == "__main__":
    main()
