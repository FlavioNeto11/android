"""Transporte NATS contra um JetStream FALSO (frente F4, item A1). Nenhum broker de verdade é tocado.

Dois defeitos que só apareceriam no dia em que alguém ligasse a bandeira:

1. **Assunto errado.** O comando era publicado em `comandos.<worker_id>` e cada réplica assina
   `comandos.<owner_id>`. O agente remoto não fala NATS — quem segura o canal dele é a réplica que hospeda o
   aparelho —, então todo comando de aparelho remoto ficava sem consumidor, parado em `created` para sempre.
2. **`ack_wait` declarado e nunca aplicado** (`config=None`): valia o padrão do servidor, e um `start` de 540 s
   seria reentregue no meio. E mesmo com o prazo certo, um comando que espera o cadeado do aparelho atrás de um
   `reset` passa do prazo sem nada estar errado — por isso o consumidor manda `in_progress` enquanto trabalha.

O falso cobre só a superfície que o transporte usa (`connect`, `jetstream`, `add_stream`, `subscribe`, `publish`,
`ack`/`term`/`in_progress`, `ConsumerConfig.ack_wait` em segundos, como no nats-py 2.x). O broker real continua
`not_run`: o aceite da bandeira é um comando real atravessando (docs/parque-distribuido.md).
"""
from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.api import executar_envelope
from app.commands.transport import ACK_WAIT_S, NatsJetStreamTransport
from app.devices.verbs import PRAZO_POR_VERBO
from app.models import CommandState

from .test_contrato_de_worker import _acao, _desfecho, _parque


@dataclass
class ConsumerConfigFalso:
    """O `nats.js.api.ConsumerConfig`: `ack_wait` em SEGUNDOS (o nats-py converte para ns ao serializar)."""

    ack_wait: float | None = None


class MensagemFalsa:
    def __init__(self, assunto: str, data: bytes) -> None:
        self.subject, self.data = assunto, data
        self.acks = self.terms = self.em_andamento = 0

    async def ack(self) -> None:
        self.acks += 1

    async def term(self) -> None:
        self.terms += 1

    async def in_progress(self) -> None:
        self.em_andamento += 1


class AssinaturaFalsa:
    def __init__(self, broker: "JetStreamFalso", assunto: str, durable: str, cb: Any, config: Any) -> None:
        self.broker, self.assunto, self.durable, self.cb, self.config = broker, assunto, durable, cb, config

    async def unsubscribe(self) -> None:
        self.broker.assinaturas.pop(self.assunto, None)


class JetStreamFalso:
    """Broker em memória, compartilhado entre réplicas: assunto EXATO → consumidor. Sem consumidor, a mensagem
    fica no stream sem ninguém para executá-la — que é o que acontecia com `comandos.<worker_id>`."""

    def __init__(self) -> None:
        self.streams: dict[str, list[str]] = {}
        self.assinaturas: dict[str, AssinaturaFalsa] = {}
        self.publicadas: list[str] = []
        self.ids_vistos: dict[str, int] = {}
        self.entregues: list[MensagemFalsa] = []
        self._tarefas: set[asyncio.Task[None]] = set()

    async def add_stream(self, *, name: str, subjects: list[str]) -> None:
        self.streams[name] = subjects

    async def subscribe(self, subject: str, *, durable: str, cb: Any, manual_ack: bool, config: Any) -> Any:
        assert manual_ack, "o ack tem de ser manual: ele só vai depois do desfecho"
        self.assinaturas[subject] = AssinaturaFalsa(self, subject, durable, cb, config)
        return self.assinaturas[subject]

    async def publish(self, subject: str, payload: bytes, *, headers: dict[str, str] | None = None) -> Any:
        # Janela de duplicata do stream: mesma `Nats-Msg-Id` não vira segunda mensagem (o servidor devolve o ack
        # com `duplicate=True`). Sem prazo aqui — o teste só republica dentro da janela.
        msg_id = (headers or {}).get("Nats-Msg-Id")
        if msg_id and msg_id in self.ids_vistos:
            return types.SimpleNamespace(seq=self.ids_vistos[msg_id], duplicate=True)
        self.publicadas.append(subject)
        if msg_id:
            self.ids_vistos[msg_id] = len(self.publicadas)
        if (assinatura := self.assinaturas.get(subject)) is not None:
            msg = MensagemFalsa(subject, payload)
            self.entregues.append(msg)
            tarefa = asyncio.get_running_loop().create_task(assinatura.cb(msg))
            self._tarefas.add(tarefa)
            tarefa.add_done_callback(self._tarefas.discard)
        return types.SimpleNamespace(seq=len(self.publicadas))


