"""O vigia da borda (29.97, e a API pedida sem credencial do 29.101) e a régua comum com a prova de fora.

Prova `simulated`: a régua pura, a volta com `httpx.MockTransport` imitando a borda (o mesmo catálogo de defeitos do
`curl` falso de `scripts/tests/test_portal_prova_de_fora.py`), o estado entre voltas, o aviso uma vez por código e por
dia, e a saúde lendo só o estado. Nenhum pedido sai da máquina: o nome público é `.invalid` e todo transporte é falso.
"""
from __future__ import annotations

import gzip
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.modules.portal.application.vigia import Resposta, SemResposta, Vigia, uma_volta
from app.modules.portal.domain import borda
from app.modules.portal.adapters.borda_http import BuscarPelaBorda

from .conftest import Harness

HOST = "site.exemplo.invalid"
CSS, JS = b"body{color:#91a2b3}", b"console.log(1)"
VCSS, VJS = hashlib.sha256(CSS).hexdigest()[:12], hashlib.sha256(JS).hexdigest()[:12]
CSP_SITE = "default-src 'self'; script-src 'self'; frame-ancestors 'none'"
AGORA = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def _raiz(extra: str = "", *, versao_css: str = VCSS) -> bytes:
    return (f'<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css?v={versao_css}">'
            f'<script src="/assets/site.js?v={VJS}" defer></script></head><body>{extra}</body></html>').encode()


def _borda(quebra: str = "") -> httpx.MockTransport:
    """A borda como ela tem de responder, com um defeito por `quebra`. Registra os pedidos em `.pedidos`."""
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        caminho = pedido.url.path
        if quebra == "timeout":
            raise httpx.ReadTimeout("tempo", request=pedido)
        if quebra == "rede":
            raise httpx.ConnectError("sem rota", request=pedido)
        if quebra == "tunel":
            return httpx.Response(522, text="Connection timed out")
        if quebra in ("502", "504"):
            return httpx.Response(int(quebra), text="Bad gateway")
        if caminho == borda.CAMINHO_DA_API:
            if quebra == "api_aberta":
                return httpx.Response(200, json=[{"serial": "emulador-segredo"}])
            if quebra == "api_404":
                return httpx.Response(404)
            return httpx.Response(401, json={"detail": "unauthorized"})
        if caminho == "/central/":
            corpo = b'<!doctype html><script type="module" src="/central/assets/index-abc.js"></script><div id="root">'
            if quebra == "beacon_no_painel":
                corpo += b"<script defer src='https://static.cloudflareinsights.com/beacon.min.js?token=x'></script>"
            cab = {"cache-control": "no-cache, must-revalidate, no-transform", "content-security-policy": CSP_SITE}
            if quebra == "painel_recomprimido":
                cab["content-encoding"] = "br"
                return httpx.Response(200, headers=cab, content=b"\x8b\x00corpo-br")
            if quebra == "painel_so_relata":
                cab = {"cache-control": "no-cache, must-revalidate, no-transform",
                       "content-security-policy-report-only": CSP_SITE}
            return httpx.Response(200, headers=cab, content=corpo)
        if caminho == "/":
            if quebra == "desafio":
                # O desafio não tem no-transform, CSP nem gzip, e traz o cookie da borda (V1).
                return httpx.Response(403, headers={"set-cookie": "__cf_bm=v; Path=/"},
                                      text="<title>Just a moment...</title>")
            extra = ""
            if quebra == "beacon":
                extra = "<script defer src='https://static.cloudflareinsights.com/beacon.min.js'></script>"
            if quebra == "rocket":
                extra = '<script src="/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js"></script>'
            corpo = _raiz(extra, versao_css="0" * 12 if quebra == "versao_velha" else VCSS)
            cab = {"cache-control": "no-store, no-transform", "content-security-policy": CSP_SITE,
                   "content-encoding": "gzip"}
            if quebra == "transformado":
                cab["cache-control"] = "no-store"
            if quebra == "cookie":
                cab["set-cookie"] = "__cf_bm=valor-secreto; Path=/"
            if quebra == "recomprimido":
                cab["content-encoding"] = "br"
                return httpx.Response(200, headers=cab, content=b"\x8b\x00corpo-br")
            return httpx.Response(200, headers=cab, content=gzip.compress(corpo))
        if caminho == "/assets/site.css":
            return httpx.Response(200, content=CSS)
        if caminho == "/assets/site.js":
            return httpx.Response(200, content=JS)
        return httpx.Response(404)

    transporte = httpx.MockTransport(responder)
    transporte.pedidos = pedidos  # type: ignore[attr-defined]
    return transporte


