"""Prontidão por subsistema: servicemanager → system_server → display (`devices/prontidao.py`).

Análise forense do wake do android-09 (25/09/2026): adb `device`, `boot_completed=1`, o `prepare_for_automation`
do worker ficou 40 s mudo (`adb shell excedeu 40s`) e o wake fechou `succeeded` 0,3 s depois. O `service check`
do PR #5 teria passado também — ele só pergunta ao `servicemanager`. A captura de tela nunca respondeu.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.devices import manager as manager_mod
from app.devices import prontidao
from app.devices.adb import AdbError, AdbTimeout
from app.models import InstanceState
from app.worker import executor as executor_mod
from app.worker.executor import VerbUncertain

from .conftest import Harness
from .test_worker_executor import AdbFalso, _estado_falso, _executor, _sem_emulador, _sem_guarda_de_ram


class Alvo:
    """Alvo mínimo da escada: cada subsistema responde, trava (sleep + timeout) ou nega."""

    def __init__(self, **modo: str) -> None:
        self.modo = {"service_manager": "ok", "system_server": "ok", "display": "ok", **modo}
        self.prazos: dict[str, float] = {}

    def _sonda(self, sub: str, timeout: float) -> bool:
        self.prazos[sub] = timeout
        m = self.modo[sub]
        if m == "trava":
            time.sleep(min(timeout, 0.05))
            raise AdbTimeout(f"{sub} excedeu {timeout}s")
        if m == "lento":                              # dorme o prazo inteiro: prova do orçamento
            time.sleep(timeout)
            raise AdbTimeout(f"{sub} excedeu {timeout}s")
        return m == "ok"

    def framework_alive(self, *, timeout: float = 25) -> bool:
        return self._sonda("service_manager", timeout)

    def system_server_alive(self, *, timeout: float = 8) -> bool:
        return self._sonda("system_server", timeout)

    def display_alive(self, *, timeout: float = 12) -> bool:
        return self._sonda("display", timeout)


# ---------------------------------------------------------------- 1, 2, 5: a escada
def test_1_servicemanager_ok_e_system_server_travado_nao_e_pronto() -> None:
    p = prontidao.avaliar(Alvo(system_server="trava"))
    assert not p.pronto and p.estado == "mudo" and p.falta == "system_server"
    assert p.respondeu == ("service_manager",) and "aguardando system_server" in p.detalhe()


def test_2_system_server_ok_e_display_travado_nao_e_pronto() -> None:
    p = prontidao.avaliar(Alvo(display="trava"))
    assert not p.pronto and p.falta == "display" and p.respondeu == ("service_manager", "system_server")
    assert "system_server respondeu; aguardando display" in p.detalhe()


def test_5_tres_subsistemas_respondendo_e_pronto() -> None:
    assert prontidao.avaliar(Alvo()).pronto


def test_servicemanager_negando_e_morto_e_os_outros_negando_sao_mudos() -> None:
    assert prontidao.avaliar(Alvo(service_manager="nega")).estado == "morto"
    assert prontidao.avaliar(Alvo(display="nega")).estado == "mudo"


# ---------------------------------------------------------------- 8: orçamento global
def test_8_orcamento_corta_cada_prazo_e_a_rodada_nao_estoura() -> None:
    alvo = Alvo(service_manager="lento")
    t0 = time.monotonic()
    p = prontidao.avaliar(alvo, restante_s=1.5)
    gasto = time.monotonic() - t0
    assert not p.pronto and alvo.prazos["service_manager"] <= 1.5
    assert gasto < 2.5, "a rodada respeita o que resta do prazo de boot/wake"
    alvo2 = Alvo()
    prontidao.avaliar(alvo2, restante_s=4.0)
    assert all(v <= 4.0 for v in alvo2.prazos.values())
    assert prontidao.prazo_da_rodada() <= 30, "três sondas em série nunca somam minutos"


# ---------------------------------------------------------------- 3, 4, 5, 6, 7, 8: worker
def _worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adb: AdbFalso, *, hibernacao: bool = False) -> Any:
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)
    if hibernacao:
        com_hib = ex._android().model_copy(update={"hibernation": True})
        monkeypatch.setattr(ex, "_android", lambda: com_hib)
        monkeypatch.setattr(ex, "snapshot_existe", lambda _spec: True)
    return ex


def _preparo(adb: AdbFalso, erro: Exception) -> None:
    def falha() -> None:
        raise erro
    adb.prepare_for_automation = falha               # type: ignore[method-assign]


async def test_3_preparo_estourado_com_service_check_ok_nao_fecha_succeeded(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """A lacuna exata de 25/09: preparo 40 s mudo, `service check` ok — e antes disso virava `succeeded`."""
    adb = AdbFalso(pronto_depois_de=1)
    _preparo(adb, AdbTimeout("adb shell excedeu 40s"))
    adb.system_server_mudo = True                    # type: ignore[attr-defined]
    ex = _worker(tmp_path, monkeypatch, adb)
    with pytest.raises(VerbUncertain) as saida:
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 0.3})
    assert "o preparo não respondeu" in str(saida.value)


async def test_3b_r1_preparo_estourado_nao_fecha_succeeded_nem_com_a_escada_toda_respondendo(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AdbTimeout` encerra só o cliente adb local: o efeito do preparo no aparelho segue incerto, e nenhuma sonda
    desta tentativa prova ser posterior a ele (a forense viu 3 s de recuperação parcial antes do travamento)."""
    adb = AdbFalso(pronto_depois_de=1)
    _preparo(adb, AdbTimeout("adb shell excedeu 40s"))
    ex = _worker(tmp_path, monkeypatch, adb)
    with pytest.raises(VerbUncertain) as saida:
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    assert "efeito do preparo" in str(saida.value)
    assert adb.prazos == [], "nenhuma sonda depois do timeout nesta tentativa"


