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
import time
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app, create_worker_app
from app.models import CommandState
from app.security import local_secret
from app.workers.portao import BLOQUEIO_S, FALHAS_ATE_BLOQUEIO, PENDENTES_POR_IP, PortaoDoWorker
from app.workers.protocol import MARCA_DE_FILA

from .conftest import Harness

PAR_LOCAL = ("127.0.0.1", 123)


def _hello(worker_id: str = "worker-lan-01") -> dict:
    return {"worker_id": worker_id, "name": "Notebook da LAN", "agent_version": "0.1.0", "os": "windows",
            "max_slots": 1, "verbs": ["start", "stop"], "devices": [], "resources": None}


def _caminhos(no: object) -> set[str]:
    """Todo caminho servido por `no`, descendo em routers incluídos.

    Achatar em vez de ler `app.routes` direto porque o FastAPI embrulha o que foi incluído por
    `include_router` (hoje num `_IncludedRouter`), e uma leitura de uma camada só devolveria conjunto vazio —
    um teste que passa sem olhar nada. O que interessa é o conjunto de caminhos, venha de onde vier.
    """
    caminho = getattr(no, "path", None)
    if isinstance(caminho, str):
        return {caminho}
    for atributo in ("routes", "original_router", "router"):
        filho = getattr(no, atributo, None)
        if isinstance(filho, list):
            return {c for f in filho for c in _caminhos(f)}
        if filho is not None and filho is not no:
            return _caminhos(filho)
    return set()


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

    # A lista acima é por NOME, e por isso passou dois dias sem ver `/docs`: o FastAPI monta `/docs`, `/docs/oauth2-redirect`,
    # `/redoc` e `/openapi.json` sozinho, e medido do worker pelo túnel o primeiro respondia 200. Conferir por nome só acha
    # o que alguém lembrou de escrever; o contrato é "nada além dos WebSockets do worker", então é isso que se afirma.
    # Dois, e os dois se autenticam sozinhos: o de comando (credencial no `hello`) e o de mídia da observação na
    # origem (`observe_local`: token de uso único emitido pelo central no pedido; `tests/test_canal_de_midia.py`).
    assert _caminhos(app) == {"/api/worker/ws", "/api/worker/midia"}, _caminhos(app)


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


# ---------------------------------------------------------------- o canal já autenticado (achado #158)
#
# O handler do canal (handshake, Refused, bind_worker, batida, ack, resultado com cerca, detach) nunca tinha sido
# executado por teste: `test_workers.py` injeta um agente falso direto no registro e pula o transporte. O ACK já
# pagava essa conta — chegava e era descartado, e `acked_at` ficava nulo em todos os comandos reais.


def test_hello_malformado_e_recusado_com_bad_hello(harness: Harness) -> None:
    """Recusa EXPLICADA, não socket fechado calado: quem escreve um agente precisa saber o que estava errado."""
    app = create_worker_app(harness.state)
    cliente = TestClient(app, client=PAR_LOCAL)
    with pytest.raises(WebSocketDisconnect) as saida:
        with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
            ws.send_text(json.dumps({"hello": {"worker_id": "worker-lan-01"},     # faltam campos obrigatórios
                                     "enrollment_token": harness.state.workers.criar_inscricao("teste")}))
            recusa = ws.receive_json()
            assert recusa["type"] == "refused" and recusa["code"] == "bad_hello"
            assert "hello inválido" in recusa["message"]
            ws.receive_json()                             # força a leitura do fechamento
    assert saida.value.code == 4400


def test_batida_e_ack_atravessam_o_canal_e_viram_estado(harness: Harness) -> None:
    """Uma batida e um ACK pelo socket de verdade. O ACK é o que separa, numa queda, "não sabemos se chegou" de
    "chegou e não sabemos o efeito" — e ele só vale se ficar no BANCO, não num `set` em memória."""
    s = harness.state
    inscricao = s.workers.criar_inscricao("worker de teste")
    linha, _ = s.commands.create(command_id="c-ack-do-canal", instance_id="android-03", verb="stop",
                                 idempotency_key="chave-do-ack-do-canal")
    s.commands.transition(linha["id"], CommandState.dispatched, worker_id="worker-lan-01")

    cliente = TestClient(create_worker_app(s), client=PAR_LOCAL)
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": _hello(), "enrollment_token": inscricao}))
        ws.receive_json()                                 # welcome
        ws.send_text(json.dumps({"type": "heartbeat", "resources": {"cpu_percent": 3.0, "cpu_count": 8,
                                                                    "ram_total_mb": 16000, "ram_free_mb": 9000,
                                                                    "disk_free_gb": 120.0}, "devices": []}))
        ws.send_text(json.dumps({"type": "ack", "command_id": "c-ack-do-canal"}))
        # O canal trata as mensagens em ordem; a próxima pergunta REST só volta depois de o handler tê-las lido.
        app_do_painel = create_app(harness.cfg, state=s)
        app_do_painel.state.poc = s
        painel = TestClient(app_do_painel, client=PAR_LOCAL)
        for _ in range(200):
            dto = [w for w in painel.get("/api/workers").json() if w["id"] == "worker-lan-01"]
            if dto and dto[0]["resources"] and s.commands.get("c-ack-do-canal")["state"] == CommandState.acked.value:
                break
            time.sleep(0.01)
    assert dto[0]["state"] == "online" and dto[0]["resources"]["ram_free_mb"] == 9000
    final = s.commands.get("c-ack-do-canal")
    assert final["state"] == CommandState.acked.value
    assert final["acked_at"], "o ACK tem de virar marca de tempo no banco, não um set em memória"


