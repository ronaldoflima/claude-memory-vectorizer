#!/bin/bash
# Health check para o pipeline Qdrant: Mac → Desktop → VPS

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
BOLD='\033[1m'

ok() { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; }

echo -e "${BOLD}=== Qdrant Pipeline Health Check ===${NC}"
echo ""

# 1. Mac push cron
echo -e "${BOLD}[Mac]${NC}"
if crontab -l 2>/dev/null | grep -q push-to-desktop; then
    ok "Push cron ativo"
else
    fail "Push cron NÃO encontrado"
fi

PUSH_LOG="$HOME/pessoal/projects/claude-memory-vectorizer/push.log"
if [[ -f "$PUSH_LOG" ]]; then
    LAST_PUSH=$(tail -1 "$PUSH_LOG" 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}' | tail -1)
    if [[ -n "$LAST_PUSH" ]]; then
        ok "Último push: $LAST_PUSH"
    else
        LAST_MOD=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$PUSH_LOG" 2>/dev/null)
        ok "Push log modificado: ${LAST_MOD:-desconhecido}"
    fi
else
    warn "Push log não encontrado"
fi
echo ""

# 2. Desktop
echo -e "${BOLD}[Desktop]${NC}"
if ssh -o ConnectTimeout=3 desktop true 2>/dev/null; then
    ok "Desktop acessível"

    DESKTOP_QDRANT=$(ssh desktop "curl -sf http://localhost:6333/healthz" 2>/dev/null)
    if [[ "$DESKTOP_QDRANT" == *"passed"* ]]; then
        ok "Qdrant rodando"
    else
        fail "Qdrant NÃO responde"
    fi

    LAST_SYNC=$(ssh desktop "cat /home/ronaldo/claude-memory-vectorizer/.sync_state 2>/dev/null")
    if [[ -n "$LAST_SYNC" ]]; then
        SYNC_EPOCH=$(ssh desktop "date -d '$LAST_SYNC' +%s 2>/dev/null")
        NOW_EPOCH=$(ssh desktop "date +%s")
        if [[ -n "$SYNC_EPOCH" ]]; then
            AGE_MIN=$(( (NOW_EPOCH - SYNC_EPOCH) / 60 ))
            if (( AGE_MIN < 15 )); then
                ok "Último sync: ${AGE_MIN}min atrás ($LAST_SYNC)"
            elif (( AGE_MIN < 30 )); then
                warn "Último sync: ${AGE_MIN}min atrás ($LAST_SYNC)"
            else
                fail "Último sync: ${AGE_MIN}min atrás ($LAST_SYNC) — ATRASADO"
            fi
        else
            ok "Último sync: $LAST_SYNC"
        fi
    else
        fail "Nenhum sync registrado"
    fi

    SESSIONS=$(ssh desktop 'curl -sf http://localhost:6333/collections/agent_sessions' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['points_count'])" 2>/dev/null)
    ARTIFACTS=$(ssh desktop 'curl -sf http://localhost:6333/collections/agent_work_artifacts' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['points_count'])" 2>/dev/null)
    ok "agent_sessions: ${SESSIONS:-?} points"
    ok "agent_work_artifacts: ${ARTIFACTS:-?} points"

    SYNC_ERRORS=$(ssh desktop "tail -50 /home/ronaldo/claude-memory-vectorizer/sync.log 2>/dev/null | grep -c 'ERROR\|WARN'" 2>/dev/null)
    if [[ "$SYNC_ERRORS" -gt 0 ]]; then
        warn "$SYNC_ERRORS warnings/errors nas últimas 50 linhas do log"
    else
        ok "Sem erros recentes no log"
    fi

    CRON_OK=$(ssh desktop "crontab -l 2>/dev/null | grep -c sync.sh")
    if [[ "$CRON_OK" -gt 0 ]]; then
        ok "Sync cron ativo"
    else
        fail "Sync cron NÃO encontrado"
    fi
else
    fail "Desktop OFFLINE"
fi
echo ""

# 3. VPS
echo -e "${BOLD}[VPS]${NC}"
if ssh -o ConnectTimeout=3 vps-mesh true 2>/dev/null; then
    ok "VPS acessível"

    VPS_HEALTH=$(ssh vps-mesh "docker inspect qdrant-mcpgw --format '{{.State.Health.Status}}'" 2>/dev/null)
    if [[ "$VPS_HEALTH" == "healthy" ]]; then
        ok "Qdrant container: healthy"
    else
        fail "Qdrant container: ${VPS_HEALTH:-não encontrado}"
    fi

    VPS_SESSIONS=$(ssh vps-mesh 'curl -sf http://localhost:6333/collections/agent_sessions' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['points_count'])" 2>/dev/null)
    VPS_ARTIFACTS=$(ssh vps-mesh 'curl -sf http://localhost:6333/collections/agent_work_artifacts' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['points_count'])" 2>/dev/null)
    ok "agent_sessions: ${VPS_SESSIONS:-?} points"
    ok "agent_work_artifacts: ${VPS_ARTIFACTS:-?} points"

    # Comparar Desktop vs VPS
    if [[ -n "$SESSIONS" && -n "$VPS_SESSIONS" ]]; then
        DIFF=$(( SESSIONS - VPS_SESSIONS ))
        ABS_DIFF=${DIFF#-}
        if (( ABS_DIFF > 100 )); then
            fail "Dessincronizado: Desktop=$SESSIONS vs VPS=$VPS_SESSIONS (diff=$DIFF)"
        elif (( ABS_DIFF > 10 )); then
            warn "Pequena diferença: Desktop=$SESSIONS vs VPS=$VPS_SESSIONS (diff=$DIFF)"
        else
            ok "Desktop↔VPS sincronizado (diff=$DIFF)"
        fi
    fi
else
    fail "VPS OFFLINE"
fi
echo ""

# 4. Busca funcional
echo -e "${BOLD}[Busca]${NC}"
SEARCH_RESULT=$(ssh vps-mesh 'curl -sf -X POST http://localhost:6333/collections/agent_sessions/points/search -H "Content-Type: application/json" -d "{\"vector\": [0.1], \"limit\": 1}" 2>/dev/null' | python3 -c "import sys,json; r=json.load(sys.stdin); print('ok' if r.get('status')=='ok' or r.get('result') is not None else 'fail')" 2>/dev/null)
if [[ "$SEARCH_RESULT" == "ok" ]]; then
    ok "Search endpoint funcional na VPS"
else
    warn "Search retornou resultado inesperado (pode ser normal se o vetor de teste não bate)"
fi
echo ""
echo -e "${BOLD}=== Comandos úteis para debug ===${NC}"
echo "  Logs do sync:  ssh desktop 'tail -50 ~/claude-memory-vectorizer/sync.log'"
echo "  Logs do push:  tail -20 ~/pessoal/projects/claude-memory-vectorizer/push.log"
echo "  Qdrant logs:   ssh vps-mesh 'docker logs qdrant-mcpgw --tail 20'"
echo "  Forçar sync:   ssh desktop '/home/ronaldo/claude-memory-vectorizer/sync.sh'"
