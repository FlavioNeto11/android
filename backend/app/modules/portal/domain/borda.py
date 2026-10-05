"""A régua da borda (29.97): o que conta como defeito quando o site e o painel são pedidos pelo nome público, como um
visitante. Puro, só biblioteca padrão.

Uma régua só para os dois que conferem a borda, para não divergirem:
- o vigia do central (`application/vigia.py`), de hora em hora;
- a prova de fora (`scripts/portal-prova-de-fora.sh`), que baixa com `curl` e passa cabeçalhos e corpo a
  `scripts/portal-regua-da-borda.py`. Esse script carrega ESTE arquivo pelo caminho, sem o venv e sem o app; por isso
  aqui nada importa de `app`.

Três desfechos, e o terceiro não é nem defeito nem sucesso: `sem_conferir` é a rede, o tempo esgotado ou a borda sem
alcançar o central (520 a 526, 530). Os defeitos nasceram de medidas de 05/10: o beacon do Web Analytics injetado na
raiz e no painel (29.85, 29.91), e o CSS e o JS guardados por 4 h com o endereço sem versão (29.95).
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import unquote

OK, DEFEITO, SEM_CONFERIR = "ok", "defeito", "sem_conferir"

PAGINA_FORA = "pagina_fora"
SCRIPT_INJETADO = "script_injetado"
HTML_TRANSFORMADO = "html_transformado"
CSP_AUSENTE = "csp_ausente"
COOKIE = "cookie"
VERSAO_DIVERGENTE = "versao_divergente"

#: A borda sem alcançar o central (o túnel): não diz nada sobre a página. O 0 é o `curl` sem resposta nenhuma.
#: 502 e 504 vêm do túnel (`cloudflared`) sem alcançar a origem: disponibilidade, não configuração da borda, que é o
#: que o vigia confere (decisão da orquestradora, 05/10 04:58Z).
STATUS_SEM_CONFERIR = frozenset({0, 502, 504, 520, 521, 522, 523, 524, 525, 526, 530})
#: Teto do que vai ao aviso do dono num `item` (um `src` `data:` pode ser enorme).
ITEM_MAX = 120
#: O pedido aceita os três: se a borda respeita o `no-transform`, a raiz chega no gzip que a ORIGEM fez.
ACEITA = "gzip, br, zstd"
#: A borda só injeta o beacon quando o pedido parece de navegador (medido em 05/10).
NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 "
             "Safari/537.36")

#: Onde se desfaz cada defeito. É o gesto da prova de fora e do aviso ao dono; nunca afrouxar a CSP.
GESTOS: Mapping[str, str] = {
    SCRIPT_INJETADO: (
        "Desligue na Cloudflare, na zona do nome público, o recurso que injeta: cloudflareinsights = Web Analytics / "
        "Real User Measurements (RUM); /cdn-cgi/scripts = Rocket Loader (Speed > Optimization); "
        "/cdn-cgi/challenge-platform = desafio JS / Bot Fight Mode; /cdn-cgi/l/email-protection = Email Address "
        "Obfuscation (Scrape Shield). Não afrouxe a CSP: a página promete que não usa rastreadores."),
    HTML_TRANSFORMADO: (
        "Confira na zona as Transform Rules e as Compression Rules; se nada mudou lá, pode ser do deploy: diga no chat "
        "da orquestradora."),
    CSP_AUSENTE: (
        "Confira as Transform Rules da zona (podem tirar cabeçalhos); no painel, confira também server.csp_do_painel no "
        "config.yaml do central."),
    COOKIE: "Desligue Bot Fight Mode na zona, ou mude o texto da página, que promete não usar cookies.",
    VERSAO_DIVERGENTE: (
        "Ou a borda guarda sem olhar a query (Caching Level em 'Ignore query string'; o certo é Standard), ou serviu "
        "cópia velha, ou ALTEROU o arquivo no caminho (minificação automática de CSS/JS, Rocket Loader)."),
    PAGINA_FORA: "Sem ver a página não há o que conferir: desafio da Cloudflare? Veja Security > Bots e o WAF da zona.",
}

_SCRIPT_SRC = re.compile(r"""<script\b[^>]*?\ssrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)
_VERSAO = re.compile(r'\s(?:href|src)="(/assets/site\.(?:css|js))\?v=([0-9a-f]+)"')


