"""Sessão do painel: cookie no lugar do cabeçalho, e um NOME no lugar de `panel`.

O que estes testes travam, item 9.1 (achados #119 e #60):

* o painel aberto de outra estação consegue ver a tela de login (as rotas de sessão respondem antes da
  credencial) e, depois do login, abre REST **e** WebSocket com o cookie — que é o único jeito de o navegador
  autenticar `<img>` e `new WebSocket(...)`;
* a auditoria passa a dizer quem pediu, e o que o CORPO da requisição diz não vence a sessão;
* o token de sessão não fica legível no banco, e o cookie só ganha `Secure` quando há TLS de verdade.
"""
from __future__ import annotations

import hashlib

import httpx
from pydantic import SecretStr

from app.main import create_app
from app.security.sessions import COOKIE

from .conftest import Harness

SEGREDO = "tk-parque-9c41d7b0a2e6f38"      # token de teste, não existe fora daqui
NOME = "Ana Ribeiro"


def _app(h: Harness):  # type: ignore[no-untyped-def]
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return app


def _cliente(h: Harness, *, base: str, par: tuple[str, int] = ("127.0.0.1", 123),
             origem: str | None = None) -> httpx.AsyncClient:
    """Cliente com JAR DE COOKIES e, quando pedido, com `Origin` — é o que faz este teste exercitar o mesmo
    caminho do navegador. Sem `Origin`, o POST do login não passaria pela conferência de CSRF do middleware, e
    o teste provaria o fluxo só para um cliente com que navegador nenhum se parece."""
    cab = {"Origin": origem} if origem else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(h), client=par), base_url=base, headers=cab)


def _expor(h: Harness, *, host: str = "parque.local") -> None:
    h.cfg.file.server.host = "0.0.0.0"                       # noqa: S104 - é exatamente o cenário sob teste
    h.cfg.file.server.public_hosts = [host]
    # A origem do navegador que abre o painel por esse nome. Faltando aqui, todo POST do painel remoto — o
    # login inclusive — levaria 403 `forbidden_origin`.
    h.cfg.file.server.allowed_origins = [*h.cfg.file.server.allowed_origins, f"http://{host}"]
    h.cfg.env.api_token = SecretStr(SEGREDO)


def _ws_codigo(h: Harness, *, host: str, cookie: str | None = None,
               par: tuple[str, int] = ("127.0.0.1", 123)) -> int:
    """Código de fechamento do `/api/ws`, ou 0 quando a conexão foi aceita. Sem `with TestClient(...)` de
    propósito: o lifespan fecharia o banco de que o harness é dono."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    cab = {"host": host}
    if cookie:
        cab["cookie"] = f"{COOKIE}={cookie}"
        cab["origin"] = f"http://{host}"
    cliente = TestClient(_app(h), client=par)
    try:
        with cliente.websocket_connect("/api/ws", headers=cab) as ws:
            ws.receive_json()
            return 0
    except WebSocketDisconnect as e:
        return e.code


# ---------------------------------------------------------------- quem sou eu
async def test_session_responde_que_ninguem_entrou_e_que_o_loopback_nao_pede_token(harness: Harness) -> None:
    """O painel pergunta ANTES de qualquer 401. No loopback 401 nunca acontece — e sem esta pergunta ninguém
    faria login ali, que é justamente onde o parque roda hoje: a auditoria seguiria dizendo `panel` para sempre.
    """
    async with _cliente(harness, base="http://127.0.0.1") as c:
        r = await c.get("/api/session")
    assert r.status_code == 200
    assert r.json() == {"operator": None, "token_required": False, "expires_at": None}


async def test_login_no_loopback_dispensa_token_e_a_auditoria_passa_a_ter_nome(harness: Harness) -> None:
    async with _cliente(harness, base="http://127.0.0.1") as c:
        entrada = await c.post("/api/login", json={"operator": f"  {NOME}  "})
        assert entrada.status_code == 200, entrada.text
        assert entrada.json()["operator"] == NOME               # normalizado: sem os espaços
        assert c.cookies.get(COOKIE)

        quem_sou = await c.get("/api/session")
        assert quem_sou.json()["operator"] == NOME

        acao = await c.post("/api/instances/android-01/actions/start",
                            json={"idempotency_key": "sessao-com-nome-01"})
        assert acao.status_code == 202, acao.text
        comando = harness.state.commands.get(acao.json()["command_id"])   # type: ignore[union-attr]
    assert comando["requested_by"] == NOME


async def test_sem_login_a_auditoria_continua_dizendo_panel(harness: Harness) -> None:
    """`panel` não sumiu: ele passou a querer dizer o que parecia querer — "veio do painel e ninguém se
    identificou". Inventar um nome aqui seria pior do que admitir que não se sabe."""
    async with _cliente(harness, base="http://127.0.0.1") as c:
        acao = await c.post("/api/instances/android-02/actions/start", json={"idempotency_key": "sem-sessao-01"})
        assert acao.status_code == 202, acao.text
        comando = harness.state.commands.get(acao.json()["command_id"])   # type: ignore[union-attr]
    assert comando["requested_by"] == "panel"


