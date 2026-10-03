"""Liga o rótulo de intenção (30.25) ao serviço do Livro: o minerador no digest e o serviço que a apresentação acha.

O `AppState` chama `ligar` depois de montar o serviço de execuções e a sombra da intenção (31.9), com as mesmas peças
da sombra: `dados` (a execução: comando sem destinos, personas por aparelho e app principal, ou `None` para não
observar), `resolver` (a RESOLVE da cadeia real, sem efeito e sem IA) e `catalogo` (o catálogo que a cadeia enxerga,
respeitando `skills.enabled` e `ai.flows`). Nenhum gancho novo no taskqueue: o digest da execução assentada já roda os
mineradores registrados (`LearningService.digerir_execucao`).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from weakref import WeakKeyDictionary

from app.db import Database
from app.modules.learning.application.intencao import CadeiaDaExecucao, ServicoDeRotulos
from app.modules.learning.application.servico import LearningService
from app.modules.learning.infrastructure.intencao_sql import RotulosSql
from app.modules.skills.domain.intent import IntentResolution
from app.planning.decisao_fechada.intencao import EntradaDeCatalogo
from app.taskqueue.sombra_intencao import DadosDaExecucao, cadeia_de
from app.util import now

_ROTULOS: WeakKeyDictionary[LearningService, ServicoDeRotulos] = WeakKeyDictionary()


def ligar(servico: LearningService, db: Database, *, dados: Callable[[str], DadosDaExecucao | None],
          resolver: Callable[[str, Sequence[str | None]], IntentResolution],
          catalogo: Callable[[], Sequence[EntradaDeCatalogo]],
          relogio: Callable[[], datetime] = now) -> ServicoDeRotulos:
    """Monta o serviço do rótulo deste livro, registra o minerador e o pendura no serviço. Rodar de novo no mesmo
    serviço devolve o que já está ligado."""
    atual = _ROTULOS.get(servico)
    if atual is not None:
        return atual

    def cadeia(run_id: str) -> CadeiaDaExecucao | None:
        lido = dados(run_id)
        if lido is None:
            return None
        comando, perfis, app = lido
        c = cadeia_de(resolver(comando, perfis))
        return CadeiaDaExecucao(resolvida=c.resolvida, sem_casamento=c.sem_casamento, empatados=c.empatados, app=app)

    rotulos = ServicoDeRotulos(servico, RotulosSql(db), cadeia=cadeia,
                               catalogo=lambda: [(e.skill_id, e.nome) for e in catalogo()], relogio=relogio)
    servico.registrar_minerador(rotulos.minerador())
    servico.anexar(rotulos)
    _ROTULOS[servico] = rotulos
    return rotulos


__all__ = ["ligar"]