async def test_4_preparo_com_erro_rapido_e_escada_ok_segue_succeeded(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    _preparo(adb, AdbError("dispensar diálogo falhou"))
    ex = _worker(tmp_path, monkeypatch, adb)
    assert (await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5}))["started"] is True


async def test_5_worker_com_os_tres_subsistemas_fecha_succeeded(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=2)
    ex = _worker(tmp_path, monkeypatch, adb)
    assert (await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5}))["started"] is True
    assert {s for s, _ in adb.prazos} == {"system_server", "display"}


async def test_6_boot_normal_servicemanager_antes_dos_outros_espera_e_nao_falha(tmp_path: Path,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot normal: serviços registrados antes de o `system_server` e o display responderem. É espera, não erro."""
    adb = AdbFalso(pronto_depois_de=1)
    adb.system_server_mudo = True                    # type: ignore[attr-defined]
    ex = _worker(tmp_path, monkeypatch, adb)

    async def liberar() -> None:
        await asyncio.sleep(0.15)
        adb.system_server_mudo = False               # type: ignore[attr-defined]
    liberador = asyncio.create_task(liberar())
    saida = await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    await liberador
    assert saida["started"] is True


async def test_7_wake_com_display_mudo_e_uncertain_nunca_succeeded(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.display_mudo = True                          # type: ignore[attr-defined]
    ex = _worker(tmp_path, monkeypatch, adb, hibernacao=True)
    with pytest.raises(VerbUncertain) as saida:
        await ex.run("wake", ex.settings.devices[0], {"boot_timeout_s": 0.3})
    assert "aguardando display" in str(saida.value)


async def test_8_worker_respeita_o_prazo_do_verbo_com_sondas_lentas(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.framework_alive = lambda *, timeout=25: (time.sleep(timeout), False)[1]   # type: ignore[method-assign]
    ex = _worker(tmp_path, monkeypatch, adb)
    t0 = time.monotonic()
    with pytest.raises(VerbUncertain):
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 1.0})
    assert time.monotonic() - t0 < 4.0, "sonda lenta não estica o prazo do verbo"


# ---------------------------------------------------------------- 1, 2, 6, 9, 10: central
def _externo(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> Any:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    monkeypatch.setattr(rt, "external", True)
    s.devices._set_state(rt, InstanceState.stopped, "emulador desligado no worker")
    monkeypatch.setattr(rt.adb, "connect", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(rt.adb, "state", lambda *a, **k: "device")
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: True)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    return rt


@pytest.mark.parametrize(("sub", "falta"), [("system_server_mudo", "system_server"), ("display_mudo", "display")])
async def test_central_nao_libera_com_subsistema_mudo(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                     sub: str, falta: str) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    setattr(harness.fakes["android-01"], sub, True)
    await s.devices._adopt_external(rt)
    dto = s.devices.dto(rt)
    assert rt.state == InstanceState.booting and dto.readiness.phase == "boot_completed"
    assert f"aguardando {prontidao.ROTULO[falta]}" in dto.readiness.detail


async def test_9_central_e_worker_decidem_igual(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
    """Mesmas respostas dos subsistemas → mesma decisão: `succeeded` no worker ⇔ `online` no central."""
    s = harness.state
    assert s is not None
    for mudo in (None, "framework_mudo", "system_server_mudo", "display_mudo"):
        adb = AdbFalso(pronto_depois_de=1)
        if mudo:
            setattr(adb, mudo, True)
        ex = _worker(tmp_path / (mudo or "ok"), monkeypatch, adb)
        try:
            await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 0.3})
            worker_pronto = True
        except VerbUncertain:
            worker_pronto = False
        rt = _externo(harness, monkeypatch)
        fake = harness.fakes["android-01"]
        fake.guest_mudo = mudo == "framework_mudo"
        fake.system_server_mudo = mudo == "system_server_mudo"   # type: ignore[attr-defined]
        fake.display_mudo = mudo == "display_mudo"               # type: ignore[attr-defined]
        rt.boot_externo_desde = 0.0
        await s.devices._adopt_external(rt)
        assert worker_pronto == (rt.state == InstanceState.online), mudo


async def test_10_sonda_de_display_nao_passa_pela_captura(harness: Harness) -> None:
    """A prontidão roda na trilha de sonda: com a fila da captura travada ela conclui, e não mexe no stream."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    antes = (rt.capture_failures, rt.frame)
    solta = threading.Event()
    asyncio.get_running_loop().create_task(rt.executor.run(lambda: solta.wait(10), timeout=15, label="screencap"))
    await asyncio.sleep(0.05)
    try:
        assert rt.executor.queue_depth > 0
        p = await asyncio.wait_for(s.devices._sondar_prontidao(rt), 5)
        assert p.pronto and (rt.capture_failures, rt.frame) == antes
    finally:
        solta.set()


# ---------------------------------------------------------------- contrato temporal: preparo depois da sonda
# Pronto = os três subsistemas responderam DEPOIS do último sinal de não-resposta, sem operação com efeito em curso.
# Um `prepare_for_automation` que estoura o prazo depois de uma sonda positiva invalida aquela prontidão e deixa
# a tentativa não pronta (efeito incerto); erro rápido invalida e decide uma rodada nova e completa.
def _preparo_que(fake: Any, *, erro: Exception, depois: dict[str, bool] | None = None) -> Any:
    def preparo(*_a: Any, **_k: Any) -> None:
        for flag, valor in (depois or {}).items():
            setattr(fake, flag, valor)
        raise erro
    return preparo


def _readocao_local(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> Any:
    """`_adopt` pelo caminho REAL de readoção (o harness desvia cedo quando há `io_factory`): processo nosso, adb
    `device`, `boot_completed` — só o que é de fora vira dublê; a prontidão fala com o aparelho falso."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    rt.state = InstanceState.stopped
    monkeypatch.setattr(s.devices, "io_factory", None)
    monkeypatch.setattr(s.devices.avd, "exists", lambda *_a: True)
    monkeypatch.setattr(s.devices.tools, "found", lambda *_a: True)
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: True)
    monkeypatch.setattr(rt.adb, "state", lambda *a, **k: "device")
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: True)
    monkeypatch.setattr(s.devices, "_start_online_tasks", lambda _rt: None)
    return rt


def _contar_rodadas(monkeypatch: pytest.MonkeyPatch, devices: Any) -> list[str]:
    rodadas: list[str] = []
    original = devices._sondar_prontidao

    async def contando(rt: Any, restante_s: float | None = None) -> Any:
        p = await original(rt, restante_s)
        rodadas.append(p.estado)
        return p
    monkeypatch.setattr(devices, "_sondar_prontidao", contando)
    return rodadas


async def test_t1_readocao_preparo_estourado_e_system_server_depois_mudo_nao_fica_online(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    fake = harness.fakes["android-01"]
    monkeypatch.setattr(rt.adb, "prepare_for_automation",
                        _preparo_que(fake, erro=AdbTimeout("adb shell excedeu 40s"), depois={"system_server_mudo": True}))
    rodadas = _contar_rodadas(monkeypatch, s.devices)
    await s.devices._adopt(rt)
    boot = rt.tasks.pop("boot", None)
    if boot:
        boot.cancel()
    assert rodadas == ["ok"], "a prontidão de antes do timeout não vale, e nesta tentativa não há outra"
    assert rt.state != InstanceState.online and rt.readiness_phase != "ready"


async def test_t2_readocao_preparo_estourado_nao_fica_online_nem_com_o_android_respondendo(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    monkeypatch.setattr(rt.adb, "prepare_for_automation",
                        _preparo_que(harness.fakes["android-01"], erro=AdbTimeout("adb shell excedeu 40s")))
    rodadas = _contar_rodadas(monkeypatch, s.devices)
    await s.devices._adopt(rt)
    boot = rt.tasks.pop("boot", None)
    if boot:
        boot.cancel()
    assert rodadas == ["ok"] and rt.state != InstanceState.online and rt.readiness_phase != "ready"


async def test_t3_externo_preparo_estourado_e_android_depois_mudo_nao_fica_online(harness: Harness,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    fake = harness.fakes["android-01"]
    monkeypatch.setattr(rt.adb, "prepare_for_automation",
                        _preparo_que(fake, erro=AdbTimeout("adb shell excedeu 40s"), depois={"display_mudo": True}))
    rodadas = _contar_rodadas(monkeypatch, s.devices)
    await s.devices._adopt_external(rt)
    assert rodadas == ["ok"]
    assert rt.state == InstanceState.booting and "efeito dele no aparelho é incerto" in rt.readiness_detail
    assert rt.boot_externo_desde > 0, "o marcador de boot só zera com prontidão confirmada"


async def test_t4_externo_preparo_estourado_fica_booting_e_a_proxima_passagem_decide(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    monkeypatch.setattr(rt.adb, "prepare_for_automation",
                        _preparo_que(harness.fakes["android-01"], erro=AdbTimeout("adb shell excedeu 40s")))
    rodadas = _contar_rodadas(monkeypatch, s.devices)
    await s.devices._adopt_external(rt)
    assert rodadas == ["ok"] and rt.state == InstanceState.booting and rt.boot_externo_desde > 0
    # A PRÓXIMA passagem (a readoção periódica) é outra tentativa: preparo ok → pronto.
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    await s.devices._adopt_external(rt)
    assert rodadas == ["ok", "ok"] and rt.state == InstanceState.online and rt.boot_externo_desde == 0.0


async def test_t5_e2_erro_rapido_benigno_do_preparo_revalida_e_segue_online(harness: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch,
                                                                            caplog: pytest.LogCaptureFixture) -> None:
    """`AdbError` não distingue "o comando recusou" de "o aparelho sumiu" (`shell()` levanta para QUALQUER saída
    não-zero). Erro benigno não bloqueia — mas a prontidão anterior não sobrevive: decide uma rodada nova."""
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    monkeypatch.setattr(rt.adb, "prepare_for_automation",
                        _preparo_que(harness.fakes["android-01"], erro=AdbError("dispensar diálogo falhou")))
    rodadas = _contar_rodadas(monkeypatch, s.devices)
    with caplog.at_level("WARNING", logger="poc.devices"):
        await s.devices._adopt_external(rt)
    assert rodadas == ["ok", "ok"], "erro rápido também exige uma rodada NOVA"
    assert rt.state == InstanceState.online and any("preparo falhou" in r.message for r in caplog.records)


async def test_e1_externo_preparo_device_offline_e_android_mudo_nao_fica_online(harness: Harness,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    fake = harness.fakes["android-01"]
    monkeypatch.setattr(rt.adb, "prepare_for_automation",
                        _preparo_que(fake, erro=AdbError("error: device offline"), depois={"guest_mudo": True}))
    rodadas = _contar_rodadas(monkeypatch, s.devices)
    await s.devices._adopt_external(rt)
    assert rodadas == ["ok", "mudo"]
    assert rt.state != InstanceState.online and rt.readiness_phase != "ready"


async def test_e1b_readocao_preparo_device_offline_e_android_mudo_nao_fica_online(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    fake = harness.fakes["android-01"]
    monkeypatch.setattr(rt.adb, "prepare_for_automation",
                        _preparo_que(fake, erro=AdbError("error: device offline"), depois={"system_server_mudo": True}))
    rodadas = _contar_rodadas(monkeypatch, s.devices)
    await s.devices._adopt(rt)
    boot = rt.tasks.pop("boot", None)
    if boot:
        boot.cancel()
    assert rodadas == ["ok", "mudo"]
    assert rt.state != InstanceState.online and rt.readiness_phase != "ready"


# ---------------------------------------------------------------- preparo "zumbi" (DriverTimeout do executor)
# `DeviceExecutor.run`: um timeout NÃO libera o aparelho — a chamada segue viva na thread ("zumbi") até retornar de
# verdade. Revalidar a prontidão enquanto o preparo antigo ainda mexe no aparelho violaria o contrato temporal.
def _preparo_bloqueado(libera: threading.Event, fim: list[float]) -> Any:
    def preparo(*_a: Any, **_k: Any) -> None:
        try:
            libera.wait(10)                          # só `libera` decide quando a chamada termina (sem sleep real)
        finally:
            fim.append(time.monotonic())
    return preparo


def _rodadas_com_hora(monkeypatch: pytest.MonkeyPatch, devices: Any) -> list[tuple[str, float]]:
    rodadas: list[tuple[str, float]] = []
    original = devices._sondar_prontidao

    async def contando(rt: Any, restante_s: float | None = None) -> Any:
        inicio = time.monotonic()
        p = await original(rt, restante_s)
        rodadas.append((p.estado, inicio))
        return p
    monkeypatch.setattr(devices, "_sondar_prontidao", contando)
    return rodadas


async def test_z1_preparo_zumbi_nao_deixa_revalidar_nem_ficar_online(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    libera, fim = threading.Event(), []
    monkeypatch.setattr(rt.adb, "prepare_for_automation", _preparo_bloqueado(libera, fim))
    monkeypatch.setattr(manager_mod, "PRAZO_DO_PREPARO_S", 0.1)
    monkeypatch.setattr(manager_mod, "ESPERA_DO_PREPARO_ZUMBI_S", 0.3)
    rodadas = _rodadas_com_hora(monkeypatch, s.devices)
    try:
        await s.devices._adopt_external(rt)
        assert rt.executor.has_zombie, "o preparo segue vivo na thread do aparelho"
        assert [e for e, _ in rodadas] == ["ok"], "nenhuma rodada nova enquanto o zumbi existe"
        assert rt.state != InstanceState.online and rt.readiness_phase != "ready"
        assert "não terminou" in rt.readiness_detail
    finally:
        libera.set()
        await rt.executor.drain(max_wait_s=5)


async def test_z2_r4_preparo_zumbi_drenado_nao_libera_esta_tentativa(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    libera, fim = threading.Event(), []
    monkeypatch.setattr(rt.adb, "prepare_for_automation", _preparo_bloqueado(libera, fim))
    monkeypatch.setattr(manager_mod, "PRAZO_DO_PREPARO_S", 0.1)
    monkeypatch.setattr(manager_mod, "ESPERA_DO_PREPARO_ZUMBI_S", 5.0)
    rodadas = _rodadas_com_hora(monkeypatch, s.devices)
    threading.Timer(0.3, libera.set).start()       # o preparo antigo termina um pouco depois do timeout do executor
    await s.devices._adopt_external(rt)
    assert fim, "o drain esperou o fim real do zumbi antes de devolver"
    assert not rt.executor.has_zombie, "nada concorre com a próxima tentativa"
    assert [e for e, _ in rodadas] == ["ok"] and rt.state != InstanceState.online, \
        "drenar prova só o fim da thread local; o efeito do timeout segue incerto nesta tentativa"


async def test_z3_zumbi_que_nao_termina_no_orcamento_nao_espera_para_sempre(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    libera, fim = threading.Event(), []
    monkeypatch.setattr(rt.adb, "prepare_for_automation", _preparo_bloqueado(libera, fim))
    monkeypatch.setattr(manager_mod, "PRAZO_DO_PREPARO_S", 0.1)
    monkeypatch.setattr(manager_mod, "ESPERA_DO_PREPARO_ZUMBI_S", 0.3)
    t0 = time.monotonic()
    try:
        await s.devices._adopt_external(rt)
        gasto = time.monotonic() - t0
        assert gasto < 3.0, "a espera pelo zumbi respeita o orçamento"
        assert rt.state != InstanceState.online and rt.readiness_phase != "ready"
    finally:
        libera.set()
        await rt.executor.drain(max_wait_s=5)


async def test_z4_adb_timeout_sem_drain_e_sem_rodada(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AdbTimeout` = o adb desistiu dentro da chamada: não há zumbi LOCAL a esperar, mas o efeito no aparelho
    segue incerto — nenhuma rodada nesta tentativa."""
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    monkeypatch.setattr(rt.adb, "prepare_for_automation",
                        _preparo_que(harness.fakes["android-01"], erro=AdbTimeout("adb shell excedeu 40s")))
    drenos: list[Any] = []
    original = rt.executor.drain

    async def espiao(*a: Any, **k: Any) -> bool:
        drenos.append(k)
        return await original(*a, **k)
    monkeypatch.setattr(rt.executor, "drain", espiao)
    rodadas = _rodadas_com_hora(monkeypatch, s.devices)
    await s.devices._adopt_external(rt)
    assert drenos == [] and [e for e, _ in rodadas] == ["ok"] and rt.state != InstanceState.online


# ---------------------------------------------------------------- sinais DEPOIS da prontidão (acerto do relógio)
# Depois da escada, `start`/`wake` ainda acertam o relógio do guest (`sync_clock`: `date`, `cmd alarm set-time` —
# este passa pelo `system_server`). Um timeout ali é sinal de não-resposta POSTERIOR à prontidão, com efeito incerto
# no aparelho: a tentativa não fica pronta. Erro rápido invalida a prontidão e decide uma rodada nova (relógio
# atrasado, por si, não impede a operação).
@pytest.mark.parametrize("verbo", ["start", "wake"])
async def test_w1_worker_relogio_estourado_e_system_server_depois_mudo_e_uncertain(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verbo: str) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.sync_clock = _preparo_que(adb, erro=AdbTimeout("adb shell excedeu 10s"),  # type: ignore[method-assign]
                                  depois={"system_server_mudo": True})
    ex = _worker(tmp_path, monkeypatch, adb, hibernacao=verbo == "wake")
    with pytest.raises(VerbUncertain) as saida:
        await ex.run(verbo, ex.settings.devices[0], {"boot_timeout_s": 5})
    assert "acerto do relógio não respondeu" in str(saida.value)


async def test_w2_r2_worker_relogio_estourado_e_uncertain_mesmo_com_a_escada_ok(tmp_path: Path,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.sync_clock = _preparo_que(adb, erro=AdbTimeout("adb shell excedeu 10s"))  # type: ignore[method-assign]
    ex = _worker(tmp_path, monkeypatch, adb)
    with pytest.raises(VerbUncertain):
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    assert [s for s, _ in adb.prazos].count("display") == 1, "nenhuma rodada depois do timeout nesta tentativa"


async def test_w3_e4_worker_relogio_com_erro_rapido_benigno_revalida_e_fecha_succeeded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.sync_clock = _preparo_que(adb, erro=AdbError("date: bad format"))  # type: ignore[method-assign]
    ex = _worker(tmp_path, monkeypatch, adb)
    assert (await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5}))["started"] is True
    assert [s for s, _ in adb.prazos].count("display") == 2, "erro rápido também exige uma rodada NOVA"


@pytest.mark.parametrize("verbo", ["start", "wake"])
async def test_e3_worker_relogio_device_offline_e_system_server_mudo_e_uncertain(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verbo: str) -> None:
    adb = AdbFalso(pronto_depois_de=1)
    adb.sync_clock = _preparo_que(adb, erro=AdbError("error: device offline"),  # type: ignore[method-assign]
                                  depois={"system_server_mudo": True})
    ex = _worker(tmp_path, monkeypatch, adb, hibernacao=verbo == "wake")
    with pytest.raises(VerbUncertain) as saida:
        await ex.run(verbo, ex.settings.devices[0], {"boot_timeout_s": 5})
    assert "aguardando system_server" in str(saida.value)


# ---------------------------------------------------------------- boot local (`_wait_boot`): zumbi e relógio
def _encurtar(monkeypatch: pytest.MonkeyPatch, rt: Any, rotulo: str, prazo: float) -> None:
    """Encurta o prazo do executor só para a chamada `rotulo` — vale para o literal antigo e para a constante."""
    original = rt.executor.run

    async def run(fn: Any, *a: Any, timeout: float, label: str = "") -> Any:
        return await original(fn, *a, timeout=min(timeout, prazo) if label == rotulo else timeout, label=label)
    monkeypatch.setattr(rt.executor, "run", run)


async def _wait_boot_saudavel(harness: Harness, monkeypatch: pytest.MonkeyPatch, *, warm: bool,
                              **dubles: Any) -> tuple[Any, bool, list[tuple[str, float]]]:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.booting, "emulador iniciado")
    rt.pid = 4242
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: True)
    monkeypatch.setattr(manager_mod, "RESPOSTA_POS_BOOT_S", 0.05)
    monkeypatch.setattr(manager_mod, "RESPOSTA_MIN_S", 0.05)
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: True)
    monkeypatch.setattr(rt.adb, "ui_ready", lambda *a, **k: True)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", dubles.get("preparo", lambda *a, **k: None))
    monkeypatch.setattr(rt.adb, "sync_clock", dubles.get("relogio", lambda *a, **k: (0, 0)), raising=False)
    monkeypatch.setattr(s.devices, "_snapshot_verdict", lambda _rt: True)
    monkeypatch.setattr(s.devices, "_start_online_tasks", lambda _rt: None)
    s.cfg.file.limits.boot_poll_s = 0.01
    for rotulo, prazo in dubles.get("encurtar", {}).items():
        _encurtar(monkeypatch, rt, rotulo, prazo)
    rodadas = _rodadas_com_hora(monkeypatch, s.devices)
    ok = await s.devices._wait_boot(rt, time.monotonic(), warm=warm)
    return rt, ok, rodadas


