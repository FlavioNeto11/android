"""Rodízio: N contas sobre K vagas. O scheduler liga o aparelho quando há tarefa, cede a vaga de um ocioso e
nunca desliga aparelho em uso, em foco, com lease do usuário ou com item que precisa do usuário."""
from __future__ import annotations

import asyncio
import time

from app.models import InstanceState

from .conftest import Harness

IDS = ["android-01", "android-02", "android-03"]


def _rotation(h: Harness, slots: int, dwell: int = 0) -> None:
    s = h.state.settings.get()                         # type: ignore[union-attr]
    h.state.settings.update({"auto_start_devices": True, "max_online_devices": slots, "min_online_dwell_s": dwell})  # type: ignore[union-attr]
    assert s is not None


async def _stop_all(h: Harness) -> None:
    devs = h.state.devices                              # type: ignore[union-attr]
    for rt in devs.devices.values():
        await devs.stop_instance(rt)
    assert all(rt.state == InstanceState.stopped for rt in devs.devices.values())


async def test_tres_contas_em_uma_vaga(harness: Harness) -> None:
    await _stop_all(harness)
    _rotation(harness, slots=1)
    devs = harness.state.devices                        # type: ignore[union-attr]
    peak = 0

    async def watch() -> None:
        nonlocal peak
        while True:
            peak = max(peak, devs.slots_used())
            await asyncio.sleep(0.01)

    w = asyncio.create_task(watch())
    run = harness.run(IDS)
    detail = await harness.wait_run(run.id, timeout=90)
    w.cancel()
    assert detail.status == "completed" and detail.counts.succeeded == 3      # nenhum ficou "aguardando usuário"
    assert peak == 1                                                           # nunca mais de 1 aparelho ligado
    for iid in IDS:
        assert len(harness.fakes[iid].messages) == 1                           # 1 mensagem por conta
    assert detail.instances_used == 3


async def test_rodizio_cede_so_as_vagas_necessarias_e_nao_esvazia_o_parque(harness: Harness) -> None:
    """Aparelho em `stopping` é vaga JÁ prometida. Como `slots_used()` ainda o conta como ocupado e `evictable` só
    aceita `online`, sem descontar as paradas em voo cada tick escolhia OUTRA vítima: o rodízio desligava o parque
    inteiro para atender UM aparelho na fila, e cada vítima pagava snapshot na saída e boot na volta."""
    devs = harness.state.devices                        # type: ignore[union-attr]
    sched = harness.state.scheduler                     # type: ignore[union-attr]
    _rotation(harness, slots=2)

    alvo = devs.get("android-03")                       # o que espera vaga
    await devs.stop_instance(alvo)
    for rt in devs.devices.values():                    # os outros, ligados e ociosos há muito
        if rt is not alvo:
            rt.state = InstanceState.online
            rt.online_since_mono = rt.last_activity_mono = 0.0

    paradas: list[str] = []

    def parada_falsa(rt, why: str) -> None:             # type: ignore[no-untyped-def]
        paradas.append(rt.id)                           # o de verdade também marca `stopping` na hora
        rt.state = InstanceState.stopping

    devs.request_stop = parada_falsa                    # type: ignore[assignment]
    sched.repo.dispatchable_objectives = lambda: [      # type: ignore[assignment]
        {"id": 1, "instance_id": alvo.id, "run_id": "r000001"}]
    sched.repo.note_waiting = lambda *a, **k: None      # type: ignore[assignment] - o objetivo aqui é fabricado

    s = harness.state.settings.get()                    # type: ignore[union-attr]
    for _ in range(5):                                  # cinco ticks seguidos, como o laço real
        sched._rotate(s)

    assert paradas == paradas[:1]                       # uma única vaga cedida, não uma por tick
    assert len(paradas) == 1, f"o rodízio despejou {paradas} para abrir UMA vaga"


async def test_sem_rodizio_aparelho_parado_bloqueia_como_antes(harness: Harness) -> None:
    devs = harness.state.devices                        # type: ignore[union-attr]
    await devs.stop_instance(devs.get("android-02"))
    run = harness.run(["android-01", "android-02"])
    detail = await harness.wait_run(run.id)
    by = {o.instance_id: o.status for o in detail.objectives}
    assert by == {"android-01": "succeeded", "android-02": "waiting_user"}


async def test_nao_desliga_aparelho_em_foco_nem_com_usuario_no_controle(harness: Harness) -> None:
    devs = harness.state.devices                        # type: ignore[union-attr]
    a1, a2 = devs.get("android-01"), devs.get("android-02")
    await devs.stop_instance(a2)
    await devs.stop_instance(devs.get("android-03"))
    _rotation(harness, slots=1)
    devs.set_focus("android-01", ttl_s=1.2)              # o usuário está olhando o android-01 no painel
    run = harness.run(["android-02"])
    await asyncio.sleep(0.8)
    assert a1.state == InstanceState.online and a2.state == InstanceState.stopped
    obj = harness.state.repo.objective_row(f"{run.id}:android-02")            # type: ignore[union-attr]
    assert obj["status"] == "pending" and "aguardando vaga" in (obj["status_detail"] or "")
    detail = await harness.wait_run(run.id, timeout=60)  # o foco expira → a vaga é cedida → a conta é atendida
    assert detail.status == "completed" and a1.state == InstanceState.stopped


async def test_recusa_de_ram_nao_vira_martelada(harness: Harness) -> None:
    devs = harness.state.devices                        # type: ignore[union-attr]
    rt = devs.get("android-03")
    await devs.stop_instance(rt)
    rt.start_backoff_until = time.monotonic() + 60       # como após uma recusa da guarda de RAM
    assert devs.request_start(rt, "teste") is False and rt.state == InstanceState.stopped
    rt.start_backoff_until = 0
    assert devs.request_start(rt, "teste") is True
    await harness.wait(lambda: rt.state == InstanceState.online, what="boot do aparelho falso")


