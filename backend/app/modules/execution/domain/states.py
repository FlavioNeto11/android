"""Máquinas de estado da execução (design §2.4, §6 e §16): execução, objetivo, etapa e tentativa, como TABELAS.

Antes, só a etapa tinha tabela (`taskqueue/states.py`, imposta em `Repository.transition_step`). A execução aceitava
qualquer alvo em `set_run_status`, o objetivo em `set_objective`, e a tabela deles só existia espalhada pelos
chamadores (`RunService`, `Scheduler._apply`, `recompute_run`). Aqui ela fica explícita, derivada do comportamento
ATUAL — cada aresta diz de onde vem —, e a suíte inteira confere que nenhuma transição real cai fora dela
(`tests/conftest.py::_transicoes_dentro_da_tabela`).

Duas fases, como o §16 manda:

1. **conferir e registrar** (agora): `Repository` lê o estado anterior, pergunta `pode(de, para)` e, fora da tabela,
   emite um evento `log` de nível `warn` e conta em `TRANSICOES_FORA_DA_TABELA` — sem bloquear. A etapa é exceção:
   ela já era imposta e continua sendo;
2. **impor** (próximo passo): com a suíte e a produção sem aviso por um ciclo, o aviso vira `InvalidTransition`,
   como a etapa já faz, e cada aresta marcada "reabertura" abaixo é revista (§2.4: `recompute_run` reabre execução
   terminal sem tabela que o declare).

Puro: sem banco, sem `app.models`. Os estados são as strings que o banco grava; `RunStatus` e os outros enums de
`app.models` são `StrEnum` e entram direto em `pode`. `tests/test_maquinas_de_estado.py` garante que o vocabulário
daqui e o dos enums são o mesmo — um estado novo no enum sem linha aqui reprova.

Uma transição de um estado para ele mesmo só é permitida onde está escrita: `recompute_run` reafirma o estado para
atualizar o detalhe, `cancel` repetido reafirma `cancelling`. Liberar `pode(x, x)` em geral esconderia o erro que a
tabela existe para mostrar.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class MaquinaDeEstados:
    """Uma tabela de transições com nome. `pode` é a única regra: o destino está na linha do estado de origem."""

    nome: str
    transicoes: Mapping[str, frozenset[str]]

    @property
    def estados(self) -> frozenset[str]:
        return frozenset(self.transicoes)

    @property
    def terminais(self) -> frozenset[str]:
        """Estados sem saída nenhuma."""
        return frozenset(e for e, destinos in self.transicoes.items() if not destinos)

    def pode(self, de: str, para: str) -> bool:
        """`str()` normaliza: um `StrEnum` vira o próprio valor, o mesmo texto que o banco grava."""
        destinos = self.transicoes.get(str(de))
        return destinos is not None and str(para) in destinos


def _tabela(linhas: Mapping[str, frozenset[str]]) -> Mapping[str, frozenset[str]]:
    return MappingProxyType(dict(linhas))


# ------------------------------------------------------------------ execução (`runs.status`)
#: Nasce `planning` (`Repository.create_run`, INSERT — não é transição). Quem escreve: `RunService` (planejar,
#: iniciar, pausar, retomar, cancelar), `Repository.request_pause` (disjuntor de conta de IA) e `recompute_run`, que
#: DERIVA o estado dos objetivos e é a única que fecha a execução.
RUN_TRANSITIONS: Mapping[str, frozenset[str]] = _tabela({
    # `_plan`: pergunta (faltou dado, recusa do provedor, skill que não compilou), plano pronto para inspeção,
    # falha; `start` direto quando `mode=execute`; `cancel` durante o planejamento. `failed` também é a credencial
    # que não se ligou à execução recém-criada (`RunService.create`).
    "planning": frozenset({"needs_input", "planned", "running", "failed", "cancelled"}),
    # Não há replanejamento: a pessoa reescreve o comando numa execução nova. Só `cancel`.
    "needs_input": frozenset({"cancelled"}),
    "planned": frozenset({"running", "cancelled"}),
    # `pause` (e o disjuntor), `cancel`; `recompute_run` fecha. `cancelled` direto: execução reaberta que já tinha
    # `cancel_requested` (resolver um item de uma execução cancelada) fecha como cancelada sem passar por
    # `cancelling` de novo.
    "running": frozenset({"paused", "cancelling", "completed", "completed_with_issues", "cancelled"}),
    # `resume`, `cancel`; e `recompute_run` fecha a execução pausada quando o trabalho em curso termina.
    "paused": frozenset({"running", "cancelling", "completed", "completed_with_issues", "cancelled"}),
    # `cancel` repetido reafirma; `Scheduler._finish_cancel` → `recompute_run` fecha.
    "cancelling": frozenset({"cancelling", "cancelled", "completed", "completed_with_issues"}),
    "completed": frozenset(),
    # A execução "em aberto" (há item aguardando pessoa): `recompute_run` reafirma com o detalhe novo, fecha como
    # `completed` quando a pessoa confirma o último item, e REABRE (`running`/`paused`) quando um item é retomado —
    # a reabertura que o §2.4 aponta. `cancel` é permitido aqui (`RunService.cancel`).
    "completed_with_issues": frozenset({"completed_with_issues", "completed", "running", "paused", "cancelling"}),
    # Reabertura: resolver (repetir) um item incerto/falho de uma execução cancelada; e reafirmação do detalhe.
    "cancelled": frozenset({"cancelled", "running", "paused"}),
    "failed": frozenset(),
})

# ------------------------------------------------------------------ objetivo (`objectives.status`)
#: Nasce `pending` (`Repository.materialize`). `Objective` é a raiz do agregado de execução (§6): é o que o
#: `Scheduler` despacha. Quem escreve: `Scheduler` (`_tick` → `_block`, `_work`, `_apply`, `_maybe_complete`,
#: `_settle_items`, `_fail_objective`, `_finish_cancel`), `RunService` (`start`, `cancel`, `_requeue`, `resolve`)
#: e `Repository.resume_objective` (aprovação decidida).
OBJECTIVE_TRANSITIONS: Mapping[str, frozenset[str]] = _tabela({
    # `_work` assume; `_block` (aparelho, app, sessão, política) e o pré-voo do `start`; `_maybe_complete`/
    # `_settle_items` fecham; `cancel`/`_finish_cancel` cancelam.
    "pending": frozenset({"running", "waiting_user", "succeeded", "failed", "cancelled"}),
    # `_apply` (espera por pessoa, incerto, aparelho retido), `_maybe_complete`, falha, cancelamento; `_block`
    # (o `_tick` também olha objetivos em `running`).
    "running": frozenset({"waiting_user", "uncertain", "succeeded", "failed", "cancelled"}),
    # Aprovação decidida (`resume_objective`) e "repetir" voltam para a fila; "confirmar concluído" segue
    # (`running`); "abandonar" falha; `_finish_cancel` cancela.
    "waiting_user": frozenset({"pending", "running", "failed", "cancelled"}),
    # Só por decisão da pessoa (`resolve`): repetir, confirmar ou abandonar. Nada reenvia sozinho.
    "uncertain": frozenset({"pending", "running", "failed"}),
    # "Tentar novamente" (`retry_failed`/`resolve`); "abandonar" um item já falho reafirma a falha com o motivo.
    "failed": frozenset({"pending", "failed"}),
    "succeeded": frozenset(),
    "cancelled": frozenset(),
})

# ------------------------------------------------------------------ etapa (`steps.status`)
#: A tabela que já existia em `taskqueue/states.py` (lá ela continua sendo IMPOSTA por `check_transition`, com os
#: enums; é derivada desta). Nasce `pending` (`_insert_steps`).
STEP_TRANSITIONS: Mapping[str, frozenset[str]] = _tabela({
    "pending": frozenset({"ready", "cancelled", "skipped"}),
    # ready → retry_wait: represada pelo limite do perfil ANTES de ser assumida. Nenhuma tentativa foi consumida e
    # nenhuma chamada de modelo foi gasta; `promote()` traz de volta para `ready` quando o prazo vence. Sem esta
    # transição, represar levantava InvalidTransition, o worker morria e ressuscitava em laço quente.
    "ready": frozenset({"running", "retry_wait", "cancelled", "skipped", "waiting_user"}),
    # running → ready: cedeu num ponto seguro (pausa/controle manual) SEM efeito externo pendente
    # running/verifying → skipped: a etapa OPCIONAL (31.36) que não se comprovou é pulada e o objetivo segue.
    "running": frozenset({"verifying", "retry_wait", "waiting_user", "failed", "uncertain", "cancelled", "ready",
                          "skipped"}),
    "verifying": frozenset({"succeeded", "retry_wait", "waiting_user", "failed", "uncertain", "cancelled", "ready",
                            "skipped"}),
    "retry_wait": frozenset({"ready", "cancelled", "skipped"}),
    # decisões do usuário
    "waiting_user": frozenset({"ready", "succeeded", "failed", "cancelled", "skipped"}),
    "uncertain": frozenset({"ready", "succeeded", "failed", "cancelled"}),
    "failed": frozenset({"ready"}),            # "tentar novamente os elegíveis"
    "succeeded": frozenset(),
    "cancelled": frozenset(),
    "skipped": frozenset(),
})

# ------------------------------------------------------------------ tentativa (`attempts.status`)
#: Nasce `running` (`Repository.claim_step`, INSERT) e fecha uma vez: `finish_attempt` (cercado pela posse da etapa)
#: ou `Scheduler._reconciliar` (`WHERE status='running'` → `interrupted`). Nenhuma tentativa reabre: a próxima é
#: outra linha (`<etapa>:a<n+1>`).
ATTEMPT_TRANSITIONS: Mapping[str, frozenset[str]] = _tabela({
    "running": frozenset({"succeeded", "failed", "interrupted", "uncertain", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "interrupted": frozenset(),
    "uncertain": frozenset(),
    "cancelled": frozenset(),
})

RUN = MaquinaDeEstados("run", RUN_TRANSITIONS)
OBJECTIVE = MaquinaDeEstados("objective", OBJECTIVE_TRANSITIONS)
STEP = MaquinaDeEstados("step", STEP_TRANSITIONS)
ATTEMPT = MaquinaDeEstados("attempt", ATTEMPT_TRANSITIONS)
MAQUINAS: tuple[MaquinaDeEstados, ...] = (RUN, OBJECTIVE, STEP, ATTEMPT)
