"""30.39: a leitura das reproduções de receita (`application/evidencia_da_receita.py`), do que o executor já grava.

A unidade é a ETAPA, pela tentativa que a fechou: a última tentativa da etapa é a que escreveu `steps.driven_by` (o
executor sobrescreve a cada tentativa com veredito), e é a dela que vale `attempts.recipe_id` (a receita que foi de fato
consultada para decidir: `exerceu(StrategyKind.recipe)`, só em modo `replay`; a sombra de candidata não o grava).

- `driven_by = 'recipe'` e a etapa `succeeded`: a receita levou a etapa até o fim, comprovada (a favor);
- `driven_by IN ('recipe+ai', 'sem_ator')`: a receita foi consultada e a etapa não terminou por ela (contra).

Várias etapas da mesma receita na mesma execução (o `for_each`, uma receita reaproveitada em duas etapas) viram UMA
linha por posição: a chave única do livro é (item, origem, posição) e a origem é a execução.

Onde a regra por etapa e o contador `replay_ok/replay_fail` divergem (a evidência é a verdade datada, o contador é o
histórico): o contador soma por TENTATIVA com veredito, a regra vê a etapa pela última; a etapa de `for_each` soma N
no contador e 1 na linha; e uma tentativa que consultou a receita mas fechou por atalho sem agir nem divergir
(`sem_ator` sem `replayed` nem `diverged`) é contra na linha e não soma `replay_fail`.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.db import Database, Row
from app.models import RUN_SEM_TRABALHO
from app.modules.learning.application.evidencia_da_receita import ReproducaoDaReceita
from app.modules.learning.domain.vocabulario import Posicao
from app.modules.learning.infrastructure import linhas

_ETAPAS = (
    "SELECT a.recipe_id AS receita, s.run_id, s.driven_by, s.seq, s.key, s.instance_id, s.finished_at,"
    " r.simulated, c.app_version"
    " FROM steps s JOIN attempts a ON a.step_id = s.id JOIN runs r ON r.id = s.run_id"
    " LEFT JOIN recipes c ON c.id = a.recipe_id"
    " WHERE a.recipe_id IS NOT NULL"
    " AND a.number = (SELECT MAX(x.number) FROM attempts x WHERE x.step_id = s.id)"
    " AND s.driven_by IN ('recipe', 'recipe+ai', 'sem_ator')"
    " AND (s.driven_by <> 'recipe' OR s.status = 'succeeded')")
_ORDEM = " ORDER BY s.run_id, a.recipe_id, s.plan_version, s.seq, s.id"
#: A etapa ainda sem a linha da sua posição: a mesma conta do agrupamento, feita em SQL para o passo não reler tudo.
_SEM_LINHA = (" AND NOT EXISTS (SELECT 1 FROM learning_evidence e WHERE e.item_ref = 'receita:' || CAST(a.recipe_id AS TEXT)"
              " AND e.origin_ref = 'reproducao:' || s.run_id"
              " AND e.stance = CASE WHEN s.driven_by = 'recipe' THEN 'for' ELSE 'against' END)")


def _texto_da_etapa(r: Row, favor: bool) -> str:
    etapa = f"etapa {linhas.inteiro(r, 'seq')} ({linhas.texto(r, 'key')})"
    if favor:
        return f"{etapa}: reproduzida"
    return (f"{etapa}: divergiu, a IA assumiu" if linhas.texto(r, "driven_by") == "recipe+ai"
            else f"{etapa}: divergiu, fechada sem a IA decidir")


def agrupar(rows: Sequence[Row]) -> list[ReproducaoDaReceita]:
    """Uma reprodução por (receita, execução, posição). A primeira etapa (ordem do plano) dá o texto e o aparelho; a
    data é a do fim da mais recente."""
    grupos: dict[tuple[int, str, Posicao], list[Row]] = {}
    for r in rows:
        posicao = Posicao.FOR if linhas.texto(r, "driven_by") == "recipe" else Posicao.AGAINST
        grupos.setdefault((linhas.inteiro(r, "receita"), linhas.texto(r, "run_id"), posicao), []).append(r)
    saida: list[ReproducaoDaReceita] = []
    for (receita, run_id, posicao), etapas in grupos.items():
        primeira = etapas[0]
        detalhe = _texto_da_etapa(primeira, posicao is Posicao.FOR)
        if len(etapas) > 1:
            detalhe += f" (e mais {len(etapas) - 1} da mesma receita nesta execução)"
        fins = [f for e in etapas if (f := linhas.texto_ou_nulo(e, "finished_at"))]
        saida.append(ReproducaoDaReceita(
            receita=receita, run_id=run_id, posicao=posicao, simulada=bool(linhas.inteiro(primeira, "simulated")),
            detalhe=detalhe, aparelho=linhas.texto_ou_nulo(primeira, "instance_id"),
            app_version=linhas.texto_ou_nulo(primeira, "app_version"), em=max(fins) if fins else None))
    return saida


class ReproducoesSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def da_execucao(self, run_id: str) -> list[ReproducaoDaReceita]:
        return agrupar(self._db.query(_ETAPAS + " AND s.run_id = ?" + _ORDEM, (run_id,)))

    def faltantes(self) -> list[ReproducaoDaReceita]:
        # 29.93: com `awaiting_person`, que antes era `completed_with_issues` e entrava aqui do mesmo jeito.
        terminais = tuple(sorted(str(x.value) for x in RUN_SEM_TRABALHO))
        achadas = agrupar(self._db.query(
            _ETAPAS + f" AND r.status IN ({linhas.marcas(len(terminais))})" + _SEM_LINHA + _ORDEM, terminais))
        # as mais novas primeiro, sem data por último: é com elas que o passo gasta a folga da retenção
        return sorted(achadas, key=lambda x: x.em or "", reverse=True)

    def linhas_por_receita(self) -> dict[int, int]:
        saida: dict[int, int] = {}
        for r in self._db.query("SELECT item_ref, COUNT(*) AS n FROM learning_evidence WHERE item_ref LIKE 'receita:%'"
                                " GROUP BY item_ref"):
            _, _, ref = linhas.texto(r, "item_ref").partition(":")
            if ref.isdigit():
                saida[int(ref)] = linhas.inteiro(r, "n")
        return saida


__all__ = ["ReproducoesSql", "agrupar"]