# ---------------------------------------------------------------------------------- hibernação por snapshot
async def test_rodizio_com_hibernacao_acorda_do_snapshot_e_ele_e_de_uso_unico(harness: Harness) -> None:
    harness.cfg.file.android.hibernation = True
    await _stop_all(harness)
    _rotation(harness, slots=1)
    devs = harness.state.devices                        # type: ignore[union-attr]
    db = harness.state.db                               # type: ignore[union-attr]
    first = await harness.wait_run(harness.run(IDS).id, timeout=90)
    assert first.counts.succeeded == 3 and all(kind == "cold" for _, kind in devs.boots)
    # quem cedeu a vaga ficou HIBERNADO (o último a rodar continua ligado)
    states = {rt.id: rt.state for rt in devs.devices.values()}
    assert sorted(states.values()) == sorted([InstanceState.hibernated, InstanceState.hibernated, InstanceState.online])

    devs.boots.clear()
    second = await harness.wait_run(harness.run(IDS).id, timeout=90)
    assert second.counts.succeeded == 3
    assert [k for _, k in devs.boots].count("warm") == 2                     # os dois hibernados acordaram do snapshot
    for iid in IDS:
        assert len(harness.fakes[iid].messages) == 2                          # 1 por conta por execução, sem duplicar
    # uso único: aparelho ligado nunca fica com snapshot válido no banco
    for rt in devs.devices.values():
        valid = db.scalar("SELECT snapshot_valid FROM instances WHERE id=?", (rt.id,))
        assert bool(valid) == (rt.state == InstanceState.hibernated)


async def test_hibernado_sobrevive_a_reinicio_do_backend_e_snapshot_falho_vira_desligado(harness: Harness) -> None:
    harness.cfg.file.android.hibernation = True
    devs = harness.state.devices                        # type: ignore[union-attr]
    await devs.stop_instance(devs.get("android-01"), hibernate=True)
    devs.fake_snapshot_ok = False                        # console do emulador não confirmou o snapshot
    await devs.stop_instance(devs.get("android-02"), hibernate=True)
    assert devs.get("android-01").state == InstanceState.hibernated
    assert devs.get("android-02").state == InstanceState.stopped             # sem snapshot confiável: próximo boot a frio
    await harness.crash()
    state = await harness.boot()
    assert state.devices.get("android-01").state == InstanceState.hibernated  # restaurado do banco
    # reset descarta o snapshot (dados apagados nunca convivem com snapshot antigo)
    await state.devices.reset_instance(state.devices.get("android-01"))
    await harness.wait(lambda: state.devices.get("android-01").state == InstanceState.online, what="boot após reset")
    assert state.devices.boots[-1] == ("android-01", "cold")
    assert state.db.scalar("SELECT snapshot_valid FROM instances WHERE id='android-01'") == 0


def test_args_do_emulador_para_snapshot() -> None:
    from pathlib import Path

    import pytest

    from app.config import AndroidCfg
    from app.devices import emulator as emu

    class Tools:
        emulator = Path("emulator.exe")

    off, on = AndroidCfg(), AndroidCfg(hibernation=True)
    assert "-no-snapshot" in emu.build_args(Tools(), "a", 5554, off, wipe_data=False)      # padrão: como sempre foi
    cold = emu.build_args(Tools(), "a", 5554, on, wipe_data=True)
    assert {"-no-snapshot-load", "-no-snapshot-save", "-wipe-data"} <= set(cold) and "-snapshot" not in cold
    warm = emu.build_args(Tools(), "a", 5554, on, wipe_data=False, from_snapshot=True)
    assert warm[warm.index("-snapshot") + 1] == emu.SNAPSHOT_NAME and "-no-snapshot-save" in warm
    with pytest.raises(emu.EmulatorError):
        emu.build_args(Tools(), "a", 5554, on, wipe_data=True, from_snapshot=True)


def test_particao_de_dados_nunca_encolhe_entre_sessoes(tmp_path) -> None:
    """Medido: o emulador aumenta `disk.dataPartition.size` no 1º boot; reescrever 4G a cada boot mudava o hardware
    entre sessões e o snapshot da hibernação era recusado ("cannot load snapshot")."""
    from app.config import AndroidCfg
    from app.devices.avd import AvdManager, _size_bytes

    from .conftest import make_config

    assert _size_bytes("4G") == 4 * 1024**3 and _size_bytes(" 6442450944") == 6442450944 and _size_bytes("x") == 0
    cfg = make_config(tmp_path)
    ini = cfg.avd_home / "a.avd" / "config.ini"
    ini.parent.mkdir(parents=True)
    ini.write_text("disk.dataPartition.size = 6442450944\nhw.ramSize = 2048\n", encoding="utf-8")
    AvdManager(cfg, tools=None).apply_hardware("a", AndroidCfg(ram_mb=1536, data_partition="4G", hibernation=True))  # type: ignore[arg-type]
    text = ini.read_text(encoding="utf-8")
    assert "disk.dataPartition.size = 6442450944" in text and "hw.ramSize=1536" in text
    assert "fastboot.forceColdBoot=no" in text
    ini.write_text("disk.dataPartition.size=2G\n", encoding="utf-8")            # menor que o configurado: cresce
    AvdManager(cfg, tools=None).apply_hardware("a", AndroidCfg(data_partition="4G"))  # type: ignore[arg-type]
    assert "disk.dataPartition.size=4G" in ini.read_text(encoding="utf-8")
