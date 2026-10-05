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
    curl -q -s -o /dev/null -m 20 -w '%{http_code}' "$@" "https://$H$caminho"
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

confere_como_navegador() { # caminho rotulo  -> o HTML pedido como navegador nao tem script injetado
    local caminho="$1" rotulo="$2"
    # 29.85: a borda da Cloudflare injeta o beacon do Web Analytics (static.cloudflareinsights.com) no HTML so
    # quando o pedido parece de navegador; o curl puro nao ve (medido em 05/10). A CSP do site bloqueia e o script
    # nao roda, mas sobra um erro de console em todo visitante, e a pagina promete "sem rastreadores". Por isso a
    # raiz e baixada COMO navegador, e qualquer <script src> de outra origem reprova. A CSP nao muda: o conserto e
    # desligar o recurso na zona. A Cloudflare tambem injeta script na PROPRIA origem, sob /cdn-cgi/ (Rocket Loader,
    # challenge-platform, ofuscacao de e-mail): relativo, passa na CSP, e tambem reprova.
    local ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
    local nav codigo_nav html html_linha de_fora src esquema
    nav="$(curl -q -s -m 20 -w '\n%{http_code}' -H 'Accept: text/html,application/xhtml+xml' -H "User-Agent: $ua" \
        "https://$H$caminho")"
    codigo_nav="${nav##*$'\n'}"; html="${nav%$'\n'*}"
    # Uma linha so: a tag pode vir quebrada em linhas, e o `=` com espaco em volta.
    html_linha="$(tr '\r\n\t' '   ' <<< "$html")"
    de_fora=""
    shopt -s nocasematch                   # HTTPS:// e o nome publico em maiuscula sao o mesmo endereco
    while IFS= read -r src; do
        # O esquema so conta ANTES do primeiro / ? ou #: `/assets/site.js?v=T01:00` e relativo, nao "de fora".
        esquema="${src%%[/?#]*}"
        case "$src" in
            *"/cdn-cgi/"*) de_fora="$de_fora $src" ;;
            "https://$H/"*|"//$H/"*) ;;
            //*) de_fora="$de_fora $src" ;;
            *) case "$esquema" in
                   *:*) de_fora="$de_fora $src" ;;   # https:, data:, javascript:
                   ""|[a-z0-9._~%-]*) ;;             # relativo a propria origem
                   *) de_fora="$de_fora $src" ;;     # o que nao se reconhece reprova
               esac ;;
        esac
    # O valor do atributo nunca tem espaco, entao o ultimo "<espaco>src=" da casada e o atributo de verdade: um
    # `?src=b` DENTRO da URL nao e precedido de espaco e nao vira o valor (um `.*src=` guloso o pegava).
    done < <(grep -oiE "<script[^>]*[[:space:]]src[[:space:]]*=[[:space:]]*[\"']?[^\"' >]+" <<< "$html_linha" |
             sed -E "s/.*[[:space:]][sS][rR][cC][[:space:]]*=[[:space:]]*[\"']?//")
    shopt -u nocasematch
    if [[ "$codigo_nav" != 200 ]]; then
        printf 'FALHOU %-28s %s  esperado 200: sem ver a pagina nao ha o que conferir (desafio da Cloudflare?)\n' \
            "$rotulo" "$codigo_nav"; FALHAS=$((FALHAS + 1))
    elif [[ -z "$html" ]]; then
        printf 'FALHOU %-28s      a raiz veio vazia pedida como navegador\n' "$rotulo"; FALHAS=$((FALHAS + 1))
    elif [[ -n "$de_fora" || "$html_linha" == *cloudflareinsights* || "$html_linha" == *"/cdn-cgi/"* ]]; then
        printf 'FALHOU %-28s      script que a pagina nao tem no HTML:%s\n' "$rotulo" "${de_fora:- (embutido)}"
        echo '       -> desligue na Cloudflare, na zona do nome publico, o recurso que injeta:'
        echo '          cloudflareinsights = Web Analytics / Real User Measurements (RUM), a injecao automatica do beacon;'
        echo '          /cdn-cgi/scripts = Rocket Loader (Speed > Optimization); /cdn-cgi/challenge-platform = desafio'
        echo '          JS / Bot Fight Mode; /cdn-cgi/l/email-protection = Email Address Obfuscation (Scrape Shield).'
        echo '          Nao afrouxe a CSP: a pagina promete que nao usa rastreadores.'
        FALHAS=$((FALHAS + 1))
    else
        printf 'ok     %-28s      (nenhum script de fora nem da Cloudflare no HTML)\n' "$rotulo"
    fi
}