class ConexaoFalsa:
    def __init__(self, broker: JetStreamFalso) -> None:
        self.broker = broker

    def jetstream(self) -> JetStreamFalso:
        return self.broker

    async def drain(self) -> None:
        return None


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> JetStreamFalso:
    """Instala `nats`/`nats.js.api` falsos em `sys.modules`: o `import` tardio de `start()` é o de verdade."""
    js = JetStreamFalso()
    nats_mod, js_mod, api_mod = (types.ModuleType("nats"), types.ModuleType("nats.js"),
                                 types.ModuleType("nats.js.api"))

    async def connect(_url: str, *, name: str | None = None) -> ConexaoFalsa:
        del name
        return ConexaoFalsa(js)

    nats_mod.connect = connect  # type: ignore[attr-defined]
    api_mod.ConsumerConfig = ConsumerConfigFalso  # type: ignore[attr-defined]
    nats_mod.js, js_mod.api = js_mod, api_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nats", nats_mod)
    monkeypatch.setitem(sys.modules, "nats.js", js_mod)
    monkeypatch.setitem(sys.modules, "nats.js.api", api_mod)
    return js


async def _replica(owner_id: str, **kw: Any) -> tuple[NatsJetStreamTransport, list[dict[str, Any]]]:
    recebidos: list[dict[str, Any]] = []

    async def handler(envelope: dict[str, Any]) -> None:
        recebidos.append(envelope)

    t = NatsJetStreamTransport("nats://falso:4222", owner_id=owner_id, **kw)
    await t.start(handler)
    return t, recebidos


async def _ate(condicao: Any, o_que: str, prazo: float = 3.0) -> None:
    t = 0.0
    while not condicao():
        await asyncio.sleep(0.01)
        t += 0.01
        assert t < prazo, f"tempo esgotado esperando {o_que}"


# ---------------------------------------------------------------- o assunto
async def test_comando_de_aparelho_remoto_chega_a_replica_que_hospeda(broker: JetStreamFalso) -> None:
    """Réplica A (só painel) aceita um comando para o aparelho que a réplica B hospeda — e cujo emulador roda no
    worker da LAN. Quem executa é B, pelo canal que ela segura com o worker; nunca um assunto do worker."""
    a, recebidos_a = await _replica("replica-a")
    b, recebidos_b = await _replica("replica-b")
    envelope = {"command_id": "c-1", "instance_id": "android-03", "worker_id": "worker-lan-01",
                "hosted_by": "replica-b", "verb": "start"}
    await a.publish(envelope)
    await _ate(lambda: recebidos_b, "a réplica hospedeira receber a ordem")
    assert broker.publicadas == ["comandos.replica-b"]
    assert recebidos_b == [envelope] and recebidos_a == []
    await _ate(lambda: broker.entregues[0].acks == 1, "o ack depois do desfecho")
    await a.close()
    await b.close()


async def test_aparelho_sem_hospedeiro_registrado_fica_com_quem_publica(broker: JetStreamFalso) -> None:
    """`hosted_by` nulo (banco anterior à 027, um backend só): é de quem publica, a regra de sempre."""
    t, recebidos = await _replica("central")
    await t.publish({"command_id": "c-2", "instance_id": "android-01", "worker_id": "central", "hosted_by": None})
    await _ate(lambda: recebidos, "a própria réplica executar")
    assert broker.publicadas == ["comandos.central"]
    await t.close()


async def test_republicacao_do_outbox_nao_vira_segunda_entrega(broker: JetStreamFalso) -> None:
    """O outbox republica o que caiu entre publicar e marcar `sent`. Com `Nats-Msg-Id` = comando, a janela de
    duplicata do stream descarta a cópia; sem ele, seriam duas entregas do mesmo comando."""
    t, recebidos = await _replica("central")
    envelope = {"command_id": "c-5", "instance_id": "android-01", "hosted_by": "central"}
    await t.publish(envelope)
    await t.publish(envelope)
    await _ate(lambda: recebidos, "a entrega")
    await asyncio.sleep(0.05)
    assert recebidos == [envelope] and broker.publicadas == ["comandos.central"]
    await t.close()


# ---------------------------------------------------------------- ack_wait e reentrega
async def test_ack_wait_chega_ao_consumidor_e_cobre_o_maior_prazo_de_verbo(broker: JetStreamFalso) -> None:
    t, _ = await _replica("central")
    config = broker.assinaturas["comandos.central"].config
    assert config is not None, "config=None deixava valer o ack_wait padrão do servidor NATS"
    assert config.ack_wait == ACK_WAIT_S
    assert ACK_WAIT_S > max(PRAZO_POR_VERBO.values()), "um reset saudável seria reentregue no meio"
    await t.close()


