#!/usr/bin/env python3
"""
ETL: GitHub PRs → Qdrant vector store
Extracts PR descriptions, comments and reviews, generates embeddings via Ollama, indexes in Qdrant.
"""

import json
import hashlib
import datetime
import subprocess
import sys
from pathlib import Path

import requests

QDRANT_URL = "http://localhost:6333"
OLLAMA_URL = "http://localhost:11434"
COLLECTION = "agent_work_artifacts"
EMBEDDING_MODEL = "nomic-embed-text"
CHUNK_MAX_CHARS = 2000

STATE_FILE = Path(__file__).parent / ".etl_prs_state.json"
DEFAULT_REPOS = ["px-center/px-torre-core"]


def get_embedding(text: str) -> list[float]:
    text = text.strip()
    if not text:
        text = "empty"
    if len(text) > 2500:
        text = text[:2500]
    resp = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBEDDING_MODEL, "input": text},
    )
    if resp.status_code != 200:
        print(f"    Embedding error ({resp.status_code}), skipping (len={len(text)})")
        return None
    return resp.json()["embeddings"][0]


def ensure_collection():
    resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION}")
    if resp.status_code == 200:
        return
    requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION}",
        json={"vectors": {"size": 768, "distance": "Cosine"}},
    ).raise_for_status()
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
    print(f"Collection '{COLLECTION}' created with fulltext index.")


