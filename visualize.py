#!/usr/bin/env python3
"""Cross-source retrieval visualizer.

Embeds a query once and runs a per-source top-K semantic search against the
unified `agent_memory` collection, then renders the hits side-by-side (one
column per origin: GitHub / Claude / Teams / Obsidian) into a standalone HTML
file. Lets you *see* whether the unified collection approximates related
content across sources for a given topic, without the global ranking burying
the shorter-text sources (Teams chat messages, notes).

Usage:
    python visualize.py "<query>" [--collection agent_memory]
                                  [--per-source 8] [--out PATH] [--open]
"""

import html
import os
import sys
import webbrowser
from pathlib import Path

import requests

# Reuse the canonical config + helpers from search.py
from search import (  # noqa: E402
    QDRANT_URL,
    derive_label,
    derive_ref,
    get_embedding,
)

# Buckets are defined by the `source` payload field, which is reliable in the
# unified collection (facet: local / vps-mcpgw / github / teams / obsidian).
# `local` + `vps-mcpgw` are both Claude conversations, so they share a bucket.
BUCKETS = [
    {"key": "github", "label": "GitHub", "sources": ["github"], "color": "#6e5494"},
    {"key": "claude", "label": "Claude", "sources": ["local", "vps-mcpgw"], "color": "#d97757"},
    {"key": "teams", "label": "Teams", "sources": ["teams"], "color": "#4b53bc"},
    {"key": "obsidian", "label": "Obsidian", "sources": ["obsidian"], "color": "#7c3aed"},
]


def search_bucket(embedding, collection, sources, limit):
    body = {
        "vector": embedding,
        "limit": limit,
        "with_payload": True,
        "filter": {"must": [{"key": "source", "match": {"any": sources}}]},
    }
    resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/search", json=body)
    resp.raise_for_status()
    return resp.json()["result"]


def score_bar(score: float) -> str:
    pct = max(0, min(100, round(score * 100)))
    return f'<div class="bar"><div class="fill" style="width:{pct}%"></div></div>'


def render_card(hit: dict) -> str:
    p = hit["payload"]
    score = hit["score"]
    text = p.get("text", "")
    snippet = html.escape(text[:700] + ("…" if len(text) > 700 else ""))
    label = html.escape(str(derive_label(p)))
    ref = html.escape(str(derive_ref(p)))
    date = html.escape(str(p.get("date", "?")))
    host = html.escape(str(p.get("source", "?")))
    return f"""
      <div class="card">
        <div class="card-head">
          <span class="score">{score:.3f}</span>
          {score_bar(score)}
        </div>
        <div class="meta">{label} · <span class="ref">{ref}</span> · {date} · <span class="host">{host}</span></div>
        <pre class="snippet">{snippet}</pre>
      </div>"""


def render_html(query: str, collection: str, columns: list[dict]) -> str:
    cols_html = ""
    for col in columns:
        bucket = col["bucket"]
        hits = col["hits"]
        best = f"{hits[0]['score']:.3f}" if hits else "—"
        cards = "\n".join(render_card(h) for h in hits) or '<div class="empty">sem resultados</div>'
        cols_html += f"""
        <section class="col">
          <header style="border-color:{bucket['color']}">
            <h2><span class="dot" style="background:{bucket['color']}"></span>{bucket['label']}</h2>
            <span class="count">{len(hits)} hits · melhor {best}</span>
          </header>
          {cards}
        </section>"""

    q = html.escape(query)
    return f"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cross-source: {q}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:#0d1117; color:#e6edf3; }}
  .top {{ padding:20px 24px; border-bottom:1px solid #21262d; position:sticky; top:0;
          background:#0d1117ee; backdrop-filter:blur(8px); z-index:10; }}
  .top h1 {{ margin:0 0 4px; font-size:15px; font-weight:500; color:#8b949e; }}
  .top .q {{ font-size:20px; font-weight:600; color:#e6edf3; }}
  .top .sub {{ margin-top:6px; font-size:12px; color:#6e7681; }}
  .grid {{ display:grid; grid-template-columns:repeat({len(columns)},1fr); gap:16px; padding:20px 24px; align-items:start; }}
  .col header {{ display:flex; justify-content:space-between; align-items:center;
                 padding:8px 10px; border-bottom:2px solid; margin-bottom:12px; }}
  .col h2 {{ margin:0; font-size:14px; font-weight:600; display:flex; align-items:center; gap:8px; }}
  .dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
  .count {{ font-size:11px; color:#6e7681; }}
  .card {{ background:#161b22; border:1px solid #21262d; border-radius:8px;
           padding:10px 12px; margin-bottom:10px; }}
  .card-head {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; }}
  .score {{ font-variant-numeric:tabular-nums; font-weight:700; font-size:13px; color:#58a6ff; }}
  .bar {{ flex:1; height:5px; background:#21262d; border-radius:3px; overflow:hidden; }}
  .fill {{ height:100%; background:linear-gradient(90deg,#1f6feb,#58a6ff); }}
  .meta {{ font-size:11px; color:#8b949e; margin-bottom:6px; word-break:break-word; }}
  .ref {{ color:#58a6ff; }} .host {{ color:#6e7681; }}
  .snippet {{ margin:0; white-space:pre-wrap; word-break:break-word; font-size:12px;
              color:#c9d1d9; max-height:220px; overflow:auto; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .empty {{ color:#6e7681; font-style:italic; padding:8px; }}
</style></head>
<body>
  <div class="top">
    <h1>Aproximação cross-source · coleção <code>{html.escape(collection)}</code></h1>
    <div class="q">{q}</div>
    <div class="sub">top-K por origem · embedding bge-m3 · scores = similaridade cosseno</div>
  </div>
  <div class="grid">{cols_html}
  </div>
</body></html>"""


def main(argv):
    query_parts, collection, per_source, out, do_open = [], "agent_memory", 8, None, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--collection" and i + 1 < len(argv):
            collection = argv[i + 1]; i += 2
        elif a == "--per-source" and i + 1 < len(argv):
            per_source = int(argv[i + 1]); i += 2
        elif a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]; i += 2
        elif a == "--open":
            do_open = True; i += 1
        else:
            query_parts.append(a); i += 1

    query = " ".join(query_parts).strip()
    if not query:
        print('Usage: python visualize.py "<query>" [--collection agent_memory] '
              "[--per-source 8] [--out PATH] [--open]")
        return 1

    print(f"Embedding query and searching {len(BUCKETS)} sources in '{collection}'…")
    embedding = get_embedding(query)
    columns = []
    for bucket in BUCKETS:
        hits = search_bucket(embedding, collection, bucket["sources"], per_source)
        best = f"{hits[0]['score']:.3f}" if hits else "—"
        print(f"  {bucket['label']:<10} {len(hits)} hits · melhor {best}")
        columns.append({"bucket": bucket, "hits": hits})

    if out is None:
        slug = "".join(c if c.isalnum() else "-" for c in query.lower())[:40].strip("-")
        out = f"/tmp/xsource-{slug}.html"
    Path(out).write_text(render_html(query, collection, columns), encoding="utf-8")
    print(f"\nHTML escrito em: {out}")
    if do_open:
        webbrowser.open(f"file://{Path(out).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