def _volta(quebra: str = "", *, site: bool = True, csp: str = "aplicar") -> tuple[borda.Desfecho, list[httpx.Request]]:
    transporte = _borda(quebra)
    d = uma_volta(BuscarPelaBorda(lambda: 5, transporte), host=HOST, site_ligado=site, csp_do_painel=csp)
    return d, transporte.pedidos  # type: ignore[attr-defined]


# ------------------------------------------------------------------ a régua e a volta
def test_volta_limpa_e_ok_com_no_maximo_cinco_get_sem_credencial() -> None:
    d, pedidos = _volta()
    assert d.estado == borda.OK, d
    assert [p.url.path for p in pedidos] == ["/api/instances", "/central/", "/", "/assets/site.css", "/assets/site.js"]
    for p in pedidos:
        assert p.method == "GET" and p.url.host == HOST and p.url.scheme == "https"
        assert "authorization" not in p.headers and "cookie" not in p.headers
        assert "Mozilla/5.0" in p.headers["user-agent"]                   # a borda só injeta para navegador
    assert pedidos[2].headers["accept-encoding"] == borda.ACEITA
    assert pedidos[3].url.query == f"v={VCSS}".encode()


def test_site_desligado_confere_so_o_painel() -> None:
    d, pedidos = _volta(site=False)
    assert d.estado == borda.OK and [p.url.path for p in pedidos] == ["/api/instances", "/central/"]


@pytest.mark.parametrize(("quebra", "codigo", "onde", "item"), [
    ("beacon", borda.SCRIPT_INJETADO, "/", "static.cloudflareinsights.com/beacon.min.js"),
    ("beacon_no_painel", borda.SCRIPT_INJETADO, "/central/", "static.cloudflareinsights.com/beacon.min.js"),
    ("rocket", borda.SCRIPT_INJETADO, "/", "/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js"),
    ("transformado", borda.HTML_TRANSFORMADO, "/", ""),
    ("recomprimido", borda.HTML_TRANSFORMADO, "/", ""),
    ("cookie", borda.COOKIE, "/", "__cf_bm"),
    ("versao_velha", borda.VERSAO_DIVERGENTE, "/assets/site.css", ""),
    ("desafio", borda.PAGINA_FORA, "/", ""),
    ("painel_so_relata", borda.CSP_AUSENTE, "/central/", ""),
    ("api_aberta", borda.API_ABERTA, "/api/instances", "/api/instances"),
])
def test_cada_defeito_da_borda_tem_codigo_lugar_e_nenhum_segredo(quebra: str, codigo: str, onde: str,
                                                                  item: str) -> None:
    d, _ = _volta(quebra)
    assert d.estado == borda.DEFEITO, d
    achado = next(a for a in d.achados if a.codigo == codigo)
    if quebra in ("desafio", "transformado", "cookie", "painel_so_relata", "api_aberta"):
        assert {a.codigo for a in d.achados} == {codigo}, d.achados   # V1: um defeito, um aviso, o gesto certo
    assert achado.onde == onde and achado.item == item
    texto = " ".join(f"{a.detalhe} {a.item}" for a in d.achados)
    assert "token=" not in texto and "valor-secreto" not in texto        # nem query do beacon, nem valor do cookie
    assert "emulador-segredo" not in texto                               # nem dado da API aberta (29.101)


def test_csp_conforme_o_modo_do_painel() -> None:
    """Em `so_relatar` (a subida do 29.91 no central) o Report-Only é o certo; `desligada` não confere."""
    assert _volta("painel_so_relata", csp="so_relatar")[0].estado == borda.OK
    assert _volta("painel_so_relata", csp="desligada")[0].estado == borda.OK
    assert _volta(csp="so_relatar")[0].estado == borda.DEFEITO


