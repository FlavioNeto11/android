"""Liga a sombra da intenção (R2 e R3, item 31.9) ao serviço de execuções, sem tocar a cadeia de resolução.

`RunService` chama `agendar` UMA vez, quando o `_plan` de uma execução termina. Aqui, no laço de eventos e sem esperar rede,
só se lê o que a cadeia real já sabe: o catálogo (habilidades publicadas e fluxos ativos, respeitando os interruptores
`skills.enabled` e `ai.flows`) e o resultado da RESOLVE. A chamada à porta, que bloqueia, vai para uma thread solta: o plano
já acabou e nada espera por ela. Com a configuração padrão (`ativo()` falso) este módulo não faz nada.

A RESOLVE é refeita (`resolve_intent` não tem efeito: não grava, não chama IA, não cria execução). Por quê: o `_plan` tem
vários pontos de saída e o enxerto no núcleo precisa ser uma linha só. A diferença possível é o catálogo ter mudado nos
segundos entre o plano e a sombra; a sombra mede a cadeia como ela está AGORA.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence

from ..modules.skills.domain.intent import IntentResolution, ResolutionStatus
from ..modules.skills.domain.lifecycle import SkillState
from ..modules.skills.domain.versions import SkillDefinition, SkillSummary
from ..planning.decisao_fechada.intencao import CadeiaObservada, ConsumidorDeIntencao, EntradaDeCatalogo

log = logging.getLogger("poc.ai")


def cadeia_de(resolucao: IntentResolution) -> CadeiaObservada:
    """A resolução da cadeia real em ids de habilidade. `candidates` só vem preenchido num empate (ou depois dele)."""
    skill = resolucao.skill
    return CadeiaObservada(
        resolvida=skill.ref.skill_id if skill is not None else None,
        sem_casamento=resolucao.status is ResolutionStatus.NO_MATCH,
        empatados=tuple(c.ref.skill_id for c in resolucao.candidates))


def catalogo_de(listar: Callable[[SkillState], Sequence[SkillSummary]],
                definicao: Callable[[str], SkillDefinition | None], *,
                skills_ligadas: bool, fluxos_ligados: bool) -> list[EntradaDeCatalogo]:
    """O catálogo que a cadeia ENXERGA: publicadas (se `skills.enabled`) e fluxos ativos (se `ai.flows`). Nome e descrição
    são do dono (C2); fluxo legado não tem descrição."""
    entradas: list[EntradaDeCatalogo] = []
    for s in listar(SkillState.PUBLISHED):
        if (s.ref.is_legacy and not fluxos_ligados) or (not s.ref.is_legacy and not skills_ligadas):
            continue
        d = None if s.ref.is_legacy else definicao(s.ref.skill_id)
        entradas.append(EntradaDeCatalogo(s.ref.skill_id, s.name, d.description if d is not None else ""))
    return entradas


class SombraDaIntencao:
    def __init__(self, consumidor: ConsumidorDeIntencao, *,
                 resolver: Callable[[str, Sequence[str | None]], IntentResolution],
                 catalogo: Callable[[], Sequence[EntradaDeCatalogo]]) -> None:
        self._consumidor = consumidor
        self._resolver = resolver
        self._catalogo = catalogo
        self._soltas: set[asyncio.Future[None]] = set()

    def ativo(self) -> bool:
        return self._consumidor.ativo()

    def agendar(self, run_id: str, comando: str, profile_ids: Sequence[str | None], app: str | None) -> None:
        """Chamado no laço, depois do `_plan`. Lê o catálogo e a resolução (barato) e solta a consulta numa thread."""
        if not self.ativo():
            return
        try:
            cadeia = cadeia_de(self._resolver(comando, profile_ids))
            catalogo = tuple(self._catalogo())
            tarefa = asyncio.ensure_future(asyncio.to_thread(
                self._consumidor.observar, run_id=run_id, comando=comando, app=app, catalogo=catalogo, cadeia=cadeia))
        except Exception:  # noqa: BLE001 - observar nunca derruba o trabalho
            log.warning("decisao_fechada: não foi possível agendar a sombra da intenção")
            return
        self._soltas.add(tarefa)
        tarefa.add_done_callback(self._soltas.discard)

    async def aguardar(self) -> None:
        """Espera as sombras soltas terminarem (testes e desligamento limpo)."""
        if self._soltas:
            await asyncio.gather(*list(self._soltas), return_exceptions=True)


__all__ = ["SombraDaIntencao", "cadeia_de", "catalogo_de"]