def text_to_id(text: str) -> int:
    h = hashlib.md5(text.encode()).hexdigest()
    return int(h[:16], 16)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"indexed_prs": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_org_repos(org: str) -> list[str]:
    cmd = ["gh", "repo", "list", org, "--limit", "500", "--no-archived", "--json", "nameWithOwner", "-q", ".[].nameWithOwner"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Error listing repos for {org}: {result.stderr.strip()}")
        return []
    repos = [r.strip() for r in result.stdout.strip().splitlines() if r.strip()]
    return repos


def fetch_prs(repo: str, since: str = None, limit: int = 100) -> list[dict]:
    fields = "number,title,body,state,createdAt,mergedAt,updatedAt,author,headRefName,labels,comments,reviews"
    cmd = [
        "gh", "pr", "list",
        "--repo", repo,
        "--state", "all",
        "--limit", str(limit),
        "--json", fields,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Error fetching PRs from {repo}: {result.stderr.strip()}")
        return []

    prs = json.loads(result.stdout)

    if since:
        since_dt = datetime.datetime.fromisoformat(since)
        prs = [pr for pr in prs if datetime.datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00")).replace(tzinfo=None) >= since_dt]

    return prs


def pr_to_chunks(pr: dict, repo: str) -> list[dict]:
    number = pr["number"]
    title = pr["title"]
    body = (pr.get("body") or "").strip()
    author = pr.get("author", {}).get("login", "unknown")
    branch = pr.get("headRefName", "")
    labels = [l["name"] for l in pr.get("labels", [])]
    state = pr.get("state", "")
    created = pr.get("createdAt", "")[:10]
    merged = (pr.get("mergedAt") or "")[:10]
    date = merged or created

    header = f"PR #{number}: {title}"
    if labels:
        header += f" [{', '.join(labels)}]"
    header += f"\nAuthor: {author} | Branch: {branch} | State: {state}"
    if merged:
        header += f" | Merged: {merged}"

    parts = [header]
    if body:
        parts.append(f"Description:\n{body}")

    comments = pr.get("comments", [])
    if comments:
        comment_texts = []
        for c in comments:
            c_author = c.get("author", {}).get("login", "?")
            c_body = c.get("body", "").strip()
            if c_body:
                comment_texts.append(f"{c_author}: {c_body}")
        if comment_texts:
            parts.append("Comments:\n" + "\n\n".join(comment_texts))

    reviews = pr.get("reviews", [])
    if reviews:
        review_texts = []
        for r in reviews:
            r_author = r.get("author", {}).get("login", "?")
            r_body = (r.get("body") or "").strip()
            r_state = r.get("state", "")
            if r_body:
                review_texts.append(f"{r_author} [{r_state}]: {r_body}")
        if review_texts:
            parts.append("Reviews:\n" + "\n\n".join(review_texts))

    full_text = "\n\n".join(parts)

    chunks = []
    if len(full_text) <= CHUNK_MAX_CHARS:
        chunks.append(full_text)
    else:
        current = ""
        for part in parts:
            if current and len(current) + len(part) > CHUNK_MAX_CHARS:
                chunks.append(current.strip())
                current = header + "\n\n"
            current += part + "\n\n"
        if current.strip():
            chunks.append(current.strip())

    return [
        {
            "text": chunk,
            "type": "pr",
            "repo": repo,
            "pr_number": number,
            "title": title,
            "author": author,
            "branch": branch,
            "state": state,
            "date": date,
            "labels": labels,
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
        points.append({
            "id": point_id,
            "vector": embedding,
            "payload": {
                "text": chunk["text"],
                "type": chunk["type"],
                "repo": chunk["repo"],
                "pr_number": chunk["pr_number"],
                "title": chunk["title"],
                "author": chunk["author"],
                "branch": chunk["branch"],
                "state": chunk["state"],
                "date": chunk["date"],
                "labels": chunk["labels"],
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


def parse_arg(flag: str) -> str | None:
    try:
        idx = sys.argv.index(flag)
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        return None


def main():
    global QDRANT_URL, STATE_FILE

    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    if v := parse_arg("--qdrant-url"):
        QDRANT_URL = v
    if v := parse_arg("--state-file"):
        STATE_FILE = Path(v)

    since = parse_arg("--since")
    limit = int(parse_arg("--limit") or "100")

    org = parse_arg("--org")
    repos_arg = parse_arg("--repos")
    if org:
        repos = fetch_org_repos(org)
        if not repos:
            print(f"No repos found for org '{org}'")
            return
    elif repos_arg:
        repos = repos_arg.split(",")
    else:
        repos = DEFAULT_REPOS

    print("GitHub PRs → Qdrant ETL")
    print(f"  Qdrant: {QDRANT_URL}")
    print(f"  Repos: {', '.join(repos)}")
    if since:
        print(f"  Since: {since}")
    print()

    if not dry_run:
        ensure_collection()

    state = load_state()
    indexed = state["indexed_prs"]

    total_chunks = 0
    for repo in repos:
        print(f"Fetching PRs from {repo}...")
        prs = fetch_prs(repo, since=since, limit=limit)
        print(f"  Found {len(prs)} PRs")

        new_prs = []
        for pr in prs:
            pr_key = f"{repo}#{pr['number']}"
            updated = pr.get("updatedAt") or pr.get("mergedAt") or pr.get("createdAt") or ""
            if not force and pr_key in indexed and indexed[pr_key] >= updated:
                continue
            new_prs.append((pr, pr_key, updated))

        print(f"  New/modified: {len(new_prs)} PRs")

        if dry_run:
            for pr, pr_key, _ in new_prs[:10]:
                chunks = pr_to_chunks(pr, repo)
                print(f"    #{pr['number']}: {pr['title'][:60]} → {len(chunks)} chunks")
            if len(new_prs) > 10:
                print(f"    ... +{len(new_prs) - 10} more")
            continue

        for i, (pr, pr_key, updated) in enumerate(new_prs):
            chunks = pr_to_chunks(pr, repo)
            n = index_chunks(chunks)
            total_chunks += n
            indexed[pr_key] = updated

            if (i + 1) % 20 == 0 or i == len(new_prs) - 1:
                save_state(state)
                print(f"    [{i+1}/{len(new_prs)}] #{pr['number']}: {n} chunks")

    save_state(state)
    print(f"\nDone! Indexed {total_chunks} chunks.")


if __name__ == "__main__":
    main()
