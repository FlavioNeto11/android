"""O que a aprovação automática (30.55) lê e grava fora do Livro, sobre as tabelas que já existem, sem migração.

- O caso da sombra é um sinal `kind = aprovaria`, `source_ref = aprovaria:<item>`, `created_by = sistema`: o índice
  único `(kind, source_ref, created_by)` faz dele UMA linha por item.
- A decisão da plataforma é a linha da trilha com `decided_by = plataforma` (`learning_transitions`): é o registro e o
  que diz que ela já decidiu o item (o que o dono desfez não volta a ser decidido por ela).
- Os pacotes de teste são os de `apps.category = 'qa'`; os contadores da receita são os da própria fonte (`recipes`).
"""
from __future__ import annotations

from app.db import Database
from app.modules.learning.application.aprovacao_automatica import ContadoresDaReceita, DecisaoDaPlataforma
from app.modules.learning.application.ports import NovoSinal, RepositorioDeAprendizado
from app.modules.learning.domain.aprovacao_automatica import PLATAFORMA
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR
from app.modules.learning.domain.vocabulario import Polaridade, SignalKind
from app.modules.learning.infrastructure import linhas

PREFIXO = "aprovaria:"


class LivroDaAprovacaoSql:
    def __init__(self, db: Database, repo: RepositorioDeAprendizado) -> None:
        self._db = db
        self._repo = repo

    def pacotes_de_teste(self) -> frozenset[str]:
        return frozenset(p for r in self._db.query("SELECT package FROM apps WHERE category='qa'")
                         if (p := linhas.texto_ou_nulo(r, "package")))

    def contadores_da_receita(self, ref: str) -> ContadoresDaReceita | None:
        if not ref.isdigit():
            return None
        r = self._db.one("SELECT replay_ok, replay_fail, shadow_agree, shadow_total FROM recipes WHERE id=?",
                         (int(ref),))
        if r is None:
            return None
        return ContadoresDaReceita(replay_ok=linhas.inteiro(r, "replay_ok"), replay_fail=linhas.inteiro(r, "replay_fail"),
                                   sombra_acertos=linhas.inteiro(r, "shadow_agree"),
                                   sombra_total=linhas.inteiro(r, "shadow_total"))

    def ja_decididos(self) -> frozenset[str]:
        return frozenset(linhas.texto(r, "item_ref") for r in self._db.query(
            "SELECT DISTINCT item_ref FROM learning_transitions WHERE decided_by=?", (PLATAFORMA,)))

    def decisoes(self, limite: int) -> list[DecisaoDaPlataforma]:
        return [DecisaoDaPlataforma(item_ref=linhas.texto(r, "item_ref"), de=linhas.texto_ou_nulo(r, "from_state"),
                                    para=linhas.texto(r, "to_state"), motivo=linhas.texto(r, "reason"),
                                    em=linhas.texto(r, "decided_at"))
                for r in self._db.query("SELECT item_ref, from_state, to_state, reason, decided_at FROM learning_transitions"
                                        " WHERE decided_by=? ORDER BY id DESC LIMIT ?", (PLATAFORMA, int(limite)))]

    def marcar(self, item_ref: str, dados: dict[str, object], *, app: str | None) -> bool:
        novo = self._repo.registrar_sinal(NovoSinal(
            kind=SignalKind.APROVARIA, source_ref=f"{PREFIXO}{item_ref}", created_by=SYSTEM_ACTOR,
            polarity=Polaridade.NEUTRAL, app_package=app or "", data=dados))  # type: ignore[arg-type]
        return novo is not None

    def marcados(self) -> int:
        r = self._db.one("SELECT COUNT(*) AS n FROM learning_signals WHERE kind=? AND created_by=?",
                         (SignalKind.APROVARIA.value, SYSTEM_ACTOR))
        return int(r["n"]) if r is not None else 0


__all__ = ["PREFIXO", "LivroDaAprovacaoSql"]