@dataclass(frozen=True, slots=True)
class Achado:
    """`codigo` é um dos códigos acima; `onde` é o caminho conferido (`/`, `/central/`, `/assets/site.css`);
    `detalhe` é curto e sem dado de ninguém (nenhum IP, nenhuma query: o beacon leva token na query). `item` é o que
    vai ao aviso do dono (contrato da Canais, 29.97): host e caminho do script, ou o NOME do cookie (nunca o valor)."""

    codigo: str
    onde: str
    detalhe: str
    item: str = ""


@dataclass(frozen=True, slots=True)
class Desfecho:
    estado: str
    achados: tuple[Achado, ...] = ()
    motivo: str = ""           # de `sem_conferir`: "tempo esgotado", "rede", "borda 522"


def sem_conferir(motivo: str) -> Desfecho:
    return Desfecho(SEM_CONFERIR, (), motivo)


def _defeito(*achados: Achado) -> Desfecho:
    return Desfecho(DEFEITO, achados) if achados else Desfecho(OK)


def _sem_query(endereco: str) -> str:
    return endereco.split("?", 1)[0].split("#", 1)[0]


def scripts_de_fora(html: str, host: str) -> list[str]:
    """Os `<script src>` que a página não tem: de outra origem, ou da Cloudflare na própria origem (`/cdn-cgi/`).
    Relativo e o próprio nome (em qualquer caixa) passam; o esquema só conta antes do primeiro `/`, `?` ou `#`
    (`/assets/site.js?v=T01:00` é relativo). Volta sem a query. Script da Cloudflare embutido, sem `src`, vira
    `(embutido)`."""
    proprio = host.lower()
    achados: list[str] = []
    for m in _SCRIPT_SRC.finditer(html):
        src = next(g for g in m.groups() if g is not None).strip()
        # Como o navegador lê: tabulação e quebra de linha somem, e `\` vale `/` (`\\outro/x.js` é `//outro/x.js`).
        baixo = re.sub(r"[\t\n\r]", "", src).replace("\\", "/").lower()
        esquema = re.split(r"[/?#]", baixo, maxsplit=1)[0]
        if "/cdn-cgi/" in baixo:
            de_fora = True
        elif baixo.startswith("//") or esquema in ("http:", "https:"):
            de_fora = baixo.split("//", 1)[-1].split("/", 1)[0] != proprio
        else:
            de_fora = ":" in esquema                       # data:, javascript:, esquema desconhecido
        if de_fora:
            achados.append(_sem_query(src))
    texto = html.lower()
    if not achados and ("cloudflareinsights" in texto or "/cdn-cgi/" in texto):
        achados.append("(embutido)")
    return achados


def cabecalho(cabecalhos: Mapping[str, str], nome: str) -> str:
    """O valor do cabeçalho `nome`, sem diferença de caixa; vazio quando falta."""
    for chave, valor in cabecalhos.items():
        if chave.lower() == nome:
            return valor.strip()
    return ""


def conferir_html(onde: str, status: int, html: str, *, host: str) -> Desfecho:
    """A página pedida como navegador: 200, com corpo, e sem script que ela não tem."""
    if status in STATUS_SEM_CONFERIR:
        return sem_conferir("sem resposta" if status == 0 else f"borda {status}")
    if status != 200:
        return _defeito(Achado(PAGINA_FORA, onde, f"status {status}"))
    if not html.strip():
        return _defeito(Achado(PAGINA_FORA, onde, "corpo vazio pedido como navegador"))
    return _defeito(*(Achado(SCRIPT_INJETADO, onde, detalhe, item)
                      for item, detalhe in map(endereco_do_script, scripts_de_fora(html, host))))