async def test_b1_boot_local_preparo_zumbi_nao_deixa_sondar_nem_ficar_online(harness: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """`_wait_boot` faz o preparo ANTES da escada — mas um `DriverTimeout` deixa o preparo vivo na thread do
    aparelho, e a escada (trilha de sonda) passaria com ele ainda mexendo no Android."""
    libera, fim = threading.Event(), []
    monkeypatch.setattr(manager_mod, "ESPERA_DO_PREPARO_ZUMBI_S", 0.3)
    t0 = time.monotonic()
    try:
        rt, ok, rodadas = await _wait_boot_saudavel(harness, monkeypatch, warm=False,
                                                    preparo=_preparo_bloqueado(libera, fim),
                                                    encurtar={"prepare": 0.1})
        assert rodadas == [], "nenhuma rodada enquanto o preparo zumbi existe"
        assert ok is False and rt.state != InstanceState.online and "não terminou" in rt.readiness_detail
        assert time.monotonic() - t0 < 3.0, "a espera pelo zumbi respeita o teto"
    finally:
        libera.set()
        await harness.state.devices.get("android-01").executor.drain(max_wait_s=5)   # type: ignore[union-attr]


async def test_b2_r4_boot_local_preparo_zumbi_drenado_nao_libera_esta_tentativa(harness: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    libera, fim = threading.Event(), []
    monkeypatch.setattr(manager_mod, "ESPERA_DO_PREPARO_ZUMBI_S", 5.0)
    threading.Timer(0.3, libera.set).start()
    rt, ok, rodadas = await _wait_boot_saudavel(harness, monkeypatch, warm=False,
                                                preparo=_preparo_bloqueado(libera, fim), encurtar={"prepare": 0.1})
    assert fim and not rt.executor.has_zombie, "o drain esperou o fim real antes de devolver"
    assert rodadas == [] and ok is False and rt.state != InstanceState.online, \
        "drenar prova só o fim da thread local; o efeito do timeout segue incerto nesta tentativa"


async def test_b3_wake_local_relogio_estourado_e_display_depois_mudo_nao_fica_online(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = harness.fakes["android-01"]
    rt, ok, rodadas = await _wait_boot_saudavel(
        harness, monkeypatch, warm=True,
        relogio=_preparo_que(fake, erro=AdbTimeout("adb shell excedeu 10s"), depois={"display_mudo": True}))
    assert [e for e, _ in rodadas] == ["ok"]
    assert ok is False and rt.state != InstanceState.online and "efeito dele no aparelho é incerto" in rt.readiness_detail


async def test_b4_r3_wake_local_relogio_estourado_nao_fica_online_mesmo_com_o_android_ok(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = harness.fakes["android-01"]
    rt, ok, rodadas = await _wait_boot_saudavel(harness, monkeypatch, warm=True,
                                                relogio=_preparo_que(fake, erro=AdbTimeout("adb shell excedeu 10s")))
    assert [e for e, _ in rodadas] == ["ok"] and ok is False and rt.state != InstanceState.online


async def test_b5_wake_local_relogio_zumbi_que_nao_termina_nao_fica_online(harness: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    libera, fim = threading.Event(), []
    monkeypatch.setattr(manager_mod, "ESPERA_DO_PREPARO_ZUMBI_S", 0.3)
    try:
        rt, ok, rodadas = await _wait_boot_saudavel(harness, monkeypatch, warm=True,
                                                    relogio=_preparo_bloqueado(libera, fim),
                                                    encurtar={"acertar relógio": 0.1})
        assert [e for e, _ in rodadas] == ["ok"], "nenhuma rodada nova enquanto o acerto do relógio segue vivo"
        assert ok is False and rt.state != InstanceState.online
    finally:
        libera.set()
        await harness.state.devices.get("android-01").executor.drain(max_wait_s=5)   # type: ignore[union-attr]


async def test_e5_wake_local_relogio_device_offline_e_display_mudo_nao_fica_online(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = harness.fakes["android-01"]
    rt, ok, rodadas = await _wait_boot_saudavel(
        harness, monkeypatch, warm=True,
        relogio=_preparo_que(fake, erro=AdbError("error: device offline"), depois={"display_mudo": True}))
    assert [e for e, _ in rodadas] == ["ok", "mudo"]
    assert ok is False and rt.state != InstanceState.online and "aguardando display" in rt.readiness_detail


async def test_e6_wake_local_relogio_com_erro_benigno_revalida_e_fica_online(harness: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    fake = harness.fakes["android-01"]
    rt, ok, rodadas = await _wait_boot_saudavel(harness, monkeypatch, warm=True,
                                                relogio=_preparo_que(fake, erro=AdbError("date: bad format")))
    assert [e for e, _ in rodadas] == ["ok", "ok"] and ok is True and rt.state == InstanceState.online
