"""Autenticação da API, que até aqui não existia.

O modelo antigo era confiança no loopback: `server.host` tinha de ser 127.0.0.1 e o middleware recusava qualquer
outro `Host`. Funcionava, e era o motivo de o worker de outra máquina só alcançar o central por túnel SSH reverso.
Estes testes travam o que passou a valer: sair do loopback é permitido, mas só com segredo e lista de hosts — e o
segredo não pode aparecer em resposta nenhuma.
"""
from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.main import conferir_exposicao, create_app
from app.security.access import token_ok

from .conftest import Harness

SEGREDO = "tk-parque-3f9a2c7d41b8e05"      # token de teste, não existe fora daqui


async def _cliente(h: Harness, *, base: str, token: str | None = None) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cab = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base, headers=cab)


def _expor(h: Harness, *, host: str = "parque.local", token: str | None = SEGREDO) -> None:
    """Põe a configuração no cenário exposto: porta de rede, um host declarado e o segredo."""
    h.cfg.file.server.host = "0.0.0.0"                       # noqa: S104 - é exatamente o cenário sob teste
    h.cfg.file.server.public_hosts = [host]
    h.cfg.env.api_token = None if token is None else SecretStr(token)


# ---------------------------------------------------------------- o portão de subida
def test_nao_sobe_em_endereco_de_rede_sem_token(harness: Harness) -> None:
    harness.cfg.file.server.host = "0.0.0.0"                 # noqa: S104 - cenário sob teste
    harness.cfg.file.server.public_hosts = ["parque.local"]
    harness.cfg.env.api_token = None
    with pytest.raises(SystemExit) as e:
        conferir_exposicao(harness.cfg)
    assert "API_TOKEN" in str(e.value)


def test_nao_sobe_em_endereco_de_rede_sem_lista_de_hosts(harness: Harness) -> None:
    """Sem a lista, a defesa contra DNS rebinding não tem como distinguir um nome legítimo de um hostil."""
    _expor(harness)
    harness.cfg.file.server.public_hosts = []
    with pytest.raises(SystemExit) as e:
        conferir_exposicao(harness.cfg)
    assert "public_hosts" in str(e.value)


def test_loopback_continua_subindo_sem_configurar_nada(harness: Harness) -> None:
    """O caminho de quem roda tudo numa máquina não pode ter ficado mais difícil."""
    conferir_exposicao(harness.cfg)                          # não levanta


# ---------------------------------------------------------------- o portão de cada chamada
async def test_chamada_de_fora_sem_credencial_e_recusada(harness: Harness) -> None:
    _expor(harness)
    async with await _cliente(harness, base="http://parque.local") as c:
        r = await c.get("/api/health")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthorized"
    assert r.headers.get("WWW-Authenticate") == "Bearer"


async def test_chamada_de_fora_com_credencial_certa_passa(harness: Harness) -> None:
    _expor(harness)
    async with await _cliente(harness, base="http://parque.local", token=SEGREDO) as c:
        r = await c.get("/api/health")
    assert r.status_code == 200


async def test_credencial_errada_e_recusada_e_nao_ecoa_o_que_foi_enviado(harness: Harness) -> None:
    """A resposta diz que não serve, não o que foi recebido — senão o log de quem chama guarda tentativas alheias."""
    _expor(harness)
    async with await _cliente(harness, base="http://parque.local", token="tk-chute-do-atacante") as c:
        r = await c.get("/api/health")
    assert r.status_code == 401
    assert "chute" not in r.text
    assert SEGREDO not in r.text                             # nem o certo: 401 não é oráculo


async def test_o_esquema_importa_e_nao_basta_o_valor(harness: Harness) -> None:
    _expor(harness)
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://parque.local",
                                 headers={"Authorization": SEGREDO}) as c:      # sem "Bearer "
        assert (await c.get("/api/health")).status_code == 401