#: Os esquemas que saem pelo nome. Outro texto antes do `:` sai como `esquema`: pode ser `usuario:senha@…` (Q1).
ESQUEMAS_CONHECIDOS = frozenset({"data", "javascript", "blob", "about", "file", "ftp", "ws", "wss"})
_ESQUEMA = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
#: IPv4 em qualquer forma que o navegador aceita (decimal, hexa, com menos de 4 partes).
_IPV4 = re.compile(r"(?:0x[0-9a-f]*|\d+)(?:\.(?:0x[0-9a-f]*|\d+)){0,3}\.?")
_FORA_DO_ALFABETO = re.compile(r"[^A-Za-z0-9._/-]")    # o filtro da Canais
_PONTAS = "".join(map(chr, range(33)))                   # controle e espaço, que o navegador tira das pontas
#: O que PODE entrar no detalhe do script: só ASCII imprimível, sem espaço. Lista do permitido, não do proibido: uma
#: quebra de linha de terceiro (U1), o NEL (U+0085), o separador de linha (U+2028), o C1 (U+009B) ou a inversão
#: bidirecional (U+202E), que vira o texto na tela do painel, viram `?` sem depender de tabela de categoria (V1 da
#: leitura do #378). Host com letra fora do ASCII aparece com `?`, e isso é aceitável.
_FORA_DO_DETALHE = re.compile(r"[^\x21-\x7e]")
#: Na linha da saúde, que mistura frase nossa com valor de terceiro: ASCII imprimível com o espaço, e as letras
#: acentuadas do Latin-1 (U+00C0 a U+00FF, sem × e ÷), para o português da frase não virar `?`.
_FORA_DA_LINHA = re.compile(r"[^\x20-\x7eÀ-ÖØ-öø-ÿ]")


def _sem_controle(texto: str) -> str:
    return _FORA_DO_DETALHE.sub("?", texto)


def linha_sem_controle(texto: str) -> str:
    """Para o texto que mistura frase nossa com valor de terceiro (cabeçalho, nome de cookie) e vai a uma linha da
    saúde ou da prova: só o permitido fica (ASCII imprimível, espaço e letra acentuada), o resto vira `?`."""
    return _FORA_DA_LINHA.sub("?", texto)


def item_do_script(src: str) -> str:
    return endereco_do_script(src)[0]


def _no_alfabeto(texto: str) -> str:
    """Corta no 1º caractere fora do alfabeto da Canais: tirar só o caractere colaria os dois lados (Q1)."""
    return _FORA_DO_ALFABETO.split(texto, maxsplit=1)[0][:ITEM_MAX]


