"""Instagram de mentira: telas suficientes para exercitar login, sessão, challenge e conta errada — e, desde a fase G
da evolução arquitetural, a caixa de mensagens e a conversa (a fatia vertical "abrir conversa").

Ele existe porque o APK real do Instagram não está no repositório (e não deve estar). O que este fixture prova é a
máquina de estados — classificação, preenchimento, reconciliação, limites, e o caminho de mensagens. Os ids das telas
de mensagens são os lidos das telas reais julgadas em 24–25/09 (`row_thread_composer_edittext`, o da prova local de
`OPEN_THREAD`); o resto da hierarquia é mínimo, não fotografia do app.

`AtorDoInstagram` é o provedor de IA de mentira que sabe conduzir ESTAS telas (o `SimulatedProvider` só conhece o QA
Messenger). Envolto no `CountingProvider`, é o que prova quantas chamadas de IA cada caminho custa.
"""
from __future__ import annotations

import io
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import quoteattr

from PIL import Image

from app.automation.driver import DriverError
from app.automation.hierarchy import UiElement, UiTree
from app.devices.adb import MorteDoApp
from app.models import AiStatus, PersonaDraft, Plan, PlannerInfo, SocialDraftDTO
from app.planning.capabilities import CapabilityNode, compose, load_catalog
from app.planning.provider import (Decision, DecisionRequest, PersonaGenerationRequest, PlanRequest, SocialRequest, Usage, Verdict,
                                   VerifyRequest)


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
    scrollable: bool = False
    enabled: bool = True


#: Conversas da caixa de entrada: usuário (como o app mostra, sem arroba) → mensagens, da mais antiga à mais nova.
CONVERSAS = {"ana": ["oi, tudo bem?", "amanhã às dez então"], "bia": ["valeu pela ajuda"]}
#: Ids das telas de mensagens. O do compositor é o da prova local de `OPEN_THREAD` (catálogo, G0 `8a2fca5`).
ID_BUSCA = "search_edit_text"
ID_LINHA = "row_inbox_username"
ID_LISTA_DA_CAIXA = "inbox_refreshable_thread_list_recyclerview"
ID_CABECALHO = "header_title"
ID_LISTA_DA_CONVERSA = "message_list"
ID_MENSAGEM = "direct_text_message_text_view"
ID_COMPOSITOR = "row_thread_composer_edittext"


