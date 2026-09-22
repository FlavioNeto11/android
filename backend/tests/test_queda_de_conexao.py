"""Queda de conexão não é falha de execução (achados #20, #6).

O defeito, do jeito que doía: o `finally` da sessão do agente fazia `for t in self._tarefas.values(): t.cancel()`.
Um `reset` (stop seguido de start com wipe) parava entre os dois; um `start` abandonava a espera de boot sem
`prepare_for_automation`. E o desfecho que o agente tentaria enviar era descartado, porque `_send` voltava calado
com `_ws is None`. No central o comando virava `uncertain` — honesto — e ninguém nunca mais perguntava por ele.

Agora: o verbo TERMINA, o desfecho vai para um diário em disco, o `hello` da reconexão diz o que ainda está em
execução, e o central aceita o resultado tardio procurando o comando no BANCO.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.api import _tratar_mensagem_do_worker
from app.models import CommandState
from app.worker import agent as agent_mod
from app.workers.protocol import Dispatch, Result
from .conftest import Harness


# ---------------------------------------------------------------- o agente
def _agente(tmp_path: Path):
    from app.worker.settings import DeviceSpec, WorkerSettings

    settings = WorkerSettings(worker_id="worker-lan-01", name="Notebook", work_dir=str(tmp_path / "farm"),
                              devices=[DeviceSpec(instance_id="android-03", avd_name="worker-01",
                                                  console_port=5554)])
    a = agent_mod.Agent(settings, enrollment="token-de-inscricao-de-teste")
    return a


class WsFalso:
    """WebSocket de mentira com um fim CONTROLADO: `encerrar()` é a rede caindo."""

    def __init__(self) -> None:
        self.enviados: list[dict[str, Any]] = []
        self.fila: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def __aenter__(self) -> "WsFalso":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    async def send(self, bruto: str) -> None:
        self.enviados.append(json.loads(bruto))

    async def recv(self) -> str:
        return json.dumps({"type": "welcome", "server_time": "2026-09-22T10:00:00.000Z", "heartbeat_s": 30.0})

    def __aiter__(self) -> "WsFalso":
        return self

    async def __anext__(self) -> str:
        item = await self.fila.get()
        if item is None:
            raise StopAsyncIteration
        return json.dumps(item)

    async def encerrar(self) -> None:
        await self.fila.put(None)

    def de_tipo(self, tipo: str) -> list[dict[str, Any]]:
        return [p for p in self.enviados if p.get("type") == tipo]


async def _ate(condicao, o_que: str, prazo: float = 5.0) -> None:
    t = 0.0
    while not condicao():
        await asyncio.sleep(0.01)
        t += 0.01
        assert t < prazo, f"esperando {o_que}"


def _dispatch(command_id: str = "c-1", fence: int = 3) -> dict[str, Any]:
    return Dispatch(command_id=command_id, fence=fence, verb="reset", instance_id="android-03",
                    serial="emulator-5554").model_dump()


async def test_queda_do_canal_nao_cancela_o_verbo_e_o_desfecho_chega_depois(tmp_path: Path,
                                                                            monkeypatch: Any) -> None:
    agente = _agente(tmp_path)
    solta = asyncio.Event()

    async def run_lento(*_a: Any, **_k: Any) -> dict[str, Any]:
        await solta.wait()
        return {"wiped": True}

    agente.executor.run = run_lento                                     # type: ignore[assignment]
    ws1, ws2 = WsFalso(), WsFalso()
    proximos = [ws1, ws2]
    monkeypatch.setattr(agent_mod.websockets, "connect", lambda *_a, **_k: proximos.pop(0))

    sessao = asyncio.create_task(agente._sessao())
    await ws1.fila.put(_dispatch())
    await _ate(lambda: "c-1" in agente._tarefas, "a tarefa do comando nascer")
    tarefa = agente._tarefas["c-1"]

    # A REDE CAI no meio do `reset`.
    await ws1.encerrar()
    await sessao
    assert agente._ws is None
    assert not tarefa.done(), "o verbo em andamento NÃO pode ser cancelado pela queda do canal"

    # O agente reconecta enquanto o verbo ainda roda, e DIZ o que continua na mão dele.
    sessao2 = asyncio.create_task(agente._sessao())
    await _ate(lambda: bool(ws2.enviados), "o hello da reconexão")
    assert ws2.enviados[0]["hello"]["inflight"] == ["c-1"]

    # O verbo termina. O desfecho vai para o diário ANTES de sair, e sai pelo canal novo.
    solta.set()
    await tarefa
    await _ate(lambda: bool(ws2.de_tipo("result")), "o desfecho tardio chegar ao central")
    desfecho = ws2.de_tipo("result")[0]
    assert desfecho["command_id"] == "c-1" and desfecho["outcome"] == "succeeded" and desfecho["fence"] == 3
    assert "c-1" in agente._diario.resultados, "sem confirmação, o desfecho continua guardado"

    # Só o `result_ack` do central apaga o diário.
    await ws2.fila.put({"type": "result_ack", "command_id": "c-1"})
    await _ate(lambda: "c-1" not in agente._diario.resultados, "o diário ser limpo pela confirmação")
    await ws2.encerrar()
    await sessao2


async def test_desfecho_produzido_sem_canal_fica_no_diario_e_e_reenviado(tmp_path: Path) -> None:
    """A metade que sobrevive até ao reinício do processo do agente: o diário está em disco."""
    agente = _agente(tmp_path)
    agente._ws = None
    msg = Dispatch(command_id="c-7", fence=2, verb="stop", instance_id="android-03", serial="emulator-5554")
    await agente._resultado(msg, "succeeded", None, {"how": "graceful"})
    assert agente._diario.resultados["c-7"]["outcome"] == "succeeded"

    # Outro processo do agente, mesmo `work_dir`: o desfecho continua lá.
    outro = _agente(tmp_path)
    assert "c-7" in outro._diario.resultados
    ws = WsFalso()
    outro._ws = ws
    await outro._reenviar_pendentes()
    assert ws.de_tipo("result")[0]["command_id"] == "c-7"
    assert outro._diario.confirmar("c-7") is True
    assert _agente(tmp_path)._diario.resultados == {}


# ---------------------------------------------------------------- o central
async def _parque(tmp_path: Path):
    from app.workers.protocol import Hello, WorkerDevice

    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    reg = h.state.workers
    hello = Hello(worker_id="worker-lan-01", name="Notebook da LAN", agent_version="0.1.0", os="windows",
                  max_slots=6, verbs=["start", "stop", "reset"],
                  devices=[WorkerDevice(serial="emulator-5554", avd_name="worker-01")])
    reg.autenticar(hello, token=None, enrollment=reg.criar_inscricao())
    ws = WsFalso()
    link = reg.attach("worker-lan-01", lambda p: ws.send(json.dumps(p)))
    return h, link, ws


def _comando_incerto(s: Any, command_id: str = "c-tardio") -> int:
    """Um comando que o central deu por perdido quando o canal caiu — o estado real de um `reset` interrompido."""
    linha, _ = s.commands.create(command_id=command_id, instance_id="android-03", verb="reset",
                                 idempotency_key=f"k-{command_id}")
    s.commands.transition(command_id, CommandState.dispatched, worker_id="worker-lan-01")
    s.commands.transition(command_id, CommandState.acked)
    s.commands.transition(command_id, CommandState.uncertain, reason="o canal do worker caiu")
    return int(linha["fence"])


async def test_central_aceita_o_resultado_tardio_e_confirma_o_recebimento(tmp_path: Path) -> None:
    """`on_result` devolvia `False` para comando fora de `link.pendentes` e o desfecho morria ali. Agora o
    comando é procurado no BANCO e a transição `uncertain → succeeded`, que a tabela sempre permitiu, acontece."""
    h, link, ws = await _parque(tmp_path)
    s = h.state
    assert s is not None
    try:
        cerca = _comando_incerto(s)
        await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                         Result(command_id="c-tardio", outcome="succeeded", fence=cerca,
                                                data={"wiped": True}))
        linha = s.commands.get("c-tardio")
        assert linha["state"] == CommandState.succeeded.value
        assert json.loads(linha["result"])["wiped"] is True
        # E o agente recebe a confirmação, senão reenviaria o mesmo desfecho para sempre.
        assert ws.de_tipo("result_ack")[0]["command_id"] == "c-tardio"
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_resultado_tardio_com_cerca_velha_e_recusado_mas_confirmado(tmp_path: Path) -> None:
    """A cerca vale também para o caminho tardio: ordem vencida não sobrescreve o presente.

    O `result_ack` sai mesmo assim, de propósito: ele diz "recebi e tratei", não "apliquei". Sem isso o agente
    reenviaria para sempre um desfecho que nunca será aceito.
    """
    h, link, ws = await _parque(tmp_path)
    s = h.state
    assert s is not None
    try:
        cerca = _comando_incerto(s)
        await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                         Result(command_id="c-tardio", outcome="succeeded", fence=cerca - 1))
        assert s.commands.get("c-tardio")["state"] == CommandState.uncertain.value
        # Recusado, e ainda assim confirmado: o agente para de reenviar em vez de martelar o central.
        assert ws.de_tipo("result_ack")[0]["command_id"] == "c-tardio"

        # Sem cerca nenhuma também não passa.
        await _tratar_mensagem_do_worker(s, "worker-lan-01", link,
                                         Result(command_id="c-tardio", outcome="succeeded"))
        assert s.commands.get("c-tardio")["state"] == CommandState.uncertain.value

        # De outro worker, tampouco.
        await _tratar_mensagem_do_worker(s, "worker-lan-02", link,
                                         Result(command_id="c-tardio", outcome="succeeded", fence=cerca))
        assert s.commands.get("c-tardio")["state"] == CommandState.uncertain.value
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_reconexao_avisa_no_painel_o_que_o_worker_ainda_executa(tmp_path: Path) -> None:
    """`uncertain` continua sendo `uncertain` — só sai por desfecho de verdade. Mas quem olha o painel precisa
    saber que o worker voltou dizendo que AQUELE comando ainda está na mão dele."""
    from app.api import _anunciar_inflight

    h, _link, _ws = await _parque(tmp_path)
    s = h.state
    assert s is not None
    try:
        _comando_incerto(s)
        _anunciar_inflight(s, "worker-lan-01", ["c-tardio", "c-que-nao-existe"])
        eventos = s.db.query("SELECT * FROM events WHERE kind='command.updated' ORDER BY id DESC")
        alvo = next(e for e in eventos if "ainda está executando" in e["message"])
        assert alvo["instance_id"] == "android-03"
        assert json.loads(alvo["data"])["inflight"] is True
        assert s.commands.get("c-tardio")["state"] == CommandState.uncertain.value
    finally:
        if h.state is not None:
            await h.state.stop()
