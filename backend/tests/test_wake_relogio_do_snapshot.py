"""RA-15 (plano-100 29.34): o relógio do wake (`wake_timeout_s`) começa no snapshot CARREGADO, não no spawn.

Medido em 03/10/2026: 4 de 23 wakes passavam de 90 s só carregando 2 GB sob CPU alta, e o prazo contado do spawn os
derrubava para o boot a frio (de minutos). Aqui o relógio de `manager` é injetável (nenhum sono real longo) e as
sondas/o veredito do log são dublês; o laço de `_wait_boot`, a prontidão e a medição são os de produção. `simulated`.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

import pytest

from app.devices import manager as manager_mod
from app.models import InstanceState

from .conftest import Harness
from .test_wait_boot_sondas import _RelogioInjetavel

WAKE_S = 90
BOOT_S = 400


def _preparar(harness: Harness, monkeypatch: pytest.MonkeyPatch, relogio: _RelogioInjetavel, *,
              carregado_em: float | None, boot_ok_em: float, ui: Any,
              uptime: float | None = None) -> tuple[Any, dict[str, int]]:
    """`carregado_em`: instante (do relógio injetável) em que o log passa a dizer "Successfully loaded" (None = nunca).
    `boot_ok_em`: instante a partir do qual `boot_completed` é verdadeiro. Cada sonda lenta custa 30 s de relógio.
    `uptime`: o que `/proc/uptime` do convidado responde (None = ilegível), 29.34."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    chamadas = {"ui_ready": 0}
    monkeypatch.setattr(manager_mod, "time", relogio)
    harness.cfg.file.instances.overrides["android-01"] = {"wake_timeout_s": WAKE_S, "boot_timeout_s": BOOT_S}

    def boot_completed(*_a: Any, **_k: Any) -> bool:
        relogio.agora += 30.0
        return relogio.agora >= boot_ok_em

    def ui_ready(*_a: Any, **_k: Any) -> bool:
        chamadas["ui_ready"] += 1
        relogio.agora += 30.0
        return ui(relogio.agora)

    s.devices._set_state(rt, InstanceState.booting, "acordando do snapshot…")
    rt.pid = 4242
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: True)
    monkeypatch.setattr(manager_mod, "RESPOSTA_POS_BOOT_S", 0.05)
    monkeypatch.setattr(manager_mod, "RESPOSTA_MIN_S", 0.05)
    monkeypatch.setattr(rt.adb, "boot_completed", boot_completed)
    monkeypatch.setattr(rt.adb, "ui_ready", ui_ready)
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    monkeypatch.setattr(rt.adb, "uptime_s", lambda *a, **k: uptime)
    monkeypatch.setattr(s.devices, "_snapshot_verdict",
                        lambda _rt: True if carregado_em is not None and relogio.agora >= carregado_em else None)
    monkeypatch.setattr(s.devices, "_start_online_tasks", lambda _rt: None)
    s.cfg.file.limits.boot_poll_s = 0.01
    return rt, chamadas


async def test_snapshot_que_carrega_depois_do_prazo_do_spawn_ainda_acorda(harness: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """O log só diz "carregado" 120 s depois do spawn (> 90 s), mas a interface fica pronta 30 s depois da carga:
    acorda (warm, snapshot mantido). Com o relógio no spawn, o laço devolvia `False` na volta em que o veredito chegava."""
    relogio = _RelogioInjetavel()
    rt, chamadas = _preparar(harness, monkeypatch, relogio, carregado_em=relogio.agora + 120,
                             boot_ok_em=relogio.agora + 120, ui=lambda _agora: True)
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, relogio.monotonic(), warm=True)     # noqa: SLF001

    assert ok is True and rt.state == InstanceState.online
    assert chamadas["ui_ready"] == 1
    assert rt.boot_seconds is not None and rt.boot_seconds >= 120, "boot_seconds segue sendo spawn→online"


