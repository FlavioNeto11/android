"""O canal do worker, o listener dedicado do túnel e o segredo local do encerramento.

O defeito de fundo que estes testes travam: o túnel SSH reverso (`-R 18000:127.0.0.1:8000`) fazia TODA conexão
vinda da máquina do worker chegar ao central com par `127.0.0.1` **de verdade** — e loopback isenta de credencial.
Medido no parque: do notebook do worker, `GET http://127.0.0.1:18000/api/workers`, `/api/instagram/profiles` e
`/api/commands` respondiam 200 sem token, e `POST /api/admin/shutdown`, `PUT` de credencial de perfil e
`POST /api/workers/enroll` estavam ao alcance de qualquer processo local daquela máquina, inclusive de usuário
não-administrador. Comprometer um worker equivalia a comprometer o central.

Conferir o endereço do par não resolve — o par É 127.0.0.1. O que separa os dois casos é qual porta atendeu.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app, create_worker_app
from app.security import local_secret
from app.workers.portao import BLOQUEIO_S, FALHAS_ATE_BLOQUEIO, PENDENTES_POR_IP, PortaoDoWorker

from .conftest import Harness

PAR_LOCAL = ("127.0.0.1", 123)


def _hello(worker_id: str = "worker-lan-01") -> dict:
    return {"worker_id": worker_id, "name": "Notebook da LAN", "agent_version": "0.1.0", "os": "windows",
            "max_slots": 1, "verbs": ["start", "stop"], "devices": [], "resources": None}


# ---------------------------------------------------------------- o listener dedicado
async def test_rest_pela_porta_do_tunel_nao_existe(harness: Harness) -> None:
    """O contrato do listener dedicado: ele serve `/api/worker/ws` e NADA MAIS. Estas são exatamente as rotas que
    respondiam 200 sem token a partir do notebook do worker."""
    app = create_worker_app(harness.state)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=PAR_LOCAL),
                                 base_url="http://127.0.0.1") as c:
        for rota in ("/api/health", "/api/workers", "/api/instagram/profiles", "/api/commands", "/api/snapshot"):
            assert (await c.get(rota)).status_code == 404, rota
        assert (await c.post("/api/admin/shutdown")).status_code == 404
        assert (await c.post("/api/workers/enroll")).status_code == 404


def test_websocket_do_worker_pela_porta_do_tunel_e_aceito_com_credencial(harness: Harness) -> None:
    """A outra metade do contrato: fechar a porta para REST não pode ter fechado o canal que o agente usa."""
    inscricao = harness.state.workers.criar_inscricao("worker de teste")

    app = create_worker_app(harness.state)
    cliente = TestClient(app, client=PAR_LOCAL)
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": _hello(), "enrollment_token": inscricao}))
        bem_vindo = ws.receive_json()
    assert bem_vindo.get("credential"), bem_vindo         # a credencial permanente volta uma única vez
    assert bem_vindo["heartbeat_s"] > 0

    # E a credencial recém-emitida vale na reconexão seguinte, pela mesma porta.
    permanente = bem_vindo["credential"]
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": _hello(), "token": permanente}))
        segundo = ws.receive_json()
    assert "credential" not in segundo                    # não se repete


def test_credencial_errada_e_recusada_e_a_tentativa_fica_registrada(harness: Harness) -> None:
    """O que faltava: a tentativa recusada não deixava rastro nenhum — nem log, nem evento. Quem tentasse se passar
    por um worker passava despercebido pelo operador."""
    app = create_worker_app(harness.state)
    cliente = TestClient(app, client=("10.0.0.5", 4001))
    with pytest.raises(WebSocketDisconnect) as saida:
        with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
            ws.send_text(json.dumps({"hello": _hello("worker-forjado"), "token": "credencial-que-nao-existe"}))
            recusa = ws.receive_json()
            assert recusa["code"] == "not_enrolled"
            ws.receive_json()                             # força a leitura do fechamento
    assert saida.value.code == 4401

    eventos = harness.state.db.query("SELECT * FROM events WHERE kind='worker.refused' ORDER BY id DESC")
    assert eventos, "a recusa tem de virar evento persistido"
    dados = json.loads(eventos[0]["data"])
    assert dados["reason"] == "not_enrolled"
    assert dados["ip"] == "10.0.0.5"
    assert dados["worker_id"] == "worker-forjado"         # o id DECLARADO, que é o que se sabe
    assert "credencial-que-nao-existe" not in eventos[0]["message"]      # o segredo recebido não entra no rastro


def test_host_fora_da_lista_e_recusado_antes_do_accept(harness: Harness) -> None:
    """Mesma defesa de DNS rebinding do resto da API. Loopback continua valendo: pelo túnel o agente chega com
    `Host: 127.0.0.1:18000`."""
    harness.cfg.file.server.public_hosts = ["central.parque.local"]
    app = create_worker_app(harness.state)
    cliente = TestClient(app, client=("10.0.0.5", 4002))
    with pytest.raises(WebSocketDisconnect) as saida:
        with cliente.websocket_connect("/api/worker/ws", headers={"host": "atacante.example"}) as ws:
            ws.receive_json()
    assert saida.value.code == 4403
    motivos = [json.loads(e["data"])["reason"] for e in
               harness.state.db.query("SELECT * FROM events WHERE kind='worker.refused'")]
    assert "forbidden_host" in motivos


def test_hello_grande_demais_e_recusado(harness: Harness) -> None:
    """O `ws_max_size` do uvicorn (16 MiB) vale para a conexão inteira e não dá para baixar só neste canal. O teto
    do que ainda NÃO foi autenticado é aplicado aqui."""
    app = create_worker_app(harness.state)
    cliente = TestClient(app, client=PAR_LOCAL)
    with pytest.raises(WebSocketDisconnect) as saida:
        with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
            ws.send_text(json.dumps({"hello": _hello(), "lixo": "x" * 64 * 1024}))
            ws.receive_json()
    assert saida.value.code == 4400


# ---------------------------------------------------------------- o freio do handshake
def test_portao_limita_handshakes_pendentes_por_ip() -> None:
    portao = PortaoDoWorker()
    for _ in range(PENDENTES_POR_IP):
        assert portao.entrar("10.0.0.5") is True
    assert portao.entrar("10.0.0.5") is False             # o quinto socket não custa nada a este processo
    assert portao.entrar("10.0.0.6") is True              # e o limite é POR IP, não global
    portao.sair("10.0.0.5")
    assert portao.entrar("10.0.0.5") is True


def test_portao_bloqueia_temporariamente_depois_de_falhas_seguidas() -> None:
    portao = PortaoDoWorker()
    for i in range(FALHAS_ATE_BLOQUEIO - 1):
        assert portao.falhou("10.0.0.5", agora=100.0 + i) is False
    assert portao.falhou("10.0.0.5", agora=104.0) is True
    assert portao.bloqueado("10.0.0.5", agora=105.0) is True
    assert portao.entrar("10.0.0.5", agora=105.0) is False
    assert portao.bloqueado("10.0.0.5", agora=105.0 + BLOQUEIO_S) is False      # é temporário, não banimento
    # Handshake bem-sucedido zera o histórico: quem errou uma vez e depois acertou não carrega a falha.
    portao.falhou("10.0.0.7", agora=200.0)
    portao.perdoou("10.0.0.7")
    assert portao.falhas.get("10.0.0.7") is None


# ---------------------------------------------------------------- o segredo local do encerramento
async def test_shutdown_exige_o_segredo_local(harness: Harness) -> None:
    """Antes bastava o par ser 127.0.0.1 — e pelo túnel ele É 127.0.0.1, então qualquer processo da máquina do
    worker derrubava o central."""
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=PAR_LOCAL),
                                 base_url="http://127.0.0.1") as c:
        r = await c.post("/api/admin/shutdown")
        assert r.status_code == 403
        assert "shutdown.token" in r.json()["detail"]["message"]

        r = await c.post("/api/admin/shutdown", headers={local_secret.CABECALHO: "chute-do-atacante"})
        assert r.status_code == 403

        segredo = local_secret.ler(harness.cfg.data_dir)
        assert segredo
        r = await c.post("/api/admin/shutdown", headers={local_secret.CABECALHO: segredo})
        assert r.status_code == 202

    # A recusa fica registrada: um pedido de desligar o parque é coisa que o operador tem de ver.
    recusas = harness.state.db.query(
        "SELECT * FROM events WHERE kind='log' AND message LIKE '%encerramento recusado%'")
    assert len(recusas) == 2
    assert all("chute-do-atacante" not in e["message"] for e in recusas)


def test_segredo_local_e_regravado_a_cada_subida(tmp_path: Path) -> None:
    """Um segredo que vazou para o log de alguém deixa de servir no próximo restart."""
    primeiro = local_secret.garantir(tmp_path)
    segundo = local_secret.garantir(tmp_path)
    assert primeiro != segundo
    assert local_secret.ler(tmp_path) == segundo
    assert local_secret.confere(tmp_path, segundo) is True
    assert local_secret.confere(tmp_path, primeiro) is False
    assert local_secret.confere(tmp_path, None) is False
    assert local_secret.confere(tmp_path / "vazio", segundo) is False       # sem arquivo, nada serve


# ---------------------------------------------------------------- as duas portas, de verdade
def test_um_servidor_dois_sockets_e_o_despachante_separa_por_porta(harness: Harness) -> None:
    """Prova de transporte, não de simulação: sobe UM `uvicorn.Server` com DOIS sockets e bate nas duas portas.

    Um servidor com dois sockets, e não dois servidores: cada `uvicorn.Server.serve()` instala os handlers de
    sinal do uvicorn (`capture_signals` usa `signal.signal`), e o segundo sobrescreveria o primeiro — Ctrl+C
    passaria a encerrar um só. Com um servidor há um laço, um `lifespan`, um `should_exit`.
    """
    import socket as sock_mod
    import threading
    import time

    import uvicorn

    from app.main import _socket_de, create_worker_app, despachante

    def porta_livre() -> int:
        s = sock_mod.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    principal_porta, worker_porta = porta_livre(), porta_livre()
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    alvo = despachante(app, create_worker_app(harness.state), worker_porta)
    sockets = [_socket_de("127.0.0.1", principal_porta), _socket_de("127.0.0.1", worker_porta)]
    # lifespan desligado: o `AppState` deste teste é o do harness, que já está de pé e será encerrado por ele.
    servidor = uvicorn.Server(uvicorn.Config(alvo, log_level="critical", lifespan="off", proxy_headers=False))
    fio = threading.Thread(target=servidor.run, kwargs={"sockets": sockets}, daemon=True)
    fio.start()
    try:
        limite = time.monotonic() + 15
        while not servidor.started and time.monotonic() < limite:
            time.sleep(0.05)
        assert servidor.started, "o servidor não subiu"

        # A porta principal serve a API como sempre serviu…
        assert httpx.get(f"http://127.0.0.1:{principal_porta}/api/health", timeout=10).status_code == 200
        # …e a porta do túnel não serve REST nenhuma. É ISTO que o worker passa a alcançar.
        for rota in ("/api/health", "/api/workers", "/api/instagram/profiles"):
            r = httpx.get(f"http://127.0.0.1:{worker_porta}{rota}", timeout=10)
            assert r.status_code == 404, (rota, r.status_code)
        assert httpx.post(f"http://127.0.0.1:{worker_porta}/api/admin/shutdown", timeout=10).status_code == 404
    finally:
        servidor.should_exit = True
        fio.join(timeout=20)
        for s in sockets:
            s.close()


def test_segunda_subida_na_mesma_porta_falha_em_vez_de_dividir_o_trafego(harness: Harness) -> None:
    """`SO_REUSEADDR` não é ligado no Windows de propósito: lá a opção deixa DOIS processos se ligarem à MESMA
    porta, cada um recebendo parte das conexões. Dois backends no mesmo banco são dois donos das mesmas etapas —
    o projeto inteiro evita isso, e os sockets passarem a ser nossos não podia mudar o comportamento."""
    import socket as sock_mod

    from app.main import _socket_de

    s = sock_mod.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.listen(1)
    try:
        with pytest.raises(SystemExit) as saida:
            _socket_de("127.0.0.1", porta)
        assert "Outro backend já está no ar" in str(saida.value)
    finally:
        s.close()


def test_socket_do_listener_respeita_host_ipv6() -> None:
    """`conferir_exposicao` aceita `server.host: "::1"` de propósito. Um `AF_INET` fixo falharia ali com a
    mensagem errada ("outro backend já está no ar?"), mandando quem depurasse procurar um processo que não
    existe."""
    import socket as sock_mod

    from app.main import _socket_de

    if not sock_mod.has_ipv6:
        pytest.skip("host sem IPv6")
    s = _socket_de("::1", 0)
    try:
        assert s.family == sock_mod.AF_INET6
        assert s.getsockname()[0] in ("::1", "0:0:0:0:0:0:0:1")
    finally:
        s.close()
