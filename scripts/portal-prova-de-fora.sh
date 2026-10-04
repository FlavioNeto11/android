#!/usr/bin/env bash
# Prova de fora do portal publico da Central (item 29.54, ADR-073).
#
# So faz pedidos SEM credencial ao endereco publico: nenhum segredo entra aqui, e a rota de login nao e chamada
# (a tranca de login e global: tentativa de fora conta contra o dono).
#
# Uso:  bash scripts/portal-prova-de-fora.sh antes     -> hostname AINDA fora de server.public_hosts (tudo recusado)
#       bash scripts/portal-prova-de-fora.sh depois    -> hostname declarado, codigo do 29.54 no ar e central reiniciado
#
# Variaveis:  HOSTNAME_PUBLICO=outro.exemplo    (padrao: dev.nvit.com.br)
#             SEM_LIMITE_DE_TAXA=1              pula a conferencia do 429 no login. Essa linha prova a regra de limite
#                                               de taxa criada NA CLOUDFLARE desta instalacao (docs/operacao.md), nao o
#                                               codigo: numa instalacao sem a regra ela falha, e o resto continua valendo.
#             WEBHOOK_DO_TRELLO=ligado          a Etapa 2 do 32.2 esta no ar (`trello.webhook.enabled: true`): o webhook
#                                               responde 200 ao HEAD e 401 ao GET e ao POST sem assinatura. Sem a
#                                               variavel, o esperado e o webhook fechado (401 ou 404).
#
# Regra de ouro: /api/instances NUNCA pode dar 200 de fora. Se der: Stop-Service Cloudflared e investigue antes de religar.
set -u
H="${HOSTNAME_PUBLICO:-dev.nvit.com.br}"
MODO="${1:-depois}"
FALHAS=0

codigo() { # caminho [args extras do curl]  -> imprime so o codigo HTTP
    local caminho="$1"; shift
    curl -s -o /dev/null -m 20 -w '%{http_code}' "$@" "https://$H$caminho"
}

confere() { # caminho esperado descricao [args extras]
    local caminho="$1" esperado="$2" descricao="$3"; shift 3
    local veio
    veio="$(codigo "$caminho" "$@")"
    if [[ " $esperado " == *" $veio "* ]]; then
        printf 'ok     %-28s %s  (%s)\n' "$caminho" "$veio" "$descricao"
    else
        printf 'FALHOU %-28s %s  esperado %s  (%s)\n' "$caminho" "$veio" "$esperado" "$descricao"
        FALHAS=$((FALHAS + 1))
    fi
}

echo "prova de fora de https://$H  modo=$MODO  $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Vale nos dois modos: o canal do worker nunca sai pelo tunel (regra do ingress), e http vira https na borda.
confere /api/worker/ws      "404" "canal do worker: 404 no ingress"
confere /api/worker/midia   "404" "canal de midia do worker: 404 no ingress"
veio_http="$(curl -s -o /dev/null -m 20 -w '%{http_code}' "http://$H/central/")"
if [[ "$veio_http" == "301" || "$veio_http" == "308" ]]; then
    printf 'ok     %-28s %s  (http vira https na borda)\n' "http://.../central/" "$veio_http"
else
    printf 'FALHOU %-28s %s  esperado 301  (Always Use HTTPS)\n' "http://.../central/" "$veio_http"; FALHAS=$((FALHAS + 1))
fi

if [[ "$MODO" == "antes" ]]; then
    for c in /api/instances /api/health /api/session /api/workers / /central/ /docs /openapi.json /api/docs; do
        confere "$c" "403" "hostname nao declarado: forbidden_host"
    done