async def test_carregado_e_sem_interface_no_prazo_do_wake_devolve_falso(harness: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot carregado de imediato, `boot_completed` ok e a interface nunca fica pronta: passados `wake_timeout_s`
    DESDE A CARGA, `_wait_boot` devolve `False` (quem chamou descarta o snapshot e tenta a frio), como antes."""
    relogio = _RelogioInjetavel()
    rt, chamadas = _preparar(harness, monkeypatch, relogio, carregado_em=relogio.agora, boot_ok_em=relogio.agora,
                             ui=lambda _agora: False)
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, relogio.monotonic(), warm=True)     # noqa: SLF001

    assert ok is False and rt.state != InstanceState.online
    assert chamadas["ui_ready"] == 3, "sondas aos 0, 30 e 60 s da carga; no laço seguinte (120 s > 90 s) desiste"


async def test_sem_veredito_do_log_o_prazo_e_o_de_boot_a_frio(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O log nunca diz nada e o uptime não lê: antes do veredito vale `boot_timeout_s` desde o spawn (não os 90 s do
    wake). A interface pronta aos 150 s desde o spawn acorda, e a medição fica sem `load_ms` (o veredito não veio)."""
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora + 120,
                      ui=lambda _agora: True)
    s = harness.state
    assert s is not None
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is True      # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert dados["kind"] == "warm" and "load_ms" in dados and dados["load_ms"] is None
    assert dados["snapshot_por"] is None and "uptime_s" not in dados