async def test_host_fora_da_lista_e_recusado_mesmo_com_credencial_certa(harness: Harness) -> None:
    """Defesa contra DNS rebinding: um nome que o atacante controla e faz resolver para 127.0.0.1 não passa por não
    estar declarado — e a credencial certa não compra a exceção."""
    _expor(harness, host="parque.local")
    async with await _cliente(harness, base="http://atacante.example", token=SEGREDO) as c:
        r = await c.get("/api/health")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "forbidden_host"


async def test_loopback_continua_sem_precisar_de_token(harness: Harness) -> None:
    """Deliberado: quem já está na máquina tem o banco e o adb na mão. Exigir segredo ali não protegeria nada e
    quebraria o frontend servido localmente."""
    _expor(harness)
    async with await _cliente(harness, base="http://127.0.0.1") as c:
        assert (await c.get("/api/health")).status_code == 200

def test_credencial_nao_ascii_e_recusa_e_nao_excecao() -> None:
    """`hmac.compare_digest` levanta `TypeError` para `str` com caractere fora de ASCII — e o Starlette decodifica
    cabecalho como latin-1, entao um byte alto chega como `str` nao-ASCII. Sem cuidado isso viraria **500 dentro do
    portao de autenticacao**: um jeito de derrubar o guarda em vez de passar por ele.

    O teste e direto na funcao de proposito: o `httpx` se recusa a ENVIAR cabecalho nao-ASCII, entao por HTTP o caso
    nao se reproduz. Quem faz isso e um cliente cru, e a defesa tem de estar onde a comparacao acontece.
    """
    assert token_ok("Bearer senha-com-\u00e7\u00e3o", SEGREDO) is False
    assert token_ok("Bearer " + SEGREDO, "esperado-com-\u00e7\u00e3o") is False
    assert token_ok("Bearer " + SEGREDO, SEGREDO) is True          # o caminho normal continua valendo
    assert token_ok(None, SEGREDO) is False
    assert token_ok("Bearer " + SEGREDO, None) is False            # sem token configurado, nada serve


# ---------------------------------------------------------------- o WebSocket, que middleware NAO protege
def _ws_recusado(h: Harness, *, host: str, token: str | None = None) -> int:
    """Devolve o codigo de fechamento, ou 0 quando a conexao foi aceita.

    O `TestClient` NAO entra como gerenciador de contexto de proposito: `with TestClient(app)` roda o *lifespan* da
    aplicacao, e o desligamento fecharia o banco que o `harness` e dono — o teste seguinte encontraria a conexao
    fechada. Para WebSocket nao ha necessidade: o `state.poc` e injetado a mao aqui.
    """
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cab = {"host": host}
    if token:
        cab["authorization"] = f"Bearer {token}"
    cliente = TestClient(app)
    try:
        with cliente.websocket_connect("/api/ws", headers=cab) as ws:
            ws.receive_json()
            return 0
    except WebSocketDisconnect as e:
        return e.code


def test_websocket_do_painel_exige_credencial_fora_do_loopback(harness: Harness) -> None:
    """O buraco que isto fecha: `@app.middleware("http")` e um `BaseHTTPMiddleware`, e o Starlette devolve o controle
    sem olhar quando o scope nao e `http`. Ou seja, **WebSocket nao passa por middleware nenhum**. Enquanto o backend
    so atendia 127.0.0.1 isso era inofensivo; com porta de rede, `/api/ws` entregaria o historico inteiro — estados
    de aparelho, execucoes, desfecho de comando — a quem estivesse na rede, sem credencial.
    """
    _expor(harness)
    assert _ws_recusado(harness, host="parque.local") == 4401             # sem credencial
    assert _ws_recusado(harness, host="parque.local", token="chute") == 4401
    assert _ws_recusado(harness, host="atacante.example", token=SEGREDO) == 4403   # host fora da lista


def test_websocket_do_painel_passa_com_credencial_e_no_loopback(harness: Harness) -> None:
    _expor(harness)
    assert _ws_recusado(harness, host="parque.local", token=SEGREDO) == 0
    assert _ws_recusado(harness, host="127.0.0.1") == 0                  # loopback segue sem token