async def test_a_aprovacao_grava_quem_decidiu(harness: Harness) -> None:
    """A outra metade da trilha: `pending_approvals` guardava `decided_at` e `decided_note` — quando e por quê —
    e nunca QUEM. A decisão humana sobre um texto que vai ser publicado é exatamente onde isso importa."""
    pedido = harness.state.approvals.open(                                # type: ignore[union-attr]
        profile_id=None, capability="dm.send", summary="mandar a primeira mensagem", content="oi")
    async with _cliente(harness, base="http://127.0.0.1") as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        r = await c.post(f"/api/approvals/{pedido.id}/decide", json={"verb": "approve"})
    assert r.status_code == 200, r.text
    decidida = harness.state.approvals.get(pedido.id)                     # type: ignore[union-attr]
    assert decidida is not None
    assert decidida.status == "approved"
    assert decidida.decided_by == NOME
    assert decidida.to_dict()["decided_by"] == NOME                       # e a API mostra


def test_o_corpo_da_requisicao_nao_vence_a_sessao() -> None:
    """`requested_by` sempre foi campo de corpo: qualquer chamador escrevia ali o nome que quisesse, e era o
    único "quem" que o banco guardava. Com sessão, o cookie manda — e o campo vira o que deveria ser: rótulo de
    quem chama a API SEM sessão."""
    from types import SimpleNamespace

    from app.api import quem

    com_sessao = SimpleNamespace(state=SimpleNamespace(operador=NOME))
    sem_sessao = SimpleNamespace(state=SimpleNamespace(operador=None))
    assert quem(com_sessao, "outra pessoa") == NOME          # type: ignore[arg-type]
    assert quem(sem_sessao, "script-de-carga") == "script-de-carga"   # type: ignore[arg-type]
    assert quem(sem_sessao, None) == "panel"                 # type: ignore[arg-type]
    assert quem(sem_sessao, "   ") == "panel"                # type: ignore[arg-type]


# ---------------------------------------------------------------- o painel de outra estação
async def test_de_fora_o_401_leva_ao_login_e_o_cookie_abre_rest_e_websocket(harness: Harness) -> None:
    """O fluxo inteiro do achado #60, numa corrida só: 401 → login → snapshot → WS → logout.

    O ponto que faz o item existir: sem a isenção das rotas de sessão, o `POST /api/login` levaria 401 antes de
    chegar à rota — a tela de login nunca apareceria, e o botão não teria efeito nenhum.
    """
    _expor(harness)
    async with _cliente(harness, base="http://parque.local", origem="http://parque.local") as c:
        assert (await c.get("/api/snapshot")).status_code == 401

        pergunta = await c.get("/api/session")
        assert pergunta.status_code == 200, pergunta.text
        assert pergunta.json() == {"operator": None, "token_required": True, "expires_at": None}

        sem_token = await c.post("/api/login", json={"operator": NOME})
        assert sem_token.status_code == 401
        assert sem_token.json()["detail"]["code"] == "invalid_credentials"
        assert SEGREDO not in sem_token.text                 # 401 não é oráculo

        entrada = await c.post("/api/login", json={"operator": NOME, "token": SEGREDO})
        assert entrada.status_code == 200, entrada.text
        cookie = c.cookies.get(COOKIE)
        assert cookie

        # A pergunta é sobre a ORIGEM, não sobre esta requisição: com a sessão aberta ela continua dizendo que
        # daqui o login pede a chave. Derivar isso do veredito da requisição fazia o campo da chave sumir da
        # tela depois de "Sair", e o login seguinte era recusado sem saída.
        assert (await c.get("/api/session")).json()["token_required"] is True

        # REST e as rotas que o navegador busca como <img> (frame, evidência, avatar) passam a autenticar sozinhas.
        assert (await c.get("/api/snapshot")).status_code == 200
        assert (await c.get("/api/health")).status_code == 200
        # E o WebSocket, que é o único canal em que o navegador NÃO consegue mandar `Authorization`.
        assert _ws_codigo(harness, host="parque.local", cookie=cookie) == 0

        assert (await c.post("/api/logout")).status_code == 200
        assert (await c.get("/api/snapshot")).status_code == 401
    assert _ws_codigo(harness, host="parque.local", cookie=cookie) == 4401   # revogado vale para o WS também