async def test_medicao_do_boot_warm_tem_load_ms(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_ms` = spawn → hora em que o veredito foi lido (inteiro, ms); os campos antigos não mudam."""
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=relogio.agora + 120,
                      boot_ok_em=relogio.agora + 120, ui=lambda _agora: True)
    s = harness.state
    assert s is not None
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is True      # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])

    assert dados["kind"] == "warm" and isinstance(dados["load_ms"], int) and dados["load_ms"] == 120_000
    assert dados["snapshot_por"] == "log"
    assert {"instance_id", "boot_seconds", "online_after", "mem_available_gb", "image"} <= set(dados)


async def test_boot_a_frio_nao_tem_load_ms(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora,
                      ui=lambda _agora: True)
    s = harness.state
    assert s is not None
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=False) is True     # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert dados["kind"] == "cold" and "load_ms" not in dados


# ------------------------------------------------------------------ 29.34: o veredito pelo uptime do convidado
async def test_log_mudo_e_uptime_grande_da_o_veredito_e_o_load_ms(harness: Harness,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """A prova real de 04/10 (android-02, wake das 16:19Z): o log só recebe "Successfully loaded snapshot" depois do
    boot (saída bufferizada) e o `load_ms` saía `None`. Com o uptime do convidado (3728 s) maior que o tempo desde o
    spawn, o veredito vem no `boot_completed`: `load_ms` = spawn → boot, e o prazo do wake passa a contar dali."""
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora + 120,
                      ui=lambda _agora: True, uptime=3728.0)
    s = harness.state
    assert s is not None
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is True      # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])

    assert (dados["kind"], dados["snapshot_por"], dados["uptime_s"]) == ("warm", "uptime", 3728.0)
    assert isinstance(dados["load_ms"], int) and dados["load_ms"] == 120_000   # 4 sondas de 30 s até o boot
    assert rt.snapshot_failures == 0


async def test_veredito_pelo_uptime_poe_o_prazo_do_wake(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com o veredito pelo uptime, a interface que nunca fica pronta derruba o wake `wake_timeout_s` depois do boot
    (como o veredito pelo log), e não os 400 s do boot a frio contados do spawn."""
    relogio = _RelogioInjetavel()
    rt, chamadas = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora,
                             ui=lambda _agora: False, uptime=3728.0)
    s = harness.state
    assert s is not None
    inicio = relogio.agora
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is False     # noqa: SLF001
    # O prazo conta do boot (lido depois da sonda de 30 s): sondas da interface até passar de 90 s dali, bem antes dos
    # 400 s do boot a frio que valeriam sem veredito.
    assert chamadas["ui_ready"] == 4
    assert relogio.agora - inicio < BOOT_S / 2


async def test_uptime_pequeno_nao_e_veredito(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uptime menor que o tempo desde o spawn (snapshot salvo logo depois de um boot a frio, ou boot a frio de fato)
    não decide nada: segue o prazo de boot a frio e nenhuma falha de snapshot é contada."""
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora + 120,
                      ui=lambda _agora: True, uptime=50.0)
    s = harness.state
    assert s is not None
    falhas_antes = rt.snapshot_failures
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is True      # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert (dados["load_ms"], dados["snapshot_por"]) == (None, None)
    assert rt.snapshot_failures == falhas_antes


def test_snapshot_pelo_uptime_so_responde_sim_com_certeza() -> None:
    assert manager_mod.snapshot_pelo_uptime(3728.0, 167.0) is True
    assert manager_mod.snapshot_pelo_uptime(None, 10.0) is False
    assert manager_mod.snapshot_pelo_uptime(170.0, 167.0) is False        # dentro da folga: não decide
    assert manager_mod.snapshot_pelo_uptime(167.0 + manager_mod.FOLGA_DO_UPTIME_S + 0.1, 167.0) is True


def test_adb_uptime_s_le_o_proc_uptime(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    adb = s.devices.get("android-01").adb
    respostas = iter([(0, "3728.41 7012.80\n"), (0, ""), (1, "error: device offline"), (0, "lixo")])

    def run(args: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        assert args == ["shell", "cat /proc/uptime"]
        rc, saida = next(respostas)
        return subprocess.CompletedProcess(args, rc, saida, "")

    monkeypatch.setattr(adb, "_run", run)
    assert [adb.uptime_s() for _ in range(4)] == [3728.41, None, None, None]


async def test_log_que_contradiz_o_uptime_avisa_sem_mudar_o_aparelho(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """29.76 (d): depois do veredito pelo uptime o log não é mais lido no laço. Se ele disser, no fim, que o snapshot
    do projeto foi recusado, o convidado carregou outro estado: a medição leva `log_contradiz` e sai um aviso; o
    aparelho segue no ar (acordou, funciona) e `snapshot_failures` não muda."""
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora + 120,
                      ui=lambda _agora: True, uptime=3728.0)
    s = harness.state
    assert s is not None
    boot_ok = relogio.agora + 120
    # Mudo até o boot (o laço decide pelo uptime); depois, o log traz a recusa.
    monkeypatch.setattr(s.devices, "_snapshot_verdict", lambda _rt: False if relogio.agora > boot_ok else None)
    avisos: list[str] = []
    emitir = s.devices.bus.emit
    monkeypatch.setattr(s.devices.bus, "emit",
                        lambda kind, msg="", **k: (avisos.append(msg) if kind == "log" else None, emitir(kind, msg, **k))[1])
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is True      # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert (dados["snapshot_por"], dados["log_contradiz"]) == ("uptime", True)
    assert any("foi recusado" in a for a in avisos)
    assert rt.snapshot_failures == 0 and rt.state == InstanceState.online


async def test_log_que_contradiz_depois_da_medicao_avisa_na_segunda_leitura(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """29.76 (d), revisão do #303: o log é bufferizado e a recusa pode chegar depois da medição. Sem a linha na hora, a
    medição sai sem `log_contradiz`, e a segunda leitura (`LOG_CONTRADIZ_RELEITURA_S` depois) avisa.

    29.120: a releitura agendada é capturada e disparada à mão depois que a recusa chega. Antes, o teste punha a
    releitura em 10 ms e ligava a recusa só depois de um `db.query`: com a máquina cheia (o PG dirigido da suíte 37),
    a consulta passou de 10 ms, a releitura leu o veredito ainda `None`, e o aviso não saiu."""
    relogio = _RelogioInjetavel()
    rt, _ = _preparar(harness, monkeypatch, relogio, carregado_em=None, boot_ok_em=relogio.agora + 120,
                      ui=lambda _agora: True, uptime=3728.0)
    s = harness.state
    assert s is not None
    chegou = {"recusa": False}
    monkeypatch.setattr(s.devices, "_snapshot_verdict", lambda _rt: False if chegou["recusa"] else None)
    avisos: list[str] = []
    monkeypatch.setattr(s.devices, "_avisar_log_contradiz", lambda _rt: avisos.append(_rt.id))
    laco = asyncio.get_running_loop()
    agendar = laco.call_later
    releituras: list[tuple[float, object]] = []

    def capturar(atraso, callback, *args, **kw):
        # Só a releitura do log; o resto (o `asyncio.sleep` inclusive) segue no laço de verdade.
        if getattr(callback, "__qualname__", "").endswith("_reler_log_do_snapshot_depois.<locals>.reler"):
            releituras.append((atraso, callback))
            return None
        return agendar(atraso, callback, *args, **kw)
    monkeypatch.setattr(laco, "call_later", capturar)
    assert await s.devices._wait_boot(rt, relogio.monotonic(), warm=True) is True      # noqa: SLF001
    dados = json.loads(s.db.query("SELECT data FROM measurements WHERE kind='boot'")[-1]["data"])
    assert dados["snapshot_por"] == "uptime" and "log_contradiz" not in dados and avisos == []
    assert [a for a, _ in releituras] == [manager_mod.LOG_CONTRADIZ_RELEITURA_S]
    chegou["recusa"] = True                  # a linha bufferizada chega ao arquivo depois do boot
    releituras[0][1]()                       # passa o prazo da segunda leitura
    assert avisos == ["android-01"]

