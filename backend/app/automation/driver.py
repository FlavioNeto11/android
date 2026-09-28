"""Interface de E/S do aparelho usada pelo executor (bloqueante; roda na thread do dispositivo).

`effect_possible` nas exceções informa se o comando pode ter chegado ao aparelho: é o que decide,
numa etapa com efeito externo, entre "pode repetir" e "precisa reconciliar antes".
"""
from __future__ import annotations

from typing import Protocol


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
