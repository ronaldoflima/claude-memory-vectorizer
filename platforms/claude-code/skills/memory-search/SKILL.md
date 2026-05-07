---
name: memory-search
description: Busca semântica + fulltext no histórico de conversas Claude Code indexado em Qdrant local. Use quando o usuário perguntar sobre decisões passadas, comandos usados, ou contexto de sessões anteriores.
---

## Quando usar

- "como fizemos X", "o que decidimos sobre Y", "onde discutimos Z"
- Contexto de sessões anteriores não disponível na conversa atual
- Encontrar comandos, soluções ou decisões passadas

## Pré-requisitos

Qdrant em `http://localhost:6333` e Ollama com `nomic-embed-text`. Se Qdrant estiver remoto:

```bash
ssh -fNL 6333:localhost:6333 vps-gateway
```

## Como buscar

```bash
python3 ~/.claude/skills/memory-search/search.py "<query>"
python3 ~/.claude/skills/memory-search/search.py "<query>" --project <nome>
python3 ~/.claude/skills/memory-search/search.py "<query>" --date 2026-03-15
python3 ~/.claude/skills/memory-search/search.py "<query>" --limit 10
```

## Interpretando resultados

```
[1] Score: 0.7231 | 2026-03-15 | px-integrations | agent-a1
```

- **Score ≥ 0.6** — match semântico (busca por embedding)
- **FULLTEXT** — match por texto exato (fallback)
- Trecho exibido: até 500 chars do chunk da conversa

## Instalação

No diretório do projeto:

```bash
bash platforms/claude-code/install.sh
```

Cria `~/.claude/skills/memory-search/` como symlink para o diretório da skill no projeto.
