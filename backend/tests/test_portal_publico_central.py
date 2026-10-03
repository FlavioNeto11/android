"""Portal público em `https://<host>/central` (29.54, ADR-073): o portão NÃO mudou, e estes testes o fixam com o
painel no novo endereço.

O túnel da Cloudflare entrega toda requisição ao processo com par `127.0.0.1` de verdade; o que separa o público do
local é o `Host`. Então, com o nome declarado em `server.public_hosts` e sem credencial, só passam o estático (que
agora é `/central/`), o redirecionamento da raiz e as três rotas de sessão; o resto de `/api` responde 401. Host não
declarado responde 403 sem exceção. E o POST do login, além da credencial, precisa da `Origin` em `allowed_origins`.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import SecretStr

from app.main import create_app
from app.state import AppState

from .conftest import Harness

PUBLICO = "dev.nvit.com.br"
SEGREDO = "tk-portal-7b3e1c9a5d20f46"      # token de teste, não existe fora daqui
PAR_DO_TUNEL = ("127.0.0.1", 41234)          # o túnel entrega de dentro da máquina: o par é loopback de verdade


def _dist(h: Harness) -> None:
    raiz = Path(h.cfg.root) / "frontend" / "dist"
    (raiz / "assets").mkdir(parents=True, exist_ok=True)
    (raiz / "index.html").write_text('<!doctype html><script src="/central/assets/a-1.js"></script>', encoding="utf-8")
    (raiz / "assets" / "a-1.js").write_text("1", encoding="utf-8")


def _publicar(h: Harness, *, token: bool = True, tls: bool = True, origem: bool = True) -> None:
    """O cenário do ADR-073: `server.host` segue loopback, o nome público é declarado, mais as três linhas."""
    h.cfg.file.server.public_hosts = [PUBLICO]
    h.cfg.file.server.tls_behind_proxy = tls
    h.cfg.env.api_token = SecretStr(SEGREDO) if token else None
    h.cfg.file.server.allowed_origins = (
        [*h.cfg.file.server.allowed_origins, f"https://{PUBLICO}"] if origem else
        [o for o in h.cfg.file.server.allowed_origins if PUBLICO not in o])


def _cliente(h: Harness, *, host: str, origem: str | None = None) -> httpx.AsyncClient:
    _dist(h)
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cab = {"Origin": origem} if origem else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=PAR_DO_TUNEL),
                             base_url=f"http://{host}", headers=cab)


# ---------------------------------------------------------------- o que abre sem credencial, de fora
async def test_host_publico_sem_credencial_abre_so_o_painel_e_a_sessao(harness: Harness) -> None:
    _publicar(harness)
    async with _cliente(harness, host=PUBLICO) as c:
        painel = await c.get("/central/")
        assert painel.status_code == 200 and "/central/assets/" in painel.text
        assert (await c.get("/central/assets/a-1.js")).status_code == 200
        raiz = await c.get("/")
        assert raiz.status_code == 307 and raiz.headers["location"] == "/central/"
        assert (await c.get("/central")).status_code == 307
        sessao = await c.get("/api/session")
        assert sessao.status_code == 200 and sessao.json()["token_required"] is True

        for rota in ("/api/health", "/api/instances", "/api/snapshot"):
            r = await c.get(rota)
            assert r.status_code == 401, rota
            assert r.json()["detail"]["code"] == "unauthorized"
            assert SEGREDO not in r.text


async def test_o_canal_do_worker_http_nao_ganha_isencao_pelo_host_publico(harness: Harness) -> None:
    """Só a parte HTTP: `/api/worker/*` começa com `/api/` e não é rota de sessão, então 401 do portão. NÃO prova o
    WebSocket (o handshake não passa pelo middleware); isso é o teste logo abaixo."""
    _publicar(harness)
    async with _cliente(harness, host=PUBLICO) as c:
        assert (await c.get("/api/worker/ws")).status_code == 401


def _ws_worker(h: Harness, *, host: str) -> int:
    """Código de fechamento do `/api/worker/ws` pelo app PRINCIPAL com esse Host, ou 0 se foi aceito."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    try:
        with TestClient(app, client=PAR_DO_TUNEL).websocket_connect("/api/worker/ws", headers={"host": host}) as ws:
            ws.close()
            return 0
    except WebSocketDisconnect as e:
        return e.code


def test_websocket_do_worker_recusa_host_publico_com_o_listener_dedicado(harness: Harness) -> None:
    """O handshake WebSocket não passa pelo middleware HTTP. Pelo túnel todo par é 127.0.0.1, então sem esta recusa um
    `hello` errado vindo da internet bloquearia o worker legítimo: com `worker_port != 0`, o nome público é recusado
    (4403) na porta do painel e o loopback segue valendo."""
    _publicar(harness)
    harness.cfg.file.server.worker_port = 8010
    assert _ws_worker(harness, host=PUBLICO) == 4403
    assert _ws_worker(harness, host="atacante.example") == 4403
    assert _ws_worker(harness, host="127.0.0.1:8000") == 0


def test_websocket_do_worker_com_worker_port_zero_mantem_o_modo_de_porta_de_rede(harness: Harness) -> None:
    _publicar(harness)
    harness.cfg.file.server.worker_port = 0
    assert _ws_worker(harness, host=PUBLICO) == 0
    assert _ws_worker(harness, host="atacante.example") == 4403