@pytest.mark.parametrize(("quebra", "motivo"), [("timeout", "tempo esgotado"), ("rede", "rede"),
                                                ("tunel", "borda 522"), ("502", "borda 502"),
                                                ("504", "borda 504")])
def test_rede_tempo_e_tunel_nao_sao_defeito_nem_sucesso(quebra: str, motivo: str) -> None:
    d, pedidos = _volta(quebra)
    assert d.estado == borda.SEM_CONFERIR and motivo in d.motivo and not d.achados
    assert len(pedidos) <= 3                                             # sem retry nem rajada: API, painel, raiz


def test_scripts_de_fora_aceita_o_proprio_e_relativo() -> None:
    html = ('<script src="/assets/site.js?v=T01:00"></script><script src="HTTPS://SITE.EXEMPLO.INVALID/a.js"></script>'
            '<script src="//site.exemplo.invalid/b.js"></script><SCRIPT SRC = "https://cdn.invalid/x.js?src=b">'
            "<script src='data:text/javascript,1'></script>")
    assert borda.scripts_de_fora(html, HOST) == ["https://cdn.invalid/x.js", "data:text/javascript,1"]
    # V2: o navegador lê `\` como `/` e ignora tabulação: os três são `//outro.invalid/x.js`, script de fora.
    for disfarce in ("\\\\outro.invalid/x.js", "/\\outro.invalid/x.js", "/\t/outro.invalid/x.js"):
        assert borda.scripts_de_fora(f'<script src="{disfarce}"></script>', HOST) == [disfarce], disfarce
    assert borda.scripts_de_fora('<script src="\\\\site.exemplo.invalid/a.js"></script>', HOST) == []
    assert borda.scripts_de_fora("<script>a='/cdn-cgi/challenge-platform/x'</script>", HOST) == ["(embutido)"]


# ------------------------------------------------------------------ o estado entre voltas e o aviso
@dataclass
class Avisado:
    enfileirado: bool = True
    motivo: str | None = None


class CanaisFalsa:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, str, str | None, int | None]] = []
        self.responde = Avisado()

    def __call__(self, codigo: str, onde: str, agora: datetime, *, achado: str | None = None,
                 horas_sem_conferir: int | None = None) -> Avisado:
        self.chamadas.append((codigo, onde, achado, horas_sem_conferir))
        return self.responde


def _vigia(quebra: Callable[[], str], canais: CanaisFalsa | None, *, n: int = 3) -> Vigia:
    def buscar(url: str, *, aceita: str | None, ler_corpo: bool = True) -> Resposta:
        return BuscarPelaBorda(lambda: 5, _borda(quebra()))(url, aceita=aceita, ler_corpo=ler_corpo)
    return Vigia(buscar, host=lambda: HOST, site_ligado=lambda: True, csp_do_painel=lambda: "aplicar",
                 voltas_sem_conferir=lambda: n, intervalo_s=lambda: 3600, avisar=lambda: canais)


def test_aviso_so_na_transicao_e_uma_vez_por_dia_por_codigo() -> None:
    estado = {"q": "beacon"}
    canais = CanaisFalsa()
    vigia = _vigia(lambda: estado["q"], canais)
    vigia.volta(AGORA)
    vigia.volta(AGORA + timedelta(hours=1))                              # o mesmo defeito na hora seguinte: nada
    assert canais.chamadas == [("script_injetado", "raiz", "static.cloudflareinsights.com/beacon.min.js", None)]
    vigia.volta(AGORA + timedelta(days=1))                               # persiste no dia seguinte: um por dia
    assert len(canais.chamadas) == 2
    estado["q"] = ""
    assert vigia.volta(AGORA + timedelta(days=1, hours=1)).estado == borda.OK
    estado["q"] = "beacon"
    vigia.volta(AGORA + timedelta(days=1, hours=2))                      # voltou depois de resolvido: avisa de novo
    assert len(canais.chamadas) == 3


def test_canal_que_nao_enfileira_tenta_de_novo_na_volta_seguinte() -> None:
    canais = CanaisFalsa()
    canais.responde = Avisado(False, "canal_desligado")
    vigia = _vigia(lambda: "cookie", canais)
    vigia.volta(AGORA)
    canais.responde = Avisado()
    vigia.volta(AGORA + timedelta(hours=1))
    vigia.volta(AGORA + timedelta(hours=2))
    assert [c[0] for c in canais.chamadas] == ["cookie", "cookie"]
    assert canais.chamadas[-1] == ("cookie", "raiz", "__cf_bm", None)