def test_websocket_de_fora_sem_cookie_continua_recusado(harness: Harness) -> None:
    _expor(harness)
    assert _ws_codigo(harness, host="parque.local") == 4401
    assert _ws_codigo(harness, host="parque.local", cookie="cookie-inventado") == 4401


async def test_o_cookie_nao_compra_um_host_fora_da_lista(harness: Harness) -> None:
    """Defesa contra DNS rebinding: cookie de sessão é EXATAMENTE o que o navegador da vítima mandaria sozinho
    para um nome que o atacante faz resolver para 127.0.0.1. `forbidden_host` não tem exceção."""
    _expor(harness)
    async with _cliente(harness, base="http://parque.local") as c:
        assert (await c.post("/api/login", json={"operator": NOME, "token": SEGREDO})).status_code == 200
        cookie = c.cookies.get(COOKIE)
    async with _cliente(harness, base="http://atacante.example") as c:
        c.cookies.set(COOKIE, cookie, domain="atacante.example")
        r = await c.get("/api/health")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "forbidden_host"
    assert _ws_codigo(harness, host="atacante.example", cookie=cookie) == 4403


# ---------------------------------------------------------------- o segredo e o cookie
async def test_o_token_da_sessao_nao_fica_legivel_no_banco(harness: Harness) -> None:
    """Um dump do banco (backup, réplica, anexo de suporte) não pode virar um passe para o painel."""
    async with _cliente(harness, base="http://127.0.0.1") as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        cookie = c.cookies.get(COOKIE)
    linhas = harness.state.db.query("SELECT * FROM panel_sessions")        # type: ignore[union-attr]
    assert len(linhas) == 1
    assert cookie not in str(linhas[0])
    assert linhas[0]["token_hash"] == hashlib.sha256(cookie.encode()).hexdigest()
    assert linhas[0]["operator"] == NOME


async def test_o_cookie_e_httponly_strict_e_so_ganha_secure_com_tls(harness: Harness) -> None:
    """`Secure` num painel servido por HTTP no loopback faria o navegador descartar o cookie em silêncio — e o
    login pararia de funcionar sem nenhuma mensagem de erro."""
    async with _cliente(harness, base="http://127.0.0.1") as c:
        bruto = (await c.post("/api/login", json={"operator": NOME})).headers["set-cookie"].lower()
    assert "httponly" in bruto and "samesite=strict" in bruto and "path=/api" in bruto
    assert "secure" not in bruto

    harness.cfg.file.server.tls_behind_proxy = True          # há um proxy TLS na frente: agora sim
    async with _cliente(harness, base="http://127.0.0.1") as c:
        com_tls = (await c.post("/api/login", json={"operator": NOME})).headers["set-cookie"].lower()
    assert "secure" in com_tls


async def test_tentativa_repetida_de_adivinhar_o_token_trava_o_login(harness: Harness) -> None:
    """Sem trava, o `POST /api/login` seria um oráculo de força bruta contra o `API_TOKEN` — e mais barato do
    que o cabeçalho, porque não precisa de nada além de JSON."""
    _expor(harness)
    async with _cliente(harness, base="http://parque.local") as c:
        vistos = [(await c.post("/api/login", json={"operator": NOME, "token": f"chute-{i}"})).status_code
                  for i in range(9)]
        # O bloqueio pega antes do nono chute, e vale até para quem sabe o token certo.
        assert 429 in vistos, vistos
        travado = await c.post("/api/login", json={"operator": NOME, "token": SEGREDO})
    assert travado.status_code == 429
    assert travado.json()["detail"]["code"] == "too_many_attempts"

    # E a trava NÃO alcança o login do loopback, que não apresenta segredo nenhum: se alcançasse, oito chutes
    # vindos da rede trancariam quem opera na própria máquina — o ataque deixaria de roubar e passaria a derrubar.
    async with _cliente(harness, base="http://127.0.0.1") as local:
        assert (await local.post("/api/login", json={"operator": NOME})).status_code == 200


async def test_nome_vazio_nao_vira_identidade(harness: Harness) -> None:
    async with _cliente(harness, base="http://127.0.0.1") as c:
        assert (await c.post("/api/login", json={"operator": " "})).status_code == 422
        assert (await c.post("/api/login", json={"operator": "a" * 61})).status_code == 422
