"""As leituras e a gravação do `resultado_posterior` (30.35) sobre as tabelas que já existem, sem migração.

- As revisões são as do CURADOR (`template_id = curador`), válidas e reais, de receita e lição.
- As mudanças do item vêm de `learning_transitions` na janela.
- O uso da receita é a tentativa que ela conduziu (`attempts.recipe_id`): `succeeded` ou `failed`, datada por
  `finished_at`. É a mesma leitura do fechamento da validação (30.31), que move `replay_ok`. `interrupted` e
  `uncertain` não dizem nada da receita e ficam fora.
- O uso da lição é a exposição no braço `with`, com o desfecho preenchido; sucesso é `succeeded` ou `completed`
  (`efeito.SUCESSO`).
- Nos dois, a execução simulada e a marcada como inválida no item (30.23) ficam fora: não provam nada.

O SQL só filtra por texto ISO-8601 e por igualdade (os dois dialetos); as contas ficam no domínio.
"""
from __future__ import annotations

from app.db import Database
from app.modules.learning.application.curador import TEMPLATE_ID
from app.modules.learning.application.resultado_posterior import RevisaoSemDesfecho
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.efeito import SUCESSO
from app.modules.learning.domain.evidencia_invalida import run_invalidada
from app.modules.learning.domain.resultado_posterior import MudancaNaJanela
from app.modules.learning.domain.vocabulario import Braco
from app.modules.learning.infrastructure import linhas

KINDS = ("receita", "licao")


def _estado(texto: str | None) -> SkillState | None:
    try:
        return SkillState(texto) if texto else None
    except ValueError:
        return None


class ResultadoPosteriorSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def abertas(self, criadas_ate: str, limite: int) -> list[RevisaoSemDesfecho]:
        return [RevisaoSemDesfecho(id=linhas.texto(r, "id"), item_ref=linhas.texto(r, "item_ref"),
                                   item_kind=linhas.texto(r, "item_kind"), criada_em=linhas.texto(r, "created_at"))
                for r in self._db.query(
                    "SELECT id, item_ref, item_kind, created_at FROM learning_reviews"
                    " WHERE template_id=? AND validade='ok' AND simulated=0 AND item_kind IN (?, ?)"
                    " AND resultado_posterior IS NULL AND created_at <= ? ORDER BY created_at, id LIMIT ?",
                    (TEMPLATE_ID, *KINDS, criadas_ate, limite))]

    def mudancas(self, item_ref: str, desde: str, ate: str) -> list[MudancaNaJanela]:
        saida: list[MudancaNaJanela] = []
        for r in self._db.query("SELECT from_state, to_state FROM learning_transitions WHERE item_ref=?"
                                " AND decided_at >= ? AND decided_at <= ? ORDER BY decided_at, id",
                                (item_ref, desde, ate)):
            para = _estado(linhas.texto_ou_nulo(r, "to_state"))
            if para is not None:
                saida.append(MudancaNaJanela(de=_estado(linhas.texto_ou_nulo(r, "from_state")), para=para))
        return saida

    def _invalidas(self, item_ref: str) -> set[str]:
        return {x for r in self._db.query("SELECT reason FROM learning_transitions WHERE item_ref=?", (item_ref,))
                if (x := run_invalidada(linhas.texto_ou_nulo(r, "reason"))) is not None}

    def usos(self, item_ref: str, item_kind: str, desde: str, ate: str) -> list[bool]:
        invalidas = self._invalidas(item_ref)
        if item_kind == "receita":
            _, _, ref = item_ref.partition(":")
            if not ref.isdigit():
                return []
            linhas_ = self._db.query(
                "SELECT a.status, s.run_id FROM attempts a JOIN steps s ON s.id = a.step_id"
                " JOIN runs r ON r.id = s.run_id"
                " WHERE a.recipe_id=? AND a.status IN ('succeeded', 'failed') AND r.simulated=0"
                " AND a.finished_at >= ? AND a.finished_at <= ? ORDER BY a.finished_at, a.id",
                (int(ref), desde, ate))
            return [linhas.texto(r, "status") == "succeeded" for r in linhas_
                    if linhas.texto_ou_nulo(r, "run_id") not in invalidas]
        if item_kind == "licao":
            linhas_ = self._db.query(
                "SELECT e.outcome, e.run_id FROM learning_exposures e LEFT JOIN runs r ON r.id = e.run_id"
                " WHERE e.item_id=? AND e.arm=? AND e.outcome IS NOT NULL AND (r.simulated IS NULL OR r.simulated=0)"
                " AND e.filled_at >= ? AND e.filled_at <= ? ORDER BY e.filled_at, e.unit_id",
                (item_ref, Braco.WITH.value, desde, ate))
            return [linhas.texto(r, "outcome") in SUCESSO for r in linhas_
                    if linhas.texto_ou_nulo(r, "run_id") not in invalidas]
        return []

    def gravar(self, review_id: str, valor: str, em: str) -> bool:
        cur = self._db.execute("UPDATE learning_reviews SET resultado_posterior=?, resultado_em=? WHERE id=?"
                               " AND template_id=? AND resultado_posterior IS NULL",
                               (valor, em, review_id, TEMPLATE_ID))
        return (cur.rowcount or 0) == 1


__all__ = ["ResultadoPosteriorSql"]
