"""29.127: o marco da subida no log do emulador (R1), offset 0 só sem arquivo (R2), o veredito do snapshot depois da
rotação (N3) e o offset da retentativa a frio (N1). Sobras das leituras do #420 (29.123)."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.config import AndroidCfg
from app.devices import emulator as emu
from app.devices.manager import DeviceManager

from .conftest import Harness
from .test_readocao_sem_log_antigo import _DIALOGO, _NO_AR, _SUBIDA, _cair_no_wait_boot, _contar_paradas, _log

_MARCO = emu.MARCO_DA_SUBIDA + ": avd 2026-10-05T13:10:00Z\n"


# ================================================================== R1: o marco antes do Popen
class _Cfg:
    def __init__(self, raiz: Path) -> None:
        self.logs_dir = raiz / "logs"
        self.data_dir = raiz / "dados"


class _Tools:
    def __init__(self, exe: Path) -> None:
        self.emulator = exe

    def env(self) -> dict[str, str]:
        return {}


def test_o_marco_esta_no_log_antes_do_popen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "logs" / "emulator-avd.log"
    vistos: list[str] = []

    class Popen:
        pid = 4242

        def __init__(self, *_a: Any, **_k: Any) -> None:
            vistos.append(log.read_text(encoding="utf-8"))

    monkeypatch.setattr(emu.subprocess, "Popen", Popen)
    exe = tmp_path / "emulator"
    exe.write_text("", encoding="utf-8")
    log.parent.mkdir(parents=True)
    log.write_bytes((_SUBIDA + _DIALOGO).encode())                    # a subida anterior terminou no diálogo
    assert emu.start_process(_Cfg(tmp_path), _Tools(exe), "avd", 5554, AndroidCfg()) == 4242  # type: ignore[arg-type]
    ultima = vistos[0].splitlines()[-1]
    assert ultima.startswith(emu.MARCO_DA_SUBIDA + ": avd "), "o marco tem de estar no arquivo antes do emulador"
    assert emu.inicio_da_subida_atual(log) == len((_SUBIDA + _DIALOGO).encode())


async def test_nos_primeiros_segundos_o_dialogo_da_subida_anterior_nao_para(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """A janela residual do 29.123: a subida anterior terminou no diálogo, e a atual ainda não descarregou a saída
    (nenhuma `emuglConfig_init` dela). Sem o marco, a última `emuglConfig_init` era a da subida anterior."""
    s = harness.state
    assert s is not None
    rt = _cair_no_wait_boot(harness, monkeypatch)
    log = _log(harness, rt)
    anterior = _MARCO + _SUBIDA + _DIALOGO
    log.write_bytes((anterior + _MARCO).encode())
    paradas = _contar_paradas(harness, monkeypatch)
    try:
        await s.devices._adopt(rt)                                                # noqa: SLF001
        assert rt.boot_log_offset == len(anterior.encode())
        await asyncio.sleep(0.3)
        assert paradas == [], "o diálogo da subida anterior parou o aparelho readotado"
        with log.open("ab") as fh:                                                # o desta subida ainda conta
            fh.write((_SUBIDA + _DIALOGO).encode())
        await asyncio.sleep(0.3)
        assert paradas == ["android-01"]
    finally:
        boot = rt.tasks.pop("boot", None)
        if boot:
            boot.cancel()


def test_subida_sem_marco_depois_de_uma_com_marco_usa_a_emugl(tmp_path: Path) -> None:
    """Agente anterior ao marco: a subida nova só tem `emuglConfig_init`. Vale a última de qualquer das duas."""
    log = tmp_path / "emulator-avd.log"
    antes = _MARCO + _SUBIDA + _DIALOGO
    log.write_bytes((antes + _SUBIDA + _NO_AR).encode())
    # a ÚLTIMA `emuglConfig_init` da subida nova (a segunda linha dela), como no 29.123
    assert emu.inicio_da_subida_atual(log) == len(antes.encode()) + len(_SUBIDA.splitlines(keepends=True)[0].encode())


# ================================================================== R2: 0 só sem arquivo
def test_sem_arquivo_e_zero(tmp_path: Path) -> None:
    assert DeviceManager._offset_da_readocao(tmp_path / "nao-existe.log", pausa_s=0) == 0


def test_erro_passageiro_tenta_de_novo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "emulator-avd.log"
    log.write_bytes((_SUBIDA + _DIALOGO + _MARCO).encode())
    original = emu.inicio_da_subida_atual
    falhas = [PermissionError("compartilhamento negado")]

    def uma_falha(caminho: Path) -> int | None:
        if falhas:
            raise falhas.pop()
        return original(caminho)

    monkeypatch.setattr(emu, "inicio_da_subida_atual", uma_falha)
    assert DeviceManager._offset_da_readocao(log, pausa_s=0) == len((_SUBIDA + _DIALOGO).encode())


def test_erro_que_nao_passa_vai_ao_fim_do_arquivo_e_nunca_a_zero(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "emulator-avd.log"
    log.write_bytes((_SUBIDA + _DIALOGO).encode())

    def sempre(_c: Path) -> int | None:
        raise PermissionError("compartilhamento negado")

    monkeypatch.setattr(emu, "inicio_da_subida_atual", sempre)
    assert DeviceManager._offset_da_readocao(log, pausa_s=0) == log.stat().st_size


def test_sem_stat_o_offset_fica_desconhecido_e_ninguem_le_o_historico(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "emulator-avd.log"
    log.write_bytes((_SUBIDA + _DIALOGO).encode())

    def sempre(_c: Path) -> int | None:
        raise PermissionError("compartilhamento negado")

    original_stat = Path.stat

    def stat(self: Path, *a: Any, **k: Any) -> Any:
        if self == log:
            raise PermissionError("compartilhamento negado")
        return original_stat(self, *a, **k)

    monkeypatch.setattr(emu, "inicio_da_subida_atual", sempre)
    monkeypatch.setattr(Path, "stat", stat)
    offset = DeviceManager._offset_da_readocao(log, pausa_s=0)
    assert offset == emu.OFFSET_DESCONHECIDO
    monkeypatch.setattr(Path, "stat", original_stat)
    assert emu.dialogo_de_crash(log, offset) is False, "offset desconhecido leu o histórico"


async def test_veredito_com_offset_desconhecido_nao_le(harness: Harness) -> None:
    assert harness.state is not None
    devs, rt = harness.state.devices, harness.state.devices.get("android-01")
    log = harness.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"emulator: Successfully loaded snapshot 'poc'\n")
    rt.boot_log_offset = emu.OFFSET_DESCONHECIDO
    assert devs._snapshot_verdict(rt) is None


# ================================================================== N3: o veredito depois da rotação
async def test_veredito_le_do_comeco_quando_o_log_foi_rotacionado_no_spawn(harness: Harness) -> None:
    """O offset é medido antes do spawn; o spawn rotaciona o log grande, e o arquivo novo é menor que o offset."""
    assert harness.state is not None
    devs, rt = harness.state.devices, harness.state.devices.get("android-01")
    log = harness.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"emulator: cannot load snapshot 'poc': hardware mismatch\n")
    rt.boot_log_offset = 9 * 1024 * 1024                                          # o tamanho do log antes de rotacionar
    assert devs._snapshot_verdict(rt) is False


# ================================================================== N1: a retentativa a frio
async def test_retentativa_a_frio_grava_o_offset_dela(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O código da retentativa a frio grava o offset antes do segundo `_spawn` (leitura da fonte: o caminho inteiro
    precisa de SDK e emulador reais)."""
    import inspect

    from app.devices import manager as manager_mod

    fonte = inspect.getsource(manager_mod.DeviceManager)
    trecho = fonte[fonte.index("acordar do snapshot falhou; boot a frio"):]
    trecho = trecho[:trecho.index("await self._wait_boot(rt, t0)")]
    assert trecho.index("rt.boot_log_offset = ") < trecho.index("self._spawn, rt, a, False, False")


def test_o_marco_nao_carrega_o_ambiente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Só o nome do AVD e a hora: o log do emulador vai para o painel (`log_do_emulador`)."""
    monkeypatch.setattr(emu.subprocess, "Popen", lambda *_a, **_k: type("P", (), {"pid": 1})())
    monkeypatch.setenv("SEGREDO_DE_TESTE", "nao-pode-aparecer")
    exe = tmp_path / "emulator"
    exe.write_text("", encoding="utf-8")
    emu.start_process(_Cfg(tmp_path), _Tools(exe), "avd", 5554, AndroidCfg())  # type: ignore[arg-type]
    linha = (tmp_path / "logs" / "emulator-avd.log").read_text(encoding="utf-8")
    assert "nao-pode-aparecer" not in linha and linha.count("\n") == 1
    _ = subprocess  # o `Popen` de verdade nunca roda nesta suíte
