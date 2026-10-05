"""Site institucional na raiz do nome público (29.77, ADR-075): o que fica exposto, com que cabeçalhos, e que a bandeira
desligada deixa tudo como estava (ADR-073).

Prova `simulated`: app ASGI com o harness de sempre, a pasta `site/` REAL do repositório copiada para a raiz do teste
(o que vai ao ar é o que é testado) e contatos fictícios. Nenhum número real entra aqui.
"""
from __future__ import annotations

import hashlib
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
from app.modules.portal.presentation.site import (CABECALHOS_DO_SITE, DIGITOS_DA_VERSAO, EXTENSOES_DO_SITE, Arquivo,
                                                  SiteInvalido, bloco_de_contatos, ler_site, versionar)

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
        assert pagina.headers["cache-control"] == "no-store, no-transform"
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


async def test_as_combinacoes_das_duas_bandeiras(harness: Harness) -> None:
    """Revisão do #333, A1: com o contato desligado o servidor troca o formulário pelo aviso (uma rota em 404 diria
    "tente de novo" para sempre); ligado, o formulário sai com o token. Contato sem site é recusado na subida (M3,
    em `test_portal_contato`); os dois desligados ficam no 307 (`test_desligado_a_raiz_segue_no_painel...`)."""
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        texto = (await c.get("/")).text
    assert "<form" not in texto and 'name="token"' not in texto and "formulário de contato está fora do ar" in texto
    assert "Prefere falar agora" in texto                                 # os telefones seguem na página
    _preparar(harness, site=True, contato_ligado=True)
    async with _cliente(harness) as c:
        texto = (await c.get("/")).text
    assert 'id="formulario-contato" method="post"' in texto and "fora do ar" not in texto
    assert re.search(r'name="token" value="\d+\.[0-9a-f]{32}"', texto)
    assert "<!--portal:" not in texto


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
    assert "<!--portal:formulario-->" in html and "<!--portal:fim-do-formulario-->" in html
    # Sem JavaScript (ou com erro de script), o Enter faz o envio padrão do navegador: com GET, nome e telefone iriam
    # na URL, que fica em log de borda. POST em `/` cai no 405 do site e nada vai para a URL.
    form = re.search(r'<form[^>]*id="formulario-contato"[^>]*>', html)
    assert form is not None and 'method="post"' in form.group(0)
    # A CSP só deixa `'self'`: recurso de fora seria bloqueado em silêncio, e a página subiria sem ele.
    css = arquivos["/assets/site.css"].corpo.decode("utf-8")
    assert not re.search(r'(?:src|href)="https?://(?!wa\.me/|dev\.nvit\.com\.br/)', html), "recurso de fora na página"
    assert "@import" not in css and not re.search(r"url\(\s*['\"]?https?:", css), "recurso de fora no CSS"


def test_padrao_do_telefone_e_o_mesmo_no_config_no_dominio_e_na_pagina() -> None:
    js = (SITE / "assets" / "site.js").read_text(encoding="utf-8")
    pagina = re.search(r"TELEFONE_VALIDO = /(.+?)/;", js)
    assert pagina is not None
    assert TELEFONE_PUBLICO.pattern == TELEFONE.pattern == pagina.group(1)


# ---------------------------------------------------------------- acabamento (29.80)
async def test_pagina_404_propria_com_status_404_e_a_csp(harness: Harness) -> None:
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        r = await c.get("/pagina-que-nao-existe")
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
        assert "Esta página não existe" in r.text and 'href="/central/"' in r.text
        assert r.headers["content-security-policy"] == CABECALHOS_DO_SITE["Content-Security-Policy"]
        assert (await c.post("/pagina-que-nao-existe", content=b"x")).status_code in (403, 405)
        assert (await c.head("/pagina-que-nao-existe")).status_code == 404
        # Pedida pelo nome, a 404 é a mesma resposta: status 404 e `no-transform` (N5 da leitura do #366).
        pelo_nome = await c.get("/404.html")
        assert pelo_nome.status_code == 404 and "no-transform" in pelo_nome.headers["cache-control"]


