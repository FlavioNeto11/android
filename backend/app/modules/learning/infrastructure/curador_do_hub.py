"""O adaptador da porta `CuradorDeIA` que passa pelo HUB de IA (item 30.12, frente Jev; `hub-de-ia-fora-de-execucao.md`
§2). Substitui o simulado do 30.11 quando o `AppState` o liga; o modo (`aprendizado.curador.modo`) continua `off` de
fábrica e nada roda sem ele.

- **Um caminho só:** `AIRouter.review_knowledge` (papel `plan` emprestado, `origem="curador"`, `ref` = `dossie_hash`).
  O teto do dia e a fatia do curador (31.6) cortam ali, com `AIError(kind="budget")`, que vira `RecusaDoProvedor`
  com o mesmo `kind`: o lote do curador para sem nova tentativa em laço.
- **O custo é MEDIDO:** a linha vai a `ai_calls` por `add_usage` (como a orquestração e o ensino) e o `usd` da resposta
  é o da própria linha, lido de volta pela tarifa de `ai.prices`. Sem chamada (hub simulado), `usd` e `ai_call_id`
  ficam `None`: nada foi medido.
- **Ponte de thread:** a volta do curador roda numa thread (`asyncio.to_thread`) e o hub é assíncrono; a chamada vai
  ao laço do processo por `run_coroutine_threadsafe`. Sem laço no ar (teste, desligamento), é recusa, nunca bloqueio.
- `simulado` é o da RESPOSTA (`ParecerBruto.simulado`), não o do hub: o roteador com papéis mistos diz `simulated = False`
  mesmo quando o `plan` é simulado. Parecer simulado é gravado com `simulated = 1` e nunca vira aviso.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Callable
from typing import Protocol

from app.db import Database
from app.modules.learning.application.ports import PedidoDeRevisao, RecusaDoProvedor, RespostaDeRevisao
from app.planning import costs
from app.planning.curador import ParecerBruto, PedidoDeParecer
from app.planning.provider import AIError, Usage

log = logging.getLogger("poc.aprendizado")

#: Acima disto a volta do curador desiste da resposta (o hub tem o próprio timeout por função; este só protege a thread).
TIMEOUT_S = 300.0


class HubDoCurador(Protocol):
    simulated: bool

    async def review_knowledge(self, req: PedidoDeParecer) -> tuple[ParecerBruto, Usage]: ...


class CuradorDoHub:
    def __init__(self, hub: HubDoCurador, db: Database, *, registrar_uso: Callable[[Usage], None],
                 precos: Callable[[], dict[str, list[float]]], timeout_s: float = TIMEOUT_S) -> None:
        self._hub = hub
        self._db = db
        self._registrar_uso = registrar_uso
        self._precos = precos
        self._timeout_s = timeout_s
        self._laco: asyncio.AbstractEventLoop | None = None
        self._provedor = "hub"
        self._simulado = bool(getattr(hub, "simulated", False))

    def ligar_laco(self, laco: asyncio.AbstractEventLoop) -> None:
        """O laço do processo, que o `AppState` passa ao subir (a volta do curador chama daqui de uma thread)."""
        self._laco = laco

    @property
    def provedor(self) -> str:
        """Quem respondeu a última revisão (a volta é sequencial: a gravação vem logo depois da resposta)."""
        return self._provedor

    @property
    def simulado(self) -> bool:
        """Se a última revisão veio de provedor simulado (antes da primeira, o que o hub declara)."""
        return self._simulado

    def revisar(self, pedido: PedidoDeRevisao) -> RespostaDeRevisao:
        laco = self._laco
        if laco is None or laco.is_closed() or not laco.is_running():
            raise RecusaDoProvedor("hub de IA fora do ar (sem laço do processo)", kind="unavailable")
        req = PedidoDeParecer(dossie=pedido.dossie, opcoes={k: list(v) for k, v in pedido.opcoes.items()},
                              classe=pedido.classe, modelo_sugerido=pedido.modelo_sugerido, ref=pedido.dossie_hash)
        fut = asyncio.run_coroutine_threadsafe(self._hub.review_knowledge(req), laco)
        try:
            parecer, usage = fut.result(timeout=self._timeout_s)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise RecusaDoProvedor(f"o hub não respondeu em {self._timeout_s:.0f} s", kind="timeout") from None
        except AIError as e:
            raise RecusaDoProvedor(str(e), kind=e.kind) from e
        self._simulado = parecer.simulado
        self._provedor = usage.provider or ("simulated" if parecer.simulado else "hub")
        ai_call_id, usd = self._medir(usage, pedido.dossie_hash)
        return RespostaDeRevisao(bruto=dict(parecer.bruto), probabilidade=parecer.probabilidade,
                                 modelo=parecer.modelo or usage.model, usd=usd, ai_call_id=ai_call_id,
                                 simulado=parecer.simulado)

    def _medir(self, usage: Usage, ref: str) -> tuple[int | None, float | None]:
        """Grava a linha e devolve (id, US$) dela. Contabilizar nunca derruba o parecer que já custou."""
        if not usage.calls and not usage.input_tokens:
            return None, None
        try:
            self._registrar_uso(usage)
            linha = self._db.one(
                "SELECT * FROM ai_calls WHERE origem = 'curador' AND ref = ? ORDER BY id DESC LIMIT 1", (ref,))
        except Exception:  # noqa: BLE001
            log.exception("aprendizado: não foi possível registrar o custo do curador")
            return None, None
        if linha is None:
            return None, None
        return int(linha["id"]), costs.row_usd(self._precos(), linha)


__all__ = ["TIMEOUT_S", "CuradorDoHub", "HubDoCurador"]