else
    confere /api/instances      "401" "API sem credencial"
    confere /api/health         "401" "saude sem credencial"
    confere /api/workers        "401" "tela de workers e /api: sem credencial"
    confere /api/session        "200" "rota de sessao: aberta, diz que nao ha sessao"
    confere /central/           "200" "painel estatico"
    confere /central            "301 302 307 308" "sem a barra final: redireciona"
    confere /                   "301 302 307 308" "raiz: redireciona para o painel"
    confere /docs               "404" "docs da API fora de /api: nao existem mais"
    confere /redoc              "404" "idem"
    confere /openapi.json       "404" "mapa da API fora de /api: nao existe mais"
    confere /api/docs           "401" "docs da API: atras do login"
    confere /api/openapi.json   "401" "mapa da API: atras do login"
    # Painel de eventos por WebSocket, sem credencial: o aperto de mao e recusado (403), nunca 101.
    confere /api/ws             "401 403" "WebSocket do painel sem credencial" --http1.1 \
        -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
        -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ=="
    # Webhook do Trello (32.2, PR #177): unica excecao sem credencial em /api/ no publico, so HEAD e POST.
    # Antes do 32.2 implantado: 401 (rota comum de /api/). Implantado e desligado: 404. Ligado: HEAD 200.
    if [[ "${WEBHOOK_DO_TRELLO:-}" == "ligado" ]]; then
        # Etapa 2 do 32.2 no ar: HEAD 200; GET e POST sem assinatura valida 401. So 1 POST ruim por rodada: 5 recusas
        # em 10 min acendem `trello_webhook_assinatura_invalida` na saude.
        confere /api/canais/trello/webhook "200" "webhook do Trello ligado: HEAD 200" -I
        confere /api/canais/trello/webhook "401" "webhook do Trello ligado: GET recusado"
        confere /api/canais/trello/webhook "401" "webhook do Trello ligado: POST sem assinatura recusado" \
            -X POST -H "Content-Type: application/json" -d "{}"
    else
        confere /api/canais/trello/webhook "401 404" "webhook do Trello: fechado (401 antes do 32.2, 404 desligado)" -I
    fi
    # Limite de taxa da Cloudflare na rota de login (so GET, que da 405 na origem e nao conta como tentativa):
    # a 2a chamada em 10 s tem de voltar 429.
    if [[ "${SEM_LIMITE_DE_TAXA:-0}" == "1" ]]; then
        printf 'pulado %-28s      (SEM_LIMITE_DE_TAXA=1)\n' "/api/login"
    else
        codigo /api/login >/dev/null
        confere /api/login          "429" "limite de taxa da Cloudflare no login (2a chamada em 10 s)"
    fi
    # A rota de sessao, de fora e sem credencial, tem de dizer que a senha e exigida e que nao ha operador:
    # e isso que faz o painel publico mostrar a tela de login em vez de so falhar com 401 nos dados.
    corpo="$(curl -s -m 20 "https://$H/api/session" | tr -d ' \n\r')"
    if [[ "$corpo" == *'"token_required":true'* && "$corpo" == *'"operator":null'* ]]; then
        printf 'ok     %-28s      (pede senha e nao ha operador)\n' "/api/session (corpo)"
    else
        printf 'FALHOU %-28s      esperado token_required true e operator null\n' "/api/session (corpo)"
        FALHAS=$((FALHAS + 1))
    fi
    # Para onde a raiz manda: tem de ser caminho relativo ao proprio endereco publico, nunca 127.0.0.1.
    destino="$(curl -s -o /dev/null -m 20 -w '%{redirect_url}' "https://$H/")"
    case "$destino" in
        "https://$H/central/"*) printf 'ok     %-28s -> %s\n' "/ (Location)" "$destino" ;;
        *) printf 'FALHOU %-28s -> %s  esperado https://%s/central/\n' "/ (Location)" "$destino" "$H"; FALHAS=$((FALHAS + 1)) ;;
    esac
fi

# Host forjado: a borda da Cloudflare nao entrega, e o central nunca ve "localhost" vindo de fora.
confere /api/instances "403 404 409 421" "Host forjado (localhost)" -H "Host: localhost"

inst="$(codigo /api/instances)"
if [[ "$inst" == "200" ]]; then
    echo "PARE: /api/instances respondeu 200 de fora. Stop-Service Cloudflared e investigue antes de religar."
    exit 2
fi
if [[ "$FALHAS" -gt 0 ]]; then echo "RESULTADO: $FALHAS falha(s)"; exit 1; fi
echo "RESULTADO: tudo como esperado"
