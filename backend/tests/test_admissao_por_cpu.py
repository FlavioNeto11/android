"""RA-4 (plano-100 29.33): admissão de boot por CPU e prazo do preparo proporcional à carga.

O defeito medido: a admissão só olhava RAM, e `PRAZO_DO_PREPARO_S = 60` (com 40 s no `shell` interno) transformava boot
lento, sob host saturado por outros boots, em degrau de reparo (3 episódios, um até o reset). Aqui:

- a CPU da última batida segura o boot novo no worker (`WorkerCapacity.sem_recurso`) e no host (`_recusa_por_capacidade`),
  com motivo legível; CPU desconhecida (`None`) NUNCA recusa;
- o preparo só começa depois do "Boot completed" e o prazo dele (executor e `shell` interno) cresce com a CPU;
- a medição `boot` grava `host_cpu_percent` e `boots_em_voo`.

Prova `simulated`: relógio injetável (nenhum `sleep` real longo), aparelho e métricas falsos. Real: `not_run`.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import load_config
from app.devices import adb as adb_mod
from app.devices import manager as manager_mod
from app.devices.adb import PRAZO_DO_AJUSTE_S, Adb, fator_de_carga_do_preparo
from app.devices.manager import PRAZO_DO_PREPARO_S
from app.metricas import metricas
from app.models import InstanceState, Metrics
from app.util import now_iso
from app.worker import executor as executor_mod
from app.workers.protocol import Heartbeat, WorkerResources
from app.workers.registry import WorkerCapacity

from .conftest import Harness
from .test_configuracao_de_exemplo import EXEMPLO
from .test_rotation_worker import WORKER, _com_worker, _comandos, _rodizio
from .test_wait_boot_sondas import _RelogioInjetavel
from .test_worker_executor import AdbFalso, _executor, _estado_falso, _sem_emulador, _sem_guarda_de_ram

LIMIAR = 85.0


def _cap(**kw: Any) -> WorkerCapacity:
    base: dict[str, Any] = dict(connected=True, maintenance=False, max_slots=6, ram_free_mb=46000,
                                disk_free_gb=400.0, last_seen_at="2026-10-04T12:00:00Z", stale=False,
                                degraded_detail=None, idade_s=3.0)
    return WorkerCapacity("worker-lan-01", "Notebook da LAN", **{**base, **kw})


def _metricas_do_host(cpu: float) -> Metrics:
    return Metrics(ts=now_iso(), cpu_percent=cpu, mem_total_gb=64.0, mem_available_gb=32.0, mem_used_percent=50.0)


# ---------------------------------------------------------------- worker: a CPU da última batida
def test_cpu_alta_segura_o_boot_novo_no_worker_e_diz_por_que() -> None:
    motivo = _cap(cpu_percent=97.0).sem_recurso(LIMIAR)
    assert motivo is not None
    assert "97 % de CPU" in motivo and "85 %" in motivo and "Notebook da LAN" in motivo and "espera" in motivo


def test_cpu_desconhecida_ou_sem_limiar_nao_segura_o_boot() -> None:
    """`None` é "não sei", e "não sei" não é "lotada": o agente antigo (sem CPU na batida) segue admitido como antes."""
    assert _cap(cpu_percent=None).sem_recurso(LIMIAR) is None
    assert _cap(cpu_percent=99.0).sem_recurso(None) is None
    assert _cap(cpu_percent=99.0).sem_recurso() is None, "sem limiar a assinatura antiga não muda"


def test_cpu_no_limiar_ou_abaixo_admite() -> None:
    assert _cap(cpu_percent=LIMIAR).sem_recurso(LIMIAR) is None
    assert _cap(cpu_percent=40.0).sem_recurso(LIMIAR) is None


def test_batida_velha_continua_segurada_pelo_motivo_dela_e_nao_pela_cpu() -> None:
    motivo = _cap(stale=True, idade_s=90.0, cpu_percent=99.0).sem_recurso(LIMIAR)
    assert motivo is not None and "sem medição recente" in motivo and "CPU" not in motivo


def test_a_ram_baixa_continua_a_primeira_razao() -> None:
    motivo = _cap(ram_free_mb=1000, mem_available_mb=1000, cpu_percent=99.0).sem_recurso(LIMIAR)
    assert motivo is not None and "RAM livre" in motivo


async def test_rodizio_segura_o_start_do_worker_com_cpu_alta_e_liga_com_cpu_desconhecida(tmp_path: Path) -> None:
    """Pelo rodízio de verdade (`_rotate`): CPU 97 % na batida → nenhum `start`, e a espera diz CPU; a batida seguinte
    sem CPU (agente antigo) → o `start` sai."""
    h, reg, _a = await _com_worker(tmp_path, remotos=["android-03"])
    try:
        s = _rodizio(h)
        sched = h.state.scheduler                                                            # type: ignore[union-attr]
        esperas: list[str] = []
        sched.repo.note_waiting = lambda oid, detail, **k: esperas.append(detail)            # type: ignore[assignment]
        sched.repo.instances_with_open_work = lambda: set()                                  # type: ignore[assignment]
        sched.repo.instances_needing_user = lambda: set()                                    # type: ignore[assignment]
        sched.repo.dispatchable_objectives = lambda: [                                       # type: ignore[assignment]
            {"id": "o-1", "instance_id": "android-03", "run_id": "r000001"}]

        reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(
            ram_free_mb=46367, disk_free_gb=400.0, cpu_percent=97.0, cpu_count=12)))
        sched._rotate(s)
        assert _comandos(h, "start") == [], "boot mandado para uma máquina com 97 % de CPU"
        assert esperas and "CPU" in esperas[-1] and "97" in esperas[-1]
        assert sched.servidores()[WORKER].vagas_livres == 0

        reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(
            ram_free_mb=46367, disk_free_gb=400.0, cpu_percent=None, cpu_count=12)))
        sched._rotate(s)
        assert len(_comandos(h, "start")) == 1, "CPU desconhecida segurou o boot"
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- host: a guarda dentro do boot
async def _parado_com_ram(h: Harness) -> tuple[Any, Any]:
    st = h.state
    assert st is not None
    devs, rt = st.devices, st.devices.get("android-01")
    await devs.stop_instance(rt)
    assert rt.state == InstanceState.stopped
    a = h.cfg.instance_android("android-01")
    h.emulator.free_mb = float(a.est_ram_host_mb() + a.min_free_ram_mb_after_boot + 4000)   # RAM de sobra
    metricas.limpar()
    return devs, rt


async def test_cpu_alta_no_host_recusa_o_boot_com_motivo_e_espera_crescente(harness: Harness) -> None:
    devs, rt = await _parado_com_ram(harness)
    devs.last_metrics = _metricas_do_host(96.0)
    devs.fake_boot_s = 0.01

    await devs.start_instance(rt)
    await devs.aguardar_boot(rt, 10)

    assert rt.state == InstanceState.stopped, "subiu com 96 % de CPU no host"
    assert "CPU do host no limite" in (rt.state_detail or "") and "96 %" in (rt.state_detail or "")
    assert "85 %" in (rt.state_detail or "")
    assert rt.start_refusals == 1 and rt.start_backoff_until > 0, "sem espera crescente o rodízio insiste a cada tick"
    assert devs._reservas == {}, "recusado não tem reserva"                                   # noqa: SLF001
    assert metricas.valor("capacidade.reserva", resultado="recusada", motivo="cpu") == 1
    assert metricas.valor("capacidade.reserva", resultado="concedida") == 0
    linha = harness.state.db.query("SELECT data FROM measurements WHERE kind='capacity'")[-1]    # type: ignore[union-attr]
    dados = json.loads(linha["data"])
    assert dados["refused"] is True and dados["motivo"] == "cpu" and dados["host_cpu_percent"] == 96.0


async def test_cpu_desconhecida_no_host_nao_recusa(harness: Harness) -> None:
    devs, rt = await _parado_com_ram(harness)
    devs.last_metrics = None                       # o laço de métricas ainda não amostrou: "não sei"
    devs.fake_boot_s = 0.01

    await devs.start_instance(rt)
    await devs.aguardar_boot(rt, 10)

    assert rt.state == InstanceState.online
    assert metricas.valor("capacidade.reserva", resultado="concedida") == 1


async def test_cpu_no_limiar_do_host_admite_e_100_desliga_a_porta(harness: Harness) -> None:
    devs, rt = await _parado_com_ram(harness)
    devs.fake_boot_s = 0.01
    devs.last_metrics = _metricas_do_host(LIMIAR)
    await devs.start_instance(rt)
    await devs.aguardar_boot(rt, 10)
    assert rt.state == InstanceState.online, "no limiar exato deve admitir (só ACIMA recusa)"

    await devs.stop_instance(rt)
    harness.cfg.file.android.max_cpu_percent_before_boot = 100.0
    devs.last_metrics = _metricas_do_host(100.0)
    await devs.start_instance(rt)
    await devs.aguardar_boot(rt, 10)
    assert rt.state == InstanceState.online, "com o limiar em 100 a porta de CPU fica aberta"


async def test_ram_curta_com_cpu_alta_diz_a_ram(harness: Harness) -> None:
    """Os dois recusariam: o texto é o da RAM, que é a que a pessoa resolve (liberar memória, imagem mais leve)."""
    devs, rt = await _parado_com_ram(harness)
    harness.emulator.free_mb = 500.0
    devs.last_metrics = _metricas_do_host(99.0)
    await devs.start_instance(rt)
    await devs.aguardar_boot(rt, 10)
    assert "Capacidade do host atingida" in (rt.state_detail or "")
    assert metricas.valor("capacidade.reserva", resultado="recusada", motivo="ram") == 1


# ---------------------------------------------------------------- o prazo do preparo
def test_fator_de_carga_do_preparo_cresce_com_a_cpu_e_para_no_teto() -> None:
    assert fator_de_carga_do_preparo(None) == 1.0, "CPU desconhecida não estica"
    assert fator_de_carga_do_preparo(0.0) == 1.0 and fator_de_carga_do_preparo(50.0) == 1.0
    assert fator_de_carga_do_preparo(75.0) == pytest.approx(2.0)
    assert fator_de_carga_do_preparo(100.0) == pytest.approx(3.0)
    assert fator_de_carga_do_preparo(250.0) == pytest.approx(3.0), "teto: acima disso o aparelho não responde de fato"


def test_o_shell_do_preparo_usa_o_prazo_proprio_do_adb() -> None:
    """O prazo de dentro cresce junto: esticar só o do executor deixaria o `shell` de 40 s estourar primeiro."""
    prazos: list[float] = []

    def run(args: list[str], *, timeout: float = 30, **_k: Any) -> Any:
        prazos.append(timeout)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    adb = Adb(SimpleNamespace(), "emulator-5640")                                # type: ignore[arg-type]
    adb._run = run                                                               # type: ignore[method-assign,assignment]
    adb.prepare_for_automation()
    assert prazos == [PRAZO_DO_AJUSTE_S]
    adb.prazo_do_ajuste_s = 90.0
    adb.prepare_for_automation()
    assert prazos[-1] == 90.0


def _boot_lento(harness: Harness, monkeypatch: pytest.MonkeyPatch, relogio: _RelogioInjetavel, *,
                boot_ok_em: float) -> tuple[Any, dict[str, Any]]:
    """Boot a frio em que `boot_completed` só chega em `boot_ok_em` (relógio injetável; cada sonda custa 30 s)."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    visto: dict[str, Any] = {"preparo_em": None, "prazos": [], "ajuste": None}
    monkeypatch.setattr(manager_mod, "time", relogio)
    harness.cfg.file.instances.overrides["android-01"] = {"boot_timeout_s": 900}

    def boot_completed(*_a: Any, **_k: Any) -> bool:
        relogio.agora += 30.0
        return relogio.agora >= boot_ok_em

    def preparo(*_a: Any, **_k: Any) -> None:
        visto["preparo_em"] = relogio.agora
        visto["ajuste"] = rt.adb.prazo_do_ajuste_s

    original = rt.executor.run

    async def run(fn: Any, *args: Any, timeout: float, label: str = "", **k: Any) -> Any:
        if label == "prepare":
            visto["prazos"].append(timeout)
        return await original(fn, *args, timeout=timeout, label=label, **k)

    s.devices._set_state(rt, InstanceState.booting, "emulador iniciado")
    rt.pid = 4242
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: True)
    monkeypatch.setattr(manager_mod, "RESPOSTA_POS_BOOT_S", 0.05)
    monkeypatch.setattr(manager_mod, "RESPOSTA_MIN_S", 0.05)
    monkeypatch.setattr(rt.adb, "boot_completed", boot_completed)
    monkeypatch.setattr(rt.adb, "ui_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", preparo)
    monkeypatch.setattr(rt.executor, "run", run)
    monkeypatch.setattr(s.devices, "_start_online_tasks", lambda _rt: None)
    s.cfg.file.limits.boot_poll_s = 0.01
    return rt, visto