def endereco_do_script(src: str) -> tuple[str, str]:
    """`(item, detalhe)` de um script de fora, lido como o navegador lê (tabulação e quebra somem, `\\` vale `/`, as
    pontas perdem controle e espaço, e `http(s):` ignora quantas barras vierem).

    - `item` vai à Canais no `achado`: só host e caminho, sem query, fragmento, `;…`, credencial nem porta, CORTADO no
      1º caractere fora do alfabeto do filtro dela (`[A-Za-z0-9._/-]`), nunca colado, com teto. Segredo em segmento de
      caminho passa: é o contrato declarado com a Canais.
    - `detalhe` vai à saúde e à linha FALHOU da prova: esquema, host, porta e caminho, sem credencial (Q4), com teto.
    - Host de IP vira `ip` nos dois (Q3); autoridade com porta que não é número (senha com `/`, `?`, `;` ou `#` sem
      codificar, Q2) vira `url-invalida`, e o navegador nem carregaria o script."""
    if src == "(embutido)":
        return "embutido", src
    # Antes da autoridade, só `?` e `#` cortam: para o navegador ela termina em `/`, `?` ou `#`, e um `;` dentro dela é
    # do userinfo (`https://usuario;sessao@cdn/x.js` é host `cdn`, R1). O `;` corta só o caminho, depois.
    limpo = re.split(r"[?#]", re.sub(r"[\t\n\r]", "", src).replace("\\", "/").strip(_PONTAS), maxsplit=1)[0]
    m = _ESQUEMA.match(limpo)
    esquema = m.group(1).lower() if m else ""
    if limpo.startswith("//"):
        resto, prefixo = limpo.lstrip("/"), "//"
    elif m and esquema in ("http", "https"):
        resto, prefixo = limpo[m.end():].lstrip("/"), f"{esquema}://"
    elif m:
        nome = esquema if esquema in ESQUEMAS_CONHECIDOS else "esquema"
        # `data:` e `javascript:` mostram o próprio script na saúde (é o que foi injetado); o resto, só o nome.
        return nome, (_sem_controle(src[:ITEM_MAX]) if nome in ("data", "javascript") else f"{nome}:...")
    else:                                                 # relativo: `/cdn-cgi/…`, `a/b:c.js`
        limpo = limpo.split(";", 1)[0]
        if ":" in limpo.split("/", 1)[0]:
            # `ht tps://usuario:senha@…` e `1usuario:senha@…` não são esquema para o navegador: são caminho relativo,
            # mas o 1º segmento com `:` tem cara de credencial. Nada dele sai (R2).
            return "relativo", '(relativo-com-":")'
        return _no_alfabeto(limpo), _sem_controle(limpo[:ITEM_MAX])
    autoridade, barra, caminho = resto.partition("/")
    caminho = caminho.split(";", 1)[0]
    # O userinfo inteiro, até o ÚLTIMO `@` da autoridade (Q2). O host decodificado serve SÓ para decidir se é IP
    # (`%31%30.0.0.5`, R3); o detalhe sai com o host cru, porque `%0d%0a` decodificado é quebra de linha (U1) e
    # `usuario%40cdn` teria cara de userinfo (U2).
    cru = autoridade.rsplit("@", 1)[-1].lower()
    nome = unquote(cru)
    if nome.startswith("["):
        host, porta = "ip", nome.partition("]")[2].removeprefix(":")
    else:
        host, _, porta = cru.partition(":")
        host = "ip" if _IPV4.fullmatch(unquote(host).split(":", 1)[0]) else host
    if porta and not porta.isdigit():
        return "url-invalida", prefixo + "(endereco-invalido)"
    detalhe = prefixo + host + (f":{porta}" if porta else "") + barra + caminho
    return _no_alfabeto(host + barra + caminho), _sem_controle(detalhe[:ITEM_MAX])


def conferir_cabecalhos(onde: str, status: int, cabecalhos: Mapping[str, str], *, sem_transformar: bool = False,
                        gzip_da_origem: bool = False, csp: str | None = None, sem_cookie: bool = False) -> Desfecho:
    """Os cabeçalhos da mesma página. Cada bandeira liga uma conferência:
    - `sem_transformar`: `no-transform` no `Cache-Control` (29.91);
    - `gzip_da_origem`: `Content-Encoding: gzip` com o pedido aceitando gzip, br e zstd (br ou zstd: a borda abriu o
      corpo);
    - `csp`: `site` (a do site), `aplicar` ou `so_relatar` (a do painel, conforme `server.csp_do_painel`);
    - `sem_cookie`: nenhum `Set-Cookie` (a página promete que não usa)."""
    if status in STATUS_SEM_CONFERIR:
        return sem_conferir("sem resposta" if status == 0 else f"borda {status}")
    achados: list[Achado] = []
    if sem_transformar:
        guarda = cabecalho(cabecalhos, "cache-control")
        if "no-transform" not in guarda.lower():
            achados.append(Achado(HTML_TRANSFORMADO, onde, f"sem no-transform no Cache-Control ({guarda or 'nenhum'})"))
    if gzip_da_origem:
        codificacao = cabecalho(cabecalhos, "content-encoding").lower()
        if "gzip" not in codificacao:
            achados.append(Achado(HTML_TRANSFORMADO, onde,
                                  f"esperado o gzip da origem; veio {codificacao or 'sem compressão'}"))
    if csp is not None and not _csp_ok(cabecalhos, csp):
        quem = "do site" if csp == "site" else f"do painel (server.csp_do_painel em {csp}?)"
        achados.append(Achado(CSP_AUSENTE, onde, f"sem a CSP {quem}"))
    if sem_cookie and cabecalho(cabecalhos, "set-cookie"):
        nome = cabecalho(cabecalhos, "set-cookie").split("=", 1)[0].strip()
        achados.append(Achado(COOKIE, onde, f"a resposta pôs cookie ({nome}); a página promete que não usa", nome))
    return _defeito(*achados)


