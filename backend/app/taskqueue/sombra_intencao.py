"""Liga a sombra da intenção (R2 e R3, item 31.9) ao serviço de execuções, sem tocar a cadeia de resolução.

`RunService` chama `agendar` UMA vez, quando o `_plan` de uma execução termina bem (não cancelado, sem exceção, sem recusa
do provedor). No laço de eventos fica SÓ o agendamento: ler a execução, a RESOLVE, o catálogo (habilidades publicadas e
fluxos ativos, respeitando `skills.enabled` e `ai.flows`) e a chamada à porta rodam numa thread solta; o plano já acabou e
nada espera por ela. Desligado (`ativo()` falso: config padrão, envio não aprovado ou C3 fora das classes), nada é feito.

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

#: O que a sombra precisa da execução: o comando SEM destinos, as personas por aparelho e o app principal.
DadosDaExecucao = tuple[str, Sequence[str | None], str | None]


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

    def agendar(self, run_id: str, ler: Callable[[], DadosDaExecucao | None]) -> None:
        """Chamado no laço, depois do `_plan`. Só agenda: `ler` (a execução: comando sem destinos, personas e app, ou `None`
        para não observar), a RESOLVE, o catálogo e a porta rodam todos numa thread."""
        if not self.ativo():
            return
        try:
            tarefa = asyncio.ensure_future(asyncio.to_thread(self._observar, run_id, ler))
        except Exception:  # noqa: BLE001 - observar nunca derruba o trabalho
            log.warning("decisao_fechada: não foi possível agendar a sombra da intenção")
            return
        self._soltas.add(tarefa)
        tarefa.add_done_callback(self._soltas.discard)

    def _observar(self, run_id: str, ler: Callable[[], DadosDaExecucao | None]) -> None:
        """Na thread: tudo o que lê banco ou resolve. O `Database` é serializado por trava e já é usado de threads."""
        try:
            dados = ler()
            if dados is None:
                return
            comando, profile_ids, app = dados
            cadeia = cadeia_de(self._resolver(comando, profile_ids))
            catalogo = tuple(self._catalogo())
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: não foi possível ler a execução para a sombra da intenção")
            return
        self._consumidor.observar(run_id=run_id, comando=comando, app=app, catalogo=catalogo, cadeia=cadeia)

    def cancelar(self) -> None:
        """Desligamento: cancela as sombras soltas (o `stop()` do `AppState`, como o `_bg`). A thread que já chamou a porta
        termina sozinha; o resultado dela é descartado."""
        for tarefa in list(self._soltas):
            tarefa.cancel()

    async def aguardar(self, timeout_s: float | None = None) -> None:
        """Espera as sombras soltas terminarem (testes e desligamento limpo). No desligamento isto vem ANTES do `cancelar`:
        cancelar só solta o `Task` que embrulha a thread, que segue lendo o banco e chamando a porta; esperar é o que garante
        que ela terminou antes do `db.close`. `timeout_s` é o que sobra do prazo do desligamento: estourou, as que restam
        ficam para o `cancelar` e não seguram o encerramento (`asyncio.wait`, diferente do `gather`, não cancela no prazo)."""
        soltas = list(self._soltas)
        if not soltas:
            return
        prontas, _ = await asyncio.wait(soltas, timeout=timeout_s)
        for t in prontas:                                # recolhe a exceção: ninguém mais a lê
            if not t.cancelled():
                t.exception()


__all__ = ["DadosDaExecucao", "SombraDaIntencao", "cadeia_de", "catalogo_de"]
