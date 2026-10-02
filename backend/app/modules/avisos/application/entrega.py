"""A entrega dos avisos: tira uma linha da fila, chama o canal e grava o desfecho (item 28.11).

Duas regras que explicam o formato:

1. **Uma linha de cada vez.** A reivindicação (`pendente` → `enviando`) acontece ANTES da chamada de rede e vale só
   para a linha que vai ser enviada agora. Reivindicar um lote e parar no meio (um 429, uma queda) deixaria linhas
   `enviando` que nunca foram tentadas — e `enviando` abandonado vira `incerto`, não é reenviado.
2. **Desfecho nunca é chute.** Sucesso só quando o canal confirmou. Erro vira nova tentativa (com espera), falha
   definitiva ou, esgotado o teto, `falhou`. O que o canal diz de errado passa pela redação do próprio adaptador: o
   texto que chega aqui já não carrega o token.

Camada de aplicação: sem rede, sem banco, sem `asyncio` — o canal e a fila entram por porta e o relógio é argumento.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

#: Teto da espera entre tentativas: um canal fora do ar por horas não pode empurrar o aviso para o dia seguinte.
ESPERA_MAX_S = 3600.0


class FalhaDeEnvio(Exception):
    """O canal não entregou. `motivo` é texto curto e já REDIGIDO (nunca o token nem a URL do bot).

    `espera_s`: o que o canal pediu para esperar (429 + `Retry-After`); `None` = backoff nosso.
    `definitiva`: tentar de novo não adianta (token recusado, chat inexistente, bot bloqueado).
    """

    def __init__(self, motivo: str, *, espera_s: float | None = None, definitiva: bool = False):
        super().__init__(motivo)
        self.motivo = motivo
        self.espera_s = espera_s
        self.definitiva = definitiva


class Canal(Protocol):
    """Por onde o aviso sai. Só saída: nenhum canal recebe nada de volta."""

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> None: ...


@dataclass(frozen=True)
class Entrega:
    id: int
    chave: str
    tipo: str
    titulo: str
    corpo: str
    link: str | None
    tentativas: int


Cerca = Callable[[], AbstractContextManager[None]]


class Fila(Protocol):
    def reivindicar_um(self, *, cerca: Cerca) -> Entrega | None: ...
    def marcar_enviado(self, entrega_id: int) -> None: ...
    def marcar_retentar(self, entrega_id: int, *, ate: datetime, erro: str) -> None: ...
    def marcar_falhou(self, entrega_id: int, *, erro: str) -> None: ...


@dataclass
class Resultado:
    enviados: int = 0
    adiados: int = 0
    falharam: int = 0
    #: O canal pediu para esperar (429): quem chama não volta a bater antes disto. É do CANAL, não da linha — as outras
    #: linhas pendentes esbarrariam no mesmo limite.
    esperar_s: float | None = None


def espera_da_tentativa(tentativas: int, *, base_s: float, pedida_s: float | None) -> float:
    """Quanto esperar depois da `tentativas`-ésima falha: o que o canal pediu, no mínimo; senão backoff exponencial."""
    exponencial = base_s * (2 ** max(0, tentativas - 1))
    return min(ESPERA_MAX_S, max(pedida_s or 0.0, exponencial))


async def entregar(fila: Fila, canal: Canal, *, cerca: Cerca, agora: Callable[[], datetime], limite: int,
                   max_tentativas: int, backoff_s: float) -> Resultado:
    """Envia até `limite` avisos devidos. Para na primeira falha que o canal pediu para esperar (429) e devolve a
    espera em `Resultado.esperar_s`: as próximas linhas pertencem ao mesmo canal e esbarrariam no mesmo limite."""
    r = Resultado()
    for _ in range(max(0, limite)):
        entrega = fila.reivindicar_um(cerca=cerca)
        if entrega is None:
            break
        try:
            await canal.enviar(entrega.titulo, entrega.corpo, entrega.link)
        except FalhaDeEnvio as falha:
            if falha.definitiva or entrega.tentativas >= max_tentativas:
                fila.marcar_falhou(entrega.id, erro=falha.motivo)
                r.falharam += 1
                continue
            espera = espera_da_tentativa(entrega.tentativas, base_s=backoff_s, pedida_s=falha.espera_s)
            fila.marcar_retentar(entrega.id, ate=agora() + timedelta(seconds=espera), erro=falha.motivo)
            r.adiados += 1
            if falha.espera_s is not None:
                r.esperar_s = falha.espera_s
                break
            continue
        fila.marcar_enviado(entrega.id)
        r.enviados += 1
    return r