confere_versao_dos_arquivos() { # -> o HTML aponta o CSS e o JS na versao que a borda entrega (29.95)
    # A borda guarda CSS e JS por 4 h no navegador (max-age=14400 no lugar do no-cache da origem). A pagina aponta
    # cada arquivo com ?v=<sha256 do conteudo>: aqui se baixa pelo endereco da pagina e se confere o hash. Diferenca
    # quer dizer que a borda guarda sem olhar a query (nivel de cache "Ignore query string"), serviu copia velha ou
    # alterou o arquivo no caminho (minificacao automatica, Rocket Loader).
    local html arquivo versao veio
    html="$(curl -q -s -m 20 "https://$H/" | tr '\r\n\t' '   ')"
    for arquivo in /assets/site.css /assets/site.js; do
        versao="$(grep -oE "[[:space:]](href|src)=\"$arquivo\\?v=[0-9a-f]+\"" <<< "$html" | head -1 | sed -E 's/.*\?v=([0-9a-f]+)"/\1/')"
        if [[ -z "$versao" ]]; then
            printf 'FALHOU %-28s      a pagina aponta o arquivo sem ?v= (o navegador guarda o velho por 4 h)\n' "$arquivo (versao)"
            FALHAS=$((FALHAS + 1)); continue
        fi
        veio="$(curl -q -s -m 20 --compressed "https://$H$arquivo?v=$versao" | sha256sum | cut -c1-${#versao})"
        if [[ "$veio" == "$versao" ]]; then
            printf 'ok     %-28s      (?v=%s e o conteudo que a borda entrega)\n' "$arquivo (versao)" "$versao"
        else
            printf 'FALHOU %-28s      a pagina pede ?v=%s e a borda entregou %s\n' "$arquivo (versao)" "$versao" "${veio:-nada}"
            echo '       -> ou a borda guarda sem olhar a query (nivel de cache "Ignore query string"), ou serviu copia'
            echo '          velha, ou ALTEROU o arquivo no caminho (minificacao automatica de CSS/JS, Rocket Loader).'
            FALHAS=$((FALHAS + 1))
        fi
    done
}

confere_html_intocado() { # caminho rotulo gzip|csp  -> a borda nao pode reescrever este HTML (29.91)
    # `no-transform` proibe a borda de mexer no HTML (beacon, Rocket Loader, e-mail ofuscado), e tambem de recomprimir.
    # Por isso o pedido aceita gzip, br e zstd: se a borda respeita, o site chega no gzip que a ORIGEM fez; br ou zstd
    # quer dizer que a borda abriu o corpo. O painel nao e comprimido na origem; nele vale a CSP do painel.
    local caminho="$1" rotulo="$2" modo="$3" ua cab guarda cod csp
    ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
    cab="$(curl -q -s -o /dev/null -D - -m 20 -H 'Accept: text/html' -H 'Accept-Encoding: gzip, br, zstd' \
        -H "User-Agent: $ua" "https://$H$caminho" | tr -d '\r')"
    guarda="$(grep -i '^cache-control:' <<< "$cab")"
    cod="$(grep -i '^content-encoding:' <<< "$cab" | tr 'A-Z' 'a-z')"
    csp="$(grep -i '^content-security-policy:' <<< "$cab")"
    if [[ "$guarda" != *no-transform* ]]; then
        printf 'FALHOU %-28s      sem no-transform no Cache-Control (%s)\n' "$rotulo" "${guarda:-nenhum}"
        FALHAS=$((FALHAS + 1))
    elif [[ "$modo" == gzip && "$cod" != *gzip* ]]; then
        printf 'FALHOU %-28s      esperado o gzip da origem; veio %s\n' "$rotulo" "${cod:-sem compressao}"
        FALHAS=$((FALHAS + 1))
    elif [[ "$modo" == csp && ( "$csp" != *"script-src 'self'"* || "$csp" != *"frame-ancestors 'none'"* ) ]]; then
        # Report-Only ou nada: `server.csp_do_painel` fora de `aplicar` no config.yaml do central.
        printf 'FALHOU %-28s      sem a CSP do painel (server.csp_do_painel em aplicar?)\n' "$rotulo"
        FALHAS=$((FALHAS + 1))
    else
        printf 'ok     %-28s      (no-transform%s)\n' "$rotulo" "$([[ "$modo" == gzip ]] && echo ', gzip da origem' || echo ', CSP do painel')"
    fi
}

