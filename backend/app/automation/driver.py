"""Interface de E/S do aparelho usada pelo executor (bloqueante; roda na thread do dispositivo).

`effect_possible` nas exceções informa se o comando pode ter chegado ao aparelho: é o que decide,
numa etapa com efeito externo, entre "pode repetir" e "precisa reconciliar antes".
"""
from __future__ import annotations

from typing import Protocol

from ..devices.adb import MorteDoApp


class DriverError(RuntimeError):
    def __init__(self, message: str, *, effect_possible: bool = True):
        super().__init__(message)
        self.effect_possible = effect_possible


class DriverTimeout(DriverError):
    """A chamada não retornou no prazo. Ela PODE ainda estar agindo sobre o aparelho."""

    def __init__(self, message: str):
        super().__init__(message, effect_possible=True)


class DriverUnavailable(DriverError):
    """Sessão de automação indisponível — nada foi enviado ao aparelho."""

    def __init__(self, message: str):
        super().__init__(message, effect_possible=False)


class FalhaDeLeitura(DriverError):
    """Uma LEITURA feita fora da sessão do Appium falhou: o screencap ou o `dumpsys` pelo adb (`AdbError`), ou a
    imagem pedida ao agente do worker ("captura na origem falhou"). Nada foi enviado ao aparelho.

    A sessão do Appium nem participou — recriá-la (DELETE + POST /session e a reinstrumentação do UiAutomator2, 27–80 s
    num convidado saturado) não conserta um screencap. É transitória como `DriverBusy`: relê-se com recuo."""

    def __init__(self, message: str):
        super().__init__(message, effect_possible=False)


class DriverBusy(DriverError):
    """A interface do aparelho está OCUPADA: o UiAutomator2 respondeu (500) que não obteve a raiz de acessibilidade
    da janela ativa a tempo ("hogging the main UI thread", "no active window").

    Quem respondeu foi o servidor da sessão — logo, a sessão está VIVA. É lentidão do convidado, transitória (uma
    sessão sobreviveu a esse 500 em 26/09), e não sessão morta: tratá-la como morta custou 5 recriações e 305,6 s de
    925 na execução r-20260928195344-02ee9e (4 e 214 s na r-20260928165254-e31953), cada uma reinstrumentando o
    UiAutomator2 no convidado já saturado. Numa LEITURA, relê-se com recuo; numa AÇÃO (`effect_possible=True`), o
    gesto pode ter chegado ao app e não se repete às cegas.

    Nos logs reais (appium.log e backend.log do central, 24–28/09) só a forma "root AccessibilityNodeInfo … hogging
    the main UI thread" apareceu; "no active window" nunca foi vista e segue reconhecida por ser a outra forma
    documentada do mesmo 500 (`appium_driver._UI_OCUPADA`)."""


#: O que o Appium diz quando a SESSÃO (ou a instrumentação do UiAutomator2 por trás dela) não existe mais, ou quando o
#: servidor dele não aceita conexão. "session" é o critério de antes ("A session is either terminated or not
#: started", e a URL `/session/<id>` do urllib3 quando o Appium recusa a conexão). A instrumentação morta não cita
#: sessão: "'DELETE /' cannot be proxied to UiAutomator2 server because the instrumentation process is not running",
#: no appium.log do central — numa ação, com o comando da vez no lugar do DELETE.
_SESSAO_PERDIDA = ("session", "instrumentation process is not running", "econnrefused", "connection refused")


def sessao_perdida(exc: DriverError) -> bool:
    """O erro diz que a sessão do Appium morreu — e só recriá-la resolve? É o critério da AÇÃO que falhou: uma recusa
    de elemento ou a UI ocupada vêm de uma sessão VIVA, e a leitura pelo adb nem passou por ela.

    Confere a mensagem e a da causa encadeada: `AppiumSession._call` corta a sua em 300 caracteres da primeira linha, e
    a parte útil pode vir depois. Da causa do Selenium vale só a mensagem (`msg`), sem a pilha Java do servidor — um
    nome de classe com "Session" lá dentro recriaria a sessão por uma simples recusa de elemento."""
    if isinstance(exc, DriverUnavailable):
        return True
    if isinstance(exc, (DriverBusy, FalhaDeLeitura)):
        return False
    causa = exc.__cause__
    # Com `msg` (Selenium), só ela — mesmo vazia: cair no `str` traria a pilha de volta.
    texto = f"{exc} {getattr(causa, 'msg', causa) or ''}".casefold()
    return any(m in texto for m in _SESSAO_PERDIDA)


class SemCampoEmFoco(DriverError):
    """`set_text` não achou campo em foco onde definir o texto — nada foi escrito. É o ÚNICO caso em que quem chamou
    pode cair no teclado: qualquer outra falha de `set_text` pode ter escrito (com atraso) e redigitar duplicaria."""

    def __init__(self, message: str):
        super().__init__(message, effect_possible=False)


class DeviceIO(Protocol):
    # Saúde do CONVIDADO (o Android de dentro), não do transporte: `True` = os serviços do `system_server` estão de
    # pé. Fica no IO, e não só no `Adb`, porque é o gerenciador de aparelhos quem pergunta — e em teste quem
    # responde é o dublê, que sabe fingir um convidado morto.
    def framework_alive(self, *, timeout: float = 25) -> bool: ...
    # Os outros dois degraus da prontidão (`devices/prontidao.py`): `system_server` e display (SurfaceFlinger).
    def system_server_alive(self, *, timeout: float = 8) -> bool: ...
    def display_alive(self, *, timeout: float = 12) -> bool: ...
    # Pressão dentro do convidado (`load1`, `mem_total_mb`, `mem_available_mb`, `ncpu`). Levanta quando o adb não
    # responde; devolve `{}` quando o IO não sabe medir (dublê antigo).
    def guest_pressure(self) -> dict[str, float]: ...
    # Internet dentro do convidado: {route, dns, tcp_443, validated}. Levanta quando o adb não responde.
    def connectivity_probe(self) -> dict[str, bool]: ...
    def screenshot_png(self) -> bytes: ...
    def page_source(self) -> str: ...
    def current_package(self) -> str | None: ...
    # Janela em foco AGORA, (pacote, atividade) — a seção viva do `dumpsys window`, nunca a cópia do último ANR. É o
    # que `open_app` e o executor esperam depois de abrir um app. Levanta quando o adb não responde.
    def current_focus(self) -> tuple[str | None, str | None]: ...
    # Mortes do app (crash/ANR) nos últimos `within_s` segundos do relógio do convidado (`Adb.app_deaths`). Levanta
    # quando não dá para saber — "não sei" nunca é "nenhuma morte".
    def app_deaths(self, package: str, *, within_s: float | None = None) -> list[MorteDoApp]: ...
    def app_version(self, package: str) -> str: ...
    def tap(self, x: int, y: int) -> None: ...
    def long_press(self, x: int, y: int, duration_ms: int) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None: ...
    # Teclado (`mobile: type`): o canal da senha (`type_secret`), do usuário no login e da digitação manual.
    def type_text(self, text: str, *, clear_first: bool) -> None: ...
    # Define o texto do campo em FOCO de uma vez, sem teclado (ACTION_SET_TEXT): o caminho da ferramenta `type_text`.
    # `clear_first=True` substitui o conteúdo; `False` acrescenta ao fim. Sem campo em foco, `SemCampoEmFoco`.
    def set_text(self, text: str, *, clear_first: bool) -> None: ...
    def press_key(self, key: str) -> None: ...
    def open_app(self, package: str, activity: str | None) -> None: ...
    def open_url(self, url: str) -> None: ...
