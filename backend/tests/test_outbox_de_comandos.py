"""Outbox de comandos e eventos entre réplicas (item 5.6, achado #30).

O defeito que estes testes travam: o "transporte" do comando era `asyncio.create_task(_do_action(...))` logo
depois de gravar o estado. Uma queda entre as duas linhas deixava um comando que o banco dizia ter sido aceito
e que ninguém nunca enviou — e no boot seguinte ele virava `uncertain`, que é falso nos dois sentidos: nada foi
executado, e `uncertain` é justamente o estado que ninguém repete sozinho.

E o segundo defeito: o `EventBus` só transmite para os WebSockets do PRÓPRIO processo, então com dois backends
no mesmo banco o painel ligado num não via nada do que o outro fazia.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.commands.outbox import PENDING, SENT, CommandOutbox
from app.commands.transport import NATS, WEBSOCKET, LocalTransport, build_transport
from app.events import EventBus
from app.main import create_app
from app.models import CommandState
from .conftest import Harness

TERMINAIS = (CommandState.succeeded.value, CommandState.failed.value, CommandState.uncertain.value,
             CommandState.rejected.value, CommandState.cancelled.value)


class TransporteMudo:
    """Um transporte que RECUSA publicar: é a queda do broker, ou a queda do processo antes do envio.

    Vale pelas duas coisas porque o que interessa é o mesmo nos dois casos — a ordem foi aceita e não saiu.
    """

    name = "mudo"

    def __init__(self) -> None:
        self.tentativas: list[dict[str, Any]] = []

    async def start(self, handler: Any) -> None:
        self.handler = handler

    async def publish(self, envelope: dict[str, Any]) -> None:
        self.tentativas.append(envelope)
        raise RuntimeError("broker fora do ar (teste)")

    async def close(self) -> None:
        return None


async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ====================================================================== a entrega vira linha


async def test_a_ordem_aceita_vira_linha_no_outbox_e_sai_uma_vez(harness: Harness) -> None:
    """Caminho feliz: a linha existe, é publicada, é marcada `sent`, e `attempt` passa a dizer a verdade."""
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/android-01/actions/home", json={"idempotency_key": "outbox-feliz-01"})
    cid = r.json()["command_id"]
    linha = harness.state.outbox.get(cid)
    assert linha is not None, "a entrega devida tem de existir no outbox"
    assert linha["verb"] == "home" and linha["instance_id"] == "android-01"
    # `confirm` vem no envelope: `commands.params` guarda só o `app_id`, e sem o corpo inteiro um reenvio
    # perderia a autorização humana que separa um `reset` pedido de um `reset` acidental.
    assert "confirm" in CommandOutbox.payload_of(linha)["body"]
    assert linha["state"] == SENT and linha["attempts"] == 1
    assert harness.state.commands.get(cid)["attempt"] == 1, "a coluna existia desde a 013 e nunca era contada"

    await harness.wait(lambda: harness.state.commands.get(cid)["state"] in TERMINAIS, what="desfecho")


async def test_transporte_fora_do_ar_nao_perde_o_comando(harness: Harness) -> None:
    """Broker recusando: o pedido continua aceito, a linha continua pendente, e o laço de repetição a publica."""
    harness.state.transport = mudo = TransporteMudo()
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/android-01/actions/home", json={"idempotency_key": "outbox-mudo-01"})
    assert r.status_code == 202, "transporte fora do ar não é erro de quem clicou"
    cid = r.json()["command_id"]
    assert mudo.tentativas and mudo.tentativas[0]["command_id"] == cid
    linha = harness.state.outbox.get(cid)
    assert linha["state"] == PENDING and linha["attempts"] == 0, "o que não saiu não pode estar marcado como saído"
    assert harness.state.commands.get(cid)["state"] not in TERMINAIS

    # O broker volta: o laço (chamado aqui à mão, para não depender de relógio) publica de novo.
    harness.state.transport = LocalTransport()
    from app.api import executar_envelope
    await harness.state.transport.start(lambda env: executar_envelope(harness.state, env))
    await harness.state._drenar_outbox(anunciar=False)
    assert harness.state.outbox.get(cid)["state"] == SENT
    await harness.wait(lambda: harness.state.commands.get(cid)["state"] in TERMINAIS, what="desfecho")


# ====================================================================== a queda


async def test_queda_antes_da_entrega_nao_vira_incerto_e_o_dreno_executa(tmp_path: Path) -> None:
    """O coração do achado #30, ponta a ponta.

    Antes: comando aceito e nunca enviado virava `uncertain` no boot — "pode ter acontecido, ninguém repete".
    Agora: a linha pendente diz que a entrega é DEVIDA, a reconciliação o deixa em paz, e o dreno o executa.
    """
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        h.state.transport = TransporteMudo()                 # a ordem é aceita e não sai
        async with await _cliente(h) as c:
            r = await c.post("/api/instances/android-01/actions/home", json={"idempotency_key": "outbox-queda-01"})
        cid = r.json()["command_id"]
        assert h.state.outbox.get(cid)["state"] == PENDING
        await h.crash()                                      # o processo morre com a entrega devida

        await h.boot()                                       # e volta
        estado = h.state.commands.get(cid)["state"]
        assert estado != CommandState.uncertain.value, (
            "a entrega pendente não é um comando sem desfecho: é um comando na fila")
        await h.wait(lambda: h.state.commands.get(cid)["state"] in TERMINAIS, what="desfecho depois do dreno")
        assert h.state.outbox.get(cid)["state"] == SENT
        assert h.state.commands.get(cid)["attempt"] == 1, "saiu uma vez, e só uma"
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_sem_entrega_pendente_a_reconciliacao_continua_como_antes(harness: Harness) -> None:
    """O outbox não afrouxa a reconciliação: comando em voo SEM linha pendente continua ganhando desfecho."""
    linha, _ = harness.state.commands.create(
        command_id="c-sem-outbox", instance_id="android-01", verb="restart",
        idempotency_key="sem-outbox-01", requested_by="panel")
    assert not harness.state.outbox.is_pending("c-sem-outbox")
    mudados = harness.state.commands.reconcile_after_restart()
    assert "c-sem-outbox" in {m["id"] for m in mudados}
    assert harness.state.commands.get("c-sem-outbox")["state"] == CommandState.failed.value
    del linha


async def test_reentrega_de_comando_ja_fechado_nao_executa_de_novo(harness: Harness) -> None:
    """Fila durável entrega AO MENOS uma vez. Quem impede o efeito duplo aqui é a máquina de estados."""
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/android-01/actions/home", json={"idempotency_key": "outbox-dupla-01"})
    cid = r.json()["command_id"]
    await harness.wait(lambda: harness.state.commands.get(cid)["state"] in TERMINAIS, what="desfecho")
    fechado = dict(harness.state.commands.get(cid))

    # A mesma ordem chega de novo pelo transporte, como faria um JetStream que não recebeu o ack.
    from app.api import executar_envelope
    linha = harness.state.outbox.get(cid)
    envelope = {"command_id": cid, "instance_id": linha["instance_id"], "worker_id": linha["worker_id"],
                "verb": linha["verb"], **CommandOutbox.payload_of(linha)}
    await executar_envelope(harness.state, envelope)
    depois = dict(harness.state.commands.get(cid))
    assert depois["state"] == fechado["state"] and depois["finished_at"] == fechado["finished_at"]


async def test_o_dreno_nao_encosta_no_aparelho_de_outro_hospedeiro(harness: Harness) -> None:
    """Dois backends no mesmo banco: o que sobe não pode drenar a fila do outro e executar na máquina errada."""
    s = harness.state
    s.outbox.enqueue(command_id="c-alheio", instance_id="android-02", verb="home", worker_id=None,
                     payload={"body": {}, "remoto": False})
    s.db.execute("UPDATE instances SET hosted_by=? WHERE id=?", ("outro-backend", "android-02"))
    meus = {r["command_id"] for r in s.outbox.pending(hospedados_por=s.cfg.owner_id)}
    assert "c-alheio" not in meus
    assert "c-alheio" in {r["command_id"] for r in s.outbox.pending(hospedados_por="outro-backend")}


async def test_descartar_tira_da_fila_so_o_que_nao_saiu(harness: Harness) -> None:
    s = harness.state
    s.outbox.enqueue(command_id="c-desc", instance_id="android-01", verb="home", worker_id=None,
                     payload={"body": {}, "remoto": False})
    assert s.outbox.discard("c-desc") is True
    s.outbox.enqueue(command_id="c-saiu", instance_id="android-01", verb="home", worker_id=None,
                     payload={"body": {}, "remoto": False})
    assert s.outbox.mark_sent("c-saiu") is True
    assert s.outbox.mark_sent("c-saiu") is False, "só a primeira saída conta"
    assert s.outbox.discard("c-saiu") is False, "o que já saiu não some da fila por descarte"


# ====================================================================== transporte


def test_transporte_padrao_e_o_de_sempre_e_o_desconhecido_falha_alto() -> None:
    assert isinstance(build_transport(WEBSOCKET, owner_id="a", url=None), LocalTransport)
    with pytest.raises(RuntimeError, match="NATS_URL"):
        build_transport(NATS, owner_id="a", url=None)
    with pytest.raises(RuntimeError, match="desconhecido"):
        build_transport("carta-registrada", owner_id="a", url=None)


async def test_transporte_local_entrega_ao_handler() -> None:
    t = LocalTransport()
    recebidos: list[dict[str, Any]] = []
    pronto = asyncio.Event()

    async def handler(env: dict[str, Any]) -> None:
        recebidos.append(env)
        pronto.set()

    await t.start(handler)
    await t.publish({"command_id": "c-1"})
    await asyncio.wait_for(pronto.wait(), timeout=2)
    assert recebidos == [{"command_id": "c-1"}]
    await t.close()


# ====================================================================== eventos entre réplicas


async def test_o_evento_de_uma_replica_chega_aos_assinantes_da_outra(harness: Harness) -> None:
    """Sem isto, o painel ligado na réplica B via a tela parada enquanto a réplica A executava um objetivo."""
    db = harness.state.db
    a = EventBus(db, origin="backend-a")
    b = EventBus(db, origin="backend-b")
    fila = b.subscribe()
    b.replicar_agora()                       # a primeira olhada só marca o presente: histórico não é novidade

    a.emit("log", "objetivo iniciado na réplica A", run_id=None)
    entregues = b.replicar_agora()
    assert [e.message for e in entregues] == ["objetivo iniciado na réplica A"]
    assert fila.get_nowait().message == "objetivo iniciado na réplica A"


async def test_a_replica_nao_entrega_duas_vezes_o_proprio_evento(harness: Harness) -> None:
    db = harness.state.db
    b = EventBus(db, origin="backend-b")
    fila = b.subscribe()
    b.replicar_agora()
    b.emit("log", "isto é meu")              # já foi entregue pelo caminho direto do emit
    assert fila.qsize() == 0, "sem laço ligado, o emit local não entrega — é o que torna o teste honesto"
    assert b.replicar_agora() == [], "e a replicação não pode reentregá-lo"


async def test_a_origem_fica_gravada_no_evento(harness: Harness) -> None:
    bus = EventBus(harness.state.db, origin="backend-z")
    rec = bus.emit("log", "com origem")
    assert harness.state.db.scalar("SELECT origin FROM events WHERE id=?", (rec.id,)) == "backend-z"


async def test_cancelar_antes_do_envio_tira_a_entrega_da_fila(harness: Harness) -> None:
    """Sem isto, o dreno de partida ressuscitaria um comando já confirmado como cancelado — e executaria no
    aparelho um verbo que ninguém mais quer."""
    from app.api import _fechar_cancelado
    s = harness.state
    s.transport = TransporteMudo()
    async with await _cliente(harness) as c:
        r = await c.post("/api/instances/android-01/actions/home", json={"idempotency_key": "outbox-cancel-01"})
    cid = r.json()["command_id"]
    assert s.outbox.is_pending(cid)
    s.commands.transition(cid, CommandState.cancel_requested, reason="teste")
    _fechar_cancelado(s, cid, "cancelado antes do envio; nada foi enviado ao worker")
    assert s.commands.get(cid)["state"] == CommandState.cancelled.value
    assert not s.outbox.is_pending(cid), "o que foi cancelado não é mais entrega devida"
    assert s.outbox.pending(hospedados_por=s.cfg.owner_id) == []
