"""Decisores da porta: o nulo (padrão), o falso (testes) e o PONTO DE EXTENSÃO do real.

O decisor REAL, que fala com a TypeSafe, NÃO existe nesta etapa. O item 31.8 o pluga depois do ADR-069, reaproveitando o
transporte e a chave do adaptador de retrieval (`modules/context_retrieval/adapters/jev.py`, único arquivo autorizado a
conhecer o host: o teste de cliente único o garante). Contrato que ele cumpre:

- `decidir(pedido, timeout_s)` recebe um pedido JÁ validado e redigido por `Porta`, é síncrono (o POST de hoje é síncrono),
  não retenta, e devolve `ResultadoDeDecisao` ou levanta `FalhaDeDecisao(motivo)` com o motivo fechado;
- `Porta` ainda reconfere cada resposta (opção conhecida, limiar) e aplica o timeout por fora: o decisor não é confiado.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Protocol

from .contrato import (
    FalhaDeDecisao, PedidoDeDecisao, RespostaDeDecisao, ResultadoDeDecisao, resultado_de_fallback,
)


class Decisor(Protocol):
    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao: ...


class DecisorNulo:
    """O padrão: nunca decide e nunca toca rede. Toda pergunta volta com `fallback_reason='desligado'`."""

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        return resultado_de_fallback(pedido, "desligado")


class DecisorFalso:
    """Para testes: respostas programadas por id de pergunta; cada chamada registrada é UMA chamada (fan-out).

    Pergunta sem resposta programada volta com `fallback_reason=desligado`. Uma `FalhaDeDecisao` programada é levantada
    como o decisor real faria. `atraso_s` simula um decisor lento (teste de timeout)."""

    def __init__(self, respostas: Mapping[str, RespostaDeDecisao] | None = None, *, falha: FalhaDeDecisao | None = None,
                 atraso_s: float = 0.0, custo_tokens: int = 0, custo_usd: float = 0.0) -> None:
        self.respostas: dict[str, RespostaDeDecisao] = dict(respostas or {})
        self.falha = falha
        self.atraso_s = atraso_s
        self.custo_tokens = custo_tokens
        self.custo_usd = custo_usd
        #: Cada item é um pedido recebido = uma chamada. Nada de rede: é só o registro.
        self.chamadas: list[PedidoDeDecisao] = []

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        self.chamadas.append(pedido)
        t0 = time.perf_counter()
        if self.atraso_s:
            time.sleep(self.atraso_s)
        if self.falha is not None:
            raise self.falha
        respostas = {p.id: self.respostas.get(p.id, RespostaDeDecisao(fallback_reason="desligado"))
                     for p in pedido.perguntas}
        return ResultadoDeDecisao(respostas, tokens=self.custo_tokens, usd=self.custo_usd,
                                  ms=(time.perf_counter() - t0) * 1000)
