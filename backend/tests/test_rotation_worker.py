"""Rodízio COM worker: vagas por máquina, aparelho remoto ligado e desligado pelo agente, espera em vez de
bloqueio.

O que estes testes travam (achados #10, #40, #41, #43, #135, #48):

- tarefa para um aparelho de outra máquina que está desligado abre um `start` PELO WORKER, em vez de bloquear
  na hora com "Inicie a instância e use Tentar novamente" — instrução que o tick seguinte repetia igual;
- `max_slots` e os recursos da batida deixam de ser enfeite do painel e entram na decisão: cada máquina tem as
  vagas dela, e capacidade cresce com worker novo em vez de esbarrar num teto global de 10;
- worker sem contato é ESPERA com prazo, e só depois dele o objetivo para para uma pessoa — com o nome do
  worker e desde quando ele sumiu, não com "inicie a instância";
- remoto ocioso cede a vaga pelo agente (hibernate/stop), em vez de ficar ligado para sempre.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from app.models import InstanceState, RunCreate
from app.workers.protocol import Heartbeat, WorkerResources
from app.workers.registry import PISO_RAM_MB, WorkerRegistry

from .conftest import Harness
from .test_workers import AgenteQueResponde, _hello

WORKER = "worker-lan-01"


async def _com_worker(tmp_path: Path, *, remotos: list[str], count: int = 4, max_slots: int = 6,
                      outcome: str = "succeeded") -> tuple[Harness, WorkerRegistry, AgenteQueResponde]:
    """Harness com N aparelhos de OUTRA máquina, hospedados por um worker conectado que responde ao despacho."""
    h = Harness(tmp_path, count,
                external={iid: f"127.0.0.1:{15555 + i}" for i, iid in enumerate(remotos)})
    await h.boot()
    assert h.state is not None
    reg = h.state.workers
    reg.autenticar(_hello(max_slots=max_slots), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteQueResponde(reg, WORKER, outcome)
    reg.attach(WORKER, agente.send)
    for iid in remotos:
        h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", (WORKER, iid))
        rt = h.state.devices.get(iid)
        rt.worker_id = WORKER
        rt.state, rt.state_detail = InstanceState.stopped, "desligado na máquina do worker"
    h.state.devices.bind_worker(WORKER, list(_hello().verbs))
    # Batida com recursos de sobra: sem ela a capacidade nasce "velha" e o piso de RAM/disco não seria porta.
    reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(cpu_count=12, ram_total_mb=65273,
                                                                 ram_free_mb=46367, disk_free_gb=400.0)))
    return h, reg, agente


def _rodizio(h: Harness, **kw: Any) -> Any:
    h.state.settings.update({"auto_start_devices": True, "min_online_dwell_s": 0, **kw})  # type: ignore[union-attr]
    return h.state.settings.get()                                                          # type: ignore[union-attr]


def _comandos(h: Harness, verbo: str | None = None) -> list[Any]:
    linhas = h.state.db.query("SELECT * FROM commands ORDER BY created_at")               # type: ignore[union-attr]
    return [c for c in linhas if verbo is None or c["verb"] == verbo]


# ---------------------------------------------------------------- ligar pelo worker
async def test_tarefa_para_remoto_desligado_liga_pelo_worker_e_conclui(tmp_path: Path) -> None:
    """O centro do achado #43: o remoto parado deixava a tarefa em `waiting_user` pedindo que uma pessoa ligasse
    o aparelho — e "Tentar novamente" com ele ainda parado só refazia o bloqueio. Agora o rodízio o liga."""
    h, _reg, agente = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        _rodizio(h)
        run = h.run(["android-03"])
        detalhe = await h.wait_run(run.id, timeout=90)
        assert detalhe.status == "completed", detalhe.status
        start = _comandos(h, "start")
        assert len(start) == 1, [dict(c) for c in start]
        assert start[0]["requested_by"] == "scheduler"      # rastreável como o clique do painel, e marcado
        assert start[0]["instance_id"] == "android-03"
        # E o comando de fato saiu pelo socket para o agente daquela máquina.
        despacho = next(p for p in agente.enviados if p.get("type") == "dispatch")
        assert despacho["verb"] == "start" and despacho["instance_id"] == "android-03"
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_pre_voo_deixa_passar_o_remoto_parado_quando_o_worker_sabe_liga_lo(tmp_path: Path) -> None:
    """A recusa do pré-voo dizia "o rodízio daqui não o liga". Isso deixou de ser verdade — e recusar por um
    motivo que não existe mais impediria a execução de nascer."""
    h, _reg, _a = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        _rodizio(h)
        assert h.state.runs.pre_voo(["android-03"]) == {}                                  # type: ignore[union-attr]
        # Com o rodízio DESLIGADO ninguém o liga sozinho, e a recusa continua — com o motivo certo.
        h.state.settings.update({"auto_start_devices": False})                             # type: ignore[union-attr]
        recusa = h.state.runs.pre_voo(["android-03"])["android-03"]                        # type: ignore[union-attr]
        assert recusa["code"] == "remote_off" and "rodízio está desligado" in recusa["motivo"]
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- vagas por máquina
async def test_sem_vaga_no_worker_o_objetivo_espera_e_ninguem_e_bloqueado(tmp_path: Path) -> None:
    """`max_slots=1`: o segundo aparelho daquela máquina espera vaga. Antes, `max_slots` não entrava em decisão
    nenhuma e o único teto era global (`max_online_devices`), que não fala da RAM da outra máquina."""
    h, _reg, _a = await _com_worker(tmp_path, remotos=["android-03", "android-04"], max_slots=1)
    try:
        s = _rodizio(h)
        devs, sched = h.state.devices, h.state.scheduler                                   # type: ignore[union-attr]
        ocupado = devs.get("android-03")
        ocupado.state = InstanceState.online                    # a única vaga do notebook já está tomada
        esperas: list[tuple[Any, str]] = []
        sched.repo.note_waiting = lambda oid, detail: esperas.append((oid, detail))         # type: ignore[assignment]
        sched.repo.dispatchable_objectives = lambda: [                                      # type: ignore[assignment]
            {"id": "o-1", "instance_id": "android-04", "run_id": "r000001"}]
        # o aparelho ligado está EM USO: sem vítima possível, a resposta certa é esperar e dizer por quê
        sched.repo.instances_with_open_work = lambda: {"android-03"}                        # type: ignore[assignment]
        sched.repo.instances_needing_user = lambda: set()                                   # type: ignore[assignment]

        sched._rotate(s)

        assert devs.get("android-04").state == InstanceState.stopped    # nada foi ligado além da vaga
        assert _comandos(h, "start") == []
        assert esperas and "aguardando vaga" in esperas[-1][1]
        assert f"1/1 ligados no worker {WORKER}" in esperas[-1][1]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_vaga_do_worker_nao_gasta_a_vaga_do_host(tmp_path: Path) -> None:
    """Capacidade cresce com worker novo: o host continua com `max_online_devices`, e os aparelhos da outra
    máquina são contados contra as vagas DELA — não contra as daqui."""
    h, _reg, _a = await _com_worker(tmp_path, remotos=["android-03", "android-04"], max_slots=2)
    try:
        s = _rodizio(h, max_online_devices=1)
        devs, sched = h.state.devices, h.state.scheduler                                    # type: ignore[union-attr]
        for iid in ("android-01", "android-02"):
            devs.get(iid).state = InstanceState.online          # o host está no limite dele
        sched.repo.note_waiting = lambda *a, **k: None                                      # type: ignore[assignment]
        sched.repo.instances_with_open_work = lambda: set()                                 # type: ignore[assignment]
        sched.repo.instances_needing_user = lambda: set()                                   # type: ignore[assignment]
        sched.repo.dispatchable_objectives = lambda: [                                      # type: ignore[assignment]
            {"id": "o-1", "instance_id": "android-03", "run_id": "r000001"},
            {"id": "o-2", "instance_id": "android-04", "run_id": "r000002"}]

        sched._rotate(s)

        # As duas vagas do notebook foram usadas, mesmo com o host lotado.
        assert {c["instance_id"] for c in _comandos(h, "start")} == {"android-03", "android-04"}
        assert devs.slots_used_of(WORKER) == 2 and devs.slots_used() == 2
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- desligar pelo worker
async def test_remoto_ocioso_cede_a_vaga_pelo_agente(tmp_path: Path) -> None:
    """A evicção e o `idle_stop_s` não alcançavam remoto: o worker segurava emulador ligado indefinidamente, e
    cada qemu ocioso custa de 0,7 a 2 núcleos na máquina dele."""
    h, _reg, _a = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        s = _rodizio(h, idle_stop_s=1)
        devs, sched = h.state.devices, h.state.scheduler                                    # type: ignore[union-attr]
        rt = devs.get("android-03")
        rt.state = InstanceState.online
        rt.online_since_mono = rt.last_activity_mono = 0.0
        sched.repo.dispatchable_objectives = lambda: []                                     # type: ignore[assignment]
        sched.repo.instances_with_open_work = lambda: set()                                 # type: ignore[assignment]
        sched.repo.instances_needing_user = lambda: set()                                   # type: ignore[assignment]

        sched._rotate(s)

        parada = _comandos(h, "hibernate") or _comandos(h, "stop")
        assert parada and parada[0]["requested_by"] == "scheduler"
        assert parada[0]["instance_id"] == "android-03"
        assert rt.state == InstanceState.stopping          # a vaga já conta como prometida neste mesmo tick
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- worker fora do ar
async def test_worker_sem_contato_espera_e_so_depois_do_prazo_bloqueia(tmp_path: Path) -> None:
    """Queda de túnel dura segundos: bloquear na hora mandava a pessoa resolver o que voltaria sozinho. Passado
    o prazo, o bloqueio diz o nome do worker e desde quando ele sumiu — não "inicie a instância"."""
    h, reg, _a = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        _rodizio(h)
        sched = h.state.scheduler                                                           # type: ignore[union-attr]
        reg.detach(WORKER, "o socket caiu")
        h.state.devices.bind_worker(WORKER, None)                                           # type: ignore[union-attr]
        esperas: list[str] = []
        bloqueios: list[tuple[str, str]] = []
        sched.repo.note_waiting = lambda oid, detail: esperas.append(detail)                 # type: ignore[assignment]
        sched._block = lambda obj, reason, needs: bloqueios.append((reason, needs))          # type: ignore[assignment]
        sched.repo.dispatchable_objectives = lambda: [                                       # type: ignore[assignment]
            {"id": "o-1", "instance_id": "android-03", "run_id": "r000001"}]

        sched._tick()
        assert not bloqueios, bloqueios
        assert esperas and "voltar" in esperas[-1] and "Notebook da LAN" in esperas[-1]

        sched._sem_worker["android-03"] = time.monotonic() - 10_000      # o prazo venceu
        sched._tick()
        assert bloqueios, "worker sumido por tempo demais tem de parar para uma pessoa"
        assert "Notebook da LAN" in bloqueios[-1][0] and "sem contato desde" in bloqueios[-1][0]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_manutencao_do_worker_e_espera_e_nao_bloqueio(tmp_path: Path) -> None:
    """"Novas atribuições estão suspensas" é o que o painel promete ao ligar a manutenção — e suspender não é
    cancelar: o objetivo espera a manutenção terminar."""
    h, reg, _a = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        _rodizio(h)
        sched = h.state.scheduler                                                            # type: ignore[union-attr]
        reg.set_maintenance(WORKER, True)
        esperas: list[str] = []
        bloqueios: list[Any] = []
        sched.repo.note_waiting = lambda oid, detail: esperas.append(detail)                 # type: ignore[assignment]
        sched._block = lambda *a, **k: bloqueios.append(a)                                   # type: ignore[assignment]
        sched.repo.dispatchable_objectives = lambda: [                                       # type: ignore[assignment]
            {"id": "o-1", "instance_id": "android-03", "run_id": "r000001"}]

        sched._tick()

        assert not bloqueios and _comandos(h, "start") == []
        assert esperas and "manutenção" in esperas[-1]
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- recursos da batida
async def test_batida_com_pouca_ram_marca_degraded_e_o_rodizio_nao_liga_mais_um(tmp_path: Path) -> None:
    """Disco e RAM livres nunca foram porta em lugar nenhum, e worker no limite seguia "online". Agora a batida
    o marca `degraded` e o rodízio para de mandar aparelho novo para lá."""
    h, reg, _a = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        s = _rodizio(h)
        sched = h.state.scheduler                                                            # type: ignore[union-attr]
        reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(ram_free_mb=PISO_RAM_MB - 1,
                                                                     disk_free_gb=400.0)))
        linha = h.state.db.one("SELECT state, state_detail FROM workers WHERE id=?", (WORKER,))  # type: ignore[union-attr]
        assert linha["state"] == "degraded" and "RAM livre" in linha["state_detail"]
        assert sched.worker_capacity(WORKER).sem_recurso() is not None

        esperas: list[str] = []
        sched.repo.note_waiting = lambda oid, detail: esperas.append(detail)                 # type: ignore[assignment]
        sched.repo.instances_with_open_work = lambda: set()                                  # type: ignore[assignment]
        sched.repo.instances_needing_user = lambda: set()                                    # type: ignore[assignment]
        sched.repo.dispatchable_objectives = lambda: [                                       # type: ignore[assignment]
            {"id": "o-1", "instance_id": "android-03", "run_id": "r000001"}]

        sched._rotate(s)

        assert _comandos(h, "start") == []
        assert esperas and "RAM livre" in esperas[-1]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_batida_velha_nao_vira_porta(tmp_path: Path) -> None:
    """Recurso velho não é recurso: com a batida vencida o central para de gatear por RAM/disco, em vez de
    decidir sobre um número que descreve outro momento. Quem decide "indisponível" continua sendo a batida."""
    h, reg, _a = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(ram_free_mb=PISO_RAM_MB - 1)))
        assert reg.capacidade(WORKER).sem_recurso() is not None
        h.state.db.execute("UPDATE workers SET last_seen_at=? WHERE id=?",                    # type: ignore[union-attr]
                           ("2020-01-01T00:00:00Z", WORKER))
        velha = reg.capacidade(WORKER)
        assert velha.stale is True and velha.sem_recurso() is None
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- tetos fixos de 10
def test_execucao_e_lote_aceitam_o_parque_inteiro() -> None:
    """Defeito ativo do achado #48: com 14 aparelhos de tarefa, "Selecionar todas" + Executar era recusado com
    422 antes de tocar em coisa alguma, porque o teto de 10 era do CÓDIGO."""
    from app.config import LimitsCfg
    from app.models import BulkBody

    ids = [f"android-{i:02d}" for i in range(1, 15)]
    assert RunCreate(command="oi pessoal", instance_ids=ids, idempotency_key="k-12345678").instance_ids == ids
    assert BulkBody(ids=ids, action="stop").ids == ids
    # E a capacidade deixa de esbarrar num 10 do código quando entra um worker novo.
    assert LimitsCfg(max_online_devices=15, max_active_devices=15).max_online_devices == 15
    with pytest.raises(ValueError):
        LimitsCfg(max_online_devices=0)
