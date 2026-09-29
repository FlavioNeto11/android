"""Cancelamento de comando de ponta a ponta (achados #23 e #157).

O que existia: `cancel_requested`/`cancelled` na máquina de estados, a mensagem `cancel` no protocolo, o
tratamento dela no agente e `WorkerRegistry.cancel` no central — tudo sem um único chamador. Na prática não havia
como cancelar um `start` remoto de 540 s nem um `reset` de 600 s: o painel só podia assistir.

O que estes testes trancam:

* a rota `POST /api/commands/{id}/cancel` existe, é idempotente e só aceita comando ABERTO;
* o pedido chega a quem executa nos dois caminhos — `cancel` pelo socket do worker, `rt.tasks["boot"]` aqui;
* `cancelled` só é dito quando o efeito NÃO aconteceu (é a definição de `013_commands.sql:25`): cancelar depois
  de o emulador subir é `uncertain`, e um desfecho real que chegue no meio ganha do pedido;
* "eu quero este aparelho no ar" não sobrevive ao cancelamento do `start`, senão o monitor religa em ≤30 s o que
  alguém acabou de mandar parar de subir.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.api import _entregar_cancelamento
from app.main import create_app
from app.models import CommandState, InstanceState
from app.workers.protocol import Result
from app.workers.registry import WorkerRegistry

from .conftest import Harness
from .test_workers import AgenteFalso, _hello

VERBOS = ["create", "start", "stop", "hibernate", "wake", "restart", "reset"]


class AgenteQueSegura(AgenteFalso):
    """Recebe o despacho, confirma e NÃO responde — é o `start` de 540 s em voo. O desfecho só sai quando o
    central manda cancelar, e o que ele responde então é o que o teste quer provar."""

    def __init__(self, registry: WorkerRegistry, worker_id: str, *, ao_cancelar: str = "cancelled",
                 motivo: str = "cancelado a pedido do servidor antes de tocar no aparelho") -> None:
        super().__init__()
        self.reg, self.worker_id = registry, worker_id
        self.ao_cancelar, self.motivo = ao_cancelar, motivo
        self.cercas: dict[str, int] = {}
        self.cancelados: list[str] = []

    async def send(self, payload: dict[str, Any]) -> None:
        await super().send(payload)
        tipo = payload.get("type")
        if tipo == "dispatch":
            self.cercas[payload["command_id"]] = payload["fence"]
            self.reg.on_ack(self.worker_id, payload["command_id"])
        elif tipo == "cancel":
            cid = str(payload["command_id"])
            self.cancelados.append(cid)
            self.reg.on_result(self.worker_id, Result(command_id=cid, outcome=self.ao_cancelar,
                                                      reason=self.motivo), fence=self.cercas.get(cid, 0))


async def _parque_com_worker(tmp_path: Path, **kw: Any) -> tuple[Harness, Any, AgenteQueSegura, httpx.AsyncClient]:
    """`android-03` hospedado num worker conectado, como ficaria em produção."""
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    reg = h.state.workers
    reg.autenticar(_hello(verbs=VERBOS), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteQueSegura(reg, "worker-lan-01", **kw)
    reg.attach("worker-lan-01", agente.send)
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", ("worker-lan-01", "android-03"))
    rt = h.state.devices.get("android-03")
    rt.worker_id = "worker-lan-01"
    h.state.devices.bind_worker("worker-lan-01", list(VERBOS))
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return h, rt, agente, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _estado(h: Harness, cid: str) -> str:
    return h.state.commands.get(cid)["state"]            # type: ignore[union-attr,index]


# ---------------------------------------------------------------- caminho do worker
async def test_cancelar_start_remoto_em_voo_chega_ao_agente_e_fecha_em_cancelado(tmp_path: Path) -> None:
    """O caso do achado: `start` remoto em voo, painel pede cancelamento, o agente confirma que não tocou no
    aparelho. Antes, `WorkerRegistry.cancel` tinha zero chamadores e o painel não tinha o que fazer."""
    h, rt, agente, cliente = await _parque_com_worker(tmp_path)
    try:
        async with cliente as c:
            cid = (await c.post("/api/instances/android-03/actions/start",
                                json={"idempotency_key": "cancel-remoto-1"})).json()["command_id"]
            await h.wait(lambda: _estado(h, cid) == CommandState.acked.value, what="o worker confirmar o recebimento")

            r = await c.post(f"/api/commands/{cid}/cancel", json={"note": "cancelado no teste"})
            assert r.status_code == 200, r.text
            assert r.json()["delivered"] is True
            assert agente.cancelados == [cid], "o pedido não chegou ao agente pelo socket"

            await h.wait(lambda: _estado(h, cid) == CommandState.cancelled.value, what="o comando fechar cancelado")
            final = (await c.get(f"/api/commands/{cid}")).json()
            assert final["reason"] == "cancelado a pedido do servidor antes de tocar no aparelho"
            assert final["finished_at"]
        # A decisão gravada antes do despacho ("quero no ar") não pode sobreviver ao cancelamento: com ela de pé,
        # o monitor readotaria em ≤30 s o aparelho que alguém acabou de mandar não subir.
        assert rt.desired_state == InstanceState.stopped.value
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_desfecho_real_ganha_do_pedido_de_cancelamento(tmp_path: Path) -> None:
    """Pedir não é ter conseguido. Se o worker terminou o trabalho no meio do pedido, o comando vale o que
    ACONTECEU — a tabela de transições sempre permitiu `cancel_requested → succeeded`."""
    h, _rt, agente, cliente = await _parque_com_worker(tmp_path, ao_cancelar="succeeded",
                                                       motivo="o aparelho já tinha subido quando o pedido chegou")
    try:
        async with cliente as c:
            cid = (await c.post("/api/instances/android-03/actions/start",
                                json={"idempotency_key": "cancel-perde-corrida"})).json()["command_id"]
            await h.wait(lambda: _estado(h, cid) == CommandState.acked.value, what="o worker confirmar")
            assert (await c.post(f"/api/commands/{cid}/cancel", json={})).status_code == 200
            await h.wait(lambda: _estado(h, cid) in (CommandState.succeeded.value, CommandState.cancelled.value),
                         what="o comando fechar")
            assert _estado(h, cid) == CommandState.succeeded.value, "o pedido inventou um cancelamento que não houve"
            assert agente.cancelados == [cid]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_cancelar_antes_do_envio_nao_manda_nada_ao_worker(tmp_path: Path) -> None:
    """O comando ainda em `created` (esperando o cadeado do aparelho) é o único caso em que se pode AFIRMAR que
    nada saiu. A conferência fica dentro do cadeado, imediatamente antes do envio."""
    h, rt, agente, cliente = await _parque_com_worker(tmp_path)
    try:
        await rt.op_lock.acquire()                        # o aparelho está ocupado: o despacho espera aqui
        async with cliente as c:
            cid = (await c.post("/api/instances/android-03/actions/stop",
                                json={"idempotency_key": "cancel-antes-do-envio"})).json()["command_id"]
            await h.wait(lambda: _estado(h, cid) == CommandState.created.value, what="o comando ficar em created")
            r = await c.post(f"/api/commands/{cid}/cancel", json={})
            assert r.status_code == 200, r.text
            assert _estado(h, cid) == CommandState.cancel_requested.value
            rt.op_lock.release()                          # o cadeado abre: o despacho reavalia antes de enviar
            await h.wait(lambda: _estado(h, cid) == CommandState.cancelled.value, what="o comando fechar cancelado")
            assert [p for p in agente.enviados if p.get("type") == "dispatch"] == [], \
                "o comando cancelado foi despachado assim mesmo"
            assert (await c.get(f"/api/commands/{cid}")).json()["reason"] == \
                "cancelado antes do envio; nada foi enviado ao worker"
    finally:
        if rt.op_lock.locked():
            rt.op_lock.release()
        if h.state is not None:
            await h.state.stop()


async def test_pedido_com_worker_desconectado_fica_registrado(tmp_path: Path) -> None:
    """Worker fora do ar não recebe o pedido — e isso não é falha do cancelamento: o comando continua em
    `cancel_requested` até o desfecho de verdade chegar. Dizer `cancelled` aqui seria inventar."""
    h, _rt, _agente, cliente = await _parque_com_worker(tmp_path)
    try:
        assert h.state is not None
        h.state.workers.detach("worker-lan-01", "teste: o notebook do worker caiu")
        linha, _ = h.state.commands.create(command_id="c-teste-offline", instance_id="android-03", verb="start",
                                           idempotency_key="cancel-worker-offline")
        linha = h.state.commands.transition("c-teste-offline", CommandState.dispatched, worker_id="worker-lan-01")
        entregue, detalhe = await _entregar_cancelamento(h.state, linha)
        assert entregue is False and "não está conectado" in detalhe
        async with cliente as c:
            assert (await c.post("/api/commands/c-teste-offline/cancel", json={})).json()["delivered"] is False
        assert _estado(h, "c-teste-offline") == CommandState.cancel_requested.value
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- caminho local
async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_cancelar_boot_local_interrompe_a_tarefa_e_o_aparelho_nao_fica_ligando(harness: Harness) -> None:
    """No caminho local quem age é `rt.tasks["boot"]`, e é ELA que precisa parar: cancelar só a espera deixaria o
    emulador subindo às escondidas depois de o painel dizer "cancelado"."""
    devs = harness.state.devices                          # type: ignore[union-attr]
    rt = devs.get("android-01")
    await devs.stop_instance(rt)
    devs.fake_boot_s = 5.0                                # boot longo: dá tempo de cancelar no meio, como em campo
    async with await _cliente(harness) as c:
        cid = (await c.post("/api/instances/android-01/actions/start",
                            json={"idempotency_key": "cancel-local-1"})).json()["command_id"]
        await harness.wait(lambda: rt.state == InstanceState.booting, what="o aparelho entrar em boot")
        assert _estado(harness, cid) == CommandState.running.value

        r = await c.post(f"/api/commands/{cid}/cancel", json={"note": "cancelado no teste"})
        assert r.status_code == 200, r.text
        assert r.json()["delivered"] is True and "boot" in r.json()["detail"]

        await harness.wait(lambda: _estado(harness, cid) == CommandState.cancelled.value,
                           what="o comando fechar cancelado")
        final = (await c.get(f"/api/commands/{cid}")).json()
        assert final["reason"] == "cancelado antes de o emulador subir; nada ficou no ar"
    # O aparelho não fica "ligando" para sempre, e a decisão volta a ser "parado" — senão o monitor o religa.
    assert rt.state == InstanceState.stopped
    assert rt.desired_state == InstanceState.stopped.value


async def test_nota_com_cara_de_credencial_recusa_o_pedido_sem_gravar_nada(harness: Harness) -> None:
    """A nota do cancelamento vai crua para `commands.reason` e dali para o evento do comando no bus. Com cara de
    credencial, a rota recusa (409 `note_looks_secret`, a regra da resolução à mão e do voto do D2) antes de escrever:
    o comando segue aberto, e a nota limpa depois é aceita."""
    st = harness.state
    assert st is not None
    st.commands.create(command_id="c-teste-nota-secreta", instance_id="android-01", verb="start",
                       idempotency_key="cancel-nota-secreta")
    st.commands.transition("c-teste-nota-secreta", CommandState.dispatched)
    async with await _cliente(harness) as c:
        r = await c.post("/api/commands/c-teste-nota-secreta/cancel", json={"note": "senha: segredo123"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "note_looks_secret"
        registro = st.commands.get("c-teste-nota-secreta")
        assert registro is not None and registro["state"] == CommandState.dispatched.value
        assert "segredo123" not in str(dict(registro))
        limpa = await c.post("/api/commands/c-teste-nota-secreta/cancel", json={"note": "desisti do boot"})
    assert limpa.status_code == 200, limpa.text
    assert _estado(harness, "c-teste-nota-secreta") in (CommandState.cancel_requested.value,
                                                         CommandState.cancelled.value)


async def test_pedido_do_painel_nao_poe_o_id_do_aparelho_na_triagem(harness: Harness) -> None:
    """Pendência de 29/09 (plano 22.2): o painel mandava a nota "cancelado no painel a partir de <aparelho>", e um id
    com cara de credencial (um AVD como `Pixel_7a-Lab.02`) fazia a triagem recusar o pedido. Agora vai `origin=panel`
    e o backend compõe o contexto; o prefixo do cliente antigo, com o id do próprio comando, continua aceito. O
    `requested_by` livre passa pela mesma triagem: recusado, nada é gravado."""
    st = harness.state
    assert st is not None
    avd = "Pixel_7a-Lab.02"
    for cid in ("c-teste-origem-novo", "c-teste-origem-antigo", "c-teste-origem-rotulo"):
        st.commands.create(command_id=cid, instance_id=avd, verb="start", idempotency_key=f"chave-{cid}")
        st.commands.transition(cid, CommandState.dispatched)
    async with await _cliente(harness) as c:
        novo = await c.post("/api/commands/c-teste-origem-novo/cancel", json={"origin": "panel"})
        antigo = await c.post("/api/commands/c-teste-origem-antigo/cancel",
                              json={"note": f"cancelado no painel a partir de {avd}"})
        recusado = await c.post("/api/commands/c-teste-origem-rotulo/cancel",
                                json={"requested_by": "senha: segredo123", "origin": "panel"})
    assert (novo.status_code, antigo.status_code) == (200, 200), (novo.text, antigo.text)
    for cid in ("c-teste-origem-novo", "c-teste-origem-antigo"):
        registro = st.commands.get(cid)
        assert registro is not None
        assert registro["reason"] == f"cancelamento pedido por panel, no painel a partir de {avd}"
    assert recusado.status_code == 409 and recusado.json()["detail"]["code"] == "note_looks_secret"
    registro = st.commands.get("c-teste-origem-rotulo")
    assert registro is not None and registro["state"] == CommandState.dispatched.value
    assert "segredo123" not in str(dict(registro))


async def test_comando_ja_encerrado_nao_e_cancelavel(harness: Harness) -> None:
    """`cancelled` depois de `succeeded` apagaria história. Só comando ABERTO aceita o pedido."""
    async with await _cliente(harness) as c:
        cid = (await c.post("/api/instances/android-01/actions/home",
                            json={"idempotency_key": "cancel-tarde-demais"})).json()["command_id"]
        await harness.wait(lambda: _estado(harness, cid) == CommandState.succeeded.value, what="o comando fechar")
        r = await c.post(f"/api/commands/{cid}/cancel", json={})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "not_open"
        assert _estado(harness, cid) == CommandState.succeeded.value


async def test_comando_inexistente_devolve_404(harness: Harness) -> None:
    async with await _cliente(harness) as c:
        assert (await c.post("/api/commands/c-que-nao-existe/cancel", json={})).status_code == 404


async def test_pedido_repetido_reenvia_o_sinal_sem_reescrever_o_registro(tmp_path: Path) -> None:
    """Reenviar é o que alguém faz quando o worker acabou de reconectar: o sinal sai de novo, e o registro não
    ganha um segundo "cancelamento pedido" por cima do primeiro."""
    h, _rt, agente, cliente = await _parque_com_worker(tmp_path)
    try:
        assert h.state is not None
        h.state.commands.create(command_id="c-teste-repetido", instance_id="android-03", verb="start",
                                idempotency_key="cancel-repetido")
        h.state.commands.transition("c-teste-repetido", CommandState.dispatched, worker_id="worker-lan-01")
        async with cliente as c:
            primeiro = (await c.post("/api/commands/c-teste-repetido/cancel",
                                     json={"note": "primeira vez"})).json()
            assert primeiro["command"]["state"] == CommandState.cancel_requested.value
            # O agente responde `cancelled` a um comando que o central não tem em voo: o resultado é descartado,
            # e o comando continua exatamente onde estava — esperando o desfecho de verdade.
            segundo = await c.post("/api/commands/c-teste-repetido/cancel", json={"note": "segunda vez"})
            assert segundo.status_code == 200
            assert "primeira vez" in segundo.json()["command"]["reason"], "o segundo pedido reescreveu o primeiro"
        assert agente.cancelados == ["c-teste-repetido", "c-teste-repetido"]
        assert _estado(h, "c-teste-repetido") == CommandState.cancel_requested.value
    finally:
        if h.state is not None:
            await h.state.stop()


@pytest.mark.parametrize("estado", [CommandState.cancel_requested, CommandState.cancelled])
def test_a_tabela_de_estados_permite_cancelar_antes_do_despacho(estado: CommandState) -> None:
    """`created` é o estado do comando que ainda não saiu: dele se pede o cancelamento (e se confirma) sem que o
    aparelho tenha sido tocado."""
    from app.commands.states import check_transition

    check_transition(CommandState.created, estado)
