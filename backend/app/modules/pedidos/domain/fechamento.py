"""Fechamento: em que estado a ocorrência fica, dado o que a execução dela virou (docs/design/pedidos-laco.md §5.3).

Uma função só por pergunta, idempotente, chamada pela varredura do laço (e, no caminho rápido, depois que o gancho
`on_run_settled` acorda o laço). O gancho não vê todo fim de execução (achado A1): falha no planejamento, cancelamento
antes de iniciar ou sem worker vivo assentam sem ele. Por isso a varredura é o caminho NORMAL desses casos.

| execução                                    | ocorrência                                           | motivo                                  |
| `planning`, `planned`, `needs_input`        | fica `despachada`                                    | —                                       |
| `running`, `paused`, `cancelling`           | `despachada → rodando` (`iniciada_em`)               | —                                       |
| `completed`                                 | `concluida`                                          | —                                       |
| `completed_with_issues` com `uncertain`     | `incerta`                                            | `objetivo incerto: {ids}`               |
| `completed_with_issues` sem `uncertain`     | `falhou`                                             | `parcial: {ok} de {total}`              |
| `awaiting_person` (29.93)                   | como `completed_with_issues` (`incerta` ou `falhou`) | idem                                    |
| `failed`                                    | `falhou`                                             | `status_detail` da execução             |
| `cancelled`                                 | `cancelada`, ou `perdida` (prazo de início)          | ver abaixo                              |
| linha de `runs` ausente (purga)             | `falhou`                                             | `execução {id} não existe mais`         |

Falha e incerteza nunca viram sucesso: `completed_with_issues` é `falhou` ou `incerta`, jamais `concluida`.

`awaiting_person` (29.93) é a execução cujo objetivo espera um gesto da pessoa, que antes assentava como
`completed_with_issues`. Fecha a ocorrência do mesmo jeito, para o domínio dos pedidos não mudar neste item; o
"esperando você" na ocorrência, se vier, é do 28.40.

**Prazo de início** (§7.5, "atraso na fila"): `despachada` com a execução ainda sem objetivo iniciado há mais de
`prazo_inicio_s` desde o despacho é cancelada pelo laço, que grava a INTENÇÃO (`resumo = 'prazo_inicio'`). O estado
final só é decidido quando a execução assenta: `cancelled` sem objetivo iniciado e com a intenção → `perdida`; se algum
objetivo começou no meio (corrida com o `_tick` do scheduler), vale `cancelada` (ou `incerta`). Uma corrida nunca vira
`perdida` com efeito possível. O instante do despacho é `runs.created_at`: a execução nasce no mesmo gesto que a
marca `despachada`, e não há coluna própria (decisão D2: o 28.4 não tem migração).

Puro: stdlib. Os status chegam como texto (`RunStatus`/`ObjectiveStatus` de `app.models` são `StrEnum`).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

#: `resumo` da ocorrência que o laço grava ao cancelar a execução por prazo de início.
INTENCAO_PRAZO_DE_INICIO = "prazo_inicio"

_EM_PREPARO = frozenset({"planning", "planned", "needs_input"})
_EM_CURSO = frozenset({"running", "paused", "cancelling"})
#: Execução que ainda pode ser cancelada por prazo de início: sem nada rodando de verdade. `needs_input` fica de fora
#: (espera a pessoa; o que fazer é do 28.5).
_ANTES_DE_COMECAR = frozenset({"planning", "planned", "running"})


@dataclass(frozen=True)
class ObjetivoVisto:
    """O que o fechamento precisa de cada objetivo da execução."""
    id: str
    status: str
    iniciado: bool = False       # `objectives.started_at` preenchido


@dataclass(frozen=True)
class Fechamento:
    estado: str                  # o estado novo da ocorrência (`rodando` não é fim: `terminal` diz)
    motivo: str | None = None
    iniciada_em: str | None = None

    @property
    def terminal(self) -> bool:
        return self.estado != "rodando"


def fechar(*, estado_atual: str, run_id: str, run_status: str | None, objetivos: Sequence[ObjetivoVisto],
           status_detail: str | None = None, started_at: str | None = None, intencao: str | None = None,
           pedido_cancelado: bool = False, prazo_inicio_s: int = 0, motivo_de_espera: str | None = None
           ) -> Fechamento | None:
    """O novo estado da ocorrência `despachada`/`rodando`, ou `None` se ela fica como está."""
    if run_status is None:
        return Fechamento("falhou", f"execução {run_id} não existe mais")
    if run_status in _EM_PREPARO:
        return None
    if run_status in _EM_CURSO:
        if estado_atual != "despachada":
            return None
        if intencao == INTENCAO_PRAZO_DE_INICIO and run_status == "cancelling":
            return None        # o laço cancelou pelo prazo: fica `despachada` até assentar (`perdida` só sai daqui)
        return Fechamento("rodando", None, started_at)
    incertos = [o.id for o in objetivos if o.status == "uncertain"]
    if run_status == "completed":
        return Fechamento("concluida")
    if run_status in ("completed_with_issues", "awaiting_person"):
        if incertos:
            return Fechamento("incerta", f"objetivo incerto: {', '.join(incertos)}")
        ok = sum(1 for o in objetivos if o.status == "succeeded")
        return Fechamento("falhou", f"parcial: {ok} de {len(objetivos)}")
    if run_status == "failed":
        return Fechamento("falhou", (status_detail or "").strip() or "a execução falhou")
    if run_status == "cancelled":
        if incertos:
            return Fechamento("incerta", f"objetivo incerto: {', '.join(incertos)}")
        if (intencao == INTENCAO_PRAZO_DE_INICIO and estado_atual == "despachada"
                and not any(o.iniciado for o in objetivos)):
            espera = f": {motivo_de_espera}" if motivo_de_espera else ""
            return Fechamento("perdida", f"não começou em {int(prazo_inicio_s)}s{espera}")
        return Fechamento("cancelada", "pedido cancelado" if pedido_cancelado else "execução cancelada")
    # Status que o modelo não conhece: nunca fechar como sucesso nem como fim; a varredura olha de novo.
    return None


def prazo_de_inicio_vencido(*, estado_atual: str, run_status: str | None, objetivos: Sequence[ObjetivoVisto],
                            despachada_em: datetime, agora: datetime, prazo_inicio_s: int,
                            intencao: str | None = None) -> bool:
    """A ocorrência `despachada` passou de `prazo_inicio_s` sem nenhum objetivo sair de `pending`?

    `objetivos` vazio (a execução ainda planeja) conta como "nenhum começou". Já cancelada pelo prazo (`intencao`
    gravada) não se cancela de novo.
    """
    if estado_atual != "despachada" or intencao == INTENCAO_PRAZO_DE_INICIO or run_status not in _ANTES_DE_COMECAR:
        return False
    if any(o.status != "pending" or o.iniciado for o in objetivos):
        return False
    return (agora - despachada_em).total_seconds() > prazo_inicio_s