def test_sem_conferir_so_avisa_na_n_esima_volta_seguida() -> None:
    estado = {"q": "timeout"}
    canais = CanaisFalsa()
    vigia = _vigia(lambda: estado["q"], canais, n=3)
    for h in range(2):
        vigia.volta(AGORA + timedelta(hours=h))
    assert canais.chamadas == [] and vigia.problemas() == []
    vigia.volta(AGORA + timedelta(hours=2))
    assert canais.chamadas == [("sem_conferir", "raiz", "tempo-esgotado", 3)]   # o aviso diz o código
    assert [p[0] for p in vigia.problemas()] == ["portal_borda_sem_conferir"]
    estado["q"] = ""
    vigia.volta(AGORA + timedelta(hours=3))                              # voltou: zera, e a saúde limpa
    assert vigia.seguidas_sem_conferir == 0 and vigia.problemas() == []


def test_sem_a_funcao_da_canais_fica_so_na_saude() -> None:
    vigia = _vigia(lambda: "beacon", None)
    vigia.volta(AGORA)
    [(codigo, mensagem, dica)] = vigia.problemas()
    assert codigo == "portal_borda_defeito" and "static.cloudflareinsights.com" in mensagem
    assert "Web Analytics" in dica and "token" not in mensagem


def test_sem_nome_publico_nao_pede_nada() -> None:
    def buscar(url: str, *, aceita: str | None, ler_corpo: bool = True) -> Resposta:
        raise AssertionError("pediu sem nome público")
    vigia = Vigia(buscar, host=lambda: None, site_ligado=lambda: True, csp_do_painel=lambda: "aplicar",
                  voltas_sem_conferir=lambda: 3, intervalo_s=lambda: 3600, avisar=lambda: None)
    assert vigia.volta(AGORA) is None and vigia.problemas() == []


def test_falha_do_transporte_vira_sem_resposta() -> None:
    with pytest.raises(SemResposta, match="tempo esgotado"):
        BuscarPelaBorda(lambda: 5, _borda("timeout"))(f"https://{HOST}/", aceita=None)


# ------------------------------------------------------------------ montagem, configuração e saúde
async def test_saude_le_so_o_estado_e_o_nome_vem_do_config(harness: Harness) -> None:
    assert harness.state is not None
    portal = harness.state.portal
    harness.cfg.file.server.public_hosts = []
    assert portal.nome_do_vigia() is None                               # sem nome público, nada roda
    harness.cfg.file.server.public_hosts = ["  Site.Exemplo.invalid ", "outro.invalid"]
    assert portal.nome_do_vigia() == "site.exemplo.invalid"             # o 1º, na ordem do config
    harness.cfg.file.server.public_hosts = ["site.invalid:8443"]
    assert portal.nome_do_vigia() is None                               # porta ou esquema: não é só um nome
    harness.cfg.file.server.public_hosts = ["site.exemplo.invalid"]
    harness.cfg.file.portal.vigia.ligado = False
    assert portal.nome_do_vigia() is None
    harness.cfg.file.portal.vigia.ligado = True

    def buscar(url: str, *, aceita: str | None, ler_corpo: bool = True) -> Resposta:
        raise AssertionError("a saúde não pode pedir nada à rede")
    portal.vigia._buscar = buscar  # type: ignore[assignment]
    portal.vigia.seguidas_sem_conferir = 5
    portal.vigia.ultima = borda.sem_conferir("tempo esgotado")
    portal.vigia.quando = AGORA
    codigos = {p.code for p in harness.state.health().problems}
    assert "portal_borda_sem_conferir" in codigos


def test_a_regua_e_o_cli_da_prova_de_fora_rodam_sem_o_venv() -> None:
    """A prova de fora roda com qualquer Python do PATH, sem o venv: a régua e o CLI que a carrega pelo caminho só podem
    importar a biblioteca padrão (e a régua, nada de `app`)."""
    import ast
    import sys
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[2]
    for arquivo in (raiz / "backend/app/modules/portal/domain/borda.py", raiz / "scripts/portal-regua-da-borda.py"):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        nomes = {a.name.split(".")[0] for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names}
        nomes |= {n.module.split(".")[0] for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom) and n.module}
        fora = sorted(n for n in nomes if n not in sys.stdlib_module_names and n != "__future__")
        assert not fora, f"{arquivo.name} importa fora da biblioteca padrão: {fora}"


