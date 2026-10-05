"""A régua da borda (29.97): o que conta como defeito quando o site e o painel são pedidos pelo nome público, como um
visitante. Puro, só biblioteca padrão.

Uma régua só para os dois que conferem a borda, para não divergirem:
- o vigia do central (`application/vigia.py`), de hora em hora;
- a prova de fora (`scripts/portal-prova-de-fora.sh`), que baixa com `curl` e passa cabeçalhos e corpo a
  `scripts/portal-regua-da-borda.py`. Esse script carrega ESTE arquivo pelo caminho, sem o venv e sem o app; por isso
  aqui nada importa de `app`.

Três desfechos, e o terceiro não é nem defeito nem sucesso: `sem_conferir` é a rede, o tempo esgotado ou a borda sem
alcançar o central (520 a 526, 530). Os defeitos nasceram de medidas de 05/10: o beacon do Web Analytics injetado na
raiz e no painel (29.85, 29.91), e o CSS e o JS guardados por 4 h com o endereço sem versão (29.95). O 29.101 junta o
defeito mais grave do endereço público: a API respondendo sem credencial.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

OK, DEFEITO, SEM_CONFERIR = "ok", "defeito", "sem_conferir"

PAGINA_FORA = "pagina_fora"
SCRIPT_INJETADO = "script_injetado"
HTML_TRANSFORMADO = "html_transformado"
CSP_AUSENTE = "csp_ausente"
COOKIE = "cookie"
VERSAO_DIVERGENTE = "versao_divergente"
API_ABERTA = "api_aberta"

#: O que o vigia pede SEM credencial para saber se a API fecha para fora (29.101): a lista de aparelhos, que é ler e
#: mexer no central. O mesmo texto vai no `achado` do aviso (contrato da Canais).
CAMINHO_DA_API = "/api/instances"
#: As recusas certas: 401 do portão do central (`security/access.py`, nome público sem credencial) ou 403 de uma
#: camada à frente dele.
API_RECUSOU = frozenset({401, 403})

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
    API_ABERTA: (
        "A API do central respondeu SEM credencial pelo endereço público: tire o nome público do ar já (pare o túnel ou "
        "a rota dele) e confira server.public_hosts e o token da API no config.yaml do central. O vigia só avisa; nada "
        "foi parado."),
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
    # O detalhe vai à saúde e à linha da prova: um `data:` de 1 MB não pode aparecer inteiro (N1 da leitura do #378).
    return _defeito(*(Achado(SCRIPT_INJETADO, onde, src[:ITEM_MAX], item_do_script(src))
                      for src in scripts_de_fora(html, host)))


def item_do_script(src: str) -> str:
    """O que vai à Canais no `achado`: só host e caminho, sem query, fragmento, credencial nem porta, no alfabeto do
    filtro dela (`[A-Za-z0-9._/-]`), com teto. O filtro não reconhece segredo em segmento de caminho, então o corte é
    aqui. `data:` e `javascript:` vão só pelo esquema; o script embutido, como `embutido`."""
    if src == "(embutido)":
        return "embutido"
    limpo = _sem_query(re.sub(r"[\t\n\r]", "", src).replace("\\", "/"))
    esquema = re.split(r"[/?#]", limpo.lower(), maxsplit=1)[0]
    if limpo.startswith("//") or esquema in ("http:", "https:"):
        autoridade, _, caminho = limpo.split("//", 1)[-1].partition("/")
        nome = autoridade.rsplit("@", 1)[-1].split(":", 1)[0]
        limpo = nome + ("/" + caminho if caminho else "")
    elif ":" in esquema:
        limpo = esquema.split(":", 1)[0]
    return re.sub(r"[^A-Za-z0-9._/-]", "", limpo)[:ITEM_MAX]


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


def conferir_api(status: int) -> Desfecho:
    """A API pedida pelo nome público sem credencial (29.101). Recusa (401, 403) é ok; 2xx é a API aberta para a
    internet. O túnel sem alcançar a origem não diz nada. Outro status (3xx, 404, 500) também não prova nem um nem
    outro: vira sem_conferir com o status, que avisa depois de N voltas em vez de gritar crítico à toa."""
    if status in API_RECUSOU:
        return Desfecho(OK)
    if status in STATUS_SEM_CONFERIR:
        return sem_conferir("sem resposta" if status == 0 else f"borda {status}")
    if 200 <= status < 300:
        return _defeito(Achado(API_ABERTA, CAMINHO_DA_API, f"status {status} sem credencial", CAMINHO_DA_API))
    return sem_conferir(f"api {status}")


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
