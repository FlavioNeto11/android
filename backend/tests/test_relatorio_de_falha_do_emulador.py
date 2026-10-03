"""29.55: relatório de falha pendente do emulador não prende mais a subida.

Incidente de 03/10/2026 19:00Z: o reinício por IRQ derrubou o emulador na saída, o crashpad deixou um dump em
`%TEMP%/AndroidEmulator/emu-crash-<versão>.db/reports/`, e toda subida passou a parar no diálogo que pede
consentimento para enviá-lo ("Showing crashdialog to get consent"). Ninguém responde: a espera ia até o prazo do boot e
a escada de reparo chegou ao terceiro degrau num aparelho com conta.

(a) `-crash-report-mode never` por padrão; (b) o dump vai para a quarentena antes de cada subida (mover, nunca apagar),
e isso vira evento; (c) a linha do diálogo no log DESTA subida encerra a espera com motivo claro, e o reparo automático
não sobe degrau por isso. Nenhum teste aqui toca o TEMP de verdade da máquina: o ambiente do emulador é sempre o do teste.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import AndroidCfg
from app.devices import emulator as emu
from app.models import InstanceState

from .conftest import Harness


class _Tools:
    def __init__(self, emulador: Path, env: dict[str, str]) -> None:
        self.emulator = emulador
        self._env = env

    def env(self) -> dict[str, str]:
        return dict(self._env)


def _dump(temp: Path, versao: str, nome: str, conteudo: bytes = b"MDMP") -> Path:
    p = temp / "AndroidEmulator" / f"emu-crash-{versao}.db" / "reports" / nome
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(conteudo)
    return p


# ---------------------------------------------------------------------------------------------- (a) a flag
def test_por_padrao_o_emulador_sobe_sem_perguntar_pelo_relatorio_de_falha(tmp_path: Path) -> None:
    args = emu.build_args(_Tools(tmp_path / "emulator", {}), "avd", 5554, AndroidCfg(), wipe_data=False)  # type: ignore[arg-type]
    i = args.index("-crash-report-mode")
    assert args[i + 1] == "never"


def test_modo_vazio_volta_ao_padrao_do_emulador(tmp_path: Path) -> None:
    args = emu.build_args(_Tools(tmp_path / "emulator", {}), "avd", 5554, AndroidCfg(crash_report_mode=""),  # type: ignore[arg-type]
                          wipe_data=False)
    assert "-crash-report-mode" not in args


def test_modo_desconhecido_e_recusado_na_carga() -> None:
    with pytest.raises(ValidationError):
        AndroidCfg(crash_report_mode="talvez")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------- (b) a quarentena
def test_sem_temp_no_ambiente_do_emulador_nao_ha_o_que_procurar() -> None:
    """O TEMP é o do ambiente que o EMULADOR recebe, nunca o deste processo."""
    assert emu.relatorios_pendentes({}) == []


def test_os_dumps_de_todas_as_versoes_vao_para_a_quarentena_sem_apagar(tmp_path: Path) -> None:
    temp, destino = tmp_path / "temp", tmp_path / "dados" / emu.PASTA_DA_QUARENTENA
    a = _dump(temp, "37.1.11", "a.dmp", b"um")
    b = _dump(temp, "37.3.2", "b.dmp", b"dois")
    (temp / "AndroidEmulator" / "emu-crash-37.1.11.db" / "settings.dat").write_bytes(b"x")  # não é relatório

    movidos = emu.quarentenar_relatorios({"TEMP": str(temp)}, destino)

    assert sorted(p.name for p in movidos) == ["a.dmp", "b.dmp"]
    assert not a.exists() and not b.exists()
    assert (destino / "a.dmp").read_bytes() == b"um" and (destino / "b.dmp").read_bytes() == b"dois"
    assert (temp / "AndroidEmulator" / "emu-crash-37.1.11.db" / "settings.dat").exists()
    assert emu.relatorios_pendentes({"TEMP": str(temp)}) == []


def test_nome_repetido_na_quarentena_ganha_sufixo_e_preserva_a_evidencia_anterior(tmp_path: Path) -> None:
    temp, destino = tmp_path / "temp", tmp_path / "q"
    destino.mkdir()
    (destino / "a.dmp").write_bytes(b"antigo")
    _dump(temp, "37.1.11", "a.dmp", b"novo")

    movidos = emu.quarentenar_relatorios({"TMP": str(temp)}, destino)

    assert [p.name for p in movidos] == ["a-1.dmp"]
    assert (destino / "a.dmp").read_bytes() == b"antigo"
    assert (destino / "a-1.dmp").read_bytes() == b"novo"


def test_dump_travado_fica_e_os_outros_seguem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O emulador que escreveu o dump ainda vivo trava o arquivo: a quarentena é higiene, nunca exceção."""
    temp, destino = tmp_path / "temp", tmp_path / "q"
    travado = _dump(temp, "37.1.11", "a.dmp")
    _dump(temp, "37.1.11", "b.dmp")
    original = os.replace

    def replace(src: Any, dst: Any) -> None:
        if Path(src) == travado:
            raise PermissionError("em uso por outro processo")
        original(src, dst)

    monkeypatch.setattr(emu.os, "replace", replace)
    movidos = emu.quarentenar_relatorios({"TEMP": str(temp)}, destino)

    assert [p.name for p in movidos] == ["b.dmp"]
    assert travado.exists()