# ---------------------------------------------------------------- a borda não reescreve o HTML (29.91)
@pytest.mark.parametrize("caminho", ["/", "/pagina-que-nao-existe"])
async def test_html_do_site_sai_sem_transformar_e_comprimido_daqui(harness: Harness, caminho: str) -> None:
    """`no-transform` impede a Cloudflare de reescrever o HTML (o beacon do 29.85); como ele também tira a compressão
    da borda, a página sai comprimida daqui quando o cliente aceita gzip. O mesmo HTML nos dois jeitos."""
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        cru = await c.get(caminho, headers={"Accept-Encoding": "identity"})
        gz = await c.get(caminho, headers={"Accept-Encoding": "gzip, br"})
        for r in (cru, gz):
            assert "no-transform" in r.headers["cache-control"] and "accept-encoding" in r.headers["vary"].lower()
        assert "content-encoding" not in cru.headers
        assert gz.headers["content-encoding"] == "gzip"
        assert int(gz.headers["content-length"]) < int(cru.headers["content-length"]) / 2      # medido: ~4x menor
        assert gz.text == cru.text and gz.status_code == cru.status_code


async def test_gzip_recusado_com_q0_e_o_estilo_fica_com_a_borda(harness: Harness) -> None:
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        r = await c.get("/", headers={"Accept-Encoding": "gzip;q=0, identity"})
        assert "content-encoding" not in r.headers and "<html" in r.text.lower()
        # O CSS e o JS não levam `no-transform`: a borda segue comprimindo, e não injeta nada neles.
        estilo = await c.get("/assets/site.css", headers={"Accept-Encoding": "gzip"})
        assert "no-transform" not in estilo.headers["cache-control"] and "content-encoding" not in estilo.headers


