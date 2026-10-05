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
#             SITE=ligado                       o site institucional esta no ar (29.77, `portal.site_ligado: true`): a
#                                               raiz responde 200 com a CSP do site e SEM Set-Cookie (a pagina
#                                               promete que nao usa cookies), em vez do 307 para o painel.
#             CONTATO=ligado                    o formulario esta no ar (`portal.contato_ligado: true`). A prova manda UM
#                                               POST com a ISCA preenchida: passa por Host, Origin, Content-Type e pela
#                                               excecao do portao, e por construcao nao grava nem avisa ninguem (202).
#                                               Nunca manda contato de verdade. 403 = falta a origem em allowed_origins;
#                                               404 = bandeira desligada; 401 = a excecao do portao nao esta no codigo.
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
    if [[ "${SITE:-}" == "ligado" ]]; then
        confere /                   "200" "raiz: o site institucional (29.77)"
        confere /robots.txt         "200" "site: robots.txt"
        confere /assets/site.css    "200" "site: estilo"
        confere /.git/config        "404" "site: nada fora da pasta do site"
        confere /config/config.yaml "404" "site: nada fora da pasta do site"
        confere /site/index.html    "404" "site: o caminho da pasta nao e servido"
        confere /rascunho.pdf       "404" "site: extensao fora da lista fechada"
        confere /pagina-que-nao-existe "404" "site: a pagina 404 propria, com status 404"
        robots="$(curl -s -m 20 "https://$H/robots.txt" | tr -d '\r')"
        if [[ "$robots" == *"Disallow: /central/"* && "$robots" == *"Disallow: /api/"* ]]; then
            printf 'ok     %-28s      (barra /central/ e /api/)\n' "/robots.txt (corpo)"
        else
            printf 'FALHOU %-28s      esperado Disallow de /central/ e /api/\n' "/robots.txt (corpo)"; FALHAS=$((FALHAS + 1))
        fi
        cabecalhos="$(curl -s -o /dev/null -D - -m 20 "https://$H/" | tr -d '\r')"
        csp="$(grep -i '^content-security-policy:' <<< "$cabecalhos")"
        if [[ "$csp" == *"script-src 'self'"* && "$csp" == *"frame-ancestors 'none'"* ]]; then
            printf 'ok     %-28s      (CSP do site)\n' "/ (cabecalhos)"
        else
            printf 'FALHOU %-28s      esperado a CSP do site\n' "/ (cabecalhos)"; FALHAS=$((FALHAS + 1))
        fi
        # A pagina diz "nao usa cookies nem rastreadores" (ADR-075). A origem nunca poe cookie no site; a borda da
        # Cloudflare poderia, conforme a zona. Se aparecer, muda o texto da pagina ou desliga-se o recurso na zona.
        if grep -qi '^set-cookie:' <<< "$cabecalhos"; then
            printf 'FALHOU %-28s      a raiz pos cookie; a pagina promete que nao usa\n' "/ (sem cookie)"; FALHAS=$((FALHAS + 1))
        else
            printf 'ok     %-28s      (nenhum Set-Cookie, como a pagina promete)\n' "/ (sem cookie)"
        fi
        # 29.85: a borda da Cloudflare injeta o beacon do Web Analytics (static.cloudflareinsights.com) no HTML so
        # quando o pedido parece de navegador; o curl puro nao ve (medido em 05/10). A CSP do site bloqueia e o script
        # nao roda, mas sobra um erro de console em todo visitante, e a pagina promete "sem rastreadores". Por isso a
        # raiz e baixada COMO navegador, e qualquer <script src> de outra origem reprova. A CSP nao muda: o conserto e
        # desligar o recurso na zona.
        ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
        html="$(curl -s -m 20 -H 'Accept: text/html,application/xhtml+xml' -H "User-Agent: $ua" "https://$H/")"
        de_fora=""
        while IFS= read -r src; do
            case "$src" in
                "https://$H/"*|"//$H/"*) ;;
                http://*|https://*|//*) de_fora="$de_fora $src" ;;
            esac
        done < <(grep -oiE "<script[^>]*[[:space:]]src=[\"']?[^\"' >]+" <<< "$html" | sed -E "s/.*[sS][rR][cC]=[\"']?//")
        if [[ -z "$html" ]]; then
            printf 'FALHOU %-28s      a raiz veio vazia pedida como navegador\n' "/ (como navegador)"; FALHAS=$((FALHAS + 1))
        elif [[ -n "$de_fora" || "$html" == *cloudflareinsights* ]]; then
            printf 'FALHOU %-28s      script de outra origem no HTML:%s\n' "/ (como navegador)" "${de_fora:- cloudflareinsights}"
            echo '       -> desligue na Cloudflare, na zona do nome publico: Web Analytics / Real User Measurements (RUM),'
            echo '          a injecao automatica do beacon. Nao afrouxe a CSP: a pagina promete que nao usa rastreadores.'
            FALHAS=$((FALHAS + 1))
        else
            printf 'ok     %-28s      (nenhum script de outra origem no HTML)\n' "/ (como navegador)"
        fi
    else
        confere /                   "301 302 307 308" "raiz: redireciona para o painel"
    fi
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
    # Para onde a raiz manda (ou /central, com o site no ar): caminho relativo ao proprio endereco, nunca 127.0.0.1.
    entrada="/"; [[ "${SITE:-}" == "ligado" ]] && entrada="/central"
    destino="$(curl -s -o /dev/null -m 20 -w '%{redirect_url}' "https://$H$entrada")"
    case "$destino" in
        "https://$H/central/"*) printf 'ok     %-28s -> %s\n' "$entrada (Location)" "$destino" ;;
        *) printf 'FALHOU %-28s -> %s  esperado https://%s/central/\n' "$entrada (Location)" "$destino" "$H"; FALHAS=$((FALHAS + 1)) ;;
    esac
    # Contato do site (29.77): a outra excecao sem credencial em /api/, so POST no caminho exato.
    confere /api/portal/contato "401" "contato do site: GET segue fechado"
    if [[ "${CONTATO:-}" == "ligado" ]]; then
        confere /api/portal/contato "202" "contato do site: POST com a isca (nao grava, nao avisa)" \
            -X POST -H "Content-Type: application/json" -H "Origin: https://$H" \
            -d '{"nome":"prova","telefone":"00000000","mensagem":"prova","consentimento":true,"site":"isca","token":""}'
        confere /api/portal/contato "415" "contato do site: tipo errado recusado antes de ler o corpo" \
            -X POST -H "Content-Type: text/plain" -H "Origin: https://$H" -d 'site=isca'
        grande="$(printf '%*s' 9000 '' | tr ' ' 'a')"
        confere /api/portal/contato "413" "contato do site: corpo acima do teto" \
            -X POST -H "Content-Type: application/json" -H "Origin: https://$H" -d "{\"site\":\"$grande\"}"
    else
        confere /api/portal/contato "401 404" "contato do site: fechado (401 antes do 29.77, 404 desligado)" \
            -X POST -H "Content-Type: application/json" -H "Origin: https://$H" -d '{"site":"isca"}'
    fi
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
