"""Aparelho falso para os testes: simula o QA Messenger (telas, hierarquia UiAutomator, status das
mensagens) e permite injetar falhas no momento exato do toque em Enviar."""
from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from xml.sax.saxutils import quoteattr

from PIL import Image

from app.automation.driver import DriverBusy, DriverError
from app.devices.adb import AdbError, MorteDoApp

PKG = "com.pocqa.messenger"
LAUNCHER = "com.android.launcher3"
#: O 500 do UiAutomator2 visto nas execuções reais (convidado android-06 com a CPU saturada).
UI_OCUPADA_500 = ("WebDriverException: Message: An unknown server-side error occurred while processing the command. "
                  "Original error: Timed out after 10000ms waiting for the root AccessibilityNodeInfo in the active "
                  "window. Make sure the active window is not constantly hogging the main UI thread")
SESSAO_PERDIDA = ("InvalidSessionIdException: Message: A session is either terminated or not started "
                  "(session not found)")
#: A instrumentação do UiAutomator2 morreu: o Appium responde, mas não tem a quem repassar o comando. É o texto do
#: appium.log do central (lá, no `DELETE /` da recriação), com o comando da vez no lugar.
INSTRUMENTACAO_MORTA = ("WebDriverException: Message: An unknown server-side error occurred while processing the "
                        "command. Original error: 'GET /source' cannot be proxied to UiAutomator2 server because the "
                        "instrumentation process is not running (probably crashed)")
CONTACTS = ["Suporte QA", "QA-003", "QA-002", "QA-001", "Equipe Testes"]
W, H = 720, 1280


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), (30, 40, 50)).save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


class _AdbSemScreencap:
    """O `Adb` de um convidado saturado: o `exec-out screencap -p` volta sem PNG (é o `AdbError` de `screencap_png`)."""

    def screencap_png(self, *, timeout: float = 20) -> bytes:
        raise AdbError("screencap falhou em emulator-5640")


@dataclass
class Message:
    contact: str
    body: str
    sent_at: float


@dataclass
class Node:
    cls: str
    bounds: tuple[int, int, int, int]
    text: str = ""
    rid: str = ""
    desc: str = ""
    clickable: bool = False
    password: bool = False
    action: str = ""
    scrollable: bool = False