class _PopenFalso:
    pid = 4242
    chamado: list[list[str]] = []

    def __init__(self, args: list[str], **_kw: object) -> None:
        _PopenFalso.chamado.append(args)


class _Cfg:
    def __init__(self, raiz: Path) -> None:
        self.logs_dir = raiz / "logs"
        self.data_dir = raiz / "dados"


def test_a_subida_tira_o_dump_do_caminho_antes_do_popen_e_avisa(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    temp = tmp_path / "temp"
    _dump(temp, "37.1.11", "a.dmp")
    ordem: list[str] = []

    class Popen(_PopenFalso):
        def __init__(self, args: list[str], **kw: object) -> None:
            ordem.append("popen")
            assert emu.relatorios_pendentes({"TEMP": str(temp)}) == [], "o Popen veio antes da quarentena"
            super().__init__(args, **kw)

    monkeypatch.setattr(emu.subprocess, "Popen", Popen)
    exe = tmp_path / "emulator"
    exe.write_text("", encoding="utf-8")
    avisos: list[list[Path]] = []

    def ao_quarentenar(movidos: list[Path]) -> None:
        ordem.append("aviso")
        avisos.append(movidos)

    pid = emu.start_process(_Cfg(tmp_path), _Tools(exe, {"TEMP": str(temp)}), "avd", 5554, AndroidCfg(),  # type: ignore[arg-type]
                            ao_quarentenar=ao_quarentenar)

    assert pid == 4242
    assert ordem == ["aviso", "popen"]
    assert [p.name for p in avisos[0]] == ["a.dmp"]
    assert (tmp_path / "dados" / emu.PASTA_DA_QUARENTENA / "a.dmp").exists()


def test_quarentena_que_quebra_nunca_derruba_a_subida(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp = tmp_path / "temp"
    _dump(temp, "37.1.11", "a.dmp")
    monkeypatch.setattr(emu.subprocess, "Popen", _PopenFalso)

    def quebra(*_a: object, **_k: object) -> list[Path]:
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(emu, "quarentenar_relatorios", quebra)
    exe = tmp_path / "emulator"
    exe.write_text("", encoding="utf-8")
    assert emu.start_process(_Cfg(tmp_path), _Tools(exe, {"TEMP": str(temp)}), "avd", 5554, AndroidCfg()) == 4242  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------- (c) a detecção
def test_a_linha_do_dialogo_so_vale_da_subida_atual(tmp_path: Path) -> None:
    log_path = tmp_path / "emulator-avd.log"
    log_path.write_bytes(f"subida anterior\n{emu.LINHA_DO_DIALOGO_DE_CRASH} to get consent\n".encode())
    offset = log_path.stat().st_size
    assert emu.dialogo_de_crash(log_path, offset) is False      # a linha é da subida de antes

    with log_path.open("ab") as fh:
        fh.write(b"INFO | boot\n")
    assert emu.dialogo_de_crash(log_path, offset) is False

    with log_path.open("ab") as fh:
        fh.write(f"INFO | {emu.LINHA_DO_DIALOGO_DE_CRASH} to get consent\n".encode())
    assert emu.dialogo_de_crash(log_path, offset) is True


def test_log_rotacionado_no_spawn_e_lido_do_comeco(tmp_path: Path) -> None:
    """`_rotate_log` corta o log no spawn: o offset gravado antes fica maior que o arquivo novo."""
    log_path = tmp_path / "emulator-avd.log"
    log_path.write_bytes(f"{emu.LINHA_DO_DIALOGO_DE_CRASH}\n".encode())
    assert emu.dialogo_de_crash(log_path, 10_000_000) is True
    assert emu.dialogo_de_crash(tmp_path / "nao-existe.log", 0) is False


# ---------------------------------------------------------------------------------------------- no gerenciador
async def _desligar(h: Harness, iid: str = "android-01") -> Any:
    st = h.state
    assert st is not None
    rt = st.devices.get(iid)
    await st.devices.stop_instance(rt)
    assert rt.state == InstanceState.stopped
    return rt


async def test_a_quarentena_antes_da_subida_vira_evento(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt = await _desligar(harness)
    harness.emulator.relatorios_em_quarentena = [Path("dados/quarentena-crash/a.dmp")]

    await st.devices.start_instance(rt)
    await harness.wait(lambda: rt.state == InstanceState.online, what="o aparelho subir")

    linhas = st.db.query("SELECT message, level FROM events WHERE kind='log' AND instance_id=? AND message LIKE ?",
                         (rt.id, "%quarentena%"))
    assert linhas, "a quarentena antes da subida tem de virar evento"
    assert "a.dmp" in linhas[-1]["message"] and linhas[-1]["level"] == "warn"


async def test_dialogo_de_crash_encerra_a_espera_com_motivo_e_sem_escada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt = await _desligar(harness)
    # O "boot" do aparelho falso não passa pela espera real (`_wait_boot`): ela é chamada aqui, como o `_boot` real
    # a chama depois do spawn — com o log desta subida contendo a linha do diálogo e um PID gravado.
    log_path = st.devices.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(b"subida anterior, sem dialogo\n")
    rt.boot_log_offset = log_path.stat().st_size
    with log_path.open("ab") as fh:
        fh.write(f"INFO    | {emu.LINHA_DO_DIALOGO_DE_CRASH} to get consent\n".encode())
    rt.pid = 41_999

    inicio = time.monotonic()
    assert await st.devices._wait_boot(rt, inicio) is False
    assert time.monotonic() - inicio < 5, "a espera tem de terminar na hora, não no prazo do boot"

    assert rt.state == InstanceState.error
    assert rt.bloqueio_de_crash is True
    assert "diálogo de consentimento" in (rt.attention or "")
    assert rt.avd_name in harness.emulator.stopped, "o lançador preso no diálogo tem de ser encerrado"
    assert rt.pid is None
    rt.desired_state = InstanceState.online.value
    assert st.devices._pedir_reparo(rt, rt.attention or "") is False, "a escada não sobe degrau por isso"

    # A próxima subida limpa a marca (e, na máquina de verdade, tira o dump do caminho antes do Popen).
    await st.devices.start_instance(rt)
    await harness.wait(lambda: rt.state == InstanceState.online, what="a nova subida")
    assert rt.bloqueio_de_crash is False


async def test_start_remoto_que_parou_no_dialogo_marca_o_aparelho_sem_escada(harness: Harness) -> None:
    """O agente fecha `failed` com `motivo` próprio (29.55 c); o central marca o aparelho e a escada não sobe degrau."""
    st = harness.state
    assert st is not None
    rt = await _desligar(harness)

    await st.devices.aplicar_desfecho_remoto(rt, "start", "failed", {"motivo": emu.MOTIVO_DIALOGO_DE_CRASH})

    assert rt.bloqueio_de_crash is True
    assert "diálogo de consentimento" in (rt.attention or "")
    rt.desired_state = InstanceState.online.value
    assert st.devices._pedir_reparo(rt, rt.attention or "") is False

    # Outro motivo de falha não marca nada (o caminho de sempre segue valendo).
    rt2 = await _desligar(harness, "android-02")
    await st.devices.aplicar_desfecho_remoto(rt2, "start", "failed", {"motivo": "outro"})
    assert rt2.bloqueio_de_crash is False