def test_configuracao_do_vigia_e_estrita() -> None:
    from pydantic import ValidationError

    from app.config import PortalVigiaCfg
    assert PortalVigiaCfg().ligado is True and PortalVigiaCfg().intervalo_s == 3600
    with pytest.raises(ValidationError):
        PortalVigiaCfg(intervalo_s=60)                                   # menos de 10 min viraria rajada
    with pytest.raises(ValidationError):
        PortalVigiaCfg.model_validate({"ligada": True})                 # chave com outro nome recusa


def test_painel_recomprimido_e_html_transformado_e_nao_pagina_fora() -> None:
    """V4: o painel em br não se lê; o defeito é a borda ter aberto o corpo, não a página fora."""
    d, _ = _volta("painel_recomprimido", site=False)
    assert {a.codigo for a in d.achados} == {borda.HTML_TRANSFORMADO}, d.achados


def test_css_e_js_pedidos_so_com_gzip() -> None:
    """V3: um `brotli` que entre no venv não pode virar corpo ilegível e versão divergente falsa."""
    _, pedidos = _volta()
    assert [p.headers["accept-encoding"] for p in pedidos[3:]] == ["gzip", "gzip"]


def test_versao_e_a_primeira_apontada_e_o_item_tem_teto() -> None:
    html = '<link href="/assets/site.css?v=aaaaaaaaaaaa"><link href="/assets/site.css?v=bbbbbbbbbbbb">'
    assert borda.versoes_pedidas(html) == {"/assets/site.css": "aaaaaaaaaaaa"}            # V8, como a prova antiga
    enorme = "https://cdn.exemplo.invalid/" + "x" * 500 + ".js"
    [achado] = borda.conferir_html("/", 200, f'<script src="{enorme}"></script>', host=HOST).achados
    assert len(achado.item) == borda.ITEM_MAX                                             # V12
    enorme = "data:text/javascript," + "x" * 500
    [achado] = borda.conferir_html("/", 200, f'<script src="{enorme}"></script>', host=HOST).achados
    assert achado.item == "data"
    assert len(achado.detalhe) == borda.ITEM_MAX and achado.detalhe.startswith("data:")  # N1: a saúde mostra


@pytest.mark.parametrize(("src", "item"), [
    ("https://usuario:senha@cdn.exemplo.invalid:8443/a/b.js?token=x#frag", "cdn.exemplo.invalid/a/b.js"),
    ("//cdn.exemplo.invalid/b.js", "cdn.exemplo.invalid/b.js"),
    ("/cdn-cgi/scripts/x/rocket-loader.min.js?v=1", "/cdn-cgi/scripts/x/rocket-loader.min.js"),
    ("javascript:alert(1)", "javascript"),
    ("data:text/javascript,alert(1)", "data"),
    ("(embutido)", "embutido"),
    ("HTTPS://CDN.EXEMPLO.INVALID/A.js", "cdn.exemplo.invalid/A.js"),
    ("https:///cdn.exemplo.invalid/x.js", "cdn.exemplo.invalid/x.js"),          # o navegador ignora as barras a mais
    ("https://cdn.exemplo.invalid/x@y/z.js", "cdn.exemplo.invalid/x"),           # corta no `@` do caminho, não cola
    ("https://cdn.exemplo.invalid/x.js;jsessionid=ABC", "cdn.exemplo.invalid/x.js"),
    ("admin:s3cret@evil.invalid/x.js", "esquema"),
    ("https://user:p/ss@cdn.exemplo.invalid/x.js", "url-invalida"),
    ("https://10.0.0.5/x.js", "ip/x.js"),
    ("https://[2001:db8::1]:8443/x.js", "ip/x.js"),
    ("a/b:c.js", "a/b"),
    ("https://usuario;sessao@cdn.exemplo.invalid/a.js", "cdn.exemplo.invalid/a.js"),     # R1
    ("ht tps://usuario:senha@cdn.exemplo.invalid/a.js", "relativo"),                     # R2
    ("https://%31%30.0.0.5/a.js", "ip/a.js"),                                            # R3
])
def test_o_achado_da_canais_e_so_host_e_caminho_no_alfabeto_dela(src: str, item: str) -> None:
    """Contrato com a Canais (leitura do #381): o filtro dela não acha segredo em segmento de caminho; o vigia manda só
    host e caminho, sem query, fragmento, `;`, credencial nem porta, e no alfabeto `[A-Za-z0-9._/-]`, cortado."""
    assert borda.item_do_script(src) == item


