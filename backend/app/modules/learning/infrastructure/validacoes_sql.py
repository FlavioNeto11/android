"""`learning_validations` (082) e as leituras da validação automática (item 30.31). O registro é a fila e a memória do
laço; as fontes só leem (`runs`, `apps`, `learning_evidence`, `attempts`, `ai_calls`). Nada daqui decide: as regras
estão em `domain/validacao.py` e a ordem das coisas em `application/validacao.py`."""
from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from app.db import Database, Row
from app.modules.learning.application.validacao import NovoPedido, Origem, PedidoVivo
from app.modules.learning.domain.validacao import EstadoDoPedido, Grupo, Motivo
from app.modules.learning.infrastructure import linhas
from app.planning import costs
from app.util import to_iso

#: O pedido `rodando` cuja execução não assentou neste prazo (ficou em `needs_input`, o digest não passou) expira.
RODANDO_NO_MAXIMO_H = 6
#: A execução que já assentou (o digest roda depois disso; `needs_input` também encerra a execução).
ASSENTADAS = frozenset({"completed", "completed_with_issues", "failed", "cancelled", "needs_input"})


def _pedido(r: Row) -> PedidoVivo:
    return PedidoVivo(id=linhas.texto(r, "id"), item_ref=linhas.texto(r, "item_ref"),
                      item_kind=linhas.texto(r, "item_kind"), scope_app=linhas.texto(r, "scope_app"),
                      grupo=Grupo(linhas.texto(r, "grupo")), comando=linhas.texto(r, "comando"),
                      aparelho_excluido=linhas.texto_ou_nulo(r, "aparelho_excluido"),
                      estado=EstadoDoPedido(linhas.texto(r, "estado")), run_id=linhas.texto_ou_nulo(r, "run_id"),
                      created_at=linhas.texto(r, "created_at"))


class RegistroDeValidacoesSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def criar(self, novo: NovoPedido, agora: datetime) -> str | None:
        pid = f"lv-{secrets.token_hex(8)}"
        em = to_iso(agora)
        # `ON CONFLICT DO NOTHING` sem alvo (os dois dialetos): o índice parcial de um pedido vivo por item recusa o
        # segundo sem abortar a transação de quem chama (no PostgreSQL, o `except IntegrityError` abortaria).
        cur = self._db.execute(
            "INSERT INTO learning_validations(id, created_at, updated_at, review_id, item_ref, item_kind, scope_app,"
            " grupo, falta, run_origem, comando, aparelho_excluido, estado, motivo, expira_em)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (pid, em, em, novo.review_id, novo.item_ref, novo.item_kind, novo.scope_app, novo.grupo,
             json.dumps(list(novo.falta)), novo.run_origem, novo.comando, novo.aparelho_excluido, novo.estado,
             novo.motivo, novo.expira_em))
        return pid if (cur.rowcount or 0) == 1 else None

    def pendentes(self) -> list[PedidoVivo]:
        return [_pedido(r) for r in self._db.query(
            "SELECT * FROM learning_validations WHERE estado='pendente' ORDER BY created_at, id")]

    def por_execucao(self, run_id: str) -> PedidoVivo | None:
        r = self._db.one("SELECT * FROM learning_validations WHERE run_id=?", (run_id,))
        return _pedido(r) if r is not None else None

    def comecar(self, pedido_id: str, run_id: str, aparelho: str, agora: datetime) -> bool:
        cur = self._db.execute(
            "UPDATE learning_validations SET estado='rodando', run_id=?, aparelho=?, updated_at=?"
            " WHERE id=? AND estado='pendente'", (run_id, aparelho, to_iso(agora), pedido_id))
        return (cur.rowcount or 0) == 1

    def fechar(self, pedido_id: str, estado: EstadoDoPedido, motivo: Motivo | None, usd: float,
               agora: datetime) -> bool:
        em = to_iso(agora)
        cur = self._db.execute(
            "UPDATE learning_validations SET estado=?, motivo=?, usd=?, feito_em=?, updated_at=?"
            " WHERE id=? AND estado='rodando'",
            (estado.value, motivo.value if motivo else None, float(usd), em, em, pedido_id))
        return (cur.rowcount or 0) == 1

    def expirar(self, agora: datetime) -> int:
        em = to_iso(agora)
        cur = self._db.execute(
            "UPDATE learning_validations SET estado='expirada', motivo=?, updated_at=? WHERE estado='pendente'"
            " AND expira_em < ?", (Motivo.EXPIROU.value, em, em))
        presos = self._db.execute(
            "UPDATE learning_validations SET estado='expirada', motivo=?, updated_at=? WHERE estado='rodando'"
            " AND updated_at < ?", (Motivo.EXPIROU.value, em, to_iso(agora - timedelta(hours=RODANDO_NO_MAXIMO_H))))
        return int(cur.rowcount or 0) + int(presos.rowcount or 0)

    def gasto_desde(self, desde: datetime) -> float:
        r = self._db.one("SELECT COALESCE(SUM(usd), 0) AS usd FROM learning_validations WHERE created_at >= ?",
                         (to_iso(desde),))
        return linhas.real(r, "usd") if r is not None else 0.0

    def comecados_desde(self, desde: datetime) -> int:
        """O ritmo por hora. Conta por `updated_at`, que o fechamento também move: uma execução começada há 2 h e
        fechada há 10 min conta na última hora. O erro é para o lado de despachar menos."""
        r = self._db.one("SELECT COUNT(*) AS n FROM learning_validations WHERE run_id IS NOT NULL AND updated_at >= ?"
                         " AND estado IN ('rodando', 'feita', 'recusada')", (to_iso(desde),))
        return linhas.inteiro(r, "n") if r is not None else 0

    def chegadas(self) -> list[PedidoVivo]:
        return [_pedido(r) for r in self._db.query(
            "SELECT * FROM learning_validations WHERE estado='feita' AND revisao_nova_id IS NULL"
            " ORDER BY feito_em, id")]

    def revisado(self, pedido_id: str, review_id: str) -> None:
        self._db.execute("UPDATE learning_validations SET revisao_nova_id=? WHERE id=? AND revisao_nova_id IS NULL",
                         (review_id, pedido_id))


