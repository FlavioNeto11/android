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


async def _cliente(h: Harness, *, base: str, token: str | None = None,
                   par: tuple[str, int] = ("127.0.0.1", 123)) -> httpx.AsyncClient:
    """`par` é o endereço do outro lado do TCP, que o cliente NÃO escolhe — `httpx.ASGITransport` o coloca em
    `scope["client"]`. É o que distingue "estou na máquina" de "escrevi `Host: localhost` num curl."""
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cab = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=par), base_url=base, headers=cab)


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
def _ws_recusado(h: Harness, *, host: str, token: str | None = None,
                 par: tuple[str, int] = ("127.0.0.1", 123)) -> int:
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
    cliente = TestClient(app, client=par)
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


# ---------------------------------------------------------------- o desvio pelo cabeçalho `Host`
async def test_host_de_loopback_vindo_de_outro_ip_nao_isenta_de_token(harness: Harness) -> None:
    """O defeito que isto fecha: a isenção de loopback era decidida **só pelo cabeçalho `Host`**, que quem chama
    escreve. Com `server.host: 0.0.0.0`, um `curl -H 'Host: localhost'` de qualquer máquina da rede atravessava o
    portão sem credencial — e a autenticação declarada como feita cabia num cabeçalho.

    Agora a isenção pede as duas coisas: par desta máquina E nome de loopback. O par é `scope["client"]`, que o
    cliente não escolhe. A recusa é 401 e não 403 de propósito: `localhost` não é um nome hostil, o que falta é o
    segredo — com ele, a mesma chamada passa (última linha).
    """
    _expor(harness)
    fora = ("10.0.0.5", 1)
    for nome in ("localhost", "127.0.0.1", "testserver"):
        async with await _cliente(harness, base=f"http://{nome}", par=fora) as c:
            r = await c.get("/api/health")
        assert r.status_code == 401, nome
        assert r.json()["detail"]["code"] == "unauthorized"
    async with await _cliente(harness, base="http://localhost", token=SEGREDO, par=fora) as c:
        assert (await c.get("/api/health")).status_code == 200


def test_websocket_do_painel_nao_isenta_host_de_loopback_de_outro_ip(harness: Harness) -> None:
    """Mesmo desvio, no canal que middleware nenhum protege."""
    _expor(harness)
    fora = ("10.0.0.5", 1)
    assert _ws_recusado(harness, host="localhost", par=fora) == 4401
    assert _ws_recusado(harness, host="127.0.0.1", par=fora) == 4401
    assert _ws_recusado(harness, host="localhost", token=SEGREDO, par=fora) == 0


def test_nomes_de_teste_nao_valem_em_producao() -> None:
    """`test`/`testserver` moravam no conjunto de produção porque são os hosts dos clientes ASGI da suíte. Quem roda
    no parque não deve conhecer nome nenhum de teste: aqui o conjunto injetado pelo `conftest` é esvaziado, que é
    exatamente o estado do processo em produção.
    """
    from app.security import access

    injetado = access.LOOPBACK_DE_TESTE
    access.LOOPBACK_DE_TESTE = frozenset()
    try:
        for nome in ("test", "testserver", "testclient"):
            assert access.avaliar(par="127.0.0.1", host=nome, authorization=None,
                                  publicos=frozenset(), token=SEGREDO) == "forbidden_host", nome
        # e o loopback de verdade continua passando, que é o caminho de quem roda tudo numa máquina
        assert access.avaliar(par="127.0.0.1", host="localhost:8000", authorization=None,
                              publicos=frozenset(), token=SEGREDO) is None
    finally:
        access.LOOPBACK_DE_TESTE = injetado


def test_par_ausente_nao_compra_isencao() -> None:
    """Transporte que não sabe dizer quem ligou não ganha o benefício da dúvida."""
    from app.security.access import avaliar

    assert avaliar(par=None, host="localhost", authorization=None, publicos=frozenset(),
                   token=SEGREDO) == "unauthorized"
    assert avaliar(par=None, host="localhost", authorization=f"Bearer {SEGREDO}", publicos=frozenset(),
                   token=SEGREDO) is None


# ---------------------------------------------------------------- a credencial do worker no disco
def test_credencial_do_worker_nao_fica_legivel_para_usuarios_locais(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`docs/worker.md` prometia "arquivo de permissão restrita", e no Windows isso era falso: o código só fazia
    `chmod` fora do Windows e confiava na herança de ACL da pasta. Medido na máquina do worker, a herança dava
    `BUILTIN\\Users:(I)(RX)` — qualquer usuário local, inclusive as contas de serviço de CI que rodam lá, lia a
    credencial permanente do worker.
    """
    import os
    import subprocess

    from app.worker.settings import WorkerSettings

    cfg = WorkerSettings(worker_id="w-teste", name="worker de teste", work_dir=str(tmp_path), devices=[])
    cfg.write_credential("credencial-sintetica-de-teste")
    caminho = cfg.credential_path()
    assert caminho.exists()

    if os.name != "nt":
        assert (caminho.stat().st_mode & 0o077) == 0
        return
    saida = subprocess.run(["icacls", str(caminho)], capture_output=True, text=True, timeout=30).stdout
    assert "Users:" not in saida, saida        # nem BUILTIN\Users nem <maquina>\Users
    assert "Everyone" not in saida, saida
