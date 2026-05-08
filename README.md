# claude-memory-vectorizer

> 🌐 [English](README.en.md) · **Português**

Indexa o histórico de conversas do Claude Code em um vector store (Qdrant) para busca semântica em sessões passadas. Permite que agentes e ferramentas de IA consultem o que foi discutido, decidido ou construído em conversas anteriores.

## Como funciona

1. Lê arquivos `.jsonl` de sessão em `~/.claude/projects/`
2. Extrai mensagens de usuário/assistente e quebra em chunks (~2000 chars)
3. Gera embeddings via Ollama (`bge-m3`, 1024 dims, roda localmente — ou `nomic-embed-text`/768 dims como alternativa mais leve)
4. Faz upsert no Qdrant com metadados (projeto, data, ID da sessão, fonte)
5. Rastreia arquivos processados em um state file para execuções incrementais

A busca é híbrida: semântica primeiro, fallback para fulltext quando o melhor score < 0.6.

## Estrutura do projeto

```
etl/
  claude/conversations.py   # ETL para sessões do Claude Code
  obsidian/notes.py         # ETL para vault do Obsidian
  github/prs.py             # ETL para PRs e commits do GitHub
mcp/
  conversation_history_search.py  # Plugin MCP para busca por agentes
  work_artifacts_search.py        # Plugin MCP para busca de PRs/commits
scripts/
  sync-and-index.sh         # Sync completo: puxa fontes do VPS + túnel + indexa
  pull-from-vps.sh          # Puxa sessões do Claude (e opcionalmente Obsidian) do VPS
  push-to-embedding-host.sh # Envia fontes locais para um host remoto de embedding
  check-health.sh           # Health check do Qdrant + Ollama
search.py                   # CLI de busca
platforms/
  claude-code/              # Skill do Claude Code + script de instalação
```

## Requisitos

### Software