def corpo_recomprimido(onde: str, codificacao: str) -> Desfecho:
    """A página veio em br ou zstd e o corpo não se lê: a borda abriu e recomprimiu o HTML (V4 da leitura). No painel,
    que a origem não comprime, é o único sinal; na raiz o `gzip_da_origem` já acusa o mesmo."""
    return _defeito(Achado(HTML_TRANSFORMADO, onde, f"corpo recomprimido pela borda ({codificacao})"))


def _csp_ok(cabecalhos: Mapping[str, str], modo: str) -> bool:
    """`aplicar` e `site` exigem o cabeçalho que barra; `so_relatar`, o Report-Only (a subida do 29.91 no central)."""
    nome = "content-security-policy-report-only" if modo == "so_relatar" else "content-security-policy"
    politica = cabecalho(cabecalhos, nome)
    return "script-src 'self'" in politica and "frame-ancestors 'none'" in politica


def versoes_pedidas(html: str) -> dict[str, str]:
    """`{"/assets/site.css": "<12 hex>", "/assets/site.js": …}` como a raiz os aponta (29.95)."""
    versoes: dict[str, str] = {}
    for caminho, versao in _VERSAO.findall(html):
        versoes.setdefault(caminho, versao)                  # o 1º, como a prova antiga (`head -1`)
    return versoes


def conferir_versao(onde: str, versao: str | None, status: int | None = None, corpo: bytes | None = None) -> Desfecho:
    """O arquivo baixado pelo endereço que a página aponta tem o sha256 que a versão diz. `versao` `None`: a página
    não aponta o arquivo com `?v=` (o navegador guardaria o velho por 4 h)."""
    if not versao:
        return _defeito(Achado(VERSAO_DIVERGENTE, onde, "a página aponta o arquivo sem ?v= (o navegador guarda o velho "
                                                        "por 4 h)"))
    if status in STATUS_SEM_CONFERIR:
        return sem_conferir("sem resposta" if status == 0 else f"borda {status}")
    if (status is not None and status != 200) or corpo is None:      # `None`: a prova de fora não lê o status
        return _defeito(Achado(VERSAO_DIVERGENTE, onde, f"pedido por ?v={versao}, veio status {status}"))
    veio = hashlib.sha256(corpo).hexdigest()[:len(versao)]
    if veio != versao:
        return _defeito(Achado(VERSAO_DIVERGENTE, onde, f"a página pede ?v={versao} e a borda entregou {veio}"))
    return Desfecho(OK)


def juntar(desfechos: Iterable[Desfecho]) -> Desfecho:
    """A volta inteira: defeito se algum achou defeito (os achados juntos); senão sem_conferir se algum não conferiu;
    senão ok."""
    lista = list(desfechos)
    achados = tuple(a for d in lista for a in d.achados)
    if achados:
        return Desfecho(DEFEITO, achados)
    motivos = [d.motivo for d in lista if d.estado == SEM_CONFERIR]
    if motivos:
        return Desfecho(SEM_CONFERIR, (), ", ".join(dict.fromkeys(motivos)))
    return Desfecho(OK)
