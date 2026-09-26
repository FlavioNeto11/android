"""Banco restaurado não trava o parque remoto (K-004, backlog B4).

A cerca do central é `MAX(fence) + 1` no banco, por aparelho. Restaurar um backup mais antigo volta esse `MAX`
para trás, mas o agente guarda no diário a maior cerca que já executou — e passava a recusar todo `start` como
"cerca N é anterior à última executada (M)", com o aparelho parado e sem reparo automático. O conserto era subir
a cerca à mão no SQLite.

Agora o agente declara no `hello` a maior cerca por aparelho, e o central despacha acima dela.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models import CommandState
from app.workers.registry import WorkerRegistry

from .conftest import Harness
from .test_contrato_de_worker import VERBOS, AgenteDeContrato, _acao, _desfecho
from .test_exclusividade_aparelho import _agente, _pronto
from .test_workers import _hello


async def _parque_restaurado(tmp_path: Path, cercas: dict[str, int]):
    """Um aparelho remoto cujo agente declara `cercas` no `hello`, e um banco que nunca as viu."""
    import httpx

    from app.main import create_app

    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    reg, worker_id = h.state.workers, "worker-lan-01"
    reg.autenticar(_hello(verbs=VERBOS, fences=cercas), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteDeContrato(h, worker_id)
    agente.link = reg.attach(worker_id, agente.send)
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", (worker_id, "android-03"))
    rt = h.state.devices.get("android-03")
    rt.worker_id = worker_id
    h.state.devices.bind_worker(worker_id, list(VERBOS))
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return h, rt, cliente, agente


def _cercas_despachadas(agente: AgenteDeContrato) -> list[int]:
    return [p["fence"] for p in agente.vistos if p.get("type") == "dispatch"]


async def test_despacho_sobe_acima_da_cerca_que_o_agente_ja_executou(tmp_path: Path) -> None:
    """O banco não tem comando nenhum de `android-03` (a cerca daqui seria 1); o agente já executou a 50."""
    h, rt, cliente, agente = await _parque_restaurado(tmp_path, {"android-03": 50})
    try:
        async with cliente as c:
            cid = await _acao(c, rt, "stop", "restaurado-0001")
            linha = await _desfecho(h, cid)
            assert linha["fence"] == 51                     # gravada, então o resultado com 51 é aceito
            assert _cercas_despachadas(agente) == [51]

            # O próximo segue o caminho normal (`MAX + 1`), já acima do piso: nada mais sobe à força.
            cid2 = await _acao(c, rt, "start", "restaurado-0002")
            assert (await _desfecho(h, cid2))["fence"] == 52
            assert _cercas_despachadas(agente) == [51, 52]
    finally:
        await h.state.stop()  # type: ignore[union-attr]


async def test_agente_antigo_sem_cercas_nao_muda_nada(tmp_path: Path) -> None:
    """Sem `fences` no `hello` (agente anterior a isto), a cerca é a do banco, como sempre foi."""
    h, rt, cliente, agente = await _parque_restaurado(tmp_path, {})
    try:
        async with cliente as c:
            cid = await _acao(c, rt, "stop", "antigo-0001")
            assert (await _desfecho(h, cid))["fence"] == 1
            assert _cercas_despachadas(agente) == [1]
    finally:
        await h.state.stop()  # type: ignore[union-attr]


async def test_cerca_de_outro_aparelho_nao_interfere(tmp_path: Path) -> None:
    h, rt, cliente, agente = await _parque_restaurado(tmp_path, {"android-99": 40})
    try:
        async with cliente as c:
            cid = await _acao(c, rt, "stop", "outro-0001")
            assert (await _desfecho(h, cid))["fence"] == 1
    finally:
        await h.state.stop()  # type: ignore[union-attr]


def test_registro_guarda_so_cercas_positivas_e_renova_a_cada_hello(harness: Harness) -> None:
    reg: WorkerRegistry = harness.state.workers  # type: ignore[union-attr]
    token = reg.autenticar(_hello(fences={"android-03": 7, "android-04": 0}), token=None,
                           enrollment=reg.criar_inscricao())
    assert reg.piso_de_cerca("worker-lan-01", "android-03") == 7
    assert reg.piso_de_cerca("worker-lan-01", "android-04") == 0
    assert reg.piso_de_cerca(None, "android-03") == 0
    # A reconexão traz o diário de novo: o que vale é a declaração mais recente.
    reg.autenticar(_hello(fences={"android-03": 9}), token=token, enrollment=None)
    assert reg.piso_de_cerca("worker-lan-01", "android-03") == 9


def test_cerca_so_sobe_em_comando_que_ainda_nao_saiu(harness: Harness) -> None:
    """Depois de despachada, a cerca é a que o agente viu e vai devolver: mudá-la recusaria o resultado."""
    cmds = harness.state.commands  # type: ignore[union-attr]
    cmds.create(command_id="c-saiu", instance_id="android-01", verb="stop", idempotency_key="k-saiu")
    cmds.transition("c-saiu", CommandState.dispatched)
    assert cmds.elevar_cerca("c-saiu", 30) == 1
    assert cmds.get("c-saiu")["fence"] == 1

    cmds.create(command_id="c-novo", instance_id="android-01", verb="start", idempotency_key="k-novo")
    assert cmds.get("c-novo")["fence"] == 2
    assert cmds.elevar_cerca("c-novo", 1) == 2          # já está acima do piso: fica
    assert cmds.elevar_cerca("c-novo", 30) == 31
    assert cmds.get("c-novo")["fence"] == 31
    with pytest.raises(KeyError):
        cmds.elevar_cerca("c-inexistente", 5)


async def test_hello_do_agente_declara_as_cercas_do_diario(tmp_path: Path) -> None:
    """O outro lado do contrato: o agente real lê o diário e manda a maior cerca por aparelho."""
    from app.workers.protocol import Dispatch

    from .test_exclusividade_aparelho import WsFalso

    agente = _agente(tmp_path)
    agente._ws = WsFalso()
    agente.executor.run = lambda *_a, **_k: _pronto()                  # type: ignore[assignment]
    assert agente._hello().fences == {}
    await agente._despachar(Dispatch(command_id="c-1", fence=12, verb="stop", instance_id="android-03",
                                     serial="emulator-5554"))
    for t in list(agente._tarefas.values()):
        await t
    assert agente._hello().fences == {"android-03": 12}
