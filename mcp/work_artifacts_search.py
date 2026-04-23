import httpx

from services.base import ServicePlugin, ToolDefinition

QDRANT_URL = "http://localhost:6333"
COLLECTION = "agent_work_artifacts"
SEMANTIC_THRESHOLD = 0.6


class WorkArtifactsSearchPlugin(ServicePlugin):
    name = "work_artifacts_search"
    required_credentials = []
    tools = [
        ToolDefinition(
            name="search",
            description=(
                "Busca semantica e fulltext em artefatos de trabalho indexados (PRs, commits, cards). "
                "Util para encontrar PRs relacionados a uma feature, bug ou ticket. "
                "Retorna trechos relevantes com metadados (repo, autor, data, branch)."
            ),
            params={
                "properties": {
                    "query": {"type": "string", "description": "Texto de busca (semantico + fulltext)"},
                    "repo": {"type": "string", "description": "Filtrar por repositorio ex: px-center/px-torre-core (opcional)"},
                    "author": {"type": "string", "description": "Filtrar por autor do PR (opcional)"},
                    "type": {"type": "string", "description": "Filtrar por tipo: pr, commit, jira (opcional)"},
                    "date": {"type": "string", "description": "Filtrar por data YYYY-MM-DD (opcional)"},
                    "limit": {"type": "integer", "description": "Numero maximo de resultados (default: 5)"},
                },
                "required": ["query"],
            },
            policy="free",
        ),
    ]

    async def execute(self, tool: str, params: dict, credentials: dict) -> dict:
        if tool == "search":
            return await self._search(params)
        raise ValueError(f"Unknown tool: {tool}")

    async def _search(self, params: dict) -> list:
        query = params["query"]
        repo = params.get("repo")
        author = params.get("author")
        artifact_type = params.get("type")
        date = params.get("date")
        limit = params.get("limit", 5)

        results = await self._semantic_search(query, limit, repo, author, artifact_type, date)

        best_score = results[0]["score"] if results else 0
        if best_score < SEMANTIC_THRESHOLD:
            text_results = await self._fulltext_search(query, limit, repo, author, artifact_type, date)
            if text_results:
                seen_ids = {r["id"] for r in results}
                for tr in text_results:
                    if tr["id"] not in seen_ids:
                        results.append(tr)
                results.sort(key=lambda r: (r["match"] == "text", r["score"]), reverse=True)
                results = results[:limit]

        return [
            {
                "text": r["payload"]["text"][:1500],
                "type": r["payload"].get("type", ""),
                "repo": r["payload"].get("repo", ""),
                "pr_number": r["payload"].get("pr_number"),
                "title": r["payload"].get("title", ""),
                "author": r["payload"].get("author", ""),
                "branch": r["payload"].get("branch", ""),
                "date": r["payload"].get("date", ""),
                "score": round(r["score"], 4) if r["match"] == "semantic" else None,
                "match_type": r["match"],
            }
            for r in results
        ]

    async def _semantic_search(self, query, limit, repo=None, author=None, artifact_type=None, date=None):
        embedding = await self._get_embedding(query)
        body = {"vector": embedding, "limit": limit, "with_payload": True}
        filters = self._build_filters(repo, author, artifact_type, date)
        if filters:
            body["filter"] = {"must": filters}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/search", json=body)
            resp.raise_for_status()

        return [{"id": r["id"], "score": r["score"], "payload": r["payload"], "match": "semantic"} for r in resp.json()["result"]]

    async def _fulltext_search(self, query, limit, repo=None, author=None, artifact_type=None, date=None):
        filters = [{"key": "text", "match": {"text": query}}]
        filters.extend(self._build_filters(repo, author, artifact_type, date))

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
                json={"filter": {"must": filters}, "limit": limit, "with_payload": True},
            )
            resp.raise_for_status()

        points = resp.json().get("result", {}).get("points", [])
        return [{"id": p["id"], "score": 1.0, "payload": p["payload"], "match": "text"} for p in points]

    async def _get_embedding(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "http://localhost:11434/api/embed",
                json={"model": "nomic-embed-text", "input": text},
            )
            resp.raise_for_status()
        return resp.json()["embeddings"][0]

    def _build_filters(self, repo=None, author=None, artifact_type=None, date=None):
        filters = []
        if repo:
            filters.append({"key": "repo", "match": {"value": repo}})
        if author:
            filters.append({"key": "author", "match": {"value": author}})
        if artifact_type:
            filters.append({"key": "type", "match": {"value": artifact_type}})
        if date:
            filters.append({"key": "date", "match": {"value": date}})
        return filters