@dataclass
class FakeInstagram:
    """Estados: `login`, `feed`, `profile`, `challenge`, `two_factor`, `save_login`, `inbox`, `thread`.

    `account` é a conta de fato logada. `stored_password` é a senha que este Instagram aceita. `threads` são as
    conversas da caixa de entrada; `thread_with`, a conversa aberta na tela `thread`.
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
    # 29.64: quantos toques em Entrar o app IGNORA (o toque chega, nada acontece), e como a tela fica depois:
    # "intacto" (preenchido, sem erro, o caso do android-13 em 04/10), "carregando" (um ProgressBar na tela) ou
    # "senha_limpa" (o app esvaziou o campo da senha), "desabilitado" (Entrar desabilitado), "identificador_trocado"
    # (outro usuário no campo), "erro" (a mensagem de senha errada, com a senha ainda no campo), "desafio" ou "feed".
    envios_ignorados: int = 0
    tela_ao_ignorar: str = "intacto"
    _carregando: bool = False
    _entrar_desabilitado: bool = False
    #: Outro pacote na frente, sem mudar a tela desenhada (o teste do pacote diferente na releitura).
    pacote_forcado: str | None = None
    hang_s: float = 3.0
    show_username_on_feed: bool = True
    # @ de OUTRA conta visível no feed (autor de reel, story seguido). Não é a conta logada, e não pode ser lido como
    # se fosse — no aparelho real, ler "@kpop_glam_cam" do feed virou um "conta errada" falso.
    foreign_on_feed: str | None = None
    # Abertura a frio: quantas leituras de foco sem janela nenhuma até a primeira tela aparecer (o Instagram real leva
    # ~8 s; 25 s na primeira abertura depois de instalar). Enquanto isso, quem olhar a tela vê o launcher.
    cold_start_reads: int = 0
    # O app de verdade, reaberto com a conta logada, RETOMA a tela em que estava (uma conversa, um post) em vez de
    # voltar ao feed — foi assim que a execução e31953 ficou presa numa conversa. Ligado, o dublê faz o mesmo.
    retoma_tela_ao_abrir: bool = False
    # Pacote "anr" (r-20260928165254-e31953 e r-20260928195344-02ee9e, android-06 com 2 vCPU saturadas): quantas das
    # próximas aberturas morrem por ANR na partida a frio. Com `hide_error_dialogs=1` o sistema fecha o app sem
    # diálogo e o launcher volta; a morte fica no `exit-info` (reason=6).
    anr_ao_abrir: int = 0
    _mortes: list[tuple[float, int]] = field(default_factory=list)   # (time.monotonic() da morte, pid)
    focus_reads: int = 0
    # No aparelho real, focar um campo abre o teclado e empurra a tela: o botão Entrar sobe. Ligado, o fake move o
    # botão assim que um campo é focado — quem tocar na posição do formulário vazio erra o botão, como no aparelho.
    keyboard_shift: bool = False
    _focus: str | None = None
    _frio: int = 0
    _tela_ao_abrir: str = "login"
    calls: list[str] = field(default_factory=list)
    midias_na_galeria: list[dict] = field(default_factory=list)      # 29.30: o que o push colocou na galeria
    typed: list[str] = field(default_factory=list)
    installed: bool = True
    # caixa de mensagens e conversa (fase G)
    threads: dict[str, list[str]] = field(default_factory=lambda: {k: list(v) for k, v in CONVERSAS.items()})
    thread_with: str | None = None
    search_query: str = ""
    composer_text: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _nodes: list[Node] = field(default_factory=list)

    # ------------------------------------------------------------------ DeviceIO: saúde do convidado
    # Os cinco que faltavam para cumprir o `DeviceIO` (o Harness os chama no boot e na prontidão). Este falso não
    # encena convidado doente: quem precisa disso é o `FakeQaDevice` (`guest_dead`, `guest_mudo`).
    def framework_alive(self, *, timeout: float = 25) -> bool:
        return True

    def system_server_alive(self, *, timeout: float = 8) -> bool:
        return True

    def display_alive(self, *, timeout: float = 12) -> bool:
        return True

    def guest_pressure(self) -> dict[str, float]:
        return {"load1": 0.5, "mem_total_mb": 2048.0, "mem_available_mb": 900.0, "ncpu": 2.0}

    def guest_culprits(self) -> dict[str, object]:
        return {"processos": [], "primeiro_plano": None}

    def connectivity_probe(self) -> dict[str, bool]:
        return {"route": True, "dns": True, "tcp_443": True, "validated": True}

    # ------------------------------------------------------------------ DeviceIO
    def screenshot_png(self) -> bytes:
        return PNG

    def current_package(self) -> str | None:
        if self.pacote_forcado is not None:
            return self.pacote_forcado
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

    def app_deaths(self, package: str, *, within_s: float | None = None) -> list[MorteDoApp]:
        """Mesmo contrato do `Adb.app_deaths`: as mortes por ANR do app (encenadas por `anr_ao_abrir`)."""
        agora = time.monotonic()
        if package != PKG:
            return []
        return [MorteDoApp(quando=f"t+{t:.3f}", pid=pid, motivo=6, anr=True, idade_s=agora - t,
                           descricao="user request after error")
                for t, pid in self._mortes if within_s is None or agora - t <= within_s]

    def system_dialog(self) -> str | None:
        """Este falso nunca encena diálogo do sistema: o foco nulo dele é o app frio desenhando a 1ª tela."""
        return None

    def dismiss_system_dialog(self, *, timeout: float = 0) -> str | None:
        return None

    def app_version(self, package: str) -> str:
        return "447.0.0(447000)"

    def enviar_midia_para_galeria(self, local: str, nome: str, *, timeout: float = 60) -> str:
        """29.30: o `Adb.enviar_midia_para_galeria` do dublê. Registra o que chegou (nome, bytes lidos do arquivo local e
        a indexação pedida) em `midias_na_galeria`; não toca na tela."""
        with open(local, "rb") as f:
            dados = f.read()
        self.midias_na_galeria.append({"nome": nome, "bytes": dados, "indexada": True})
        return f"/sdcard/Pictures/Central/{nome}.jpg"

    def force_stop(self, package: str) -> None:
        self.screen = "launcher"

    def page_source(self) -> str:
        with self._lock:
            self._nodes = self._build()
            rows = "".join(
                f"<node class={quoteattr(n.cls)} package={quoteattr(PKG)} text={quoteattr(n.text)} "
                f"resource-id={quoteattr((PKG + ':id/' + n.rid) if n.rid else '')} content-desc={quoteattr(n.desc)} "
                f'clickable="{str(n.clickable).lower()}" enabled="{str(n.enabled).lower()}" focused="false" '
                f'password="{str(n.password).lower()}" scrollable="{str(n.scrollable).lower()}" '
                f'bounds="[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]" />'
                for n in self._nodes)
            return f'<hierarchy rotation="0">{rows}</hierarchy>'

    def _build(self) -> list[Node]:
        if self.screen == "inbox":
            return self._caixa_de_entrada()
        if self.screen == "thread":
            return self._conversa()
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
                         clickable=True, action="inbox")]
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
        if self._carregando:
            erro = [*erro, Node("android.widget.ProgressBar", (330, y(760), 390, y(790)), rid="login_progress")]
        return [
            Node("android.widget.TextView", (40, y(200), 680, y(260)), text="Instagram", rid="logo"),
            Node("android.widget.EditText", (40, y(400), 680, y(470)), text=self.username_field, rid="login_username",
                 clickable=True, editable=True, action="focus:username"),
            Node("android.widget.EditText", (40, y(500), 680, y(570)), text=self.password_field, rid="login_password",
                 clickable=True, editable=True, password=True, action="focus:password"),
            *erro,
            Node("android.widget.Button", (40, y(680), 680, y(750)), text="Log in", rid="login_button",
                 clickable=True, action="submit", enabled=not self._entrar_desabilitado),
            Node("android.widget.TextView", (40, y(800), 680, y(850)), text="Log in with Facebook", clickable=True),
            Node("android.widget.TextView", (40, y(900), 680, y(950)), text="Forgot password?", clickable=True),
        ]

    def _caixa_de_entrada(self) -> list[Node]:
        """A caixa de mensagens: título, busca EDITÁVEL e uma linha por conversa. A busca é o motivo de a prova local
        de `OPEN_THREAD` exigir o compositor pelo id: "há um campo editável na tela" passaria aqui (G0)."""
        filtro = self.search_query.strip().lstrip("@").casefold()
        usuarios = [u for u in self.threads if not filtro or filtro in u.casefold()]
        linhas = [Node("android.widget.TextView", (40, 240 + i * 120, 680, 290 + i * 120), text=u, rid=ID_LINHA,
                       clickable=True, action=f"thread:{u}") for i, u in enumerate(usuarios)]
        return [
            Node("android.widget.TextView", (40, 60, 500, 110), text=self.account or "", rid="action_bar_large_title"),
            Node("android.widget.EditText", (40, 140, 680, 200), text=self.search_query or "Search", rid=ID_BUSCA,
                 clickable=True, editable=True, action="focus:search"),
            Node("androidx.recyclerview.widget.RecyclerView", (0, 220, 720, 1160), rid=ID_LISTA_DA_CAIXA,
                 scrollable=True),
            *linhas,
            *self._tab_bar(),
        ]

    def _conversa(self) -> list[Node]:
        """A conversa aberta: voltar, cabeçalho com o usuário, as mensagens e o campo de escrever (compositor)."""
        mensagens = self.threads.get(self.thread_with or "", [])
        return [
            Node("android.widget.ImageView", (20, 60, 100, 110), desc="Back", rid="action_bar_button_back",
                 clickable=True, action="back"),
            Node("android.widget.TextView", (120, 60, 500, 110), text=self.thread_with or "", rid=ID_CABECALHO),
            Node("androidx.recyclerview.widget.RecyclerView", (0, 130, 720, 1150), rid=ID_LISTA_DA_CONVERSA,
                 scrollable=True),
            *[Node("android.widget.TextView", (40, 150 + i * 70, 680, 200 + i * 70), text=m, rid=ID_MENSAGEM)
              for i, m in enumerate(mensagens)],
            Node("android.widget.EditText", (20, 1180, 580, 1240), text=self.composer_text or "Message…",
                 rid=ID_COMPOSITOR, clickable=True, editable=True, action="focus:composer"),
        ]

    def _voltar(self) -> None:
        """Voltar do Android nas telas de mensagens: conversa → caixa → feed. Nas demais telas, nada muda."""
        if self.screen == "thread":
            self.screen, self.thread_with, self.composer_text = "inbox", None, ""
        elif self.screen == "inbox":
            self.screen, self.search_query = "feed", ""

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
        elif hit.action == "inbox":
            self.screen, self.search_query = "inbox", ""
        elif hit.action.startswith("thread:"):
            self.screen, self.thread_with, self.composer_text = "thread", hit.action.split(":", 1)[1], ""
        elif hit.action == "back":
            self._voltar()
        elif hit.action.startswith("focus:"):
            self._focus = hit.action.split(":", 1)[1]
        elif hit.action == "submit":
            self._submit()

    def _submit(self) -> None:
        self.calls.append("submit")
        if self.envios_ignorados > 0:
            self.envios_ignorados -= 1
            t = self.tela_ao_ignorar
            self._carregando = t == "carregando"
            self._entrar_desabilitado = t == "desabilitado"
            if t == "senha_limpa":
                self.password_field = ""
            elif t == "identificador_trocado":
                self.username_field = "outra.pessoa.1234"
            elif t == "erro":
                self.screen = "login_error"
            elif t == "desafio":
                self.screen = "challenge"
            elif t == "feed":
                self.screen = "feed"
            return
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
        if campo == "search":
            self.search_query = text if clear_first else self.search_query + text
        elif campo == "composer":
            self.composer_text = text if clear_first else self.composer_text + text
        elif campo == "password":
            self.password_field = text if clear_first else self.password_field + text
        else:
            self.username_field = text if clear_first else self.username_field + text

    def set_text(self, text: str, *, clear_first: bool) -> None:
        # Mesmo campo, mesmo registro em `calls`: a diferença de transporte é do aparelho real
        # (test_digitacao_atomica a modela).
        self.type_text(text, clear_first=clear_first)

    def press_key(self, key: str) -> None:
        self.calls.append(f"key:{key}")
        if key == "back":
            self._voltar()

    def open_url(self, url: str) -> None:
        # `DeviceIO.open_url` (ADR-025): o dublê do Instagram não tem navegador — só registra o pedido, para um
        # fluxo que o chame não quebrar por falta do método.
        self.urls_abertas = [*getattr(self, "urls_abertas", []), url]

    def open_app(self, package: str, activity: str | None) -> None:
        if self.anr_ao_abrir > 0:
            # Partida a frio num convidado sem CPU: ANR, o sistema fecha o app em silêncio e o launcher volta.
            self.anr_ao_abrir -= 1
            self._mortes.append((time.monotonic(), 7000 + len(self._mortes)))
            self.screen = "launcher"
            return
        # Reabrir o app não faz um desafio sumir, nem o "Salvar dados de login?" pendente: eles voltam a aparecer até
        # serem resolvidos na tela.
        if self.screen in ("challenge", "two_factor", "save_login"):
            return
        if self.retoma_tela_ao_abrir and self.account and self.screen not in ("launcher", "login", "login_error"):
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


# ==================================================================== ator de IA de mentira (fase G)
_ARROBA = re.compile(r"@([a-zA-Z0-9._]{2,30})")
_CONVERSA_COM = re.compile(r"conversa com @?([a-zA-Z0-9._]{2,30})", re.IGNORECASE)


def _alvo(objetivo: str) -> str:
    """O usuário da conversa, lido do objetivo JÁ resolvido ("Abrir a conversa com @ana, …"), como o modelo o lê:
    os parâmetros do contexto são filtrados pela capability, e o do planejador vem nos `bindings`."""
    achado = _CONVERSA_COM.search(objetivo)
    return achado.group(1) if achado else ""


class AtorDoInstagram:
    """Provedor de IA por regras que conduz as telas de MENSAGENS deste Instagram falso. Não é IA e não finge ser.

    Reconhece a etapa pelo objetivo que o catálogo escreve (`OPEN_INBOX`, `OPEN_THREAD`, `READ_MESSAGES`), porque a
    chave da etapa muda com a skill (`abrir_conversa` avulsa, `abrir_abrir_conversa` composta) e o objetivo não. Age
    sempre por `element_id` — é o que deixa a tentativa virar receita (`distill` recusa toque sem alvo resolvido).
    O `plan` só existe para o caminho SEM skill (o planejador); na fatia G ele não pode ser chamado.
    """

    name = "ator-do-instagram"
    model = "roteiro-das-telas-de-mensagens"
    simulated = True

    def status(self) -> AiStatus:
        return AiStatus(provider=self.name, model=self.model, configured=True, simulated=True,
                        sends_data_externally=False, effort=None,
                        notice="Teste: ator por regras das telas de mensagens do Instagram falso.")

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        catalogo = load_catalog(PKG)
        assert catalogo is not None
        achado = _ARROBA.search(req.command)
        # O alvo vai em `parameters` e a etapa o referencia por `{username}`, como o planejador de verdade faz: é o que
        # deixa o fluxo aprendido desta execução casar o mesmo comando com outro usuário.
        nos = [CapabilityNode(key="abrir_inbox", capability="OPEN_INBOX"),
               CapabilityNode(key="abrir_conversa", capability="OPEN_THREAD", depends_on=["abrir_inbox"],
                              bindings={"username": "{username}"})]
        etapas, faltando = compose(catalogo, nos)
        return Plan(summary="[roteiro] abrir conversa no Instagram", app_id="instagram", app_package=PKG,
                    parameters={"username": f"@{achado.group(1)}" if achado else ""}, steps=etapas,
                    missing=faltando, planner=PlannerInfo(provider=self.name, model=self.model, simulated=True)), Usage()

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        tree: UiTree = req.screen.tree
        objetivo = req.ctx.step_goal.casefold()

        def d(tool: str, why: str, **args: Any) -> tuple[Decision, Usage]:
            return Decision(tool=tool, args={"rationale": f"[roteiro] {why}", **args}), Usage()

        def toque(el: UiElement, why: str) -> tuple[Decision, Usage]:
            return d("tap", why, element_id=el.id, x=None, y=None, is_commit_action=False)

        def primeiro(**kw: Any) -> UiElement | None:
            achados = tree.find(**kw)
            return achados[0] if achados else None

        if objetivo.startswith("abrir a caixa de mensagens"):
            if primeiro(resource_id=ID_BUSCA):
                return d("step_done", "caixa de mensagens aberta", evidence="busca e lista de conversas visíveis",
                         delivery_level=None)
            botao = primeiro(resource_id="action_bar_inbox_button")
            return toque(botao, "abrir as mensagens") if botao else d("press_back", "voltar ao feed")
        if objetivo.startswith("abrir a conversa com"):
            alvo = _alvo(req.ctx.step_goal)
            cabecalho = primeiro(resource_id=ID_CABECALHO)
            if cabecalho is not None:
                if cabecalho.text == alvo and primeiro(resource_id=ID_COMPOSITOR):
                    return d("step_done", "conversa certa aberta", evidence=f"cabeçalho {alvo} e compositor",
                             delivery_level=None)
                return d("press_back", "conversa de outra pessoa")
            linha = next((e for e in tree.find(text=alvo, exact=True) if e.resource_id.endswith(ID_LINHA)), None)
            if linha is not None:
                return toque(linha, f"abrir a conversa com {alvo}")
            return d("step_blocked", "conversa não encontrada", kind="missing_info", needs_user=True,
                     reason=f"{alvo} não aparece na caixa de entrada")
        if objetivo.startswith("ler as mensagens"):
            lista = primeiro(resource_id=ID_LISTA_DA_CONVERSA)
            if lista is not None:
                return d("collect_list", "ler a conversa", element_id=lista.id, item_selector=f"id={ID_MENSAGEM}",
                         exclude=[], expect_done=True)
            return d("step_blocked", "nenhuma conversa aberta", kind="other", needs_user=False,
                     reason="a tela não é de conversa")
        return d("step_blocked", "etapa desconhecida", kind="other", needs_user=False,
                 reason=f"o roteiro não sabe executar: {req.ctx.step_goal}")

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        tree: UiTree = req.screen.tree
        objetivo = req.ctx.step_goal.casefold()
        if objetivo.startswith("abrir a caixa de mensagens"):
            ok = bool(tree.find(resource_id=ID_BUSCA))
            return Verdict(satisfied="yes" if ok else "no", evidence="[roteiro] caixa de mensagens"), Usage()
        if objetivo.startswith("abrir a conversa com"):
            alvo = _alvo(req.ctx.step_goal)
            ok = bool(tree.find(text=alvo, exact=True, resource_id=ID_CABECALHO)) and bool(
                tree.find(resource_id=ID_COMPOSITOR))
            return Verdict(satisfied="yes" if ok else "no", evidence="[roteiro] conversa aberta"), Usage()
        return Verdict(satisfied="no", evidence="[roteiro] etapa desconhecida"), Usage()

    async def generalize(self, req: Any) -> tuple[dict[str, Any], Usage]:
        raise AssertionError("o ator do Instagram falso não generaliza demonstrações")

    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]:
        return SocialDraftDTO(refused=True, rationale="[roteiro] não escreve texto",
                              refusal_reason="o ator do Instagram falso não escreve mensagens"), Usage()

    async def generate_persona(self, req: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]:
        from app.planning.simulated_provider import persona_simulada

        return persona_simulada(req), Usage()

    async def generate_text(self, system: str, prompt: str, *, max_tokens: int = 64) -> tuple[str, Usage]:
        return "[roteiro]", Usage()

    async def transcribe(self, req: Any) -> tuple[Any, Usage]:
        raise AssertionError("o ator do Instagram falso não lê recortes de tela (leitura visual, item 12.5)")