def _png(corpo: bytes) -> tuple[int, int]:
    assert corpo[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(corpo[16:20], "big"), int.from_bytes(corpo[20:24], "big")


def test_previa_de_link_e_icones_da_mesma_origem_e_leves() -> None:
    arquivos = ler_site(SITE)
    html = arquivos["/index.html"].corpo.decode("utf-8")
    imagem = re.search(r'<meta property="og:image" content="https://dev\.nvit\.com\.br(/[^"]+)">', html)
    assert imagem is not None and imagem.group(1) in arquivos, "og:image tem de ser um arquivo da própria pasta"
    assert _png(arquivos[imagem.group(1)].corpo) == (1200, 630)          # PNG: o WhatsApp não lê SVG
    for prop in ("og:title", "og:description", "og:url", "og:image:alt"):
        assert f'property="{prop}"' in html, prop
    assert _png(arquivos["/assets/icone-180.png"].corpo) == (180, 180)
    assert arquivos["/favicon.ico"].corpo[:4] == b"\x00\x00\x01\x00"
    pesados = {p: len(a.corpo) for p, a in arquivos.items() if len(a.corpo) > 200_000}
    assert not pesados, f"arquivo do site acima de 200 KB: {pesados}"


def test_enderecos_absolutos_da_pagina_batem_entre_si() -> None:
    """O nome público é fixo no HTML estático (ADR-073 e ADR-075): `canonical`, `og:url` e `og:image` levam o mesmo
    `https://<nome>/`. Sem marcador trocado pelo servidor; se o nome mudar, os três mudam juntos."""
    html = ler_site(SITE)["/index.html"].corpo.decode("utf-8")
    canonical = re.search(r'<link rel="canonical" href="(https://[^/"]+)/"', html)
    og_url = re.search(r'<meta property="og:url" content="(https://[^/"]+)/"', html)
    og_imagem = re.search(r'<meta property="og:image" content="(https://[^/"]+)/assets/', html)
    assert canonical and og_url and og_imagem
    assert canonical.group(1) == og_url.group(1) == og_imagem.group(1) == f"https://{PUBLICO}"


def _luminancia(cor: tuple[float, float, float]) -> float:
    def canal(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = cor
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def _contraste(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    claro, escuro = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (claro + 0.05) / (escuro + 0.05)


def _hex(valor: str) -> tuple[float, float, float]:
    v = valor.strip().lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def test_rotulo_ilustracao_tem_contraste_aa_nos_fundos_reais() -> None:
    """O rótulo "Ilustração" é texto que quem enxerga lê (diz que o console e a ficha são ilustrações); o leitor de
    tela recebe o `aria-label` da figura. Medido no endereço público em 05/10 (29.80): com `#6a8198` dava 3,9 no
    console e 2,98 na ficha. Os fundos vêm do próprio CSS: o gradiente do console (`--noite-3` → `--noite-2`) e o
    topo da ficha (teal a 18 % sobre `--noite-2`, o ponto mais claro do gradiente)."""
    css = (SITE / "assets" / "site.css").read_text(encoding="utf-8")
    var = dict(re.findall(r"--(noite-[23]):\s*(#[0-9a-fA-F]{6})", css))
    noite2, noite3 = _hex(var["noite-2"]), _hex(var["noite-3"])
    topo = tuple(45 * 0.18 + c * 0.82 for c in noite2[:1]) + (212 * 0.18 + noite2[1] * 0.82, 191 * 0.18 + noite2[2] * 0.82)
    assert "rgba(45, 212, 191, .18)" in css                            # o topo da ficha que o cálculo supõe
    for seletor, fundos in ((".console-barra em", (noite3, noite2)), (".ficha-topo em", (topo,))):
        cor = re.search(re.escape(seletor) + r" \{[^}]*color: (#[0-9a-fA-F]{6})", css)
        assert cor, seletor
        for fundo in fundos:
            assert _contraste(_hex(cor.group(1)), fundo) >= 4.5, (seletor, cor.group(1), fundo)


# ---------------------------------------------------------------- 29.95: endereço novo a cada conteúdo novo
async def test_paginas_apontam_para_a_versao_que_a_origem_serve(harness: Harness) -> None:
    """A borda guarda CSS, JS e imagens por 4 h no navegador (troca o `no-cache` da origem por `max-age=14400`, medido
    em 05/10). Com `?v=` igual ao começo do sha256 do que a origem serve, conteúdo novo é endereço novo."""
    _preparar(harness, site=True)
    async with _cliente(harness) as c:
        for pagina in ("/", "/pagina-que-nao-existe"):
            html = (await c.get(pagina)).text
            versoes = dict(re.findall(r'\s(?:href|src)="(/[^"?#]+)\?v=([0-9a-f]+)"', html))
            assert "/assets/site.css" in versoes, pagina
            if pagina == "/":
                assert {"/assets/site.js", "/assets/marca-ana.svg", "/favicon.svg"} <= set(versoes)
            for caminho, versao in versoes.items():
                r = await c.get(f"{caminho}?v={versao}")
                assert r.status_code == 200, caminho
                assert hashlib.sha256(r.content).hexdigest()[:DIGITOS_DA_VERSAO] == versao, caminho
            # Nada da pasta fica sem versão; o que não é arquivo do site (painel, raiz, âncora) fica como está.
            assert not re.search(r'\s(?:href|src)="/(?:assets/|favicon)[^"?]*"', html), pagina
        assert 'href="/central/"' in (await c.get("/")).text


def test_versionar_muda_o_endereco_so_quando_o_conteudo_muda() -> None:
    def site(css: bytes) -> dict[str, Arquivo]:
        html = (b'<html><link rel="stylesheet" href="/assets/a.css"><a href="/outra.html">x</a>'
                b'<img src="/assets/sumiu.svg"><a href="/#contato">c</a></html>')
        return {"/index.html": Arquivo(html, "text/html; charset=utf-8", '"e"'),
                "/outra.html": Arquivo(b"<html></html>", "text/html; charset=utf-8", '"o"'),
                "/assets/a.css": Arquivo(css, "text/css; charset=utf-8", '"c"')}

    um, outro, igual = (versionar(site(b"body{}"))["/index.html"], versionar(site(b"body{color:red}"))["/index.html"],
                        versionar(site(b"body{}"))["/index.html"])
    v = hashlib.sha256(b"body{}").hexdigest()[:DIGITOS_DA_VERSAO]
    assert f'href="/assets/a.css?v={v}"'.encode() in um.corpo
    assert um.corpo != outro.corpo and um.etag != outro.etag and um == igual
    # Página para página, arquivo que não está na pasta e âncora ficam como estão.
    for intacto in (b'href="/outra.html"', b'src="/assets/sumiu.svg"', b'href="/#contato"'):
        assert intacto in um.corpo
