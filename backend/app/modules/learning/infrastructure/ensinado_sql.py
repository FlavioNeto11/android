"""`LeitorDoEnsinado` (30.80 B) sobre as fontes nativas: a sessão de treino que ensinou, se outra receita ou fluxo
ativo segura o lugar, e o instante da transição na trilha. Só leitura, dentro da transação de quem mudou o status."""
from __future__ import annotations

from app.db import Database
from app.modules.learning.domain.ensinado import sessao_de_treino
from app.modules.learning.domain.livro import ref_da_trilha
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import linhas


class LeitorDoEnsinadoSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def sessao_de_treino(self, kind: LivroKind, ref: str) -> str | None:
        if kind is LivroKind.RECEITA:
            if not ref.isdigit():
                return None
            row = self._db.one("SELECT learned_from_step AS origem FROM recipes WHERE id=?", (int(ref),))
        elif kind is LivroKind.FLUXO:
            row = self._db.one("SELECT source AS origem FROM flows WHERE id=?", (ref,))
        else:
            return None
        return sessao_de_treino(linhas.texto_ou_nulo(row, "origem")) if row is not None else None

    def tem_ativo_no_lugar(self, kind: LivroKind, ref: str) -> bool:
        if kind is LivroKind.RECEITA:
            if not ref.isdigit():
                return False
            # A mesma família de `RecipeStore._aposentar_legadas`: cada parte da chave é a da receita, ou a receita a
            # tem vazia (a provada da chave COMPLETA atende a consulta que a legada sem assinatura ou variante atendia).
            return self._db.one(
                "SELECT o.id FROM recipes r JOIN recipes o ON o.app_package=r.app_package AND"
                " o.app_version=r.app_version AND (r.app_signature='' OR o.app_signature=r.app_signature) AND"
                " (r.variant='' OR o.variant=r.variant) AND"
                " o.step_hash=r.step_hash AND o.id<>r.id AND o.status='active' WHERE r.id=? LIMIT 1",
                (int(ref),)) is not None
        if kind is LivroKind.FLUXO:
            return self._db.one(
                "SELECT o.id FROM flows f JOIN flows o ON o.match_key=f.match_key AND o.id<>f.id AND"
                " o.status='active' WHERE f.id=? LIMIT 1", (ref,)) is not None
        return False

    def instante_da_transicao(self, kind: LivroKind, ref: str) -> str | None:
        # `id` desempata: duas transições no mesmo instante, vale a última gravada
        row = self._db.one("SELECT decided_at FROM learning_transitions WHERE item_ref=? ORDER BY id DESC LIMIT 1",
                           (ref_da_trilha(kind, ref),))
        return linhas.texto_ou_nulo(row, "decided_at") if row is not None else None


__all__ = ["LeitorDoEnsinadoSql"]