echo "prova de fora de https://$H  modo=$MODO  $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Vale nos dois modos: o canal do worker nunca sai pelo tunel (regra do ingress), e http vira https na borda.
confere /api/worker/ws      "404" "canal do worker: 404 no ingress"
confere /api/worker/midia   "404" "canal de midia do worker: 404 no ingress"
veio_http="$(curl -q -s -o /dev/null -m 20 -w '%{http_code}' "http://$H/central/")"
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
    # 29.91: o painel nao tinha CSP, e a borda injetou nele o beacon do Web Analytics em 05/10.
    confere_como_navegador /central/ "/central/ (como navegador)"
    confere_html_intocado /central/ "/central/ (sem transformar)" csp
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
        robots="$(curl -q -s -m 20 "https://$H/robots.txt" | tr -d '\r')"
        if [[ "$robots" == *"Disallow: /central/"* && "$robots" == *"Disallow: /api/"* ]]; then
            printf 'ok     %-28s      (barra /central/ e /api/)\n' "/robots.txt (corpo)"
        else
            printf 'FALHOU %-28s      esperado Disallow de /central/ e /api/\n' "/robots.txt (corpo)"; FALHAS=$((FALHAS + 1))
        fi
        cabecalhos="$(curl -q -s -o /dev/null -D - -m 20 "https://$H/" | tr -d '\r')"
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
        confere_como_navegador / "/ (como navegador)"
        confere_html_intocado / "/ (sem transformar)" gzip
        confere_versao_dos_arquivos
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
    corpo="$(curl -q -s -m 20 "https://$H/api/session" | tr -d ' \n\r')"
    if [[ "$corpo" == *'"token_required":true'* && "$corpo" == *'"operator":null'* ]]; then
        printf 'ok     %-28s      (pede senha e nao ha operador)\n' "/api/session (corpo)"
    else
        printf 'FALHOU %-28s      esperado token_required true e operator null\n' "/api/session (corpo)"
        FALHAS=$((FALHAS + 1))
    fi
    # Para onde a raiz manda (ou /central, com o site no ar): caminho relativo ao proprio endereco, nunca 127.0.0.1.
    entrada="/"; [[ "${SITE:-}" == "ligado" ]] && entrada="/central"
    destino="$(curl -q -s -o /dev/null -m 20 -w '%{redirect_url}' "https://$H$entrada")"
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
        # A isca de novo, agora lendo os cabecalhos: a resposta do contato tambem nao poe cookie (ADR-075).
        cab_isca="$(curl -q -s -o /dev/null -D - -m 20 -X POST -H "Content-Type: application/json" -H "Origin: https://$H" \
            -d '{"nome":"prova","telefone":"00000000","mensagem":"prova","consentimento":true,"site":"isca","token":""}' \
            "https://$H/api/portal/contato" | tr -d '\r')"
        if grep -qi '^set-cookie:' <<< "$cab_isca"; then
            printf 'FALHOU %-28s      a resposta pos cookie; a pagina promete que nao usa\n' "POST isca (sem cookie)"; FALHAS=$((FALHAS + 1))
        else
            printf 'ok     %-28s      (nenhum Set-Cookie)\n' "POST isca (sem cookie)"
        fi
        confere /api/portal/contato "415" "contato do site: tipo errado recusado antes de ler o corpo" \
            -X POST -H "Content-Type: text/plain" -H "Origin: https://$H" -d 'site=isca'
        grande="$(printf '%*s' 9000 '' | tr ' ' 'a')"
        confere /api/portal/contato "413" "contato do site: corpo acima do teto" \
            -X POST -H "Content-Type: application/json" -H "Origin: https://$H" -d "{\"site\":\"$grande\"}"
    else
        confere /api/portal/contato "401 404" "contato do site: fechado (401 antes do 29.77, 404 desligado)" \
            -X POST -H "Content-Type: application/json" -H "Origin: https://$H" -d '{"site":"isca"}'
    fi
    # Exclusao a pedido do titular (29.83): so uma pessoa na sessao; de fora, sem credencial, 401. O corpo e de
    # proposito INVALIDO (ids vazio, telefone curto): se o portao falhasse, a rota responderia 422 e nada seria apagado.
    confere /api/portal/contatos/busca   "401" "exclusao (29.83): busca so com sessao" \
        -X POST -H "Content-Type: application/json" -H "Origin: https://$H" -d '{"telefone":"0"}'
    confere /api/portal/contatos/excluir "401" "exclusao (29.83): excluir so com sessao" \
        -X POST -H "Content-Type: application/json" -H "Origin: https://$H" -d '{"ids":[],"pedido_por":""}'
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
