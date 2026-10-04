"""Site institucional na raiz do nome público (29.77, ADR-075): o que fica exposto, com que cabeçalhos, e que a bandeira
desligada deixa tudo como estava (ADR-073).

Prova `simulated`: app ASGI com o harness de sempre, a pasta `site/` REAL do repositório copiada para a raiz do teste
(o que vai ao ar é o que é testado) e contatos fictícios. Nenhum número real entra aqui.
"""
from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from starlette.routing import Mount

from app.config import TELEFONE_PUBLICO, ContatoPublicoCfg
from app.main import create_app
from app.modules.portal.domain.campos import TELEFONE
from app.modules.portal.presentation.site import (CABECALHOS_DO_SITE, EXTENSOES_DO_SITE, SiteInvalido,
                                                  bloco_de_contatos, ler_site)

from .conftest import Harness

SITE = Path(__file__).resolve().parents[2] / "site"
PUBLICO = "dev.nvit.com.br"
SEGREDO = "tk-portal-site-4f81c2"            # token de teste, não existe fora daqui
PAR_DO_TUNEL = ("127.0.0.1", 41234)
FICTICIOS = [ContatoPublicoCfg(nome='Pessoa <b>"Teste"</b> & Cia', telefone="+55 (00) 0000-0001"),
             ContatoPublicoCfg(nome="Outra Pessoa", telefone="+55 (00) 0000-0002")]


def _preparar(h: Harness, *, site: bool, contatos: bool = True, contato_ligado: bool = False) -> None:
    raiz = Path(h.cfg.root)
    (raiz / "frontend" / "dist" / "assets").mkdir(parents=True, exist_ok=True)
    (raiz / "frontend" / "dist" / "index.html").write_text("<!doctype html><title>painel</title>", encoding="utf-8")
    if not (raiz / "site").exists():
        shutil.copytree(SITE, raiz / "site")
    h.cfg.file.portal.site_ligado = site
    h.cfg.file.portal.contato_ligado = contato_ligado
    h.cfg.file.portal.contatos = list(FICTICIOS) if contatos else []
    h.cfg.file.server.public_hosts = [PUBLICO]
    h.cfg.file.server.tls_behind_proxy = True
    h.cfg.env.api_token = SecretStr(SEGREDO)


def _cliente(h: Harness, *, host: str = PUBLICO) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=PAR_DO_TUNEL), base_url=f"http://{host}")


# ---------------------------------------------------------------- bandeira desligada: nada muda
async def test_desligado_a_raiz_segue_no_painel_e_o_site_nao_existe(harness: Harness) -> None:
    _preparar(harness, site=False)
    async with _cliente(harness) as c:
        raiz = await c.get("/")
        assert raiz.status_code == 307 and raiz.headers["location"] == "/central/"
        for caminho in ("/index.html", "/assets/site.css", "/robots.txt"):
            assert (await c.get(caminho)).status_code == 404, caminho


# ---------------------------------------------------------------- ligado: a superfície
async def test_ligado_a_raiz_e_do_site_e_o_painel_segue_em_central(harness: Harness) -> None:
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        pagina = await c.get("/")
        assert pagina.status_code == 200 and pagina.headers["content-type"].startswith("text/html")
        assert "ANA" in pagina.text and "/central/" in pagina.text
        assert pagina.headers["cache-control"] == "no-store"
        for nome, valor in CABECALHOS_DO_SITE.items():
            assert pagina.headers[nome] == valor
        assert pagina.headers["x-content-type-options"] == "nosniff"      # os de sempre, pelo `guarda`
        assert (await c.get("/index.html")).status_code == 200
        assert (await c.get("/central")).status_code == 307
        assert (await c.get("/central/")).status_code == 200
        robots = await c.get("/robots.txt")
        assert robots.status_code == 200 and "Disallow: /central/" in robots.text


async def test_ligado_a_api_segue_fechada_de_fora_e_o_site_nao_a_engole(harness: Harness) -> None:
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        for rota in ("/api/instances", "/api/inexistente", "/api/portal/info"):
            r = await c.get(rota)
            assert r.status_code == 401, rota
    async with _cliente(harness, host="127.0.0.1") as local:
        assert (await local.get("/api/inexistente")).status_code == 404
        assert (await local.get("/api/instances")).status_code == 200


async def test_ligado_so_get_e_head_e_nada_fora_da_pasta(harness: Harness) -> None:
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        assert (await c.head("/")).status_code == 200
        assert (await c.post("/", content=b"x")).status_code in (403, 405)   # sem Origin própria: 405 do site
        for caminho in ("/.env", "/config/config.yaml", "/../config/config.yaml", "/%2e%2e/backend/app/main.py",
                        "/site/index.html", "/assets/../../backend/app/main.py", "/backend/app/main.py"):
            r = await c.get(caminho)
            assert r.status_code == 404, caminho
            assert "api_token" not in r.text and "import" not in r.text


