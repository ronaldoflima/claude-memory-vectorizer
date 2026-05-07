import httpx

from services.base import ServicePlugin, ToolDefinition

QDRANT_URL = "http://localhost:6333"
COLLECTION = "agent_sessions"
SEMANTIC_THRESHOLD = 0.6


class ConversationHistorySearchPlugin(ServicePlugin):
    name = "conversation_history_search"
    required_credentials = []
    tools = [
        ToolDefinition(
            name="search",
            description=(
                "Busca semântica e fulltext no histórico de conversas do Claude Code. "
                "Útil para encontrar contexto de discussões passadas, decisões técnicas, "
                "bugs resolvidos, comandos executados, etc. "
                "Retorna trechos relevantes com metadados (projeto, data, sessão)."
            ),
            params={
                "properties": {
                    "query": {"type": "string", "description": "Texto de busca (semântico + fulltext)"},
                    "project": {"type": "string", "description": "Filtrar por nome do projeto (opcional)"},
                    "date": {"type": "string", "description": "Filtrar por data YYYY-MM-DD (opcional)"},
                    "source": {"type": "string", "description": "Filtrar por source: local, vps-mcpgw (opcional)"},
                    "limit": {"type": "integer", "description": "Número máximo de resultados (default: 5)"},
                },
                "required": ["query"],
            },
            policy="free",
        ),
        ToolDefinition(
            name="list_projects",
            description="Lista todos os projetos disponíveis no histórico de conversas.",
            params={
                "properties": {},
                "required": [],
            },
            policy="free",
        ),
    ]

    async def execute(self, tool: str, params: dict, credentials: dict) -> dict:
        if tool == "search":
            return await self._search(params)
        elif tool == "list_projects":
            return await self._list_projects(params)
        raise ValueError(f"Unknown tool: {tool}")

    async def _search(self, params: dict) -> list:
        query = params["query"]
        project = params.get("project")
        date = params.get("date")
        source = params.get("source")
        limit = params.get("limit", 5)

        results = await self._semantic_search(query, limit, project, date, source)

        best_score = results[0]["score"] if results else 0
        if best_score < SEMANTIC_THRESHOLD:
            text_results = await self._fulltext_search(query, limit, project, date, source)
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
                "project": r["payload"].get("project", ""),
                "date": r["payload"].get("date", ""),
                "session_id": r["payload"].get("session_id", "")[:8],
                "source": r["payload"].get("source", ""),
                "score": round(r["score"], 4) if r["match"] == "semantic" else None,
                "match_type": r["match"],
            }
            for r in results
        ]

    async def _semantic_search(self, query: str, limit: int, project: str = None, date: str = None, source: str = None) -> list[dict]:
        embedding = await self._get_embedding(query)

        body = {"vector": embedding, "limit": limit, "with_payload": True}

        filters = self._build_filters(project, date, source)
        if filters:
            body["filter"] = {"must": filters}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/search", json=body)
            resp.raise_for_status()

        return [
            {"id": r["id"], "score": r["score"], "payload": r["payload"], "match": "semantic"}
            for r in resp.json()["result"]
        ]

    async def _fulltext_search(self, query: str, limit: int, project: str = None, date: str = None, source: str = None) -> list[dict]:
        filters = [{"key": "text", "match": {"text": query}}]
        filters.extend(self._build_filters(project, date, source))

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

    def _build_filters(self, project: str = None, date: str = None, source: str = None) -> list[dict]:
        filters = []
        if project:
            filters.append({"key": "project", "match": {"value": project}})
        if date:
            filters.append({"key": "date", "match": {"value": date}})
        if source:
            filters.append({"key": "source", "match": {"value": source}})
        return filters

    async def _list_projects(self, params: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
                json={"limit": 1, "with_payload": ["project"]},
            )
            resp.raise_for_status()

            resp2 = await client.post(
                f"{QDRANT_URL}/collections/{COLLECTION}/points/count",
                json={},
            )
            resp2.raise_for_status()
            total = resp2.json()["result"]["count"]

            projects = set()
            offset = None
            while True:
                body = {"limit": 100, "with_payload": ["project"]}
                if offset:
                    body["offset"] = offset
                r = await client.post(
                    f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
                    json=body,
                )
                r.raise_for_status()
                data = r.json()["result"]
                for p in data.get("points", []):
                    proj = p.get("payload", {}).get("project", "")
                    if proj:
                        projects.add(proj)
                offset = data.get("next_page_offset")
                if not offset:
                    break

        return {"total_chunks": total, "projects": sorted(projects)}
