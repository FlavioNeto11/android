"""`LeitorDoEnsinado` (30.80 B) sobre as fontes nativas: a sessão de treino que ensinou, se outra receita ou fluxo
ativo segura o lugar, e o instante da transição na trilha. Só leitura, dentro da transação de quem mudou o status.

`EnsinoDaValidacaoSql` (30.81): os fluxos ensinados que ainda esperam a prova, numa consulta só por volta da validação.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.db import Database
from app.modules.learning.application.validacao import EnsinadoAProvar
from app.modules.learning.domain.aprovacao_automatica import PLATAFORMA
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR
from app.modules.learning.domain.conteudo import PREFIXO_DE_TREINO
from app.modules.learning.domain.ensinado import EsperaDoEnsinado, sessao_de_treino
from app.modules.learning.domain.livro import CONFIRMADO_QUE_FICA, EntradaDoLivro, ref_da_trilha
from app.modules.learning.domain.validacao import MOTIVOS_QUE_ESPERAM_A_PESSOA, PREFIXO_DO_ENSINO, comando_do_ensino
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import linhas

if TYPE_CHECKING:
    from app.modules.learning.application.servico import LearningService

log = logging.getLogger(__name__)

_ESPERAM = tuple(sorted(m.value for m in MOTIVOS_QUE_ESPERAM_A_PESSOA))
_EM = ",".join("?" * len(_ESPERAM))
#: Só o "Confirmar que fica" explícito de uma pessoa tira o ensinado da espera (a mesma regra de
#: `flows.ensinado_em_prova`): nem o sistema, nem a régua da plataforma, nem um treino, nem outra linha da pessoa.
_DE_PESSOA = "t.reason LIKE ? AND t.decided_by NOT IN (?,?) AND t.decided_by NOT LIKE ?"
_NAO_PESSOAS = (f"{CONFIRMADO_QUE_FICA}%", SYSTEM_ACTOR, PLATAFORMA, f"{PREFIXO_DE_TREINO}%")


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
            return self._db.one(
                "SELECT o.id FROM recipes r JOIN recipes o ON o.app_package=r.app_package AND"
                " o.app_version=r.app_version AND o.app_signature=r.app_signature AND o.variant=r.variant AND"
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

    def espera_da_pessoa(self, kind: LivroKind, ref: str) -> str | None:
        espera = self._espera(kind, ref)
        return espera[0] if espera is not None else None

    def motivo_da_espera(self, kind: LivroKind, ref: str) -> str | None:
        espera = self._espera(kind, ref)
        return espera[1] if espera is not None else None

    def _espera(self, kind: LivroKind, ref: str) -> tuple[str, str] | None:
        """`(desde, motivo)` do fluxo ensinado que espera a pessoa: o nascimento e o motivo do último pedido recusado
        que o passou a ela."""
        if kind is not LivroKind.FLUXO:
            return None
        trilha = ref_da_trilha(kind, ref)
        row = self._db.one(
            "SELECT f.created_at, v.motivo FROM flows f JOIN learning_validations v ON v.item_ref=?"
            f" AND v.review_id LIKE ? AND v.estado='recusada' AND v.motivo IN ({_EM})"
            " WHERE f.id=? AND f.status='active' AND f.source LIKE ?"
            " AND NOT EXISTS (SELECT 1 FROM learning_transitions t WHERE t.item_ref=? AND t.decided_at>=f.created_at"
            f"  AND {_DE_PESSOA}) ORDER BY v.created_at DESC, v.id DESC LIMIT 1",
            (trilha, f"{PREFIXO_DO_ENSINO}%", *_ESPERAM, ref, f"{PREFIXO_DE_TREINO}%", trilha, *_NAO_PESSOAS))
        if row is None:
            return None
        return linhas.texto(row, "created_at"), linhas.texto(row, "motivo")


def _exemplos(proposta: str | None) -> dict[str, str]:
    """Os `example` dos parâmetros da proposta salva da sessão (ilegível: nenhum, e o pedido nasce `sem_origem`)."""
    try:
        p = json.loads(proposta or "{}")
    except ValueError:
        return {}
    params = p.get("parameters") if isinstance(p, dict) else None
    return {str(x["name"]): str(x.get("example") or "") for x in params or []
            if isinstance(x, dict) and x.get("name")}


class EnsinoDaValidacaoSql:
    """`EnsinoDaValidacao` (30.81). A consulta parte de `flows` (poucas linhas, filtradas por status e origem); as
    subconsultas usam os índices de `learning_evidence(item_ref)` e `learning_transitions(item_ref)`. A de
    `learning_validations` filtra por `item_ref`, que só tem o índice parcial dos vivos: a tabela é pequena hoje."""

    def __init__(self, db: Database, servico: LearningService) -> None:
        self._db = db
        self._servico = servico

    def a_provar(self) -> list[EnsinadoAProvar]:
        ref = "'fluxo:' || f.id"
        revisao = "? || ts.id"
        saida: list[EnsinadoAProvar] = []
        for r in self._db.query(
                "SELECT f.id, f.command_template, f.created_at, f.app_id, ts.id AS sessao, ts.instance_id,"
                " ts.profile_id, ts.proposal,"
                f" (SELECT COUNT(*) FROM learning_validations v WHERE v.item_ref = {ref}"
                f"   AND v.review_id = {revisao}) AS tentativas,"
                f" EXISTS (SELECT 1 FROM learning_validations v WHERE v.item_ref = {ref}"
                "   AND v.estado IN ('pendente','rodando')) AS vivo,"
                f" EXISTS (SELECT 1 FROM learning_validations v WHERE v.item_ref = {ref}"
                f"   AND v.review_id = {revisao} AND v.estado='recusada' AND v.motivo IN ({_EM})) AS esperando"
                " FROM flows f JOIN training_sessions ts ON f.source = ? || ts.id"
                " WHERE f.status='active' AND f.source LIKE ? AND ts.status='saved'"
                f" AND NOT EXISTS (SELECT 1 FROM learning_evidence e JOIN runs ru ON ru.id = e.run_id"
                f"   WHERE e.item_ref = {ref} AND e.stance='for' AND e.simulated=0 AND ru.prova_fluxo_id = f.id"
                "   AND e.observed_at >= f.created_at AND NOT EXISTS (SELECT 1 FROM learning_evidence i"
                "   WHERE i.item_ref = e.item_ref AND i.origin_ref = e.origin_ref AND i.stance='invalida'))"
                f" AND NOT EXISTS (SELECT 1 FROM learning_transitions t WHERE t.item_ref = {ref}"
                f"   AND t.decided_at >= f.created_at AND {_DE_PESSOA})"
                " ORDER BY f.created_at, f.id",
                (PREFIXO_DO_ENSINO, PREFIXO_DO_ENSINO, *_ESPERAM, PREFIXO_DE_TREINO, f"{PREFIXO_DE_TREINO}%",
                 *_NAO_PESSOAS)):
            saida.append(EnsinadoAProvar(
                fluxo_id=linhas.texto(r, "id"), sessao=linhas.texto(r, "sessao"),
                comando=comando_do_ensino(linhas.texto(r, "command_template"),
                                          _exemplos(linhas.texto_ou_nulo(r, "proposal"))),
                aparelho=linhas.texto_ou_nulo(r, "instance_id"), persona=linhas.texto_ou_nulo(r, "profile_id"),
                app=linhas.texto_ou_nulo(r, "app_id") or "", desde=linhas.texto(r, "created_at"),
                tentativas=int(r["tentativas"] or 0), vivo=bool(r["vivo"]),
                esperando_pessoa=bool(r["esperando"])))
        return saida

    def entrada(self, fluxo_id: str) -> EntradaDoLivro | None:
        try:
            return self._servico.entrada(LivroKind.FLUXO, fluxo_id)
        except Exception:  # noqa: BLE001 - o fluxo sumiu entre a consulta e agora: nada a abrir
            log.info("aprendizado: ensinado %s sem entrada no livro", fluxo_id)
            return None

    def espera_decisao(self, aviso: EsperaDoEnsinado) -> None:
        self._servico.avisar_espera_do_ensinado(aviso)


__all__ = ["EnsinoDaValidacaoSql", "LeitorDoEnsinadoSql"]
