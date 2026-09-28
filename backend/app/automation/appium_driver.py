"""Sessão Appium/UiAutomator2 por dispositivo, com `udid` explícito e portas exclusivas
(systemPort, mjpegServerPort, chromedriverPort) — requisito para execução paralela."""
from __future__ import annotations

import logging
import math
import re
import urllib.error
import urllib.request
from typing import Any

from ..config import AppiumCfg
from ..devices.adb import Adb, AdbError
from .driver import DriverBusy, DriverError, DriverUnavailable

log = logging.getLogger("poc.appium")

#: O que o UiAutomator2 responde quando a UI do app não entrega a raiz de acessibilidade a tempo (convidado com a CPU
#: saturada, animação sem fim). A resposta vem DO servidor da sessão: ela está viva. Execuções
#: r-20260928195344-02ee9e e r-20260928165254-e31953 recriavam a sessão a cada ocorrência (`DriverBusy`).
_UI_OCUPADA = ("root accessibilitynodeinfo", "hogging the main ui thread", "no active window")


#: Quanto o primeiro movimento do arrasto anda de uma vez, e em quanto tempo. O touch slop do Android é 8 dp (16 px em
#: xhdpi, 21 px em 420 dpi) e a checagem é estrita (`>`): 24 px passa nos dois. Duração curta mas não nula, para o
#: DOWN e o primeiro MOVE não saírem com o mesmo instante.
SLOP_PX = 24
SLOP_MS = 10


def ui_ocupada(mensagem: str) -> bool:
    """O erro é o de UI ocupada (transitório, sessão viva)? Confere a mensagem INTEIRA: a linha útil pode vir
    depois do cabeçalho genérico do Selenium, e o corte de 300 caracteres da mensagem normalizada a perderia."""
    baixa = mensagem.casefold()
    return any(m in baixa for m in _UI_OCUPADA)


def appium_no_ar(url: str, timeout: float = 3.0) -> bool:
    """O Appium daquele endereço responde `ready`?

    Existe para o `appium: local` do worker: aceitar a declaração e só descobrir no meio da primeira tarefa que
    não há Appium do outro lado seria trocar um caminho provado (o central, pelo túnel) por um silêncio. Falha de
    rede, recusa de conexão e resposta ilegível contam todas como "não está no ar" — nenhuma delas autoriza
    dirigir um aparelho por ali.
    """
    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/status", timeout=timeout) as resp:  # noqa: S310
            import json as _json

            return bool(_json.loads(resp.read()).get("value", {}).get("ready", False))
    except (urllib.error.URLError, OSError, ValueError):
        return False


