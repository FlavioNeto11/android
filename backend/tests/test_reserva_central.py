"""Reserva de RAM do lado do central na guarda de boot (frente F4, fase B; desenho da F5).

O defeito: com `boot_parallelism` 2, dois `_boot` entravam juntos em `_recusa_por_capacidade`. O desconto de "boot em
andamento" só enxergava aparelho `booting` COM PID — e no instante da guarda nenhum dos dois tinha PID ainda. Os dois
passavam numa RAM que comportava um só. Agora a guarda grava a reserva no instante em que admite (antes de qualquer
`await`), o próximo a vê, e ela sai no fim do `_boot` — ou fica órfã enquanto o emulador pode estar vivo sem ser
contado (cancelado com PID), até se saber que ele morreu ou virou aparelho online.

Prova `simulated`: aparelho falso e memória escolhida pelo teste (`FakeEmulatorBackend.free_mb`).
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.devices.manager import Limiter
from app.metricas import metricas
from app.models import InstanceState

from .conftest import Harness


async def _dois_parados(h: Harness) -> tuple[Any, Any, Any, Any]:
    st = h.state
    assert st is not None
    devs = st.devices
    um, dois = devs.get("android-01"), devs.get("android-02")
    for rt in (um, dois):
        await devs.stop_instance(rt)
        assert rt.state == InstanceState.stopped
    a = h.cfg.instance_android("android-01")
    assert h.cfg.instance_android("android-02").est_ram_host_mb() == a.est_ram_host_mb()
    metricas.limpar()
    return devs, um, dois, a


def _ram_para_um(h: Harness, a: Any) -> None:
    est = a.est_ram_host_mb()
    h.emulator.free_mb = float(est + a.min_free_ram_mb_after_boot + est // 2)


async def test_dois_starts_simultaneos_com_ram_para_um_so_admitem_um(harness: Harness) -> None:
    devs, um, dois, a = await _dois_parados(harness)
    devs.boot_limiter = Limiter(2)                 # dois boots podem entrar juntos na guarda
    devs.fake_boot_s = 0.3                         # e ficar em voo ao mesmo tempo
    _ram_para_um(harness, a)

    await devs.start_instance(um)
    await devs.start_instance(dois)
    await asyncio.wait_for(asyncio.gather(um.tasks["boot"], dois.tasks["boot"]), timeout=10)

    estados = sorted([um.state, dois.state], key=lambda s: s.value)
    assert estados == sorted([InstanceState.online, InstanceState.stopped], key=lambda s: s.value), (
        "os dois boots passaram numa RAM que comportava um só")
    recusado = um if um.state == InstanceState.stopped else dois
    assert "reservados para boots em andamento" in (recusado.state_detail or "")
    assert devs._reservas == {} and devs._reservas_orfas == {} and devs._reservas_ate == {}  # noqa: SLF001
    assert metricas.valor("capacidade.reserva", resultado="concedida") == 1
    assert metricas.valor("capacidade.reserva", resultado="recusada", motivo="ram") == 1


async def test_boot_cancelado_antes_do_processo_solta_a_reserva(harness: Harness) -> None:
    devs, um, _dois, _a = await _dois_parados(harness)
    devs.fake_boot_s = 30.0
    await devs.start_instance(um)
    for _ in range(200):
        if um.id in devs._reservas:                                          # noqa: SLF001
            break
        await asyncio.sleep(0.01)
    assert um.id in devs._reservas, "admitido sem reserva"                  # noqa: SLF001
    um.tasks["boot"].cancel()
    await asyncio.gather(um.tasks["boot"], return_exceptions=True)
    assert um.pid is None and devs._reservas == {}, "reserva vazou num boot cancelado"   # noqa: SLF001


async def test_reserva_orfa_fica_enquanto_o_emulador_pode_estar_vivo(harness: Harness) -> None:
    """Boot que terminou (cancelado, prazo) com o PID vivo: o emulador segue alocando RAM sem estar contado. A
    reserva fica até o processo morrer — ou o aparelho ficar online e ser contado pela própria RAM livre."""
    devs, um, dois, a = await _dois_parados(harness)
    _ram_para_um(harness, a)
    assert devs._recusa_por_capacidade(um, a) is None                        # noqa: SLF001 - admitido: reserva
    um.state, um.pid, um.resources = InstanceState.booting, 4242, None
    devs._soltar_reserva(um)                                                 # noqa: SLF001 - fim do `_boot`
    assert um.id in devs._reservas_orfas                                     # noqa: SLF001
    um.state = InstanceState.error                                           # nem online, nem contado

    assert devs._recusa_por_capacidade(dois, a) is not None, "a RAM do órfão foi esquecida"   # noqa: SLF001

    harness.emulator.dead.add(um.avd_name)                                   # o monitor confirma: morreu
    dois.start_refusals = 0
    assert devs._recusa_por_capacidade(dois, a) is None                      # noqa: SLF001
    assert um.id not in devs._reservas and um.id not in devs._reservas_orfas  # noqa: SLF001


async def test_reserva_orfa_sai_quando_o_aparelho_fica_online(harness: Harness) -> None:
    devs, um, dois, a = await _dois_parados(harness)
    _ram_para_um(harness, a)
    assert devs._recusa_por_capacidade(um, a) is None                        # noqa: SLF001
    um.state, um.pid = InstanceState.booting, 4243
    devs._soltar_reserva(um)                                                 # noqa: SLF001
    um.state = InstanceState.online                                          # readotado: agora é RAM usada
    harness.emulator.free_mb = float(a.est_ram_host_mb() + a.min_free_ram_mb_after_boot + 10)
    assert devs._recusa_por_capacidade(dois, a) is None                      # noqa: SLF001
    assert um.id not in devs._reservas                                       # noqa: SLF001


async def test_reserva_orfa_vence_no_prazo_do_boot(harness: Harness) -> None:
    """Igual ao agente: passado o prazo do boot (contado da admissão), o que o processo tinha a alocar já alocou
    — a órfã sai mesmo com o emulador vivo e o aparelho fora de online."""
    import time

    devs, um, dois, a = await _dois_parados(harness)
    _ram_para_um(harness, a)
    assert devs._recusa_por_capacidade(um, a) is None                        # noqa: SLF001
    assert devs._reservas_ate[um.id] > time.monotonic() + a.wake_timeout_s - 5  # noqa: SLF001 - prazo do boot
    um.state, um.pid, um.resources = InstanceState.booting, 4244, None
    devs._soltar_reserva(um)                                                 # noqa: SLF001
    um.state = InstanceState.error
    assert devs._recusa_por_capacidade(dois, a) is not None                  # noqa: SLF001 - ainda no prazo
    devs._reservas_ate[um.id] = time.monotonic() - 1                          # noqa: SLF001 - o prazo venceu
    dois.start_refusals = 0
    assert devs._recusa_por_capacidade(dois, a) is None                      # noqa: SLF001
    assert um.id not in devs._reservas and um.id not in devs._reservas_orfas  # noqa: SLF001