@dataclass
class FakeQaDevice:
    account: str
    screen: str = "launcher"            # launcher | home | chat | login
    contact: str | None = None
    input_text: str = ""
    focused: str | None = None
    messages: list[Message] = field(default_factory=list)
    interstitial: bool = False
    require_login: bool = False
    # Tela de login: o que foi digitado em cada campo (id → texto). O campo de senha aparece MASCARADO na hierarquia,
    # como no Android de verdade — é o que o canal sensível lê para saber se a digitação chegou.
    login_fields: dict[str, str] = field(default_factory=dict)
    # Teclado que sobe e ROLA a página ao focar um campo (a WebView do Chrome faz isso): as posições mudam.
    rola_ao_focar: bool = False
    # Texto da barra de endereço do navegador (resource-id do Chrome), ou None = tela sem barra.
    barra_de_endereco: str | None = None
    version: str = "1.0(1)"             # versão do app instalada neste aparelho (chave das receitas)
    # Outros pacotes instalados que encenam as MESMAS telas do QA (item 24.7): com eles, trocar de app entre etapas
    # troca o pacote em primeiro plano, e é o pacote que o executor confere. `foreground` é o app aberto agora.
    pacotes_extras: tuple[str, ...] = ()
    foreground: str = PKG
    frozen: bool = False                # app travado: aceita toques mas a tela não muda (até ser encerrado)
    # Android do convidado morto por dentro (system_server caído): o adb responde, `boot_completed` é 1, e
    # `service check` diz `not found`. `guest_mudo` é o outro caso medido: o adb não responde a tempo.
    guest_dead: bool = False
    guest_mudo: bool = False
    # Pressão fingida do convidado (`load1`, `mem_total_mb`, `mem_available_mb`, `ncpu`); `None` = folgado.
    pressure: dict[str, float] | None = None
    # falhas injetáveis no toque em Enviar:
    #   "error_after_effect"  → a mensagem é enviada, mas o driver devolve erro (resultado desconhecido)
    #   "error_lost"          → o driver devolve erro e a mensagem NÃO é enviada
    #   "hang_after_effect"   → a mensagem é enviada e a chamada trava por `hang_s` (timeout do executor)
    send_fault: str | None = None
    # UI ocupada (r-20260928195344-02ee9e): as próximas N leituras da hierarquia devolvem o 500 do UiAutomator2
    # "waiting for the root AccessibilityNodeInfo" — a sessão está viva, o convidado é que está saturado.
    busy_reads: int = 0
    # Sessão morta de verdade: as próximas N leituras devolvem o erro de sessão inexistente do Appium.
    session_lost_reads: int = 0
    # Instrumentação morta: as próximas N leituras da hierarquia devolvem o erro do Appium sem UiAutomator2 vivo.
    instrumentacao_morta_reads: int = 0
    # Toque que volta com a instrumentação morta ANTES de chegar ao app (o Appium não teve a quem repassar).
    tap_instrumentacao_morta: str | None = None     # texto do nó cujo toque devolve o erro
    # Screencap pelo adb que falha (convidado saturado: "screencap falhou em emulator-…"): as próximas N capturas
    # passam pela conversão DE VERDADE de `AndroidDeviceIO.screenshot_png` — é ela que dá o tipo do erro.
    screenshot_falhas: int = 0
    # Toque que chega ao app e mesmo assim volta com o 500 de UI ocupada (o gesto aconteceu; a resposta, não).
    busy_after_tap: str | None = None           # texto do nó cujo toque devolve DriverBusy depois do efeito
    # Pacote "anr" (execuções reais r-20260928165254-e31953 e r-20260928195344-02ee9e, android-06): quantas das
    # próximas aberturas do app terminam em ANR. Com `hide_error_dialogs=1` o sistema fecha o app sem diálogo e o
    # launcher volta; a morte fica no `exit-info` (reason=6), com o horário dela.
    anr_ao_abrir: int = 0
    _mortes: list[tuple[float, int]] = field(default_factory=list)   # (time.monotonic() da morte, pid)
    hang_s: float = 3.0
    action_delay_s: float = 0.0
    # T.2: a hora que o aparelho usa para envelhecer a mensagem ("Enviando…" → "Enviada" → "Entregue") e datar as
    # mortes por ANR. É `time.monotonic` por padrão; o teste que PULA o tempo (`tests/relogio_virtual.py`) entrega um
    # relógio que avança junto com o `dormir` das ferramentas, e o aparelho envelhece o que o `wait_for` esperou.
    relogio: Callable[[], float] = time.monotonic
    sent_after_s: float = 0.15
    delivered_after_s: float = 0.4
    calls: list[str] = field(default_factory=list)
    concurrent: int = 0
    max_concurrent: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _nodes: list[Node] = field(default_factory=list)

    # ------------------------------------------------------------------ infraestrutura
    def _enter(self, name: str) -> None:
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.calls.append(name)
        if self.action_delay_s:
            time.sleep(self.action_delay_s)

    def _leave(self) -> None:
        with self._lock:
            self.concurrent -= 1

    def status_of(self, m: Message) -> str:
        age = self.relogio() - m.sent_at
        if age < self.sent_after_s:
            return "Enviando…"
        if age < self.delivered_after_s or m.contact == "QA-002":
            return "Enviada ✓"
        return "Entregue ✓✓"

    # ------------------------------------------------------------------ DeviceIO
    def system_server_alive(self, *, timeout: float = 8) -> bool:
        """`system_server_mudo` finge o wake de 25/09/2026: `service check` ok, `settings` sem resposta."""
        if self.guest_mudo or getattr(self, "system_server_mudo", False):
            raise DriverError("settings get excedeu o prazo", effect_possible=False)
        return not self.guest_dead

    def display_alive(self, *, timeout: float = 12) -> bool:
        """`display_mudo` finge o SurfaceFlinger que nunca respondeu depois do restore."""
        if self.guest_mudo or getattr(self, "display_mudo", False):
            raise DriverError("screencap excedeu o prazo", effect_possible=False)
        return not self.guest_dead

    def framework_alive(self, *, timeout: float = 25) -> bool:
        """Saúde do convidado. `guest_dead`/`guest_mudo` fingem o que se mediu no parque: o `system_server` morto
        (serviços `not found`) e o adb que não responde a tempo."""
        if self.guest_mudo:
            raise DriverError("o aparelho não respondeu ao `service check`", effect_possible=False)
        return not self.guest_dead

    def guest_pressure(self) -> dict[str, float]:
        if self.guest_mudo:
            raise DriverError("o aparelho não respondeu à leitura de /proc", effect_possible=False)
        return dict(self.pressure or {"load1": 0.5, "mem_total_mb": 2048.0, "mem_available_mb": 900.0, "ncpu": 2.0})

    def connectivity_probe(self) -> dict[str, bool]:
        """`internet` finge a rede do convidado: dict parcial sobrescreve o saudável (ex.: {"dns": False})."""
        if self.guest_mudo:
            raise DriverError("o aparelho não respondeu à sonda de rede", effect_possible=False)
        return {"route": True, "dns": True, "tcp_443": True, "validated": True, **(getattr(self, "internet", None) or {})}

    def screenshot_png(self) -> bytes:
        self._enter("screenshot")
        try:
            if self.screenshot_falhas > 0:
                self.screenshot_falhas -= 1
                from app.automation.appium_driver import AndroidDeviceIO

                return AndroidDeviceIO(_AdbSemScreencap(), None).screenshot_png()  # type: ignore[arg-type]
            return PNG
        finally:
            self._leave()

    def current_package(self) -> str | None:
        return LAUNCHER if self.screen == "launcher" else self.foreground

    def current_focus(self) -> tuple[str | None, str | None]:
        """Mesmo contrato do `Adb.current_focus` (a seção VIVA do `dumpsys window`): o launcher ou o app."""
        return (LAUNCHER, ".Launcher") if self.screen == "launcher" else (self.foreground, ".MainActivity")

    def app_deaths(self, package: str, *, within_s: float | None = None) -> list[MorteDoApp]:
        """Mesmo contrato do `Adb.app_deaths`: as mortes por ANR do app, com a idade medida agora."""
        agora = self.relogio()
        if package != PKG:
            return []
        return [MorteDoApp(quando=f"t+{t:.3f}", pid=pid, motivo=6, anr=True, idade_s=agora - t,
                           descricao="user request after error")
                for t, pid in self._mortes if within_s is None or agora - t <= within_s]

    def _abrir(self) -> None:
        """Abrir o app a partir do launcher. Com `anr_ao_abrir`, a partida a frio morre por ANR e o launcher volta."""
        if self.anr_ao_abrir > 0:
            self.anr_ao_abrir -= 1
            self._mortes.append((self.relogio(), 4000 + len(self._mortes)))
            self.screen = "launcher"
            return
        self.screen = "login" if self.require_login else "home"

    def app_version(self, package: str) -> str:
        return self.version

    def force_stop(self, package: str) -> None:
        self._enter("force_stop")
        try:
            if self.pacotes_extras and package != self.foreground:
                return                      # encerrar o app que está no fundo não muda a tela
            self.screen, self.contact, self.input_text, self.frozen = "launcher", None, "", False
            self._nodes = []
        finally:
            self._leave()

    def page_source(self) -> str:
        self._enter("page_source")
        try:
            if self.busy_reads > 0:
                self.busy_reads -= 1
                raise DriverBusy(UI_OCUPADA_500, effect_possible=False)
            if self.session_lost_reads > 0:
                self.session_lost_reads -= 1
                raise DriverError(SESSAO_PERDIDA, effect_possible=False)
            if self.instrumentacao_morta_reads > 0:
                self.instrumentacao_morta_reads -= 1
                raise DriverError(INSTRUMENTACAO_MORTA, effect_possible=False)
            self._nodes = self._build()
            pkg = self.current_package()
            rows = "".join(
                f"<node class={quoteattr(n.cls)} package={quoteattr(pkg)} text={quoteattr(n.text)} "
                f"resource-id={quoteattr(n.rid if ':' in n.rid else (self.foreground + ':id/' + n.rid) if n.rid else '')} content-desc={quoteattr(n.desc)} "
                f"clickable=\"{str(n.clickable).lower()}\" enabled=\"true\" focused=\"{str(n.rid == self.focused and bool(n.rid)).lower()}\" "
                f"password=\"{str(n.password).lower()}\" scrollable=\"{str(n.scrollable).lower()}\" bounds=\"[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]\" />"
                for n in self._nodes)
            return f"<hierarchy rotation=\"0\">{rows}</hierarchy>"
        finally:
            self._leave()

    def _build(self) -> list[Node]:
        if self.screen == "launcher":
            return [Node("android.widget.TextView", (40, 900, 200, 1000), text="QA Messenger", clickable=True, action="open")]
        if self.screen == "login":
            dy = -150 if self.rola_ao_focar and self.focused else 0
            barra = ([Node("android.widget.EditText", (0, 0, 720, 60), text=self.barra_de_endereco,
                           rid="com.android.chrome:id/url_bar")] if self.barra_de_endereco is not None else [])
            return barra + [
                Node("android.widget.TextView", (40, 100 + dy, 680, 160 + dy), text="Sessão expirada. Entre novamente.", rid="login_notice"),
                Node("android.widget.EditText", (40, 200 + dy, 680, 280 + dy), rid="login_account", clickable=True,
                     text=self.login_fields.get("login_account", ""), action="focus:login_account"),
                Node("android.widget.EditText", (40, 300 + dy, 680, 380 + dy), rid="login_pin", clickable=True, password=True,
                     text="•" * len(self.login_fields.get("login_pin", "")), action="focus:login_pin"),
                Node("android.widget.Button", (40, 420 + dy, 680, 500 + dy), text="Entrar", rid="login_button", clickable=True)]
        if self.interstitial:
            return [Node("android.widget.TextView", (60, 300, 660, 380), text="Novidades da versão", rid="interstitial_title"),
                    Node("android.widget.Button", (60, 800, 660, 880), text="Agora não", rid="interstitial_dismiss",
                         clickable=True, action="dismiss")]
        if self.screen == "home":
            nodes = [Node("android.widget.TextView", (20, 40, 500, 100), text=f"Conta: {self.account}", rid="account_label"),
                     Node("android.widget.Button", (520, 40, 700, 100), text="Perfil", rid="btn_profile", clickable=True),
                     Node("android.widget.ListView", (0, 180, 720, 1100), rid="conversation_list", scrollable=True)]
            for i, c in enumerate(CONTACTS):
                y = 200 + i * 120
                nodes.append(Node("android.widget.TextView", (20, y, 700, y + 60), text=c, rid="conversation_name",
                                  clickable=True, action=f"chat:{c}"))
            return nodes
        nodes = [Node("android.widget.Button", (10, 40, 90, 100), desc="Voltar", rid="chat_back", clickable=True, action="back"),
                 Node("android.widget.TextView", (100, 40, 500, 100), text=self.contact or "", rid="chat_title"),
                 Node("android.widget.TextView", (100, 100, 500, 140), text=f"como {self.account}", rid="chat_account")]
        y = 200
        for m in [m for m in self.messages if m.contact == self.contact][-6:]:
            nodes.append(Node("android.widget.TextView", (100, y, 700, y + 50), text=m.body, rid="message_text"))
            nodes.append(Node("android.widget.TextView", (100, y + 50, 700, y + 90), text=self.status_of(m), rid="message_status"))
            y += 110
        nodes.append(Node("android.widget.EditText", (20, 1160, 560, 1240), text=self.input_text, rid="message_input",
                          clickable=True, action="focus:message_input"))
        nodes.append(Node("android.widget.Button", (580, 1160, 700, 1240), text="Enviar", rid="send_button", desc="Enviar",
                          clickable=True, action="send"))
        return nodes

    def tap(self, x: int, y: int) -> None:
        self._enter(f"tap:{x},{y}")
        try:
            hit = next((n for n in self._nodes if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                        and n.bounds[1] <= y <= n.bounds[3]), None)
            if hit is None or self.frozen:
                return
            if self.tap_instrumentacao_morta is not None and hit.text == self.tap_instrumentacao_morta:
                self.tap_instrumentacao_morta = None
                raise DriverError(INSTRUMENTACAO_MORTA.replace("'GET /source'", "'POST /appium/gestures/click'"),
                                  effect_possible=True)
            act = hit.action
            if act == "open":
                self.foreground = PKG       # o ícone do launcher é o do QA
                self._abrir()
            elif act == "dismiss":
                self.interstitial = False
            elif act.startswith("chat:"):
                self.screen, self.contact = "chat", act.split(":", 1)[1]
            elif act == "back":
                self.screen, self.contact = "home", None
            elif act.startswith("focus:"):
                self.focused = act.split(":", 1)[1]
            elif act == "send":
                self._send()
            if self.busy_after_tap is not None and hit.text == self.busy_after_tap:
                self.busy_after_tap = None
                raise DriverBusy(UI_OCUPADA_500, effect_possible=True)
        finally:
            self._leave()

    def _send(self) -> None:
        fault, self.send_fault = self.send_fault, None
        if fault == "error_lost":
            raise DriverError("socket hang up (simulado): o toque não chegou ao app", effect_possible=True)
        if self.input_text.strip():
            self.messages.append(Message(self.contact or "", self.input_text.strip(), self.relogio()))
            self.input_text = ""
        if fault == "error_after_effect":
            raise DriverError("socket hang up (simulado) após o toque", effect_possible=True)
        if fault == "hang_after_effect":
            time.sleep(self.hang_s)

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.tap(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self._enter("swipe")
        self._leave()

    def type_text(self, text: str, *, clear_first: bool) -> None:
        self._enter("type")
        try:
            if self.screen == "chat":
                self.input_text = text if clear_first else self.input_text + text
            elif self.screen == "login" and self.focused in ("login_account", "login_pin"):
                antes = "" if clear_first else self.login_fields.get(self.focused, "")
                self.login_fields[self.focused] = antes + text
        finally:
            self._leave()

    def set_text(self, text: str, *, clear_first: bool) -> None:
        # No dublê, definir o texto e digitá-lo dão no mesmo campo: a diferença entre os dois caminhos (IME, fila de
        # teclas que perde a cauda) é do aparelho real, e o teste que precisa dela a modela (test_digitacao_atomica).
        # Delegar mantém valendo os testes que trocam `type_text` para simular corte ou transformação.
        self.type_text(text, clear_first=clear_first)

    def press_key(self, key: str) -> None:
        self._enter(f"key:{key}")
        try:
            if key == "back" and self.screen == "chat":
                self.screen, self.contact = "home", None
            elif key == "home":
                self.screen = "launcher"
        finally:
            self._leave()

    def open_url(self, url: str) -> None:
        self._enter("open_url")
        try:
            self.urls_abertas = [*getattr(self, "urls_abertas", []), url]
        finally:
            self._leave()

    def open_app(self, package: str, activity: str | None) -> None:
        self._enter(f"open_app:{package}" if self.pacotes_extras else "open_app")
        try:
            if self.pacotes_extras and package in (PKG, *self.pacotes_extras) and package != self.foreground:
                # Outro app vem à frente na tela inicial dele; o que estava aberto fica no fundo.
                self.foreground, self.screen, self.contact, self.input_text = package, "home", None, ""
                if self.require_login:
                    self.screen = "login"
            elif self.screen == "launcher":
                self._abrir()
        finally:
            self._leave()
