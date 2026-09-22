"""Marcas de tempo verdadeiras num comando de aparelho remoto (achados #24, #7, #148).

O defeito medido no parque: nos 8 comandos reais da tabela, `acked_at` estava NULO em todos — inclusive nos 4
que passaram por `worker-lan-01` — e `started_at` diferia de `dispatched_at` em menos de 1 ms, porque o central
gravava `running` ANTES de despachar e o ACK do agente morria num `set` em memória que ninguém lia.

Aqui o comando de um aparelho remoto percorre os quatro marcos com quem de fato os produz: o envio pelo socket
carimba `dispatched` (com o `worker_id`), o `Ack` do worker carimba `acked`, o primeiro `Progress` carimba
`running`, e só então vem o desfecho.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from app.api import _do_action, _tratar_mensagem_do_worker
from app.main import create_app
from app.models import CommandState, InstanceActionBody
from app.workers.protocol import Ack, Hello, Progress, Result, WorkerDevice, WorkerResources
from .conftest import Harness


def _hello(**kw: Any) -> Hello:
    base = dict(worker_id="worker-lan-01", name="Notebook da LAN", agent_version="0.1.0", os="windows",
                max_slots=6, verbs=["start", "stop", "hibernate", "reset", "wake", "restart"],
                devices=[WorkerDevice(serial="emulator-5554", avd_name="worker-01", state="online", adb_port=5555)],
                resources=WorkerResources(cpu_count=12, ram_total_mb=65273, ram_free_mb=46367))
    return Hello(**{**base, **kw})


class AgenteMudo:
    """Recebe o despacho e NÃO responde nada. Quem responde é o teste, mensagem por mensagem, na ordem que
    quiser — que é a única forma de conferir cada marca de tempo isoladamente."""

    def __init__(self) -> None:
        self.enviados: list[dict[str, Any]] = []
        #: Segura o ENVIO do despacho até o teste liberar. É o que permite olhar o banco no instante exato em que
        #: o comando está a caminho e ainda não chegou — o instante que o carimbo antigo mentia.
        self.porteiro = asyncio.Event()

    async def send(self, payload: dict[str, Any]) -> None:
        self.enviados.append(payload)
        if payload.get("type") == "dispatch":
            await self.porteiro.wait()

    def despacho(self) -> dict[str, Any] | None:
        return next((p for p in self.enviados if p.get("type") == "dispatch"), None)


async def _parque_com_worker(tmp_path: Path):
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    reg = h.state.workers
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteMudo()
    link = reg.attach("worker-lan-01", agente.send)
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", ("worker-lan-01", "android-03"))
    rt = h.state.devices.get("android-03")
    rt.worker_id = "worker-lan-01"
    h.state.devices.bind_worker("worker-lan-01", list(_hello().verbs))
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return h, agente, link, cliente


async def test_as_quatro_marcas_de_um_comando_remoto_vem_de_quem_as_produz(tmp_path: Path) -> None:
    h, agente, link, cliente = await _parque_com_worker(tmp_path)
    s = h.state
    assert s is not None
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "marcas-0001"})
            assert r.status_code == 202, r.text
            cid = r.json()["command_id"]
            # A resposta HTTP NÃO chama de despachado o que ainda não saiu: o envio acontece na tarefa de fundo.
            assert r.json()["state"] == CommandState.created.value

            # O despacho está EM TRÂNSITO (o agente o recebeu, o envio ainda não retornou): o comando continua
            # `created`. Era exatamente aqui que o carimbo antigo mentia, dentro da requisição HTTP.
            await h.wait(lambda: agente.despacho() is not None, what="despacho chegar ao agente")
            assert s.commands.get(cid)["state"] == CommandState.created.value
            assert s.commands.get(cid)["dispatched_at"] is None

            # 1) O envio CONCLUIU: só agora `dispatched`, e com o worker já gravado.
            agente.porteiro.set()
            await h.wait(lambda: s.commands.get(cid)["dispatched_at"] is not None, what="marca de despacho")
            linha = s.commands.get(cid)
            assert linha["state"] == CommandState.dispatched.value
            assert linha["dispatched_at"] and linha["worker_id"] == "worker-lan-01"
            assert linha["acked_at"] is None and linha["started_at"] is None

            # 2) O worker confirma o RECEBIMENTO. Era isto que nunca chegava ao banco.
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link, Ack(command_id=cid))
            linha = s.commands.get(cid)
            assert linha["state"] == CommandState.acked.value and linha["acked_at"]
            assert linha["started_at"] is None, "receber não é começar"

            # 3) O primeiro progresso do worker é o que significa "começou a agir".
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                             Progress(command_id=cid, message="desligando worker-01"))
            linha = s.commands.get(cid)
            assert linha["state"] == CommandState.running.value and linha["started_at"]

            # 4) Desfecho.
            cerca = int(linha["fence"])
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                             Result(command_id=cid, outcome="succeeded", fence=cerca))
            await h.wait(lambda: s.commands.get(cid)["state"] == CommandState.succeeded.value,
                         what="comando concluído")
            linha = s.commands.get(cid)
            marcas = [linha["created_at"], linha["dispatched_at"], linha["acked_at"], linha["started_at"],
                      linha["finished_at"]]
            assert all(marcas), marcas
            assert marcas == sorted(marcas), f"as marcas têm de ser monotônicas: {marcas}"
            # O defeito medido: `started_at` a menos de 1 ms de `dispatched_at` em TODOS os comandos reais.
            assert linha["dispatched_at"] != linha["started_at"]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_progresso_do_worker_vira_evento_do_comando_e_nao_log_solto(tmp_path: Path) -> None:
    """Antes, `Progress` virava `bus.emit("log", ...)` sem `instance_id` e sem vínculo com o comando — a
    interface não tinha como mostrar."""
    h, agente, link, cliente = await _parque_com_worker(tmp_path)
    s = h.state
    assert s is not None
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/start", json={"idempotency_key": "marcas-0002"})
            cid = r.json()["command_id"]
            agente.porteiro.set()
            await h.wait(lambda: s.commands.get(cid)["dispatched_at"] is not None, what="marca de despacho")
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                             Progress(command_id=cid, message="subindo worker-01 na porta 5554"))
        eventos = s.db.query("SELECT * FROM events WHERE kind='command.updated' ORDER BY id DESC")
        alvo = next(e for e in eventos if "subindo worker-01" in e["message"])
        assert alvo["instance_id"] == "android-03"
        dados = json.loads(alvo["data"])
        assert dados["progress"] == "subindo worker-01 na porta 5554"
        assert dados["command"]["id"] == cid and dados["command"]["state"] == CommandState.running.value
    finally:
        if h.state is not None:
            await h.state.stop()


def test_reconciliacao_separa_sem_ack_de_com_ack(tmp_path: Path) -> None:
    """O que o ACK persistido compra numa queda: dois motivos diferentes para dois casos diferentes.

    Continua `uncertain` nos dois — o envio ter tido sucesso não prova que chegou, e o ACK pode ter se perdido na
    MESMA queda —, mas quem for decidir se repete passa a ver qual dos dois casos é.
    """
    from app.db import Database
    from app.commands.store import CommandStore
    from .conftest import make_config

    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    loja = CommandStore(db)

    loja.create(command_id="c-sem-ack", instance_id="android-01", verb="stop", idempotency_key="k-sem-ack")
    loja.transition("c-sem-ack", CommandState.dispatched, worker_id="worker-lan-01")
    loja.create(command_id="c-com-ack", instance_id="android-02", verb="stop", idempotency_key="k-com-ack")
    loja.transition("c-com-ack", CommandState.dispatched, worker_id="worker-lan-01")
    loja.transition("c-com-ack", CommandState.acked)

    loja.reconcile_after_restart()
    sem, com = loja.get("c-sem-ack"), loja.get("c-com-ack")
    assert sem["state"] == com["state"] == CommandState.uncertain.value
    assert "nunca confirmou o recebimento" in sem["reason"]
    assert "depois de o worker confirmar" in com["reason"]


async def test_worker_que_some_entre_o_aceite_e_a_tarefa_nao_deixa_o_comando_preso(tmp_path: Path) -> None:
    """A rota é decidida UMA vez, no aceite. Se o worker cair antes de a tarefa rodar, o comando fecha com
    `failed` — nada aconteceu no aparelho. Reavaliar a rota aqui deixaria o comando aberto para sempre, e comando
    aberto agora TRANCA o aparelho (409 `device_busy`): o defeito cosmético viraria um aparelho inutilizado."""
    h, _agente, link, cliente = await _parque_com_worker(tmp_path)
    s = h.state
    assert s is not None
    try:
        async with cliente:
            s.commands.create(command_id="c-sumiu", instance_id="android-03", verb="stop",
                              idempotency_key="k-sumiu")
            rt = s.devices.get("android-03")
            # A queda acontece entre o aceite HTTP e a primeira volta da tarefa.
            s.workers.detach("worker-lan-01", "socket fechou", link)
            s.devices.bind_worker("worker-lan-01", None)

            await _do_action(s, rt, "stop", InstanceActionBody(), "c-sumiu", True)
            linha = s.commands.get("c-sumiu")
            assert linha["state"] == CommandState.failed.value
            assert "não está conectado" in linha["reason"]
            assert linha["finished_at"] and linha["dispatched_at"] is None
            # E o aparelho volta a aceitar comando em vez de ficar trancado.
            assert s.commands.open_for_instance("android-03") is None
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_mensagem_do_worker_que_quebra_nao_derruba_o_canal(tmp_path: Path) -> None:
    """Registrar estado é importante; manter o worker conectado é mais. Um erro ao gravar não pode custar o canal."""
    h, _agente, link, cliente = await _parque_com_worker(tmp_path)
    s = h.state
    assert s is not None
    try:
        async with cliente:
            # Comando que não existe: `get` devolve None e nada estoura; e mesmo um erro real fica contido.
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link, Ack(command_id="c-que-nao-existe"))
            await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                             Progress(command_id="c-que-nao-existe", message="oi"))
            assert s.workers.live.get("worker-lan-01") is link
            await asyncio.sleep(0)
    finally:
        if h.state is not None:
            await h.state.stop()
