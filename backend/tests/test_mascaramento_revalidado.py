"""29.126: a prova do mascaramento de log vale para o processo provado, não para sempre.

Antes, `log_masking_active` ficava True depois de provada na subida: se o nosso Appium morresse e um externo assumisse a porta, o canal sensível seguia
digitando num servidor sem prova. Agora a marca de uma prova é reconferida a cada leitura (resultado guardado por 2 s): o processo provado tem de estar
vivo, ser nosso e ser dono da porta. `simulated`: processos e donos da porta falsos.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from app.automation import appium_server as mod
from app.automation.appium_server import AppiumServer
from app.security.sensitive_input import SensitiveInputChannel

from .test_saude_do_appium import _server


class _Proc:
    def __init__(self, cmdline: list[str], vivo: bool = True) -> None:
        self._cmdline, self._vivo = cmdline, vivo

    def is_running(self) -> bool:
        return self._vivo

    def cmdline(self) -> list[str]:
        return self._cmdline


def _cenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, donos: list[int], vivos: dict[int, bool] | None = None,
             alheios: set[int] | None = None) -> AppiumServer:
    server = _server(tmp_path)
    nosso = ["node", str(server._package / "index.js"), "server"]
    alheios = alheios or set()

    def processo(pid: int) -> _Proc:
        if vivos is not None and pid not in vivos:
            raise psutil.NoSuchProcess(pid)
        return _Proc(["node", "outro-appium.js"] if pid in alheios else nosso, (vivos or {}).get(pid, True))

    monkeypatch.setattr(mod.psutil, "Process", processo)
    monkeypatch.setattr(server, "_donos_da_porta", lambda: list(donos))
    return server


def test_prova_com_o_processo_vivo_dono_da_porta_vale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _cenario(tmp_path, monkeypatch, donos=[501])
    server._provar(501)
    assert server.log_masking_active is True


def test_um_externo_que_assume_a_porta_derruba_a_marca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    donos = [501]
    server = _cenario(tmp_path, monkeypatch, donos=donos, alheios={777})
    server._provar(501)
    assert server.log_masking_active is True
    donos[:] = [777]                                   # o nosso saiu da porta e um alheio escuta nela
    server._conferido = None                           # passou a validade do resultado guardado
    assert server.log_masking_active is False


def test_um_alheio_ao_lado_do_nosso_tambem_derruba(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _cenario(tmp_path, monkeypatch, donos=[501, 777], alheios={777})
    server._provar(501)
    assert server.log_masking_active is False


def test_o_pid_provado_fora_dos_donos_derruba_mesmo_com_todos_nossos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _cenario(tmp_path, monkeypatch, donos=[502])          # outro Appium NOSSO escuta; o provado (501) não
    server._provar(501)
    assert server.log_masking_active is False


def test_o_processo_provado_que_morreu_derruba_a_marca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _cenario(tmp_path, monkeypatch, donos=[], vivos={501: False})
    server._provar(501)
    assert server.log_masking_active is False
    server2 = _cenario(tmp_path / "b", monkeypatch, donos=[], vivos={})       # nem existe mais
    server2._provar(501)
    assert server2.log_masking_active is False


def test_sem_ver_os_donos_vale_o_processo_nosso_e_vivo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _cenario(tmp_path, monkeypatch, donos=[])
    server._provar(501)
    assert server.log_masking_active is True


def test_o_resultado_da_reconferencia_vale_por_2_s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    donos = [501]
    server = _cenario(tmp_path, monkeypatch, donos=donos, alheios={777})
    server._provar(501)
    assert server.log_masking_active is True
    donos[:] = [777]
    assert server.log_masking_active is True           # ainda dentro dos 2 s: não consulta a porta de novo
    t0 = time.monotonic()
    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 3.0)
    assert server.log_masking_active is False


def test_marca_posta_de_fora_continua_valendo_como_antes(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert server.log_masking_active is False
    server.log_masking_active = True                   # o harness e os testes de login marcam assim, sem processo
    assert server.log_masking_active is True
    server.log_masking_active = False
    assert server.log_masking_active is False


def test_o_canal_sensivel_acompanha_a_reconferencia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    donos = [501]
    server = _cenario(tmp_path, monkeypatch, donos=donos, alheios={777})
    canal = SensitiveInputChannel(lambda: server.log_masking_active)
    server._provar(501)
    assert canal.available() is True
    donos[:] = [777]
    server._conferido = None
    assert canal.available() is False


def test_start_zera_a_marca_de_uma_prova_anterior_quando_a_nova_falha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _cenario(tmp_path, monkeypatch, donos=[501])
    server._provar(501)
    monkeypatch.setattr(server, "is_up", lambda timeout=2.0: False)
    monkeypatch.setattr(server, "_reuse_running", lambda: False)           # o que escutava foi trocado
    monkeypatch.setattr(server, "_subir", lambda _w: False)                # e o novo não subiu
    assert server.start(wait_s=1) is False
    assert server.log_masking_active is False


def test_duas_chamadas_de_start_nao_se_sobrepoem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    ativos, maior = [0], [0]
    guarda = threading.Lock()

    def lenta(_w: float) -> bool:
        with guarda:
            ativos[0] += 1
            maior[0] = max(maior[0], ativos[0])
        time.sleep(0.05)
        with guarda:
            ativos[0] -= 1
        return True

    monkeypatch.setattr(server, "_start", lenta)
    hs: list[Any] = [threading.Thread(target=server.start) for _ in range(4)]
    for h in hs:
        h.start()
    for h in hs:
        h.join()
    assert maior[0] == 1
