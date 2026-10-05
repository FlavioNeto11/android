"""O vigia da borda (29.97) e a régua comum com a prova de fora.

Prova `simulated`: a régua pura, a volta com `httpx.MockTransport` imitando a borda (o mesmo catálogo de defeitos do
`curl` falso de `scripts/tests/test_portal_prova_de_fora.py`), o estado entre voltas, o aviso uma vez por código e por
dia, e a saúde lendo só o estado. Nenhum pedido sai da máquina: o nome público é `.invalid` e todo transporte é falso.
"""
from __future__ import annotations

import gzip
import hashlib
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
def test_volta_limpa_e_ok_com_no_maximo_quatro_get_sem_credencial() -> None:
    d, pedidos = _volta()
    assert d.estado == borda.OK, d
    assert [p.url.path for p in pedidos] == ["/central/", "/", "/assets/site.css", "/assets/site.js"]
    for p in pedidos:
        assert p.method == "GET" and p.url.host == HOST and p.url.scheme == "https"
        assert "authorization" not in p.headers and "cookie" not in p.headers
        assert "Mozilla/5.0" in p.headers["user-agent"]                   # a borda só injeta para navegador
    assert pedidos[1].headers["accept-encoding"] == borda.ACEITA
    assert pedidos[2].url.query == f"v={VCSS}".encode()


def test_site_desligado_confere_so_o_painel() -> None:
    d, pedidos = _volta(site=False)
    assert d.estado == borda.OK and [p.url.path for p in pedidos] == ["/central/"]


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
])
def test_cada_defeito_da_borda_tem_codigo_lugar_e_nenhum_segredo(quebra: str, codigo: str, onde: str,
                                                                  item: str) -> None:
    d, _ = _volta(quebra)
    assert d.estado == borda.DEFEITO, d
    achado = next(a for a in d.achados if a.codigo == codigo)
    if quebra in ("desafio", "transformado", "cookie", "painel_so_relata"):
        assert {a.codigo for a in d.achados} == {codigo}, d.achados   # V1: um defeito, um aviso, o gesto certo
    assert achado.onde == onde and achado.item == item
    texto = " ".join(f"{a.detalhe} {a.item}" for a in d.achados)
    assert "token=" not in texto and "valor-secreto" not in texto        # nem query do beacon, nem valor do cookie


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
    assert len(pedidos) <= 2                                             # sem retry nem rajada


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
    def buscar(url: str, *, aceita: str | None) -> Resposta:
        return BuscarPelaBorda(lambda: 5, _borda(quebra()))(url, aceita=aceita)
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
    def buscar(url: str, *, aceita: str | None) -> Resposta:
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

    def buscar(url: str, *, aceita: str | None) -> Resposta:
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
    assert [p.headers["accept-encoding"] for p in pedidos[2:]] == ["gzip", "gzip"]


def test_versao_e_a_primeira_apontada_e_o_item_tem_teto() -> None:
    html = '<link href="/assets/site.css?v=aaaaaaaaaaaa"><link href="/assets/site.css?v=bbbbbbbbbbbb">'
    assert borda.versoes_pedidas(html) == {"/assets/site.css": "aaaaaaaaaaaa"}            # V8, como a prova antiga
    enorme = "data:text/javascript," + "x" * 500
    [achado] = borda.conferir_html("/", 200, f'<script src="{enorme}"></script>', host=HOST).achados
    assert len(achado.item) == borda.ITEM_MAX                                             # V12