#: Cada marcador é um pedaço que NUNCA pode sair no item nem no detalhe (leitura do #378, Q1 a Q4). A porta só não
#: pode sair no item: o detalhe da saúde e da prova mostra esquema, host, porta e caminho.
_MARCAS = ("USUARIO", "SENHA", "QUERY", "FRAG", "SESSAO", "1234", "10.0.0.5", "%31%30", "2001", "db8", "c0a8")
_PORTA = "8443"
_CDN = "cdn.exemplo.invalid"
_SRCS_ADVERSARIOS = [
    f"https://USUARIO:SENHA@{_CDN}:8443/a.js?QUERY=1#FRAG",
    f"HTTPS://USUARIO:SENHA@{_CDN.upper()}:8443/A.js?QUERY",
    f"\\\\USUARIO:SENHA@{_CDN}:8443\\a.js",
    f"https:\\\\USUARIO:SENHA@{_CDN}/a.js",
    f"ht\ttps://USUARIO:SE\nNHA@{_CDN}/a.js",
    f"https:/USUARIO:SENHA@{_CDN}/a.js",
    f"https:///USUARIO:SENHA@{_CDN}/a.js",
    f"https:USUARIO:SENHA@{_CDN}/a.js",
    f"//USUARIO:SENHA@{_CDN}:8443/a.js#FRAG",
    f"/\\USUARIO:SENHA@{_CDN}/a.js",
    f"https://USUARIO@SENHA@{_CDN}/a.js",
    f"https://USUARIO:SENHA@{_CDN}/x@y/a.js",
    f"https://{_CDN}/a.js;jsessionid=SESSAO",
    f"https://{_CDN}/a.js;SESSAO?QUERY",
    f"https://USUARIO:SENHA/SENHA@{_CDN}/a.js",
    f"https://USUARIO:SENHA?SENHA@{_CDN}/a.js",
    f"https://USUARIO:SENHA;SENHA@{_CDN}/a.js",
    f"https://USUARIO:SENHA#SENHA@{_CDN}/a.js",
    "https://USUARIO:SENHA@10.0.0.5:8443/a.js?QUERY",
    "https://[2001:db8::1]:8443/a.js",
    "https://[::ffff:c0a8:1]/a.js",
    "USUARIO:SENHA@evil.invalid/a.js",
    f"https://USUARIO;SESSAO@{_CDN}/a.js",                    # R1: o `;` é do userinfo, o host é o cdn
    f"https://USUARIO:1234;SESSAO@{_CDN}/a.js",
    f"ht tps://USUARIO:SENHA@{_CDN}/a.js",                    # R2: relativo para o navegador
    "1USUARIO:SENHA@evil.invalid/a.js",
    "https://%31%30.0.0.5/a.js",                              # R3: IP codificado
    "\x01 https://USUARIO:SENHA@evil.invalid/a.js",
]


@pytest.mark.parametrize("src", _SRCS_ADVERSARIOS)
def test_nenhum_pedaco_de_credencial_porta_query_ou_ip_sai_no_item_nem_no_detalhe(src: str) -> None:
    """Propriedade, não exemplo: com o `src` montado de marcadores em várias formas (caixa, `\\\\`, tabulação, uma e três
    barras, `@` no caminho, `;`, IPv4, IPv6), nenhum marcador sai no `item` (Canais) nem no `detalhe` (saúde, prova)."""
    item, detalhe = borda.endereco_do_script(src)
    for marca in _MARCAS:
        assert marca.lower() not in item.lower(), (marca, item)
        assert marca.lower() not in detalhe.lower(), (marca, detalhe)
    assert _PORTA not in item, item
    assert re.fullmatch(r"[A-Za-z0-9._/-]{0,120}", item)
    [achado] = borda.conferir_html("/", 200, f'<script src="{src}"></script>', host=HOST).achados
    assert (achado.item, achado.detalhe) == (item, detalhe)