async def test_handler_demorado_avisa_que_esta_vivo_e_so_confirma_no_fim(broker: JetStreamFalso) -> None:
    """O comando que espera o cadeado do aparelho passa do `ack_wait` sem nada estar errado. Sem `in_progress`,
    a reentrega o encontraria ainda em `created` e o despacharia de novo."""
    liberar = asyncio.Event()
    em_curso = asyncio.Event()

    async def handler(_envelope: dict[str, Any]) -> None:
        em_curso.set()
        await liberar.wait()

    t = NatsJetStreamTransport("nats://falso:4222", owner_id="central", ack_wait_s=0.09)
    await t.start(handler)
    await t.publish({"command_id": "c-3", "instance_id": "android-01", "hosted_by": "central"})
    await asyncio.wait_for(em_curso.wait(), timeout=2)
    msg = broker.entregues[0]
    await _ate(lambda: msg.em_andamento >= 2, "dois avisos de 'ainda estou nisto'")
    assert msg.acks == 0, "ack antes do desfecho transformaria queda no meio em comando perdido"
    liberar.set()
    await _ate(lambda: msg.acks == 1, "o ack depois do desfecho")
    parado = msg.em_andamento
    await asyncio.sleep(0.12)
    assert msg.em_andamento == parado, "o aviso de vida continuou depois de o comando terminar"
    await t.close()


async def test_handler_que_quebra_nao_confirma_e_para_de_avisar(broker: JetStreamFalso) -> None:
    """Sem ack, o JetStream reentrega — e o aviso de vida tem de parar, senão a reentrega nunca viria."""
    async def handler(_envelope: dict[str, Any]) -> None:
        raise RuntimeError("quebrou no consumo (teste)")

    t = NatsJetStreamTransport("nats://falso:4222", owner_id="central", ack_wait_s=0.06)
    await t.start(handler)
    await t.publish({"command_id": "c-4", "instance_id": "android-01", "hosted_by": "central"})
    await _ate(lambda: broker.entregues, "a entrega")
    await asyncio.sleep(0.1)
    msg = broker.entregues[0]
    assert msg.acks == 0 and msg.em_andamento == 0
    await t.close()


async def test_cancelar_a_entrega_enquanto_o_aviso_de_vida_morre_nao_confirma(broker: JetStreamFalso,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """31.347: o `suppress(CancelledError)` que cercava a espera do aviso de vida engolia o cancelamento da própria entrega se ele chegasse
    NESSA janela, e o `ack` seguinte confirmava a mensagem de um consumidor que estava sendo derrubado. A janela é alargada: o `in_progress`
    leva 0,3 s para morrer e o cancelamento da entrega chega no meio."""
    morrendo = asyncio.Event()
    liberar = asyncio.Event()

    async def in_progress_que_demora_para_morrer(self: MensagemFalsa) -> None:
        self.em_andamento += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            morrendo.set()
            await asyncio.sleep(0.3)
            raise

    monkeypatch.setattr(MensagemFalsa, "in_progress", in_progress_que_demora_para_morrer)

    async def handler(_envelope: dict[str, Any]) -> None:
        await liberar.wait()

    t = NatsJetStreamTransport("nats://falso:4222", owner_id="central", ack_wait_s=0.06)
    await t.start(handler)
    await t.publish({"command_id": "c-5", "instance_id": "android-01", "hosted_by": "central"})
    await _ate(lambda: broker.entregues and broker.entregues[0].em_andamento >= 1, "o primeiro aviso de vida preso")
    entrega = next(iter(broker._tarefas))
    liberar.set()                                  # o handler termina; o `finally` cancela o aviso de vida, que demora para morrer
    await asyncio.wait_for(morrendo.wait(), timeout=2)
    entrega.cancel()                               # chega com a entrega esperando o aviso de vida morrer
    await asyncio.wait({entrega}, timeout=3)
    msg = broker.entregues[0]
    assert msg.acks == 0, "a entrega cancelada confirmou a mensagem (o cancelamento foi engolido)"
    assert entrega.cancelled()
    await t.close()


# ---------------------------------------------------------------- de ponta a ponta, pelo central
async def test_comando_de_aparelho_do_worker_atravessa_o_broker_e_fecha(tmp_path: Path,
                                                                         broker: JetStreamFalso) -> None:
    """O caminho inteiro com a bandeira ligada: painel → outbox → NATS → réplica hospedeira → worker → desfecho.

    Antes, a ordem ia para `comandos.worker-lan-01`, que ninguém assina, e o comando ficava em `created`."""
    h, rt, cliente, _worker_id = await _parque(tmp_path, "agente")
    try:
        assert h.state is not None
        nats_t = NatsJetStreamTransport("nats://falso:4222", owner_id=h.state.cfg.owner_id or "local")
        await nats_t.start(lambda envelope: executar_envelope(h.state, envelope))
        h.state.transport = nats_t
        async with cliente as c:
            cid = await _acao(c, rt, "stop", "nats-ponta-a-ponta-01")
            linha = await _desfecho(h, cid, timeout=10.0)
        assert linha["state"] == CommandState.succeeded.value
        hospedeiro = h.state.db.scalar("SELECT hosted_by FROM instances WHERE id=?", (rt.id,))
        assert broker.publicadas == [f"comandos.{hospedeiro or h.state.cfg.owner_id}"]
        await _ate(lambda: broker.entregues[0].acks == 1, "o ack depois do desfecho")
        await nats_t.close()
    finally:
        await h.state.stop()  # type: ignore[union-attr]
