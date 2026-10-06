"""31.137: a abertura do app de prova não paga 60 s nem retoma a tarefa no meio.

A tarefa do Configurações guarda a busca (de OUTRO pacote, `com.google.android.settings.intelligence`): o `am start` comum a retomava onde
parou e o foco do app nunca chegava (60 s de `esperar_foco`, medido em d92795 e 1157c6). Aqui, `simulated`: o comando de abertura com a tarefa
limpa, a extensão do driver, a espera do foco que aceita os pacotes vizinhos da etapa (31.123) e a abertura do manager.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.automation import tools
from app.automation.appium_driver import AndroidDeviceIO
from app.automation.driver import DriverError
from app.devices import installer
from app.devices.adb import ABERTURA_COM_TAREFA_LIMPA, Adb, AdbError
from app.devices.manager import DeviceManager

SETTINGS = "com.android.settings"
BUSCA = "com.google.android.settings.intelligence"


def _leitor(foco: str | None) -> Any:
    async def ler() -> tuple[str | None, str | None]:
        return foco, None
    return ler


def test_so_o_app_de_prova_abre_com_a_tarefa_limpa() -> None:
    assert ABERTURA_COM_TAREFA_LIMPA == frozenset({SETTINGS})          # app com conta real segue retomando onde parou


def _adb_que_grava() -> tuple[Any, list[str]]:
    comandos: list[str] = []
    falso = SimpleNamespace(shell=lambda cmd, timeout=30: comandos.append(cmd) or "Starting: Intent")
    return falso, comandos


def test_start_app_comum_continua_igual() -> None:
    falso, comandos = _adb_que_grava()
    Adb.start_app(falso, SETTINGS, ".Settings")
    Adb.start_app(falso, SETTINGS)
    assert comandos == [f"am start -n {SETTINGS}/.Settings", f"monkey -p {SETTINGS} -c android.intent.category.LAUNCHER 1"]


def test_start_app_com_tarefa_limpa_apaga_a_tarefa_e_abre_a_tela_inicial() -> None:
    falso, comandos = _adb_que_grava()
    Adb.start_app(falso, SETTINGS, ".Settings", tarefa_limpa=True)
    Adb.start_app(falso, SETTINGS, None, tarefa_limpa=True)
    assert comandos[0] == f"am start -n {SETTINGS}/.Settings --activity-clear-task"
    assert comandos[1] == ("am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER "
                           f"-p {SETTINGS} --activity-clear-task")


def test_a_abertura_so_usa_opcoes_que_o_am_do_android_34_conhece() -> None:
    """Medido no aparelho (`am help`, 06/10): `--activity-clear-task` existe; `--activity-new-task` NÃO (o `am` falhava com "Unknown option" e a
    abertura de Configurações do 31.137 quebrava no deploy 52). O `am start` já abre em tarefa nova, então só a primeira vale."""
    falso, comandos = _adb_que_grava()
    Adb.start_app(falso, SETTINGS, None, tarefa_limpa=True)
    opcoes = {p for p in comandos[0].split() if p.startswith("--")}
    assert opcoes == {"--activity-clear-task"}


def test_start_app_com_tarefa_limpa_recusa_activity_invalida_e_erro_do_am() -> None:
    falso, _ = _adb_que_grava()
    with pytest.raises(AdbError):
        Adb.start_app(falso, SETTINGS, "x; rm -rf /", tarefa_limpa=True)
    com_erro = SimpleNamespace(shell=lambda cmd, timeout=30: "Error: Activity class does not exist")
    with pytest.raises(AdbError):
        Adb.start_app(com_erro, SETTINGS, ".Nada", tarefa_limpa=True)


def test_driver_abre_com_tarefa_limpa_e_traduz_o_erro() -> None:
    chamadas: list[tuple[str, str | None, bool]] = []

    def start_app(package: str, activity: str | None = None, *, tarefa_limpa: bool = False) -> None:
        chamadas.append((package, activity, tarefa_limpa))

    driver = SimpleNamespace(adb=SimpleNamespace(start_app=start_app))
    AndroidDeviceIO.open_app_tarefa_limpa(driver, SETTINGS, None)       # type: ignore[arg-type]
    assert chamadas == [(SETTINGS, None, True)]

    def quebra(package: str, activity: str | None = None, *, tarefa_limpa: bool = False) -> None:
        raise AdbError("sem activity")

    with pytest.raises(DriverError):
        AndroidDeviceIO.open_app_tarefa_limpa(SimpleNamespace(adb=SimpleNamespace(start_app=quebra)), SETTINGS, None)  # type: ignore[arg-type]


async def test_esperar_foco_aceita_o_pacote_vizinho_da_etapa() -> None:
    t0 = time.monotonic()
    assert await tools.esperar_foco(_leitor(BUSCA), SETTINGS, aceitos=[BUSCA]) is True
    assert time.monotonic() - t0 < 1.0                                 # na frente já na 1ª leitura, não 60 s


async def test_esperar_foco_sem_aceitos_continua_exigindo_o_pacote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "INTERVALO_DO_FOCO_S", 0.02)
    monkeypatch.setattr(tools, "INTERVALO_INICIAL_DO_FOCO_S", 0.01)
    assert await tools.esperar_foco(_leitor(BUSCA), SETTINGS, ate=time.monotonic() + 0.2) is False
    assert await tools.esperar_foco(_leitor(None), SETTINGS, aceitos=[BUSCA], ate=time.monotonic() + 0.2) is False
    assert await tools.esperar_foco(_leitor("com.outro"), SETTINGS, aceitos=[BUSCA], ate=time.monotonic() + 0.2) is False


class _Executor:
    async def run(self, fn: Any, *args: Any, **_kw: Any) -> Any:
        return fn(*args)


async def test_wait_for_focus_do_instalador_aceita_os_vizinhos() -> None:
    rt = SimpleNamespace(executor=_Executor(), adb=SimpleNamespace(current_focus=lambda: (BUSCA, None)))
    assert await installer.wait_for_focus(rt, SETTINGS, deadline_s=0.2, poll_s=0.01, aceitos=[BUSCA]) is True
    assert await installer.wait_for_focus(rt, SETTINGS, deadline_s=0.1, poll_s=0.01) is False


async def test_open_app_do_manager_abre_o_app_de_prova_com_a_tarefa_limpa_e_o_resto_como_sempre() -> None:
    chamadas: list[tuple[str, str | None, bool]] = []
    foco = {"dono": BUSCA}

    def start_app(package: str, activity: str | None = None, *, tarefa_limpa: bool = False) -> None:
        chamadas.append((package, activity, tarefa_limpa))
        foco["dono"] = package

    adb = SimpleNamespace(current_focus=lambda: (foco["dono"], None), keyevent=lambda *_a: None, start_app=start_app)
    rt = SimpleNamespace(id="android-01", executor=_Executor(), adb=adb, capture_now=SimpleNamespace(set=lambda: None),
                         training_session_id=None)
    gerente = SimpleNamespace(io_factory=None, on_training_input=None, _guard_not_running_ai=lambda _rt: None)
    ok, _ = await DeviceManager.open_app(gerente, rt, {"id": "settings", "package": SETTINGS, "activity": None},  # type: ignore[arg-type]
                                         pela_execucao=True, aceitos=[BUSCA])
    assert ok and chamadas == [(SETTINGS, None, True)]

    chamadas.clear()
    foco["dono"] = "com.android.launcher"
    ok, _ = await DeviceManager.open_app(gerente, rt, {"id": "ig", "package": "com.instagram.android", "activity": None},  # type: ignore[arg-type]
                                         pela_execucao=True)
    assert ok and chamadas == [("com.instagram.android", None, False)]
