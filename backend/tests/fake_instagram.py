"""Instagram de mentira: telas suficientes para exercitar login, sessão, challenge e conta errada.

Ele existe porque o APK real do Instagram não está no repositório (e não deve estar). O que este fixture prova é a
máquina de estados — classificação, preenchimento, reconciliação, limites. Os seletores reais só aparecem quando o
app de verdade for instalado, e isso está registrado como pendência.
"""
from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import quoteattr

from PIL import Image

from app.automation.driver import DriverError

PKG = "com.instagram.android"
W, H = 720, 1280


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), (20, 20, 28)).save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


@dataclass
class Node:
    cls: str
    bounds: tuple[int, int, int, int]
    text: str = ""
    rid: str = ""
    desc: str = ""
    clickable: bool = False
    password: bool = False
    editable: bool = False
    action: str = ""


@dataclass
class FakeInstagram:
    """Estados: `login`, `feed`, `profile`, `challenge`, `two_factor`, `save_login`.

    `account` é a conta de fato logada. `stored_password` é a senha que este Instagram aceita.
    """

    account: str | None = None                 # None = deslogado
    stored_password: str = "senha-correta"
    screen: str = "login"
    username_field: str = ""
    password_field: str = ""
    locale: str = "en-US"
    # comportamentos injetáveis
    challenge_on_login: bool = False
    two_factor_on_login: bool = False
    wrong_password_message: bool = True
    submit_fault: str | None = None            # "lost" (não chega) | "timeout" (demora e o efeito ocorre)
    hang_s: float = 3.0
    show_username_on_feed: bool = True
    # @ de OUTRA conta visível no feed (autor de reel, story seguido). Não é a conta logada, e não pode ser lido como
    # se fosse — no aparelho real, ler "@kpop_glam_cam" do feed virou um "conta errada" falso.
    foreign_on_feed: str | None = None
    # Abertura a frio: quantas leituras de foco sem janela nenhuma até a primeira tela aparecer (o Instagram real leva
    # ~8 s; 25 s na primeira abertura depois de instalar). Enquanto isso, quem olhar a tela vê o launcher.
    cold_start_reads: int = 0
    focus_reads: int = 0
    # No aparelho real, focar um campo abre o teclado e empurra a tela: o botão Entrar sobe. Ligado, o fake move o
    # botão assim que um campo é focado — quem tocar na posição do formulário vazio erra o botão, como no aparelho.
    keyboard_shift: bool = False
    _focus: str | None = None
    _frio: int = 0
    _tela_ao_abrir: str = "login"
    calls: list[str] = field(default_factory=list)
    typed: list[str] = field(default_factory=list)
    installed: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _nodes: list[Node] = field(default_factory=list)

    # ------------------------------------------------------------------ DeviceIO
    def screenshot_png(self) -> bytes:
        return PNG

    def current_package(self) -> str | None:
        return PKG if self.screen != "launcher" else "com.android.launcher3"

    def current_focus(self) -> tuple[str | None, str | None]:
        """Mesmo contrato do `Adb.current_focus`: (pacote, atividade), ou (None, None) sem janela em foco."""
        self.focus_reads += 1
        if self._frio > 0:
            self._frio -= 1
            if self._frio == 0:
                self.screen = self._tela_ao_abrir
            return None, None
        return self.current_package(), ".MainActivity"

    def system_dialog(self) -> str | None:
        """Este falso nunca encena diálogo do sistema: o foco nulo dele é o app frio desenhando a 1ª tela."""
        return None

    def dismiss_system_dialog(self, *, timeout: float = 0) -> str | None:
        return None

    def app_version(self, package: str) -> str:
        return "447.0.0(447000)"

    def force_stop(self, package: str) -> None:
        self.screen = "launcher"

    def page_source(self) -> str:
        with self._lock:
            self._nodes = self._build()
            rows = "".join(
                f"<node class={quoteattr(n.cls)} package={quoteattr(PKG)} text={quoteattr(n.text)} "
                f"resource-id={quoteattr((PKG + ':id/' + n.rid) if n.rid else '')} content-desc={quoteattr(n.desc)} "
                f'clickable="{str(n.clickable).lower()}" enabled="true" focused="false" '
                f'password="{str(n.password).lower()}" scrollable="false" '
                f'bounds="[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]" />'
                for n in self._nodes)
            return f'<hierarchy rotation="0">{rows}</hierarchy>'

    def _build(self) -> list[Node]:
        if self.screen == "launcher":
            return [Node("android.widget.TextView", (40, 900, 200, 1000), text="Instagram", clickable=True,
                         action="open")]
        if self.screen == "challenge":
            return [
                Node("android.widget.TextView", (40, 200, 680, 280),
                     text="We detected an unusual login attempt", rid="challenge_title"),
                Node("android.widget.TextView", (40, 300, 680, 360), text="Help us confirm it's you"),
                Node("android.widget.Button", (40, 900, 680, 960), text="Continue", clickable=True),
            ]
        if self.screen == "two_factor":
            return [
                Node("android.widget.TextView", (40, 200, 680, 280), text="Enter the 6-digit security code"),
                Node("android.widget.EditText", (40, 320, 680, 390), rid="code", clickable=True, editable=True),
            ]
        if self.screen == "save_login":
            return [
                Node("android.widget.TextView", (40, 300, 680, 380), text="Save your login info?"),
                Node("android.widget.Button", (40, 900, 340, 960), text="Not now", clickable=True, action="dismiss"),
                Node("android.widget.Button", (360, 900, 680, 960), text="Save", clickable=True, action="dismiss"),
            ]
        if self.screen == "profile":
            return [
                Node("android.widget.TextView", (40, 120, 400, 180), text=self.account or "",
                     rid="row_profile_header_textview_username"),
                *self._tab_bar(),
            ]
        if self.screen == "feed":
            topo = [Node("android.widget.TextView", (40, 60, 400, 110), text="Instagram", rid="action_bar_title_logo"),
                    Node("android.widget.ImageView", (600, 60, 680, 110), desc="Messages", rid="action_bar_inbox_button",
                         clickable=True)]
            if self.show_username_on_feed and self.account:
                topo.append(Node("android.widget.TextView", (40, 200, 400, 250), text=f"@{self.account}",
                                 rid="feed_account_hint"))
            if self.foreign_on_feed:
                # Reel em foco: o MESMO id de cabeçalho (`action_bar_title`) passa a mostrar o autor do reel, não a
                # conta logada. É o que enganava a leitura no aparelho real.
                topo.append(Node("android.widget.TextView", (40, 300, 400, 350), text=self.foreign_on_feed,
                                 rid="action_bar_title", clickable=True))
            return [*topo, *self._tab_bar()]
        # login
        # Com um campo focado (teclado aberto), a tela inteira sobe, preservando a ordem dos elementos. Uma leitura
        # feita com o formulário vazio aponta para os lugares antigos, e tocar ali erra os campos e o botão — a falha
        # vista no aparelho real: campos preenchidos, sem erro, parado no login.
        sobe = 140 if (self.keyboard_shift and self._focus) else 0

        def y(v: int) -> int:
            return v - sobe

        erro = []
        if self.screen == "login_error" and self.wrong_password_message:
            erro = [Node("android.widget.TextView", (40, y(600), 680, y(650)),
                         text="Incorrect password. Please try again.", rid="login_error")]
        return [
            Node("android.widget.TextView", (40, y(200), 680, y(260)), text="Instagram", rid="logo"),
            Node("android.widget.EditText", (40, y(400), 680, y(470)), text=self.username_field, rid="login_username",
                 clickable=True, editable=True, action="focus:username"),
            Node("android.widget.EditText", (40, y(500), 680, y(570)), text=self.password_field, rid="login_password",
                 clickable=True, editable=True, password=True, action="focus:password"),
            *erro,
            Node("android.widget.Button", (40, y(680), 680, y(750)), text="Log in", rid="login_button",
                 clickable=True, action="submit"),
            Node("android.widget.TextView", (40, y(800), 680, y(850)), text="Log in with Facebook", clickable=True),
            Node("android.widget.TextView", (40, y(900), 680, y(950)), text="Forgot password?", clickable=True),
        ]

    def _tab_bar(self) -> list[Node]:
        y = 1180
        return [
            Node("android.widget.ImageView", (20, y, 140, 1260), desc="Home", rid="feed_tab", clickable=True),
            Node("android.widget.ImageView", (160, y, 280, 1260), desc="Search", rid="search_tab", clickable=True),
            Node("android.widget.ImageView", (300, y, 420, 1260), desc="Reels", rid="clips_tab", clickable=True),
            Node("android.widget.ImageView", (440, y, 560, 1260), desc="Shop", rid="shopping_tab", clickable=True),
            Node("android.widget.ImageView", (580, y, 700, 1260), desc="Profile", rid="profile_tab", clickable=True,
                 action="profile"),
        ]

    # ------------------------------------------------------------------ interação
    def tap(self, x: int, y: int) -> None:
        self.calls.append(f"tap:{x},{y}")
        hit = next((n for n in self._nodes if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                    and n.bounds[1] <= y <= n.bounds[3]), None)
        if hit is None:
            return
        if hit.action == "open":
            self.screen = "feed" if self.account else "login"
        elif hit.action == "profile":
            self.screen = "profile"
        elif hit.action == "dismiss":
            self.screen = "feed"
        elif hit.action.startswith("focus:"):
            self._focus = hit.action.split(":", 1)[1]
        elif hit.action == "submit":
            self._submit()

    def _submit(self) -> None:
        self.calls.append("submit")
        if self.submit_fault == "lost":
            self.submit_fault = None
            raise DriverError("socket hang up (simulado): o toque não chegou ao app", effect_possible=False)
        if self.submit_fault == "timeout":
            self.submit_fault = None
            self._apply_login()
            time.sleep(self.hang_s)
            return
        self._apply_login()

    def _apply_login(self) -> None:
        if self.password_field != self.stored_password:
            self.screen = "login_error"
            self.password_field = ""
            return
        if self.challenge_on_login:
            self.screen = "challenge"
            return
        if self.two_factor_on_login:
            self.screen = "two_factor"
            return
        self.account = self.username_field.lstrip("@")
        self.password_field = ""
        self.screen = "feed"

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.tap(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.calls.append("swipe")

    def type_text(self, text: str, *, clear_first: bool) -> None:
        campo = getattr(self, "_focus", "username")
        self.calls.append(f"type:{campo}:{'clear' if clear_first else 'append'}")
        if text:
            self.typed.append(text)
        if campo == "password":
            self.password_field = text if clear_first else self.password_field + text
        else:
            self.username_field = text if clear_first else self.username_field + text

    def press_key(self, key: str) -> None:
        self.calls.append(f"key:{key}")

    def open_app(self, package: str, activity: str | None) -> None:
        # Reabrir o app não faz um desafio sumir, nem o "Salvar dados de login?" pendente: eles voltam a aparecer até
        # serem resolvidos na tela.
        if self.screen in ("challenge", "two_factor", "save_login"):
            return
        self.screen = "feed" if self.account else "login"
        if self.cold_start_reads > 0:
            self._tela_ao_abrir, self.screen, self._frio = self.screen, "launcher", self.cold_start_reads

    # ------------------------------------------------------------------ superfície de adb usada pelo autenticador
    def getprop(self, name: str) -> str:
        return {"ro.product.locale": self.locale, "ro.product.cpu.abi": "x86_64",
                "ro.product.cpu.abilist": "x86_64,arm64-v8a", "ro.build.version.sdk": "34"}.get(name, "")

    def start_app(self, package: str, activity: str | None = None) -> None:
        self.open_app(package, activity)

    def clear_data(self, package: str) -> None:
        self.account = None
        self.username_field = self.password_field = ""
        self.screen = "login"

    def is_installed(self, package: str) -> bool:
        return self.installed