class AppiumSession:
    def __init__(self, cfg: AppiumCfg, serial: str, system_port: int, mjpeg_port: int, chromedriver_port: int):
        self.cfg = cfg
        #: Appium de OUTRA máquina. Nulo = o deste servidor, que é o caminho provado em campo. Preenchido quando
        #: o worker que hospeda o aparelho declara `appium: local` no `hello` — a saída prevista para worker em
        #: WAN, onde a latência do ADB pelo túnel é desconhecida (docs/parque-distribuido.md).
        self.remote_url: str | None = None
        self.serial = serial
        self.system_port = system_port
        self.mjpeg_port = mjpeg_port
        self.chromedriver_port = chromedriver_port
        self._drv: Any = None

    @property
    def server_url(self) -> str:
        return self.remote_url or f"http://{self.cfg.host}:{self.cfg.port}"

    def apontar_para(self, url: str | None, serial: str | None) -> bool:
        """Troca o Appium (e o `udid`) que dirigem ESTE aparelho. Devolve `True` quando algo mudou.

        O `udid` vem junto de propósito: o serial que o central usa é o do TÚNEL (`127.0.0.1:port`), e num Appium
        que roda na máquina do worker esse serial não existe — lá o aparelho é `emulator-55xx`. Mandar a URL sem
        mandar o udid trocaria o servidor e pediria a ele um aparelho que ele não enxerga.

        Uma sessão aberta contra o servidor anterior não sobrevive à troca: ela é FECHADA aqui, senão o próximo
        comando iria para um driver preso ao Appium errado.
        """
        novo_serial = serial or self.serial
        if url == self.remote_url and novo_serial == self.serial:
            return False
        try:
            self.close()
        except Exception:  # noqa: BLE001 - fechar sessão velha nunca pode impedir a troca
            log.warning("%s: a sessão anterior não fechou na troca de Appium", self.serial)
        self.remote_url, self.serial = url, novo_serial
        return True

    def capabilities(self) -> dict[str, Any]:
        return {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:udid": self.serial,                       # sessão presa a ESTE aparelho
            "appium:systemPort": self.system_port,            # exclusivo por sessão paralela
            "appium:mjpegServerPort": self.mjpeg_port,
            "appium:chromedriverPort": self.chromedriver_port,
            "appium:noReset": True,                           # nunca limpar dados/sessão do app
            "appium:autoLaunch": False,
            "appium:newCommandTimeout": self.cfg.new_command_timeout_s,
            "appium:uiautomator2ServerLaunchTimeout": self.cfg.server_launch_timeout_ms,
            "appium:uiautomator2ServerInstallTimeout": self.cfg.server_launch_timeout_ms,
            "appium:adbExecTimeout": self.cfg.adb_exec_timeout_ms,
            "appium:skipUnlock": True,
            "appium:disableWindowAnimation": True,
            "appium:settings[waitForIdleTimeout]": 800,
            "appium:settings[waitForSelectorTimeout]": 0,
        }

    # -- ciclo de vida ------------------------------------------------------------
    def connect(self) -> None:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options

        options = UiAutomator2Options().load_capabilities(self.capabilities())
        self._drv = webdriver.Remote(command_executor=self.server_url, options=options)

    @property
    def session_id(self) -> str | None:
        return getattr(self._drv, "session_id", None) if self._drv is not None else None

    def delete_stale(self, session_id: str | None) -> None:
        """Encerra, por id, uma sessão que ESTE projeto abriu antes (ex.: backend reiniciado)."""
        if not session_id or not re.fullmatch(r"[A-Za-z0-9-]{8,80}", session_id):
            return
        req = urllib.request.Request(f"{self.server_url}/session/{session_id}", method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=30).close()  # noqa: S310 - loopback
        except (urllib.error.URLError, OSError):
            pass

    def close(self) -> None:
        drv, self._drv = self._drv, None
        if drv is not None:
            try:
                drv.quit()
            except Exception:  # noqa: BLE001
                pass

    @property
    def connected(self) -> bool:
        return self._drv is not None

    def ping(self) -> bool:
        if self._drv is None:
            return False
        try:
            self._drv.current_package  # noqa: B018
            return True
        except Exception:  # noqa: BLE001
            return False

    def _driver(self) -> Any:
        if self._drv is None:
            raise DriverUnavailable("Sessão de automação (Appium) não está pronta neste aparelho.")
        return self._drv

    def _call(self, fn: Any, *, effect: bool) -> Any:
        try:
            return fn()
        except DriverError:
            raise
        except Exception as exc:  # noqa: BLE001 - normaliza erros do Selenium/Appium
            texto = str(exc)
            msg = f"{type(exc).__name__}: {(texto.splitlines() or [''])[0][:300]}"
            if ui_ocupada(texto):
                raise DriverBusy(msg, effect_possible=effect) from exc
            raise DriverError(msg, effect_possible=effect) from exc

    # -- leitura --------------------------------------------------------------------
    def page_source(self) -> str:
        d = self._driver()
        return self._call(lambda: d.page_source, effect=False)

    def current_package(self) -> str | None:
        d = self._driver()
        return self._call(lambda: d.current_package, effect=False)

    # -- ações ----------------------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        d = self._driver()
        self._call(lambda: d.execute_script("mobile: clickGesture", {"x": int(x), "y": int(y)}), effect=True)

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        d = self._driver()
        self._call(lambda: d.execute_script("mobile: longClickGesture",
                                            {"x": int(x), "y": int(y), "duration": int(duration_ms)}), effect=True)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        d = self._driver()
        # Primeiro movimento depois de encostar: um passo curto na direção do arrasto que já passa do touch slop.
        dist = math.hypot(x2 - x1, y2 - y1)
        passo = min(dist, SLOP_PX)
        sx = int(x1) + (round((x2 - x1) * passo / dist) if dist else 0)
        sy = int(y1) + (round((y2 - y1) * passo / dist) if dist else 0)

        def run() -> None:
            from selenium.webdriver.common.actions import interaction
            from selenium.webdriver.common.actions.action_builder import ActionBuilder
            from selenium.webdriver.common.actions.mouse_button import MouseButton
            from selenium.webdriver.common.actions.pointer_input import PointerInput

            # Cada movimento com a SUA duração: a padrão do ActionBuilder valia também para posicionar o dedo no ar.
            # E nada de pausa depois de encostar — com o convidado lento, dedo parado além de ~400 ms é toque longo:
            # menu de contexto na r-20260928165254-e31953; na r-20260928195344-02ee9e o arrasto foi o MotionEvent
            # do primeiro ANR. Passado o slop, o app já decidiu que é rolagem, por mais que o resto atrase.
            dedo = PointerInput(interaction.POINTER_TOUCH, "touch")
            gesto = ActionBuilder(d, mouse=dedo)
            dedo.create_pointer_move(duration=0, x=int(x1), y=int(y1), origin="viewport")
            dedo.create_pointer_down(button=MouseButton.LEFT)
            dedo.create_pointer_move(duration=SLOP_MS, x=sx, y=sy, origin="viewport")
            dedo.create_pointer_move(duration=max(50, int(duration_ms)), x=int(x2), y=int(y2), origin="viewport")
            dedo.create_pointer_up(button=MouseButton.LEFT)
            gesto.perform()

        self._call(run, effect=True)

    def type_text(self, text: str, *, clear_first: bool) -> None:
        d = self._driver()

        def run() -> None:
            if clear_first:
                try:
                    d.switch_to.active_element.clear()
                except Exception:  # noqa: BLE001 - sem elemento focado: segue e digita
                    pass
            d.execute_script("mobile: type", {"text": text})

        self._call(run, effect=True)

    def press_keycode(self, keycode: int) -> None:
        d = self._driver()
        self._call(lambda: d.execute_script("mobile: pressKey", {"keycode": int(keycode)}), effect=True)

    def activate_app(self, package: str) -> None:
        d = self._driver()
        self._call(lambda: d.activate_app(package), effect=True)