class FontesDaValidacaoSql:
    """As leituras do pedido e do fechamento. `fluxo_ativo_para` e `vetado` vêm de fora (a loja de fluxos do scheduler
    e o veto do livro), para esta classe só falar SQL."""

    def __init__(self, db: Database, *, precos: Callable[[], dict[str, list[float]]],
                 fluxo_ativo_para: Callable[[str], bool], vetado: Callable[[object], bool]) -> None:
        self._db = db
        self._precos = precos
        self._fluxo_ativo_para = fluxo_ativo_para
        self._vetado = vetado

    def origem(self, run_id: str) -> Origem | None:
        r = self._db.one("SELECT command, instance_ids FROM runs WHERE id=?", (run_id,))
        if r is None:
            return None
        aparelhos = linhas.json_legado(linhas.texto_ou_nulo(r, "instance_ids") or "[]")
        primeiro = aparelhos[0] if isinstance(aparelhos, list) and aparelhos else None
        return Origem(comando=linhas.texto(r, "command"), aparelho=primeiro if isinstance(primeiro, str) else None)

    def app_de_qa(self, pacote: str | None) -> bool:
        if not pacote:
            return False
        r = self._db.one("SELECT category FROM apps WHERE package=?", (pacote,))
        return r is not None and linhas.texto_ou_nulo(r, "category") == "qa"

    def apps_do_item(self, item_ref: str) -> tuple[str, ...]:
        """Os pacotes que o fluxo exige (`flow_required_apps`); vazio nos outros tipos e no id sem linha em `apps`
        (o despachante soma o `scope_app` do pedido)."""
        kind, _, ref = item_ref.partition(":")
        if kind != "fluxo" or not ref:
            return ()
        return tuple(linhas.texto(r, "package") for r in self._db.query(
            "SELECT DISTINCT a.package FROM flow_required_apps f JOIN apps a ON a.id = f.app_id"
            " WHERE f.flow_id=? AND a.package IS NOT NULL AND a.package <> '' ORDER BY a.package", (ref,)))

    def fluxo_ativo_para(self, comando: str) -> bool:
        return self._fluxo_ativo_para(comando)

    def vetado(self, e: object) -> bool:
        return self._vetado(e)

    def evidencia_da_execucao(self, item_ref: str, run_id: str) -> bool:
        """O item ganhou evidência DESTA execução: o fluxo, uma linha a favor da sombra; a receita, uma tentativa
        conduzida por ela que deu certo (é o que move `replay_ok`)."""
        kind, _, ref = item_ref.partition(":")
        if kind == "fluxo":
            return self._db.one("SELECT 1 AS x FROM learning_evidence WHERE item_ref=? AND run_id=? AND stance='for'",
                                (item_ref, run_id)) is not None
        if kind == "receita" and ref.isdigit():
            return self._db.one(
                "SELECT 1 AS x FROM attempts a JOIN steps s ON s.id = a.step_id"
                " WHERE s.run_id=? AND a.recipe_id=? AND a.status='succeeded'", (run_id, int(ref))) is not None
        return False

    def desfecho(self, run_id: str) -> tuple[str, float] | None:
        r = self._db.one("SELECT status FROM runs WHERE id=?", (run_id,))
        if r is None:
            return None
        status = linhas.texto(r, "status")
        if status not in ASSENTADAS:
            return None
        precos = self._precos()
        usd = 0.0
        # A regra do `/api/usage` (`costs.spent_usd`): declarado onde há, tokens × preço onde não, simulado fora.
        for c in self._db.query("SELECT model, input_tokens, cache_read, cache_write, output_tokens, usd FROM ai_calls"
                                " WHERE run_id=? AND COALESCE(provider,'') <> 'simulated'", (run_id,)):
            usd += linhas.real(c, "usd") if c["usd"] is not None else costs.usd(
                precos, linhas.texto(c, "model"),
                [linhas.real(c, k) for k in ("input_tokens", "cache_read", "cache_write", "output_tokens")])
        return status, round(usd, 6)


__all__ = ["ASSENTADAS", "RODANDO_NO_MAXIMO_H", "FontesDaValidacaoSql", "RegistroDeValidacoesSql"]