async def test_docs_da_api_nao_abrem_sem_credencial_pelo_host_publico(harness: Harness) -> None:
    """`/docs`, `/redoc` e `/openapi.json` não começavam com `/api/` e abriam como "estático" (o mapa inteiro da API).
    Agora moram sob `/api/`: o portão exige credencial; os caminhos antigos não existem."""
    _publicar(harness)
    async with _cliente(harness, host=PUBLICO) as c:
        for rota in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert (await c.get(rota)).status_code == 404, rota
        for rota in ("/api/docs", "/api/redoc", "/api/openapi.json"):
            assert (await c.get(rota)).status_code == 401, rota
        com_token = await c.get("/api/openapi.json", headers={"Authorization": f"Bearer {SEGREDO}"})
        assert com_token.status_code == 200
    async with _cliente(harness, host="127.0.0.1") as c:
        assert (await c.get("/api/openapi.json")).status_code == 200


async def test_host_publico_com_o_token_certo_abre_a_api(harness: Harness) -> None:
    _publicar(harness)
    async with _cliente(harness, host=PUBLICO) as c:
        r = await c.get("/api/instances", headers={"Authorization": f"Bearer {SEGREDO}"})
        assert r.status_code == 200


# ---------------------------------------------------------------- host que ninguém declarou
async def test_host_nao_declarado_leva_403_no_painel_e_na_api(harness: Harness) -> None:
    _publicar(harness)
    async with _cliente(harness, host="atacante.example") as c:
        for rota in ("/central/", "/central/assets/a-1.js", "/", "/api/session", "/api/health", "/api/instances"):
            r = await c.get(rota)
            assert r.status_code == 403, rota
            assert r.json()["detail"]["code"] == "forbidden_host"
        com_token = await c.get("/api/instances", headers={"Authorization": f"Bearer {SEGREDO}"})
        assert com_token.status_code == 403                # a credencial certa não compra um Host não declarado


async def test_recuo_tirando_o_nome_de_public_hosts_fecha_tudo(harness: Harness) -> None:
    """O recuo documentado: sem o nome na lista, o mesmo hostname volta a 403, painel inclusive."""
    _publicar(harness)
    harness.cfg.file.server.public_hosts = []
    async with _cliente(harness, host=PUBLICO) as c:
        assert (await c.get("/central/")).status_code == 403
        assert (await c.get("/api/session")).status_code == 403


# ---------------------------------------------------------------- loopback de verdade: como sempre
async def test_loopback_de_verdade_continua_sem_token(harness: Harness) -> None:
    _publicar(harness)
    async with _cliente(harness, host="127.0.0.1") as c:
        assert (await c.get("/central/")).status_code == 200
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/api/instances")).status_code == 200


# ---------------------------------------------------------------- o login precisa da origem na lista
async def test_login_com_origem_fora_da_lista_leva_403(harness: Harness) -> None:
    _publicar(harness, origem=False)
    async with _cliente(harness, host=PUBLICO, origem=f"https://{PUBLICO}") as c:
        r = await c.post("/api/login", json={"operator": "Ana", "token": SEGREDO})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "forbidden_origin"
        assert SEGREDO not in r.text


async def test_login_com_origem_na_lista_segue_para_a_checagem_de_credencial(harness: Harness) -> None:
    _publicar(harness)
    async with _cliente(harness, host=PUBLICO, origem=f"https://{PUBLICO}") as c:
        errado = await c.post("/api/login", json={"operator": "Ana", "token": "tk-chute"})
        assert errado.status_code == 401
        assert errado.json()["detail"]["code"] == "invalid_credentials"
        certo = await c.post("/api/login", json={"operator": "Ana", "token": SEGREDO})
        assert certo.status_code == 200, certo.text


# ---------------------------------------------------------------- a saúde diz quando a exposição está incompleta
def _problema(state: AppState) -> object | None:
    return next((p for p in state.health().problems if p.code == "exposicao_publica_incompleta"), None)


def test_sem_host_publico_nao_ha_problema_de_exposicao(harness: Harness) -> None:
    assert harness.state is not None
    assert not harness.cfg.file.server.public_hosts
    assert _problema(harness.state) is None


def test_host_publico_sem_token_sem_tls_e_sem_origem_vira_problema_sem_citar_segredo(harness: Harness) -> None:
    assert harness.state is not None
    _publicar(harness, token=False, tls=False, origem=False)
    p = _problema(harness.state)
    assert p is not None
    texto = p.message + " " + p.hint  # type: ignore[attr-defined]
    for esperado in (PUBLICO, "API_TOKEN", "tls_behind_proxy", f"https://{PUBLICO}"):
        assert esperado in texto, esperado
    assert SEGREDO not in texto


def test_cada_falta_sozinha_basta_para_o_problema(harness: Harness) -> None:
    assert harness.state is not None
    for faltas in ({"token": False}, {"tls": False}, {"origem": False}):
        _publicar(harness, **faltas)
        assert _problema(harness.state) is not None, faltas


def test_com_as_tres_coisas_o_problema_some(harness: Harness) -> None:
    assert harness.state is not None
    _publicar(harness)
    assert _problema(harness.state) is None