class AndroidDeviceIO:
    """Implementação real de DeviceIO: screenshots por ADB; hierarquia e gestos por Appium."""

    def __init__(self, adb: Adb, session: AppiumSession):
        self.adb = adb
        self.session = session

    def framework_alive(self, *, timeout: float = 25) -> bool:
        return self.adb.framework_alive(timeout=timeout)

    def system_server_alive(self, *, timeout: float = 8) -> bool:
        return self.adb.system_server_alive(timeout=timeout)

    def display_alive(self, *, timeout: float = 12) -> bool:
        return self.adb.display_alive(timeout=timeout)

    def guest_pressure(self) -> dict[str, float]:
        return self.adb.guest_pressure()

    def connectivity_probe(self) -> dict[str, bool]:
        return self.adb.connectivity_probe()

    def screenshot_png(self) -> bytes:
        try:
            return self.adb.screencap_png()
        except AdbError as exc:
            raise DriverError(str(exc), effect_possible=False) from exc

    def page_source(self) -> str:
        return self.session.page_source()

    def current_package(self) -> str | None:
        return self.session.current_package()

    def app_version(self, package: str) -> str:
        return self.adb.app_version(package)

    def tap(self, x: int, y: int) -> None:
        self.session.tap(x, y)

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.session.long_press(x, y, duration_ms)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.session.swipe(x1, y1, x2, y2, duration_ms)

    def type_text(self, text: str, *, clear_first: bool) -> None:
        self.session.type_text(text, clear_first=clear_first)

    def press_key(self, key: str) -> None:
        from ..devices.adb import KEYCODES

        self.session.press_keycode(KEYCODES[key])

    def open_app(self, package: str, activity: str | None) -> None:
        try:
            if activity:
                self.adb.start_app(package, activity)
            else:
                self.session.activate_app(package)
        except AdbError as exc:
            raise DriverError(str(exc), effect_possible=True) from exc

    def open_url(self, url: str) -> None:
        try:
            self.adb.open_url(url)
        except AdbError as exc:
            raise DriverError(str(exc), effect_possible=True) from exc
