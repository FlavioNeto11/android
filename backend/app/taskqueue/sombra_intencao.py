"""Liga a sombra da intenção (R2 e R3, item 31.9) ao serviço de execuções, sem tocar a cadeia de resolução.

`RunService` chama `agendar` UMA vez, quando o `_plan` de uma execução termina bem (não cancelado, sem exceção, sem recusa
do provedor). No laço de eventos fica SÓ o agendamento: ler a execução, a RESOLVE, o catálogo (habilidades publicadas e
fluxos ativos, respeitando `skills.enabled` e `ai.flows`) e a chamada à porta rodam numa thread solta; o plano já acabou e
nada espera por ela. Desligado (`ativo()` falso: config padrão, envio não aprovado ou C3 fora das classes), nada é feito.

A RESOLVE é refeita (`resolve_intent` não tem efeito: não grava, não chama IA, não cria execução). Por quê: o `_plan` tem
vários pontos de saída e o enxerto no núcleo precisa ser uma linha só. A diferença possível é o catálogo ter mudado nos
segundos entre o plano e a sombra; a sombra mede a cadeia como ela está AGORA.

Desde o 31.13 o mesmo gancho leva a sombra dos apps do comando (R5, `ligar_apps`), travada no código até o GO do 31.10
(`privacidade.R5_LIBERADA`). Cada consumidor só custa quando está ativo: a intenção desligada não resolve nem lê o catálogo,
e a R5 travada não lê o cadastro de apps.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence

from ..modules.skills.domain.intent import IntentResolution, ResolutionStatus, StageOutcome
from ..modules.skills.domain.lifecycle import SkillState
from ..modules.skills.domain.versions import SkillDefinition, SkillSummary
from ..planning.apps_do_comando import apps_citados, nomes_do_app
from ..planning.decisao_fechada.apps import AppDeclarado, ConsumidorDeApps
from ..planning.decisao_fechada.intencao import CadeiaObservada, ConsumidorDeIntencao, EntradaDeCatalogo
from ..planning.decisao_fechada.porta import Porta
from ..planning.decisao_fechada.sombra import RepositorioDeSombra
from ..planning.provider import AppContext

log = logging.getLogger("poc.ai")

#: O que a sombra precisa da execução: o comando SEM destinos, as personas por aparelho e o app principal; desde a
#: rodada E do 31.9, opcionalmente o comando ORIGINAL (com destinos), em que a C7 também é conferida; e, desde a rodada F,
#: os nomes do catálogo de destinos real (personas, handles e aparelhos), que desfazem o "com X" do original (F-B).
DadosDaExecucao = (tuple[str, Sequence[str | None], str | None] | tuple[str, Sequence[str | None], str | None, str]
                   | tuple[str, Sequence[str | None], str | None, str, Sequence[str]])
#: Uma linha do cadastro de apps como a sombra a lê (`AppRepository.listar`): só `id`, `name` e `package` importam.
LinhaDeApp = Mapping[str, object]


def cadeia_de(resolucao: IntentResolution) -> CadeiaObservada:
    """A resolução da cadeia real em ids de habilidade. `candidates` só vem preenchido num empate (ou depois dele); a
    contagem de etapas AMBIGUOUS sai da trilha (RA-2)."""
    skill = resolucao.skill
    return CadeiaObservada(
        resolvida=skill.ref.skill_id if skill is not None else None,
        sem_casamento=resolucao.status is ResolutionStatus.NO_MATCH,
        empatados=tuple(c.ref.skill_id for c in resolucao.candidates),
        ambiguos=sum(1 for t in resolucao.trace if t.outcome is StageOutcome.AMBIGUOUS))


def apps_de(linhas: Sequence[LinhaDeApp]) -> list[AppContext]:
    """O cadastro como a leitura de apps citados o vê (`RunService._apps_configurados`): só o que ela lê."""
    return [AppContext(str(r["id"]), str(r.get("name") or ""), str(r.get("package") or ""), None, None, None)
            for r in linhas if r.get("id")]


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
        self._apps: ConsumidorDeApps | None = None
        self._listar_apps: Callable[[], Sequence[LinhaDeApp]] = lambda: ()
        self._soltas: set[asyncio.Future[None]] = set()

    def ligar_apps(self, porta: Porta, repositorio: RepositorioDeSombra, listar: Callable[[], Sequence[LinhaDeApp]]) -> None:
        """Liga a R5 (31.13) no mesmo gancho. Travada no código (`privacidade.R5_LIBERADA`): ligar não faz nada até o GO."""
        self._apps = ConsumidorDeApps(porta, repositorio)
        self._listar_apps = listar

    def _intencao_ativa(self) -> bool:
        return self._consumidor.ativo()

    def _apps_ativos(self) -> bool:
        return self._apps is not None and self._apps.ativo()

    def ativo(self) -> bool:
        return self._intencao_ativa() or self._apps_ativos()

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
            comando, profile_ids, app = dados[:3]
            original = dados[3] if len(dados) > 3 else None
            destinos = tuple(dados[4]) if len(dados) > 4 else ()
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: não foi possível ler a execução para a sombra da intenção")
            return
        if self._intencao_ativa():
            self._observar_intencao(run_id, comando, profile_ids, app, original, destinos)
        if self._apps_ativos():
            self._observar_apps(run_id, comando, original, destinos)

    def _observar_intencao(self, run_id: str, comando: str, profile_ids: Sequence[str | None], app: str | None,
                           original: str | None, destinos: tuple[str, ...]) -> None:
        try:
            cadeia = cadeia_de(self._resolver(comando, profile_ids))
            catalogo = tuple(self._catalogo())
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: não foi possível ler a execução para a sombra da intenção")
            return
        self._consumidor.observar(run_id=run_id, comando=comando, app=app, catalogo=catalogo, cadeia=cadeia,
                                  original=original, destinos=destinos)

    def _observar_apps(self, run_id: str, comando: str, original: str | None, destinos: tuple[str, ...]) -> None:
        """A R5: o controle é a leitura de apps citados sobre o comando ORIGINAL, o texto que o roteamento real lê
        (`RunService._app_do_comando`); o Jev vê o comando sem destinos e filtrado."""
        if self._apps is None:
            return
        try:
            apps = apps_de(self._listar_apps())
            citados = [str(a.id) for a in apps_citados(original or comando, apps) if a.id]
            declarados = [AppDeclarado(str(a.id), tuple(nomes_do_app(a))) for a in apps if a.id]
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: não foi possível ler o cadastro para a sombra dos apps do comando")
            return
        self._apps.observar(run_id=run_id, comando=comando, apps=declarados, citados=citados, original=original,
                            destinos=destinos)

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


__all__ = ["DadosDaExecucao", "LinhaDeApp", "SombraDaIntencao", "apps_de", "cadeia_de", "catalogo_de"]
