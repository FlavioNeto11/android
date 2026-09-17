"""Aparelho falso para os testes: simula o QA Messenger (telas, hierarquia UiAutomator, status das
mensagens) e permite injetar falhas no momento exato do toque em Enviar."""
from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

from PIL import Image

from app.automation.driver import DriverError

PKG = "com.pocqa.messenger"
CONTACTS = ["Suporte QA", "QA-003", "QA-002", "QA-001", "Equipe Testes"]
W, H = 720, 1280


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), (30, 40, 50)).save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


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
    version: str = "1.0(1)"             # versão do app instalada neste aparelho (chave das receitas)
    frozen: bool = False                # app travado: aceita toques mas a tela não muda (até ser encerrado)
    # falhas injetáveis no toque em Enviar:
    #   "error_after_effect"  → a mensagem é enviada, mas o driver devolve erro (resultado desconhecido)
    #   "error_lost"          → o driver devolve erro e a mensagem NÃO é enviada
    #   "hang_after_effect"   → a mensagem é enviada e a chamada trava por `hang_s` (timeout do executor)
    send_fault: str | None = None
    hang_s: float = 3.0
    action_delay_s: float = 0.0
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
        age = time.monotonic() - m.sent_at
        if age < self.sent_after_s:
            return "Enviando…"
        if age < self.delivered_after_s or m.contact == "QA-002":
            return "Enviada ✓"
        return "Entregue ✓✓"

    # ------------------------------------------------------------------ DeviceIO
    def screenshot_png(self) -> bytes:
        self._enter("screenshot")
        try:
            return PNG
        finally:
            self._leave()

    def current_package(self) -> str | None:
        return "com.android.launcher3" if self.screen == "launcher" else PKG

    def app_version(self, package: str) -> str:
        return self.version

    def force_stop(self, package: str) -> None:
        self._enter("force_stop")
        try:
            self.screen, self.contact, self.input_text, self.frozen = "launcher", None, "", False
            self._nodes = []
        finally:
            self._leave()

    def page_source(self) -> str:
        self._enter("page_source")
        try:
            self._nodes = self._build()
            pkg = self.current_package()
            rows = "".join(
                f"<node class={quoteattr(n.cls)} package={quoteattr(pkg)} text={quoteattr(n.text)} "
                f"resource-id={quoteattr((PKG + ':id/' + n.rid) if n.rid else '')} content-desc={quoteattr(n.desc)} "
                f"clickable=\"{str(n.clickable).lower()}\" enabled=\"true\" focused=\"{str(n.rid == self.focused and bool(n.rid)).lower()}\" "
                f"password=\"{str(n.password).lower()}\" bounds=\"[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]\" />"
                for n in self._nodes)
            return f"<hierarchy rotation=\"0\">{rows}</hierarchy>"
        finally:
            self._leave()

    def _build(self) -> list[Node]:
        if self.screen == "launcher":
            return [Node("android.widget.TextView", (40, 900, 200, 1000), text="QA Messenger", clickable=True, action="open")]
        if self.screen == "login":
            return [Node("android.widget.TextView", (40, 100, 680, 160), text="Sessão expirada. Entre novamente.", rid="login_notice"),
                    Node("android.widget.EditText", (40, 200, 680, 280), rid="login_account", clickable=True),
                    Node("android.widget.EditText", (40, 300, 680, 380), rid="login_pin", clickable=True, password=True),
                    Node("android.widget.Button", (40, 420, 680, 500), text="Entrar", rid="login_button", clickable=True)]
        if self.interstitial:
            return [Node("android.widget.TextView", (60, 300, 660, 380), text="Novidades da versão", rid="interstitial_title"),
                    Node("android.widget.Button", (60, 800, 660, 880), text="Agora não", rid="interstitial_dismiss",
                         clickable=True, action="dismiss")]
        if self.screen == "home":
            nodes = [Node("android.widget.TextView", (20, 40, 500, 100), text=f"Conta: {self.account}", rid="account_label"),
                     Node("android.widget.Button", (520, 40, 700, 100), text="Perfil", rid="btn_profile", clickable=True)]
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
            act = hit.action
            if act == "open":
                self.screen = "login" if self.require_login else "home"
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
        finally:
            self._leave()

    def _send(self) -> None:
        fault, self.send_fault = self.send_fault, None
        if fault == "error_lost":
            raise DriverError("socket hang up (simulado): o toque não chegou ao app", effect_possible=True)
        if self.input_text.strip():
            self.messages.append(Message(self.contact or "", self.input_text.strip(), time.monotonic()))
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
        finally:
            self._leave()

    def press_key(self, key: str) -> None:
        self._enter(f"key:{key}")
        try:
            if key == "back" and self.screen == "chat":
                self.screen, self.contact = "home", None
            elif key == "home":
                self.screen = "launcher"
        finally:
            self._leave()

    def open_app(self, package: str, activity: str | None) -> None:
        self._enter("open_app")
        try:
            if self.screen == "launcher":
                self.screen = "login" if self.require_login else "home"
        finally:
            self._leave()