def test_relativo_com_dois_pontos_no_1o_segmento_nao_mostra_nada_dele() -> None:
    """R2: para o navegador são caminho relativo, e a saúde mostrava o original inteiro."""
    for src in ("ht tps://usuario:senha@cdn.exemplo.invalid/a.js", "1usuario:senha@evil.invalid/a.js"):
        assert borda.endereco_do_script(src) == ("relativo", '(relativo com ":")')


# ------------------------------------------------------------------ 29.101: a API pedida de fora sem credencial
@pytest.mark.parametrize(("status", "estado", "motivo"), [
    (401, borda.OK, ""), (403, borda.OK, ""), (200, borda.DEFEITO, ""), (204, borda.DEFEITO, ""),
    (502, borda.SEM_CONFERIR, "borda 502"), (504, borda.SEM_CONFERIR, "borda 504"), (0, borda.SEM_CONFERIR, "sem resposta"),
    (404, borda.SEM_CONFERIR, "api 404"), (302, borda.SEM_CONFERIR, "api 302"), (500, borda.SEM_CONFERIR, "api 500"),
])
def test_a_api_sem_credencial_tem_de_recusar(status: int, estado: str, motivo: str) -> None:
    """401 ou 403 é ok; 2xx é a API aberta; o túnel não diz nada; o resto não prova nem um nem outro e não grita
    crítico à toa: sem_conferir com o status, que avisa depois de N voltas."""
    d = borda.conferir_api(status)
    assert d.estado == estado and d.motivo == motivo
    if estado == borda.DEFEITO:
        assert [(a.codigo, a.onde, a.item) for a in d.achados] == [(borda.API_ABERTA, "/api/instances", "/api/instances")]


def test_a_api_e_o_primeiro_pedido_sem_credencial_e_o_corpo_nao_e_lido() -> None:
    transporte = _borda("api_aberta")
    buscar = BuscarPelaBorda(lambda: 5, transporte)
    resposta = buscar(f"https://{HOST}/api/instances", aceita=None, ler_corpo=False)
    assert resposta.status == 200 and resposta.corpo == b""              # aberta, o corpo seria dado do central
    d, pedidos = _volta("api_aberta", site=False)
    api = pedidos[0]
    assert api.url.path == "/api/instances" and api.method == "GET"
    assert "authorization" not in api.headers and "cookie" not in api.headers
    assert d.estado == borda.DEFEITO and {a.codigo for a in d.achados} == {borda.API_ABERTA}


def test_api_aberta_avisa_na_hora_e_a_saude_a_poe_primeiro_e_separada() -> None:
    estado = {"q": "api_aberta"}
    canais = CanaisFalsa()
    vigia = _vigia(lambda: estado["q"], canais)
    vigia.volta(AGORA)
    assert canais.chamadas == [("api_aberta", "api", "/api/instances", None)]   # na 1ª volta, sem esperar N
    [(codigo, mensagem, dica)] = vigia.problemas()
    assert codigo == "portal_api_aberta" and "SEM credencial" in mensagem and "emulador-segredo" not in mensagem
    assert "tire o nome público do ar" in dica and "nada foi parado" in dica
    estado["q"] = "beacon"                                               # a API fechou e a borda injetou
    vigia.volta(AGORA + timedelta(hours=1))
    estado["q"] = "api_aberta"
    vigia.volta(AGORA + timedelta(hours=2))
    # Reabriu no mesmo dia: o vigia chama de novo; quem segura a repetição é a chave da Canais, por código e dia.
    assert [c[0] for c in canais.chamadas] == ["api_aberta", "script_injetado", "api_aberta"]


def test_api_que_responde_outra_coisa_so_avisa_depois_de_n_voltas() -> None:
    canais = CanaisFalsa()
    vigia = _vigia(lambda: "api_404", canais, n=2)
    vigia.volta(AGORA)
    assert canais.chamadas == []
    vigia.volta(AGORA + timedelta(hours=1))
    assert canais.chamadas == [("sem_conferir", "raiz", "api-404", 2)]