- **Python** 3.11+
- **Docker** (para rodar o Qdrant localmente) — ou acesso a um Qdrant remoto
- **[Ollama](https://ollama.com)** com `bge-m3` (default, 1024 dims) ou `nomic-embed-text` (768 dims, mais leve)
- **[Qdrant](https://qdrant.tech)** local via Docker ou remoto

### Sistema operacional

- macOS, Linux ou WSL2 no Windows
- `bash` (scripts em `scripts/` usam features de bash; `sync-and-index.sh` usa `date -v` no formato BSD/macOS — em Linux pode ser necessário trocar para `date -d`)

### Uso de recursos

Os custos de RAM são **picos durante a indexação**, não uso constante. Em idle (sem indexar) o Qdrant fica em ~100-200 MB e o Ollama descarrega o modelo após o `keep_alive` (default 5 min).

| Componente | Idle | Durante indexação | Disco |
|------------|------|-------------------|-------|
| Ollama (`bge-m3`) | ~0 MB (modelo descarregado) | ~2 GB enquanto roda | ~1.2 GB (modelo) |
| Qdrant | ~100-200 MB | ~500 MB | cresce com o histórico (tipicamente centenas de MB) |
| Total recomendado | — | 8 GB RAM mínimo, 16 GB confortável | 3-10 GB livres |

CPU/GPU: indexação roda em CPU sem problema; GPU ou Apple Silicon acelera bastante a primeira passada. Depois é incremental — só processa arquivos novos/modificados.

### Fonte de dados

- Histórico do Claude Code em `~/.claude/projects/` (criado automaticamente pelo uso da CLI)
- Opcional: vault do Obsidian e repositórios GitHub para indexar PRs/commits

## Setup

### 1. Qdrant

**Local** (com volume persistente):

```bash
docker compose up -d
```

**Túnel remoto** (opcional — caso rodando em VPS):

```bash
ssh -fNL 6333:localhost:6333 seu-vps-host
```

### 2. Ollama

```bash
# Instale o Ollama (Linux/macOS):
curl -fsSL https://ollama.com/install.sh | sh

# Default (recomendado): bge-m3 — 1024 dims, multilíngue, contexto maior
ollama pull bge-m3

# Alternativa mais leve: nomic-embed-text — 768 dims
# ollama pull nomic-embed-text
```

### 3. Dependências Python

Se ainda não tem o `pip`:

```bash
python3 -m ensurepip --upgrade
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

### 4. Configuração

```bash
cp .env.example .env
# edite .env com seus valores
```

Variáveis principais:

| Variável | Descrição |
|----------|-----------|
| `QDRANT_URL` | Endpoint do Qdrant (default: `http://localhost:6333`) |
| `OLLAMA_URL` | Endpoint do Ollama (default: `http://localhost:11434`) |
| `EMBEDDING_MODEL` | Modelo do Ollama (default: `bge-m3`; alternativa: `nomic-embed-text`) |
| `VECTOR_SIZE` | Dimensão do vetor — precisa bater com o modelo (`1024` para `bge-m3`, `768` para `nomic-embed-text`). Mudar isso exige reindexar. |
| `VPS_HOST` | Host SSH onde o Qdrant roda (para o túnel em `sync-and-index.sh`) |
| `VPS_SOURCE_HOST` | Host SSH de onde puxar sessões do Claude (opcional) |
| `VPS_SOURCE_USER` | Usuário no host de origem do VPS |
| `VPS_SOURCE_OBSIDIAN_DIR` | Caminho do vault do Obsidian no VPS (opcional) |
| `GITHUB_ORG` | Org/usuário do GitHub para indexar PRs (opcional) |
| `EMBEDDING_HOST` | Host que roda o ETL, para o `push-to-embedding-host.sh` |
| `REMOTE_DIR` | Caminho remoto para as fontes sincronizadas |
| `OBSIDIAN_DIR` | Caminho local do vault do Obsidian |
| `PROJECT_PATH_STRIP` | Fragmentos de caminho separados por `:` para remover dos nomes de diretório internos do Claude |

## Uso

### Indexar conversas (incremental)

```bash
python3 etl/claude/conversations.py
```

Só processa arquivos de sessão novos ou modificados desde a última execução. O estado é salvo em `etl/claude/.etl_state.json`.

### Opções de indexação

```bash
python3 etl/claude/conversations.py --dry-run
python3 etl/claude/conversations.py --force
python3 etl/claude/conversations.py --qdrant-url http://localhost:6333
python3 etl/claude/conversations.py --source-dir /caminho/para/.claude/projects
python3 etl/claude/conversations.py --history /caminho/para/history.jsonl
python3 etl/claude/conversations.py --source-label minha-maquina
python3 etl/claude/conversations.py --state-file /caminho/para/state.json
```

### Busca

```bash
python3 search.py "como implementamos autenticação"
python3 search.py "setup do qdrant" --project meu-projeto
python3 search.py "deploy pipeline" --date 2026-03-15
python3 search.py "correção de bug" --limit 10
```

## Setup multi-fonte (opcional)

Puxe sessões de um VPS e indexe junto com as locais:

```bash
# Puxa fontes do VPS para local
./scripts/pull-from-vps.sh

# Indexa sessões locais
python3 etl/claude/conversations.py --source-label local

# Indexa sessões do VPS
python3 etl/claude/conversations.py \
  --source-dir /tmp/vps-source-claude-projects \
  --history /tmp/vps-source-claude-history.jsonl \
  --source-label vps \
  --state-file .etl_state_vps.json
```

`scripts/sync-and-index.sh` automatiza esse fluxo completo, incluindo o túnel SSH para o Qdrant.

## Sincronizar fontes para um host de embedding remoto

`scripts/push-to-embedding-host.sh` faz rsync dos seus projetos do Claude, history e vault do Obsidian para um host remoto que roda o ETL:

```bash
./scripts/push-to-embedding-host.sh
# ou sobrescreva o host
EMBEDDING_HOST=meu-host ./scripts/push-to-embedding-host.sh
```

## Automação com crontab

Para indexar automaticamente em segundo plano, adicione uma entrada no crontab.

### Indexação local a cada hora

```bash
crontab -e
```

Adicione:

```cron
# Indexar sessões Claude locais a cada hora
0 * * * * cd /caminho/para/claude-memory-vectorizer && python3 etl/claude/conversations.py >> /tmp/claude-vectorizer.log 2>&1
```

### Sync completo (VPS + local) a cada hora

Se você usa o `sync-and-index.sh` (que abre túnel SSH, puxa fontes do VPS e indexa tudo):

```cron
# Sync completo + indexação a cada hora
0 * * * * cd /caminho/para/claude-memory-vectorizer && ./scripts/sync-and-index.sh >> /tmp/claude-vectorizer-sync.log 2>&1
```

### Dicas

- Substitua `/caminho/para/claude-memory-vectorizer` pelo caminho absoluto do repositório.
- Use `crontab -l` para listar entradas existentes.
- Verifique os logs em `/tmp/claude-vectorizer.log` se algo não funcionar.
- O script é incremental: só processa arquivos novos/modificados desde a última execução.
- Para rodar apenas em dias úteis: `0 * * * 1-5 cd /caminho/... && python3 etl/claude/conversations.py`

---

## Skill do Claude Code

Instale a skill de busca de memória no Claude Code:

```bash
./platforms/claude-code/install.sh
```

Isso cria um symlink de `platforms/claude-code/skills/memory-search.md` em `~/.claude/skills/`, permitindo que o Claude busque seu histórico de conversas diretamente.
