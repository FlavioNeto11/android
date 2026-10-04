"""A entrega dos avisos: tira uma linha da fila, chama o canal e grava o desfecho (item 28.11).

Duas regras que explicam o formato:

1. **Uma mensagem de cada vez.** A reivindicação (`pendente` → `enviando`) acontece ANTES da chamada de rede e vale só
   para o que vai sair AGORA: uma linha, ou as linhas de uma rajada que viram UMA mensagem agrupada (28.19). Reivindicar
   um lote de mensagens e parar no meio (um 429, uma queda) deixaria linhas `enviando` que nunca foram tentadas — e
   `enviando` abandonado vira `incerto`, não é reenviado. O custo do agrupado: uma queda no meio do envio deixa as N
   linhas dele `incerto`, não uma; é o mesmo fato (uma mensagem), então é o mesmo "pode ter saído".
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

from app.modules.avisos.domain.mensagem import FAMILIA_DO_GRUPO

#: Teto da espera entre tentativas: um canal fora do ar por horas não pode empurrar o aviso para o dia seguinte.
ESPERA_MAX_S = 3600.0


class FalhaDeEnvio(Exception):
    """O canal não entregou. `motivo` é texto curto e já REDIGIDO (nunca o token nem a URL do bot).

    `espera_s`: o que o canal pediu para esperar (429 + `Retry-After`); `None` = backoff nosso.
    `definitiva`: tentar de novo não adianta (token recusado, chat inexistente, bot bloqueado).
    `status`: o HTTP do canal, quando houve resposta; quem lê decide a orientação por ele (401 não é 400).
    """

    def __init__(self, motivo: str, *, espera_s: float | None = None, definitiva: bool = False,
                 status: int | None = None):
        super().__init__(motivo)
        self.motivo = motivo
        self.espera_s = espera_s
        self.definitiva = definitiva
        self.status = status


class Canal(Protocol):
    """Por onde o aviso sai. Devolve o id da mensagem no canal (o `message_id` do Telegram), ou `None` se o canal
    não informa: é o que liga a resposta (reply) da pessoa ao fato do aviso (28.15)."""

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> int | None: ...


@dataclass(frozen=True)
class Entrega:
    id: int
    chave: str
    tipo: str
    titulo: str
    corpo: str
    link: str | None
    tentativas: int
    #: As linhas que esta mensagem cobre (28.19): vazio = só `id`; com mais de uma, é o aviso agrupado de uma rajada.
    ids: tuple[int, ...] = ()

    @property
    def linhas(self) -> tuple[int, ...]:
        return self.ids or (self.id,)

    @property
    def agrupada(self) -> bool:
        return len(self.linhas) > 1


Cerca = Callable[[], AbstractContextManager[None]]


class Fila(Protocol):
    def reivindicar_um(self, *, cerca: Cerca, agrupar_s: float = 0.0, agrupar_a_partir_de: int = 3) -> Entrega | None: ...
    def marcar_enviado(self, entrega_id: int, *, message_id: int | None = None, fato: str | None = None) -> None: ...
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
                   max_tentativas: int, backoff_s: float, agrupar_s: float = 0.0,
                   agrupar_a_partir_de: int = 3) -> Resultado:
    """Envia até `limite` mensagens devidas. Para na primeira falha que o canal pediu para esperar (429) e devolve a
    espera em `Resultado.esperar_s`: as próximas linhas pertencem ao mesmo canal e esbarrariam no mesmo limite.

    `agrupar_s` > 0 liga o agrupamento das rajadas (28.19; a regra está em `fila_sql.reivindicar_um`). Os contadores do
    `Resultado` contam MENSAGENS: um agrupado de 10 linhas é um enviado."""
    r = Resultado()
    for _ in range(max(0, limite)):
        entrega = fila.reivindicar_um(cerca=cerca, agrupar_s=agrupar_s, agrupar_a_partir_de=agrupar_a_partir_de)
        if entrega is None:
            break
        try:
            message_id = await canal.enviar(entrega.titulo, entrega.corpo, entrega.link)
        except FalhaDeEnvio as falha:
            if falha.definitiva or entrega.tentativas >= max_tentativas:
                for linha in entrega.linhas:
                    fila.marcar_falhou(linha, erro=falha.motivo)
                r.falharam += 1
                continue
            espera = espera_da_tentativa(entrega.tentativas, base_s=backoff_s, pedida_s=falha.espera_s)
            for linha in entrega.linhas:
                fila.marcar_retentar(linha, ate=agora() + timedelta(seconds=espera), erro=falha.motivo)
            r.adiados += 1
            if falha.espera_s is not None:
                r.esperar_s = falha.espera_s
                break
            continue
        if entrega.agrupada:
            # O reply ao agrupado não responde a nenhum fato: a `canal_enviadas` guarda só a família do grupo.
            primeira, *resto = entrega.linhas
            fila.marcar_enviado(primeira, message_id=message_id, fato=f"{FAMILIA_DO_GRUPO}:{entrega.tipo}")
            for linha in resto:
                fila.marcar_enviado(linha)
        else:
            fila.marcar_enviado(entrega.id, message_id=message_id)
        r.enviados += 1
    return r