def test_progresso_de_fila_nao_carimba_running_e_o_de_trabalho_carimba(harness: Harness) -> None:
    """Esperar vaga na fila de boot do worker não é executar.

    `running` significa "a outra máquina COMEÇOU a agir": carimbá-lo enquanto o aparelho espera na fila seria o
    mesmo carimbo falso que saiu do despacho (nos 8 comandos reais de 21/09, `started_at` ficava a ≤1 ms de
    `dispatched_at`). O progresso continua aparecendo no painel — o que muda é o estado que ele afirma.
    """
    s = harness.state
    inscricao = s.workers.criar_inscricao("worker de teste")
    linha, _ = s.commands.create(command_id="c-fila-do-canal", instance_id="android-03", verb="start",
                                 idempotency_key="chave-da-fila-do-canal")
    s.commands.transition(linha["id"], CommandState.dispatched, worker_id="worker-lan-01")

    cliente = TestClient(create_worker_app(s), client=PAR_LOCAL)
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": _hello(), "enrollment_token": inscricao}))
        ws.receive_json()                                 # welcome
        ws.send_text(json.dumps({"type": "progress", "command_id": "c-fila-do-canal",
                                 "message": f"worker-01 está {MARCA_DE_FILA} deste worker (um emulador por vez)"}))
        ws.send_text(json.dumps({"type": "ack", "command_id": "c-fila-do-canal"}))
        for _ in range(200):
            if s.commands.get("c-fila-do-canal")["state"] == CommandState.acked.value:
                break
            time.sleep(0.01)
        # Na fila: nada de `running`, nada de `started_at`.
        assert s.commands.get("c-fila-do-canal")["started_at"] is None
        ws.send_text(json.dumps({"type": "progress", "command_id": "c-fila-do-canal",
                                 "message": "subindo worker-01 na porta 5554"}))
        for _ in range(200):
            if s.commands.get("c-fila-do-canal")["state"] == CommandState.running.value:
                break
            time.sleep(0.01)
    final = s.commands.get("c-fila-do-canal")
    assert final["state"] == CommandState.running.value and final["started_at"]


def test_desconexao_devolve_o_aparelho_ao_que_o_transporte_alcanca(harness: Harness) -> None:
    """Sem agente do outro lado, o painel para de oferecer o ciclo de vida daquele aparelho: `bind_worker(None)`
    no `finally` do canal é o que impede o botão que promete o que ninguém vai executar."""
    s = harness.state
    rt = s.devices.get("android-03")
    rt.worker_id = "worker-lan-01"
    inscricao = s.workers.criar_inscricao("worker de teste")
    cliente = TestClient(create_worker_app(s), client=PAR_LOCAL)
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": _hello(), "enrollment_token": inscricao}))
        ws.receive_json()
        assert rt.worker_verbs == ["start", "stop"]       # o que o agente declarou no `hello`
    for _ in range(200):                                  # o `finally` do handler roda depois do fechamento
        if rt.worker_verbs is None:
            break
        time.sleep(0.01)
    assert rt.worker_verbs is None
    assert s.workers.live.get("worker-lan-01") is None


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


# ---------------------------------------------------------------- hibernate/wake só onde há snapshot
def test_worker_sem_hibernacao_nao_oferece_hibernate_nem_wake_ao_aparelho(harness: Harness) -> None:
    """Medido no painel: o agente anunciava `hibernate` com `android.hibernation` desligado lá, o botão aparecia e
    o clique morria no 409 do pré-voo. `Hello.hibernation` é quem sabe; `supported_verbs` é o que o painel lê —
    e um agente antigo (que declara tudo) não pode devolver o botão, então o filtro é do central."""
    rt = harness.state.devices.get("android-01")
    rt.external, rt.worker_id, rt.serial = True, "worker-lan-01", "127.0.0.1:15555"
    inscricao = harness.state.workers.criar_inscricao("worker de teste")
    cliente = TestClient(create_worker_app(harness.state), client=PAR_LOCAL)
    hello = {**_hello(), "verbs": ["start", "stop", "hibernate", "wake", "restart"], "hibernation": False}
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": hello, "enrollment_token": inscricao}))
        ws.receive_json()
        assert set(rt.worker_verbs or []) == {"start", "stop", "restart"}
        assert not {"hibernate", "wake"} & set(harness.state.devices.dto(rt).supported_verbs)


def test_worker_com_hibernacao_mantem_os_dois_verbos(harness: Harness) -> None:
    rt = harness.state.devices.get("android-01")
    rt.external, rt.worker_id, rt.serial = True, "worker-lan-01", "127.0.0.1:15555"
    inscricao = harness.state.workers.criar_inscricao("worker de teste")
    cliente = TestClient(create_worker_app(harness.state), client=PAR_LOCAL)
    hello = {**_hello(), "verbs": ["start", "stop", "hibernate", "wake"], "hibernation": True}
    with cliente.websocket_connect("/api/worker/ws", headers={"host": "127.0.0.1:18000"}) as ws:
        ws.send_text(json.dumps({"hello": hello, "enrollment_token": inscricao}))
        ws.receive_json()
        assert {"hibernate", "wake"} <= set(rt.worker_verbs or [])
