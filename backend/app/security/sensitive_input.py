"""Canal de entrada sensível: o único caminho por onde uma credencial chega ao aparelho.

Por que ele existe: digitar pelo caminho normal (`TypeText` -> `execute_tool` -> `rt.io.type_text`) grava o texto em
três lugares — no argumento da ação persistida, no histórico que vai ao modelo e no log do Appium. Este canal fala
direto com o driver, fora do laço de ferramentas, e registra apenas que a digitação aconteceu.

O que ele NÃO faz: não recebe o valor pronto (recebe uma função que o resolve no último instante), não devolve o
valor, não o coloca em exceção, e não registra nem o comprimento.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..automation.hierarchy import UiElement, UiTree

log = logging.getLogger("poc.security")

# Quantas vezes tentar esvaziar o campo antes de desistir. Esvaziar é obrigatório: uma tentativa anterior que
# preencheu o campo e caiu antes de enviar faria a próxima digitar a credencial duas vezes concatenada.
CLEAR_ATTEMPTS = 2


async def _ran(coro: Awaitable[Any]) -> bool:
    """Executa e devolve só sucesso/fracasso. A exceção original é descartada DENTRO desta função, e quem levanta o
    erro fica fora de qualquer bloco `except` — assim a exceção do driver, que pode conter o texto digitado, não fica
    encadeada em `__context__` nem aparece em traceback."""
    try:
        await coro
    except Exception:  # noqa: BLE001 - proposital: a mensagem original nunca pode escapar
        return False
    return True


class SensitiveInputError(RuntimeError):
    """Falha ao preencher um campo sensível. A mensagem é fixa: nunca carrega texto do driver nem do campo."""


class SensitiveInputUnavailable(SensitiveInputError):
    """O mascaramento de log do Appium não está comprovadamente ativo — o canal se recusa a operar."""


@dataclass(slots=True)
class SensitiveInputReceipt:
    """O que fica registrado no lugar da ação. Sem valor e sem comprimento."""

    sensitive_input_completed: bool = True
    field: str = ""          # identificação estrutural do campo (resource-id), nunca o conteúdo

    def to_dict(self) -> dict[str, Any]:
        return {"sensitive_input_completed": self.sensitive_input_completed, "field": self.field}


class SensitiveInputChannel:
    def __init__(self, masking_active: Callable[[], bool]):
        self._masking_active = masking_active

    def available(self) -> bool:
        return bool(self._masking_active())

    async def fill(
        self,
        *,
        call: Callable[..., Awaitable[Any]],
        io: Any,
        observe: Callable[[], Awaitable[UiTree]],
        locate: Callable[[UiTree], UiElement | None],
        secret: Callable[[], str],
    ) -> SensitiveInputReceipt:
        """Toca no campo, garante que está vazio e digita o segredo uma única vez.

        `call` é o `rt.executor.run` do aparelho (fila exclusiva, com timeout e rótulo); `observe` devolve a
        hierarquia fresca; `locate` acha o campo nessa hierarquia; `secret` resolve o valor no último instante.
        """
        if not self.available():
            raise SensitiveInputUnavailable(
                "O mascaramento de log do Appium não está ativo; preenchimento de credencial bloqueado.")

        field = await self._focus_and_clear(call=call, io=io, observe=observe, locate=locate)

        value = secret()
        try:
            if not value:
                raise SensitiveInputError("Credencial vazia.")
            await self._type(call=call, io=io, text=value)
        finally:
            del value                       # solta a referência assim que possível; sem promessa de zeroização

        # `after.password` é SEMPRE verdadeiro aqui — `locate` só devolve campo com esse atributo —, então incluí-lo
        # anulava a condição inteira e a trava virava código morto. O único sinal que distingue os dois casos é o
        # texto: com conteúdo, a hierarquia devolve a máscara; vazio, devolve vazio (é o mesmo critério de
        # `_is_empty`). Sem esta guarda, digitação que não chega ao campo de senha passa despercebida — e o segredo
        # pode ter ido parar no campo ao lado, em texto claro, e seguir no envio.
        after = locate(await observe())
        if after is None or not after.text:
            raise SensitiveInputError("O campo sensível continuou vazio depois da digitação.")
        log.info("entrada sensível concluída no campo %s", field.resource_id or field.class_name)
        return SensitiveInputReceipt(field=field.resource_id or field.class_name)

    # ------------------------------------------------------------------ internos
    async def _focus_and_clear(self, *, call, io, observe, locate) -> UiElement:  # type: ignore[no-untyped-def]
        field = locate(await observe())
        if field is None:
            raise SensitiveInputError("Campo sensível não encontrado na tela.")
        for _ in range(CLEAR_ATTEMPTS):
            x, y = field.center
            await self._tap(call=call, io=io, x=x, y=y)
            await self._type(call=call, io=io, text="", clear_first=True)
            field = locate(await observe())
            if field is None:
                raise SensitiveInputError("Campo sensível sumiu da tela durante a limpeza.")
            if self._is_empty(field):
                return field
        raise SensitiveInputError("O campo sensível não ficou vazio; digitação abortada para não concatenar.")

    @staticmethod
    def _is_empty(field: UiElement) -> bool:
        """Campo de senha nunca mostra o valor: a hierarquia troca por máscara quando há conteúdo, então texto
        vazio é a única leitura possível de campo vazio — nos dois casos."""
        return not field.text

    async def _tap(self, *, call, io, x, y) -> None:  # type: ignore[no-untyped-def]
        if not await _ran(call(io.tap, x, y, timeout=20, label="focar campo sensível")):
            raise SensitiveInputError("Falha ao focar o campo sensível.")

    async def _type(self, *, call, io, text: str, clear_first: bool = False) -> None:  # type: ignore[no-untyped-def]
        if not await _ran(call(lambda: io.type_text(text, clear_first=clear_first), timeout=30,
                               label="entrada sensível")):
            raise SensitiveInputError("Falha ao preencher o campo sensível.")
