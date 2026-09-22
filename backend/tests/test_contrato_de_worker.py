"""UM contrato: o mesmo cenário contra o `LocalWorker` e contra o agente.

Este arquivo é a rede de segurança do item 2.1 do plano. Até aqui o central chamava o `DeviceManager` direto e o
aparelho de outra máquina falava `Dispatch`/`Ack`/`Result`: duas implementações do mesmo verbo, com semânticas
diferentes — `start` local respondia `succeeded` antes de o aparelho ligar, `desired_state` só era gravado num
dos lados, e `acked` não existia para comando local nenhum.

Cada teste daqui roda DUAS vezes, com a mesma bateria: `local` (os aparelhos deste servidor, executados pelo
`workers/local.LocalWorker`) e `agente` (um aparelho de outra máquina, executado por um agente falso que fala o
protocolo). O agente falso entra pelo MESMO handler de mensagens do agente de verdade
(`api._tratar_mensagem_do_worker`) — se ele entrasse por um atalho, o teste provaria o atalho, não o contrato.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.api import _tratar_mensagem_do_worker
from app.main import create_app
from app.models import CommandState, InstanceState
from app.workers.protocol import Ack, Dispatch, Progress, Result, parse_upstream

from .conftest import Harness
from .test_workers import _hello

#: Um worker que declara o ciclo de vida inteiro — o caso que o aceite 1 descreve (mesmos verbos aqui e lá).
VERBOS = ["create", "start", "stop", "hibernate", "wake", "restart", "reset"]
HOSPEDEIROS = ["local", "agente"]


class AgenteDeContrato:
    """Agente falso que responde pelo canal, como o de verdade: `ack`, `progress` e `result`, nessa ordem.

    `responder=False` é o worker que recebeu e nunca respondeu — o caso em que o desfecho honesto é `uncertain`.
    """

    def __init__(self, h: Harness, worker_id: str, *, responder: bool = True) -> None:
        self.h, self.worker_id, self.responder = h, worker_id, responder
        self.link: Any = None
        self.vistos: list[dict[str, Any]] = []
        #: O que devolver por verbo, no formato que o agente real devolve.
        self.dados: dict[str, dict[str, Any]] = {"hibernate": {"hibernated": True}, "reset": {"reset": True}}

    async def send(self, payload: dict[str, Any]) -> None:
        self.vistos.append(payload)
        if payload.get("type") != "dispatch" or not self.responder:
            return
        msg = Dispatch.model_validate(payload)
        asyncio.get_running_loop().create_task(self._executar(msg))

    async def _executar(self, msg: Dispatch) -> None:
        await self._sobe(Ack(command_id=msg.command_id))
        await self._sobe(Progress(command_id=msg.command_id, message=f"executando '{msg.verb}' nesta máquina"))
        await self._sobe(Result(command_id=msg.command_id, outcome="succeeded", fence=msg.fence,
                                data=self.dados.get(msg.verb)))

    async def _sobe(self, mensagem: Any) -> None:
        # Pelo mesmo caminho do agente de verdade: serializa, faz o parse do contrato e entrega ao handler.
        await _tratar_mensagem_do_worker(self.h.state, self.worker_id, self.link,  # type: ignore[arg-type]
                                         parse_upstream(mensagem.model_dump()))


async def _parque(tmp_path: Path, hospedeiro: str, *, responder: bool = True):
    """Devolve `(harness, rt, cliente, worker_id)` — o mesmo cenário nos dois hospedeiros."""
    if hospedeiro == "local":
        h = Harness(tmp_path, 3)
        # O agente declara `hibernation=True` no `hello`; aqui quem declara é a configuração da máquina. Ligada
        # nos dois, o cenário é o mesmo — que é o ponto deste arquivo.
        h.cfg.file.android.hibernation = True
        await h.boot()
        assert h.state is not None
        rt = h.state.devices.get("android-01")
        worker_id = h.cfg.owner_id
        # O worker local já está conectado por `AppState.start()`: é isto que o item 2.1 entrega.
        assert rt.worker_id == worker_id and rt.worker_verbs
    else:
        h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
        await h.boot()
        assert h.state is not None
        reg, worker_id = h.state.workers, "worker-lan-01"
        reg.autenticar(_hello(hibernation=True, verbs=VERBOS), token=None, enrollment=reg.criar_inscricao())
        agente = AgenteDeContrato(h, worker_id, responder=responder)
        agente.link = reg.attach(worker_id, agente.send)
        h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", (worker_id, "android-03"))
        rt = h.state.devices.get("android-03")
        rt.worker_id = worker_id
        h.state.devices.bind_worker(worker_id, list(VERBOS))
    if not responder:
        # "Recebeu e nunca respondeu", igual dos dois lados: o comando SAI pelo canal (entra em voo, com cerca) e
        # o desfecho nunca chega. Trocar o `send` do link é o ponto exato em que isso acontece.
        h.state.workers.live[worker_id].send = _engole  # type: ignore[union-attr]
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return h, rt, cliente, worker_id


async def _engole(_payload: dict[str, Any]) -> None:
    """Um `send` que entrega e nunca responde — o worker que sumiu depois de receber."""
    return None


async def _desfecho(h: Harness, cid: str, estados: tuple[str, ...] = ("succeeded",), timeout: float = 20.0) -> Any:
    await h.wait(lambda: h.state.commands.get(cid)["state"] in estados,  # type: ignore[union-attr]
                 timeout, what=f"comando em {estados}")
    return h.state.commands.get(cid)  # type: ignore[union-attr]


async def _acao(c: httpx.AsyncClient, rt: Any, verbo: str, chave: str, **corpo: Any) -> str:
    r = await c.post(f"/api/instances/{rt.id}/actions/{verbo}", json={"idempotency_key": chave, **corpo})
    assert r.status_code == 202, r.text
    return r.json()["command_id"]


# ---------------------------------------------------------------- a máquina de estados
@pytest.mark.parametrize("hospedeiro", HOSPEDEIROS)
async def test_o_comando_passa_pelas_mesmas_marcas_nos_dois_hospedeiros(tmp_path: Path, hospedeiro: str) -> None:
    """`dispatched` → `acked` → `running` → desfecho, com o `worker_id` de quem executou.

    O que isto tranca: antes, comando de aparelho local não tinha `acked` nem `worker_id` — "o executor recebeu"
    simplesmente não era um estado deste lado, e o histórico não dizia QUEM agiu. Com um contrato só, as marcas
    são as mesmas morando o aparelho onde morar.
    """
    h, rt, cliente, worker_id = await _parque(tmp_path, hospedeiro)
    try:
        async with cliente as c:
            linha = await _desfecho(h, await _acao(c, rt, "stop", "contrato-marcas"))
        assert linha["worker_id"] == worker_id
        for marca in ("dispatched_at", "acked_at", "started_at", "finished_at"):
            assert linha[marca], f"{marca} não foi carimbado no hospedeiro '{hospedeiro}'"
        assert linha["dispatched_at"] <= linha["acked_at"] <= linha["started_at"] <= linha["finished_at"]
    finally:
        await h.state.stop()  # type: ignore[union-attr]


@pytest.mark.parametrize("hospedeiro", HOSPEDEIROS)
async def test_parar_grava_a_decisao_e_deixa_o_aparelho_parado(tmp_path: Path, hospedeiro: str) -> None:
    """`stop` bem-sucedido é DECISÃO: `desired_state` fica gravado nos dois caminhos.

    Era a divergência medida no parque: `set_desired_state` só era chamado no caminho local, então o "Parar" de um
    aparelho remoto era desfeito pela readoção automática do monitor em ≤30 s.
    """
    h, rt, cliente, _w = await _parque(tmp_path, hospedeiro)
    try:
        async with cliente as c:
            await _desfecho(h, await _acao(c, rt, "stop", "contrato-stop"))
        assert rt.desired_state == InstanceState.stopped.value
        assert h.state.db.scalar("SELECT desired_state FROM instances WHERE id=?",  # type: ignore[union-attr]
                                 (rt.id,)) == InstanceState.stopped.value
        assert rt.state == InstanceState.stopped
    finally:
        await h.state.stop()  # type: ignore[union-attr]


@pytest.mark.parametrize("hospedeiro", HOSPEDEIROS)
async def test_ligar_so_conclui_com_o_aparelho_no_ar(tmp_path: Path, hospedeiro: str) -> None:
    """`start` só responde `succeeded` quando o aparelho está online — nos dois caminhos.

    A divergência que isto fecha foi medida em produção: um `start` local ia de `dispatched` a `succeeded` em
    1 ms, porque o verbo apenas ENFILEIRAVA o boot, enquanto o remoto só concluía depois do boot.
    """
    h, rt, cliente, _w = await _parque(tmp_path, hospedeiro)
    try:
        async with cliente as c:
            await _desfecho(h, await _acao(c, rt, "stop", "contrato-start-0"))
            linha = await _desfecho(h, await _acao(c, rt, "start", "contrato-start-1"))
        assert linha["state"] == CommandState.succeeded.value
        assert rt.desired_state == InstanceState.online.value
        assert rt.state == InstanceState.online, f"'{hospedeiro}': {rt.state} / {rt.state_detail}"
    finally:
        await h.state.stop()  # type: ignore[union-attr]


@pytest.mark.parametrize("hospedeiro", HOSPEDEIROS)
async def test_hibernar_termina_com_o_aparelho_hibernado(tmp_path: Path, hospedeiro: str) -> None:
    """Hibernar que salvou snapshot termina `succeeded` E com o aparelho em `hibernated` — os dois caminhos."""
    h, rt, cliente, _w = await _parque(tmp_path, hospedeiro)
    try:
        async with cliente as c:
            await _desfecho(h, await _acao(c, rt, "hibernate", "contrato-hibernate"))
        assert rt.state == InstanceState.hibernated, f"'{hospedeiro}': {rt.state} / {rt.state_detail}"
        assert rt.snapshot_valid
    finally:
        await h.state.stop()  # type: ignore[union-attr]


@pytest.mark.parametrize("hospedeiro", HOSPEDEIROS)
async def test_resetar_apaga_o_que_o_central_afirmava_sobre_o_disco(tmp_path: Path, hospedeiro: str) -> None:
    """`reset` bem-sucedido invalida a evidência de conta nos dois caminhos: o aparelho ficou vazio."""
    h, rt, cliente, _w = await _parque(tmp_path, hospedeiro)
    try:
        h.state.db.execute(  # type: ignore[union-attr]
            "UPDATE instances SET account_evidence=?, account_evidence_ts=? WHERE id=?",
            ("qa-user-01", "2026-09-22T00:00:00Z", rt.id))
        async with cliente as c:
            await _desfecho(h, await _acao(c, rt, "reset", "contrato-reset", confirm=True))
        assert h.state.db.scalar("SELECT account_evidence FROM instances WHERE id=?",  # type: ignore[union-attr]
                                 (rt.id,)) is None
    finally:
        await h.state.stop()  # type: ignore[union-attr]


# ---------------------------------------------------------------- os dois caminhos de recusa
@pytest.mark.parametrize("hospedeiro", HOSPEDEIROS)
async def test_prazo_estourado_vira_incerto_e_nunca_falha(tmp_path: Path, hospedeiro: str) -> None:
    """Worker que recebeu e não respondeu = `uncertain`. Nunca `failed`: ele pode ter agido.

    A regra vale para as duas máquinas, e é a razão de o prazo morar em `devices/verbs.PRAZO_POR_VERBO` — uma
    tabela só, consultada por quem despacha, em vez de uma no caminho remoto e nenhuma no local.
    """
    from app.devices import verbs

    h, rt, cliente, _w = await _parque(tmp_path, hospedeiro, responder=False)
    verbs.PRAZO_POR_VERBO["stop"] = 0.3       # `stop` usa o prazo padrão; encurtado, o teste não espera 300 s
    try:
        async with cliente as c:
            linha = await _desfecho(h, await _acao(c, rt, "stop", "contrato-prazo"),
                                    ("uncertain", "failed", "succeeded"))
        assert linha["state"] == CommandState.uncertain.value, linha["reason"]
    finally:
        verbs.PRAZO_POR_VERBO.pop("stop", None)
        await h.state.stop()  # type: ignore[union-attr]


@pytest.mark.parametrize("hospedeiro", HOSPEDEIROS)
async def test_resultado_com_cerca_velha_e_recusado(tmp_path: Path, hospedeiro: str) -> None:
    """A cerca é a proteção contra o worker que voltou do limbo — e ela é do REGISTRO, não de um dos caminhos.

    Um resultado com cerca diferente da do despacho (ou sem cerca nenhuma) não fecha comando algum, venha do
    agente da outra máquina ou do executor deste servidor.
    """
    h, rt, cliente, worker_id = await _parque(tmp_path, hospedeiro, responder=False)
    try:
        async with cliente as c:
            cid = await _acao(c, rt, "stop", "contrato-cerca")
            await h.wait(lambda: cid in (h.state.workers.live[worker_id].pendentes),  # type: ignore[union-attr]
                         what="o comando entrar em voo")
            cerca = h.state.commands.get(cid)["fence"]  # type: ignore[union-attr]
            forjado = Result(command_id=cid, outcome="succeeded", fence=cerca + 1)
            assert h.state.workers.on_result(worker_id, forjado, fence=forjado.fence) is False  # type: ignore[union-attr]
            assert h.state.workers.on_result(  # type: ignore[union-attr]
                worker_id, Result(command_id=cid, outcome="succeeded")) is False
            assert h.state.commands.get(cid)["state"] not in (  # type: ignore[union-attr]
                CommandState.succeeded.value, CommandState.failed.value)
    finally:
        await h.state.stop()  # type: ignore[union-attr]
