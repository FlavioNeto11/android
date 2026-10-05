"""O painel servido pelo backend: o que o navegador pode guardar, e por quanto tempo.

Existe por um defeito medido em produção. O deploy correu inteiro e conferiu: `frontend/dist` reconstruído,
`/api/health` devolvendo o commit certo, o `index.html` do servidor apontando `index-D5sunKr-.js`. E o painel
aberto no navegador continuava executando `index-q9q6a0zs.js`, um bundle que já nem existia em disco.

Nada estava quebrado. O `StaticFiles` do Starlette manda `ETag` e `Last-Modified` e nenhum `Cache-Control`, e
sem essa instrução o navegador guarda por conta própria — cache heurístico — sem revalidar. Como `index.html`
é o ÚNICO arquivo sem hash no nome, e é ele que diz qual bundle carregar, guardá-lo congela a versão inteira do
painel. O sintoma é "o deploy deu certo e o usuário vê o código velho", sem erro em lugar nenhum.

Desde o 29.54 (ADR-073) o painel mora em `/central/`, e não mais na raiz: o `index.html` do Vite aponta para
`/central/assets/...`, `GET /` redireciona e nada estático é servido fora de `/central`. O cache continua o mesmo.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.main import create_app

from .conftest import Harness


def _dist(cfg: object) -> Path:
    """Um `dist` mínimo mas REAL: o mount só acontece se o diretório existir, e o teste que não o cria passaria
    sem exercitar uma linha do que pretende cobrir."""
    raiz = Path(getattr(cfg, "root")) / "frontend" / "dist"
    (raiz / "assets").mkdir(parents=True, exist_ok=True)
    (raiz / "index.html").write_text(
        '<!doctype html><script type="module" src="/central/assets/index-abc123.js"></script>', encoding="utf-8")
    (raiz / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (raiz / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (raiz / "assets" / "index-abc123.css").write_text("body{}", encoding="utf-8")
    return raiz


def _cliente(harness: Harness) -> httpx.AsyncClient:
    _dist(harness.cfg)
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5000)),
                             base_url="http://127.0.0.1")


async def test_o_index_revalida_e_o_bundle_com_hash_nao(harness: Harness) -> None:
    """As duas metades do par, que só fazem sentido juntas.

    `index.html` com `no-cache` NÃO quer dizer "não guarde": quer dizer "guarde, mas pergunte antes de usar".
    Com `ETag` a pergunta costuma custar um 304 de alguns bytes. Já o que tem hash no nome nunca muda de
    conteúdo sob o mesmo nome, então perguntar por ele é desperdício puro — daí o `immutable`.
    """
    async with _cliente(harness) as c:
        pagina = await c.get("/central/index.html")
        assert pagina.status_code == 200
        guarda = pagina.headers.get("cache-control", "")
        assert "no-cache" in guarda, f"o index.html precisa revalidar; veio {guarda!r}"
        assert "immutable" not in guarda

        for arquivo in ("/central/assets/index-abc123.js", "/central/assets/index-abc123.css"):
            bundle = await c.get(arquivo)
            assert bundle.status_code == 200, arquivo
            guarda = bundle.headers.get("cache-control", "")
            assert "immutable" in guarda, f"{arquivo}: tem hash no nome e podia ser eterno; veio {guarda!r}"
            assert "max-age=31536000" in guarda, arquivo


async def test_central_com_barra_serve_o_index_e_tambem_revalida(harness: Harness) -> None:
    """`GET /central/` serve o mesmo `index.html` por outro caminho (`html=True`), e é por ele que o painel é
    aberto. Cobrir só `/central/index.html` deixaria de fora justamente a URL que as pessoas usam."""
    async with _cliente(harness) as c:
        pagina = await c.get("/central/")
        assert pagina.status_code == 200
        assert "/central/assets/index-abc123.js" in pagina.text
        assert "no-cache" in pagina.headers.get("cache-control", "")


async def test_o_html_do_painel_barra_script_de_fora_e_a_borda_nao_o_reescreve(harness: Harness) -> None:
    """29.91: em 05/10 a Cloudflare injetava o beacon do Web Analytics no `/central/` e, sem CSP, ele rodava. O
    `index.html` sai com a CSP do painel (só scripts do próprio painel) e `no-transform` (a borda não reescreve o
    HTML). O bundle com hash não precisa de CSP, porque não é documento."""
    from app.main import CSP_DO_PAINEL, origens_de_websocket, politica_do_painel

    assert "script-src 'self';" in CSP_DO_PAINEL and "unsafe-inline" not in CSP_DO_PAINEL
    assert "img-src 'self' blob: data:;" in CSP_DO_PAINEL            # o quadro do aparelho e os anexos são blob:
    servidor = harness.cfg.file.server
    esperada = politica_do_painel(origens_de_websocket(servidor.public_hosts, servidor.allowed_origins))
    async with _cliente(harness) as c:
        for caminho in ("/central/", "/central/index.html"):
            pagina = await c.get(caminho)
            assert pagina.headers["content-security-policy"] == esperada, caminho
            assert "no-transform" in pagina.headers["cache-control"], caminho
            assert "no-cache" in pagina.headers["cache-control"], caminho      # e segue revalidando
        bundle = await c.get("/central/assets/index-abc123.js")
        assert "no-transform" not in bundle.headers["cache-control"]


@pytest.mark.parametrize(("modo", "presente", "ausente"), [
    ("aplicar", "content-security-policy", "content-security-policy-report-only"),
    ("so_relatar", "content-security-policy-report-only", "content-security-policy"),
    ("desligada", None, "content-security-policy"),
])
async def test_csp_do_painel_se_desfaz_pelo_config_sem_deploy(harness: Harness, modo: str, presente: str | None,
                                                             ausente: str) -> None:
    """`server.csp_do_painel`: se a CSP quebrar uma tela no central, `so_relatar` (só relata, no console do navegador)
    ou `desligada` e o reinício da `farm-central` desfazem sem deploy. O `no-transform` fica nos três."""
    harness.cfg.file.server.csp_do_painel = modo  # type: ignore[assignment]
    harness.cfg.file.server.public_hosts = ["painel.exemplo.invalid"]
    harness.cfg.file.server.allowed_origins = ["http://127.0.0.1:8000"]
    async with _cliente(harness) as c:
        pagina = await c.get("/central/")
        assert pagina.status_code == 200
        if presente:
            # O `wss` do nome público e o `ws` do acesso local, explícitos: a CSP 2 (Safari e iOS antigos) não os
            # cobre pelo `'self'`, e o dono usa o painel no celular.
            assert ("connect-src 'self' wss://painel.exemplo.invalid ws://127.0.0.1:8000;"
                    in pagina.headers[presente]), pagina.headers[presente]
            assert "script-src 'self';" in pagina.headers[presente]
        assert ausente not in pagina.headers
        if modo == "desligada":
            assert "content-security-policy-report-only" not in pagina.headers
        assert "no-transform" in pagina.headers["cache-control"]


async def test_central_sem_barra_leva_ao_painel_com_redirecionamento_relativo(harness: Harness) -> None:
    """O Location é RELATIVO: o redirecionamento automático do Starlette montaria URL absoluta com o esquema que o
    processo enxerga (`http`, porque `proxy_headers` está desligado), e atrás do túnel TLS o navegador voltaria
    em http."""
    async with _cliente(harness) as c:
        r = await c.get("/central")
        assert r.status_code == 307
        assert r.headers["location"] == "/central/"
        seguido = await c.get("/central", follow_redirects=True)
        assert seguido.status_code == 200 and "/central/assets/" in seguido.text


async def test_a_raiz_redireciona_para_o_painel(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.get("/")
        assert r.status_code == 307
        assert r.headers["location"] == "/central/"
        assert (await c.head("/")).status_code == 307
        assert (await c.get("/", follow_redirects=True)).status_code == 200


async def test_nada_estatico_e_servido_fora_do_central(harness: Harness) -> None:
    """A raiz não serve mais arquivo: o que existe em `dist` só abre sob `/central`."""
    async with _cliente(harness) as c:
        for caminho in ("/index.html", "/favicon.svg", "/assets/index-abc123.js", "/assets/index-abc123.css"):
            r = await c.get(caminho)
            assert r.status_code == 404, f"{caminho} não podia abrir fora de /central (veio {r.status_code})"
        assert (await c.get("/central/favicon.svg")).status_code == 200


async def test_sem_dist_a_raiz_continua_sem_rota(harness: Harness) -> None:
    """Sem `frontend/dist` (instalação só de API) não há painel nem redirecionamento: `/` é 404 como sempre."""
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5000)),
                                 base_url="http://127.0.0.1") as c:
        assert (await c.get("/")).status_code == 404
        assert (await c.get("/central/")).status_code == 404


def test_origens_de_websocket_vem_da_configuracao_e_nao_injetam_diretiva() -> None:
    """Só nome ou IP com porta entra; `;`, espaço, esquema estranho ou caminho ficam fora, e a ordem não repete."""
    from app.main import origens_de_websocket, politica_do_painel

    origens = origens_de_websocket(
        ["Painel.Exemplo.invalid", "mal.invalid; script-src *", "x.invalid/caminho", "[::1]:8000"],
        ["http://127.0.0.1:8000", "https://painel.exemplo.invalid", "ftp://a.invalid", "http://a.invalid 'unsafe-eval'",
         "http://127.0.0.1:8000"])
    assert origens == ["wss://painel.exemplo.invalid", "wss://[::1]:8000", "ws://127.0.0.1:8000"]
    politica = politica_do_painel(origens)
    assert "connect-src 'self' wss://painel.exemplo.invalid wss://[::1]:8000 ws://127.0.0.1:8000;" in politica
    assert politica.count(";") == politica_do_painel(()).count(";") and "unsafe" not in politica