async def test_estatico_com_etag_revalida_com_304(harness: Harness) -> None:
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        css = await c.get("/assets/site.css")
        assert css.status_code == 200 and css.headers["cache-control"] == "no-cache"
        assert css.headers["content-type"].startswith("text/css")
        etag = css.headers["etag"]
        de_novo = await c.get("/assets/site.css", headers={"If-None-Match": etag})
        assert de_novo.status_code == 304 and de_novo.content == b""


def _achatar(rotas: list[object]) -> Iterator[object]:
    """O `include_router` do FastAPI 0.141 deixa um `_IncludedRouter` (ver `test_cobertura_de_rotas._achatar`)."""
    for r in rotas:
        interno = getattr(r, "original_router", None)
        if interno is not None:
            yield from _achatar(list(interno.routes))
        else:
            yield r


async def test_superficie_exata_fora_da_api(harness: Harness) -> None:
    """Fora de `/api/` o `guarda` trata tudo como público: a lista do que existe ali é fechada e conferida aqui."""
    _preparar(harness, site=True)
    app = create_app(harness.cfg, state=harness.state)
    fora_da_api = sorted((type(r).__name__, r.path) for r in _achatar(list(app.routes))
                         if not r.path.startswith("/api/"))
    assert fora_da_api == [("APIRoute", "/central"), ("Mount", ""), ("Mount", "/central")]
    assert isinstance(app.routes[-1], Mount) and app.routes[-1].path == "", "o site tem de ser montado por último"


# ---------------------------------------------------------------- contatos e token
async def test_contatos_entram_escapados_e_somem_sem_config(harness: Harness) -> None:
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        texto = (await c.get("/")).text
    assert "Pessoa &lt;b&gt;&quot;Teste&quot;&lt;/b&gt; &amp; Cia" in texto and "<b>\"Teste\"" not in texto
    assert 'href="tel:+5500000000' in texto and 'href="https://wa.me/5500000000' in texto
    assert "<!--portal:" not in texto

    _preparar(harness, site=True, contatos=False)
    async with _cliente(harness) as c:
        texto = (await c.get("/")).text
    assert "Prefere falar agora" not in texto and "<!--portal:" not in texto


async def test_token_so_sai_com_o_contato_ligado(harness: Harness) -> None:
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        assert 'name="token" value=""' in (await c.get("/")).text
    _preparar(harness, site=True, contato_ligado=True)
    async with _cliente(harness) as c:
        assert re.search(r'name="token" value="\d+\.[0-9a-f]{32}"', (await c.get("/")).text)


def test_bloco_de_contatos_vazio_e_texto_vazio() -> None:
    assert bloco_de_contatos([]) == ""


# ---------------------------------------------------------------- a pasta do site
def test_pasta_com_arquivo_fora_da_lista_derruba_a_subida(tmp_path: Path) -> None:
    shutil.copytree(SITE, tmp_path / "site")
    ler_site(tmp_path / "site")                                         # a pasta real passa
    (tmp_path / "site" / "rascunho.pdf").write_bytes(b"%PDF")
    with pytest.raises(SiteInvalido, match="extensão"):
        ler_site(tmp_path / "site")
    (tmp_path / "site" / "rascunho.pdf").unlink()
    (tmp_path / "site" / "assets" / ".env").write_text("x", encoding="utf-8")
    with pytest.raises(SiteInvalido, match="oculto"):
        ler_site(tmp_path / "site")


def test_pasta_real_so_tem_extensoes_da_lista_e_nada_em_linha() -> None:
    """A CSP não tem `unsafe-inline`: script ou estilo em linha quebraria a página no ar sem erro no teste."""
    arquivos = ler_site(SITE)
    assert all(Path(p).suffix in EXTENSOES_DO_SITE for p in arquivos)
    html = arquivos["/index.html"].corpo.decode("utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html), "script em linha"
    assert "<style" not in html and not re.search(r"\sstyle=", html), "estilo em linha"
    assert not re.search(r"\son[a-z]+=", html), "manipulador de evento em linha"
    assert "<!--portal:contatos-->" in html and "<!--portal:token-->" in html


def test_padrao_do_telefone_e_o_mesmo_no_config_no_dominio_e_na_pagina() -> None:
    js = (SITE / "assets" / "site.js").read_text(encoding="utf-8")
    pagina = re.search(r"TELEFONE_VALIDO = /(.+?)/;", js)
    assert pagina is not None
    assert TELEFONE_PUBLICO.pattern == TELEFONE.pattern == pagina.group(1)
