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


class DeviceIO(Protocol):
    # Saúde do CONVIDADO (o Android de dentro), não do transporte: `True` = os serviços do `system_server` estão de
    # pé. Fica no IO, e não só no `Adb`, porque é o gerenciador de aparelhos quem pergunta — e em teste quem
    # responde é o dublê, que sabe fingir um convidado morto.
    def framework_alive(self) -> bool: ...
    # Pressão dentro do convidado (`load1`, `mem_total_mb`, `mem_available_mb`, `ncpu`). Levanta quando o adb não
    # responde; devolve `{}` quando o IO não sabe medir (dublê antigo).
    def guest_pressure(self) -> dict[str, float]: ...
    def screenshot_png(self) -> bytes: ...
    def page_source(self) -> str: ...
    def current_package(self) -> str | None: ...
    def app_version(self, package: str) -> str: ...
    def tap(self, x: int, y: int) -> None: ...
    def long_press(self, x: int, y: int, duration_ms: int) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None: ...
    def type_text(self, text: str, *, clear_first: bool) -> None: ...
    def press_key(self, key: str) -> None: ...
    def open_app(self, package: str, activity: str | None) -> None: ...