async def test_preparo_so_comeca_depois_do_boot_completed_e_com_o_prazo_de_sempre_sem_carga(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot lento (300 s de relógio até o `boot_completed`): os 300 s NÃO entram no prazo do preparo — ele só nasce
    depois do "Boot completed" e vale os 60 s base (40 s no `shell`) quando a CPU está livre ou é desconhecida."""
    relogio = _RelogioInjetavel()
    boot_ok_em = relogio.agora + 300
    rt, visto = _boot_lento(harness, monkeypatch, relogio, boot_ok_em=boot_ok_em)
    s = harness.state
    assert s is not None
    s.devices.last_metrics = None

    assert await s.devices._wait_boot(rt, relogio.monotonic()) is True            # noqa: SLF001

    assert visto["preparo_em"] is not None and visto["preparo_em"] >= boot_ok_em, "preparo antes do Boot completed"
    assert visto["prazos"] == [PRAZO_DO_PREPARO_S] and visto["ajuste"] == PRAZO_DO_AJUSTE_S
    assert rt.state == InstanceState.online and rt.boot_seconds is not None and rt.boot_seconds >= 300


async def test_host_carregado_estica_o_prazo_do_preparo_e_o_do_shell_juntos(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com a CPU em 100 %, o mesmo boot lento prepara com 180 s no executor e 120 s no `shell` do adb: o preparo
    sadio e devagar não estoura (era o `AdbTimeout` → tentativa incerta → degrau de reparo)."""
    relogio = _RelogioInjetavel()
    rt, visto = _boot_lento(harness, monkeypatch, relogio, boot_ok_em=relogio.agora + 300)
    s = harness.state
    assert s is not None
    s.devices.last_metrics = _metricas_do_host(100.0)

    assert await s.devices._wait_boot(rt, relogio.monotonic()) is True            # noqa: SLF001

    assert visto["prazos"] == [PRAZO_DO_PREPARO_S * 3] and visto["ajuste"] == PRAZO_DO_AJUSTE_S * 3
    assert rt.state == InstanceState.online, "o boot lento sob carga virou erro/degrau de reparo"


async def test_a_readocao_tambem_prepara_com_o_prazo_proporcional(harness: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """O outro ponto de chamada (`_preparar_e_revalidar`, adoção/readoção) usa a mesma conta."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    prazos: list[float] = []

    async def run(fn: Any, *args: Any, timeout: float, label: str = "", **_k: Any) -> Any:
        prazos.append(timeout)
        return None

    monkeypatch.setattr(rt.executor, "run", run)
    s.devices.last_metrics = _metricas_do_host(75.0)
    anterior = SimpleNamespace(estado="ok")
    assert await s.devices._preparar_e_revalidar(rt, anterior) is anterior          # type: ignore[arg-type]  # noqa: SLF001
    assert prazos == [pytest.approx(PRAZO_DO_PREPARO_S * 2)]
    assert rt.adb.prazo_do_ajuste_s == pytest.approx(PRAZO_DO_AJUSTE_S * 2)


# ---------------------------------------------------------------- a medição `boot`
async def test_medicao_do_boot_grava_a_cpu_e_os_boots_em_voo(harness: Harness,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    relogio = _RelogioInjetavel()
    rt, _visto = _boot_lento(harness, monkeypatch, relogio, boot_ok_em=relogio.agora)
    s = harness.state
    assert s is not None
    s.devices.last_metrics = _metricas_do_host(91.26)
    for outro in ("android-02", "android-03"):                    # dois outros boots em voo nesta máquina
        s.devices._set_state(s.devices.get(outro), InstanceState.booting, "emulador iniciado")   # noqa: SLF001

    assert await s.devices._wait_boot(rt, relogio.monotonic()) is True            # noqa: SLF001

    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert dados["host_cpu_percent"] == 91.3 and dados["boots_em_voo"] == 2
    assert {"instance_id", "boot_seconds", "kind", "online_after", "mem_available_gb", "image"} <= set(dados), (
        "os campos de antes não podem sumir")


async def test_medicao_do_boot_sem_cpu_amostrada_grava_nulo_e_nao_zero(harness: Harness,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    relogio = _RelogioInjetavel()
    rt, _visto = _boot_lento(harness, monkeypatch, relogio, boot_ok_em=relogio.agora)
    s = harness.state
    assert s is not None
    s.devices.last_metrics = None

    assert await s.devices._wait_boot(rt, relogio.monotonic()) is True            # noqa: SLF001

    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert dados["host_cpu_percent"] is None and dados["boots_em_voo"] == 0


# ---------------------------------------------------------------- o agente do worker
async def test_agente_do_worker_estica_o_shell_do_preparo_pela_cpu_da_maquina(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    monkeypatch.setattr(executor_mod.psutil, "cpu_percent", lambda interval=None: 100.0)
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    adb = AdbFalso(pronto_depois_de=1)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)

    await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})

    assert adb.preparou and adb.prazo_do_ajuste_s == pytest.approx(PRAZO_DO_AJUSTE_S * 3)


# ---------------------------------------------------------------- configuração
def test_o_exemplo_traz_a_chave_com_o_padrao_do_modelo() -> None:
    assert load_config(EXEMPLO).file.android.max_cpu_percent_before_boot == 85.0
    assert adb_mod.PRAZO_DO_AJUSTE_S == 40.0 and PRAZO_DO_PREPARO_S == 60.0, "a relação 40 + 12 < 60 é de apps_de_fundo"
