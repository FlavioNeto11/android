"""`learning_validations` (082) e as leituras da validação automática (item 30.31). O registro é a fila e a memória do
laço; as fontes só leem (`runs`, `apps`, `learning_evidence`, `attempts`, `ai_calls`). Nada daqui decide: as regras
estão em `domain/validacao.py` e a ordem das coisas em `application/validacao.py`."""
from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from app.db import Database, Row
from app.models import Plan
from app.modules.learning.application.validacao import (COMANDO_NA_LISTA, NovoPedido, Origem, PedidoListado,
                                                       PedidoVivo)
from app.modules.learning.domain.validacao import EstadoDoPedido, Grupo, Motivo
from app.modules.learning.domain.vocabulario import LivroKind, Posicao
from app.modules.learning.infrastructure import linhas
from app.planning import costs
from app.taskqueue.flows import FlowStore
from app.taskqueue.recipes import hash_generico_da_etapa, para_hash, step_template_hash
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


def _listado(r: Row) -> PedidoListado:
    return PedidoListado(
        id=linhas.texto(r, "id"), estado=linhas.texto(r, "estado"), motivo=linhas.texto_ou_nulo(r, "motivo"),
        item_ref=linhas.texto(r, "item_ref"), item_kind=linhas.texto(r, "item_kind"),
        scope_app=linhas.texto(r, "scope_app"), grupo=linhas.texto(r, "grupo"), run_id=linhas.texto_ou_nulo(r, "run_id"),
        run_origem=linhas.texto_ou_nulo(r, "run_origem"), aparelho=linhas.texto_ou_nulo(r, "aparelho"),
        usd=linhas.real(r, "usd"), teto_usd=linhas.real(r, "teto_usd") if r.get("teto_usd") is not None else None,
        created_at=linhas.texto(r, "created_at"), feito_em=linhas.texto_ou_nulo(r, "feito_em"),
        expira_em=linhas.texto(r, "expira_em"), revisao_nova_id=linhas.texto_ou_nulo(r, "revisao_nova_id"),
        comando=linhas.texto(r, "comando")[:COMANDO_NA_LISTA])


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
            " grupo, falta, run_origem, comando, aparelho_excluido, estado, motivo, expira_em, teto_usd)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (pid, em, em, novo.review_id, novo.item_ref, novo.item_kind, novo.scope_app, novo.grupo,
             json.dumps(list(novo.falta)), novo.run_origem, novo.comando, novo.aparelho_excluido, novo.estado,
             novo.motivo, novo.expira_em, novo.teto_usd))
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

    def recusar(self, pedido_id: str, motivo: Motivo, agora: datetime) -> bool:
        """O pedido ainda `pendente` fecha `recusada` sem execução (30.36, `sem_caminho` ao despachar)."""
        em = to_iso(agora)
        cur = self._db.execute(
            "UPDATE learning_validations SET estado='recusada', motivo=?, feito_em=?, updated_at=?"
            " WHERE id=? AND estado='pendente'", (motivo.value, em, em, pedido_id))
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
        """Os pedidos que voltaram com resposta para o curador: a favor (`feita`) e, desde o 30.36, contra
        (`evidencia_contra`) e a variante sem caminho (`sem_caminho`, SÓ da receita: a marca "variante sem caminho" do
        dossiê só existe para ela; o fluxo sem caminho voltaria ao curador sem marca, ele pediria evidência de novo e o
        laço seria pago). A forma e o "sem evidência" não respondem nada."""
        return [_pedido(r) for r in self._db.query(
            "SELECT * FROM learning_validations WHERE revisao_nova_id IS NULL AND (estado='feita'"
            " OR (estado='recusada' AND (motivo=? OR (motivo=? AND item_kind=?)))) ORDER BY feito_em, id",
            (Motivo.EVIDENCIA_CONTRA.value, Motivo.SEM_CAMINHO.value, LivroKind.RECEITA.value))]

    def sem_evidencia(self) -> list[PedidoVivo]:
        return [_pedido(r) for r in self._db.query(
            "SELECT * FROM learning_validations WHERE estado='recusada' AND motivo=? AND run_id IS NOT NULL"
            " ORDER BY feito_em, id", (Motivo.SEM_EVIDENCIA.value,))]

    def remotivar(self, pedido_id: str, motivo: Motivo, agora: datetime) -> bool:
        """Só de `sem_evidencia` (30.36): o motivo passa ao da evidência que a execução ganhou depois. O CAS no motivo
        de antes torna o passo idempotente."""
        cur = self._db.execute(
            "UPDATE learning_validations SET motivo=?, updated_at=? WHERE id=? AND estado='recusada' AND motivo=?",
            (motivo.value, to_iso(agora), pedido_id, Motivo.SEM_EVIDENCIA.value))
        return (cur.rowcount or 0) == 1

    def para_reabrir(self) -> list[NovoPedido]:
        """30.37: o pedido de fluxo fechado `sem_evidencia`/`divergencia_de_forma` cuja execução rodou ANTES da prova
        (`runs.prova_fluxo_id` nulo), com o fluxo ainda ligado e sem pedido POSTERIOR do mesmo item. O novo pedido é
        posterior e tira o antigo desta lista (idempotente); a prova que fecha `sem_evidencia` não reabre (a execução
        dela tem `prova_fluxo_id`). `expira_em` e `teto_usd` ficam para o serviço."""
        saida: list[NovoPedido] = []
        for r in self._db.query(
                "SELECT v.* FROM learning_validations v JOIN runs r ON r.id = v.run_id"
                " JOIN flows f ON 'fluxo:' || f.id = v.item_ref"
                " WHERE v.item_kind=? AND v.estado='recusada' AND v.motivo IN (?, ?) AND v.run_id IS NOT NULL"
                " AND r.prova_fluxo_id IS NULL AND f.status <> 'disabled'"
                " AND NOT EXISTS (SELECT 1 FROM learning_validations n WHERE n.item_ref = v.item_ref"
                " AND n.created_at > v.created_at) ORDER BY v.created_at, v.id",
                (LivroKind.FLUXO.value, Motivo.SEM_EVIDENCIA.value, Motivo.DIVERGENCIA_DE_FORMA.value)):
            falta = linhas.json_legado(linhas.texto_ou_nulo(r, "falta") or "[]")
            saida.append(NovoPedido(
                review_id=linhas.texto(r, "review_id"), item_ref=linhas.texto(r, "item_ref"),
                item_kind=linhas.texto(r, "item_kind"), scope_app=linhas.texto(r, "scope_app"),
                grupo=linhas.texto(r, "grupo"), falta=tuple(str(f) for f in falta) if isinstance(falta, list) else (),
                run_origem=linhas.texto_ou_nulo(r, "run_origem"), comando=linhas.texto(r, "comando"),
                aparelho_excluido=linhas.texto_ou_nulo(r, "aparelho_excluido"), estado=EstadoDoPedido.PENDENTE.value,
                motivo=None, expira_em=""))
        return saida

    def revisado(self, pedido_id: str, review_id: str) -> None:
        self._db.execute("UPDATE learning_validations SET revisao_nova_id=? WHERE id=? AND revisao_nova_id IS NULL",
                         (review_id, pedido_id))

    def listar(self, estado: EstadoDoPedido | None, limite: int, antes: str | None) -> list[PedidoListado]:
        """30.38 (b): as mais novas primeiro; `id` desempata o mesmo instante (a página seguinte usa só `created_at`,
        e o mesmo instante na fronteira de duas páginas é raro e só repete a linha). Pelo índice `(estado, created_at)`
        com o filtro de estado."""
        where, params = [], list[object]()
        if estado is not None:
            where.append("estado=?")
            params.append(estado.value)
        if antes:
            where.append("created_at<?")
            params.append(antes)
        sql = "SELECT * FROM learning_validations" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._db.query(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*params, limite))
        return [_listado(r) for r in rows]

    def contagens(self) -> dict[str, int]:
        return {linhas.texto(r, "estado"): linhas.inteiro(r, "n")
                for r in self._db.query("SELECT estado, COUNT(*) AS n FROM learning_validations GROUP BY estado")}


class FontesDaValidacaoSql:
    """As leituras do pedido e do fechamento. `fluxo_ativo_para` e `vetado` vêm de fora (a loja de fluxos do scheduler
    e o veto do livro), para esta classe só falar SQL."""

    def __init__(self, db: Database, *, precos: Callable[[], dict[str, list[float]]],
                 fluxo_ativo_para: Callable[[str], bool], vetado: Callable[[object], bool],
                 plano_ativo_para: Callable[[str], Plan | None] | None = None) -> None:
        self._db = db
        self._precos = precos
        self._fluxo_ativo_para = fluxo_ativo_para
        self._vetado = vetado
        #: 30.36: o plano do fluxo ATIVO que casa o comando (o `FlowStore.match` do scheduler). Sem ele (os testes de
        #: antes), toda receita tem caminho, como até o 30.35.
        self._plano_ativo_para = plano_ativo_para

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

    def posicao_da_execucao(self, item_ref: str, run_id: str) -> Posicao | None:
        """A evidência que o item ganhou DESTA execução: o fluxo, pela linha da sombra (a favor vence; depois a forma;
        depois o contra efetivo, `linhas.contra_efetivo`); a receita, a tentativa conduzida por ela que deu certo (é o
        que move `replay_ok`) — a receita não tem contra nem forma por validação."""
        kind, _, ref = item_ref.partition(":")
        if kind == "fluxo":
            posicoes = {linhas.texto(r, "stance") for r in self._db.query(
                f"SELECT e.stance FROM learning_evidence e WHERE e.item_ref=? AND e.run_id=?"
                f" AND (e.stance IN ('for', 'forma') OR {linhas.contra_efetivo('e')})", (item_ref, run_id))}
            for p in (Posicao.FOR, Posicao.FORMA, Posicao.AGAINST, Posicao.CONFLICT):
                if p.value in posicoes:
                    return p
            return None
        if kind == "receita" and ref.isdigit():
            feita = self._db.one(
                "SELECT 1 AS x FROM attempts a JOIN steps s ON s.id = a.step_id"
                " WHERE s.run_id=? AND a.recipe_id=? AND a.status='succeeded'", (run_id, int(ref))) is not None
            return Posicao.FOR if feita else None
        return None

    def caminho_da_receita(self, item_ref: str, comando: str) -> bool:
        """O plano do fluxo ativo do comando chega à etapa da receita: o `step_hash` dela é a identidade de alguma etapa
        do plano, pela chave específica ou pela genérica — as duas que o executor consulta (`RecipeStore.find`), no
        cálculo da cobertura dos fluxos (`capacidades.cobertura_do_fluxo`). Sem fluxo ativo ou fora da receita, `True`:
        quem responde por isso é o `sem_fluxo_ativo`. Medido no P4 de 03/10: a receita 54 (send_message, 55f3aca9…)
        nunca roda, porque o fluxo ativo chega à 78 (2ad58b2c…) pela mesma etapa."""
        kind, _, ref = item_ref.partition(":")
        if kind != "receita" or not ref.isdigit() or self._plano_ativo_para is None:
            return True
        plano = self._plano_ativo_para(comando)
        r = self._db.one("SELECT step_hash FROM recipes WHERE id=?", (int(ref),))
        if plano is None or r is None:
            return True
        parametros = dict(plano.parameters)
        alcancados = {h for e in plano.steps
                      for h in (step_template_hash(para_hash(e, parametros)), hash_generico_da_etapa(e)) if h}
        return linhas.texto(r, "step_hash") in alcancados

    def molde_do_fluxo(self, item_ref: str, comando: str) -> bool:
        """30.37: o comando de origem cabe no molde do fluxo (`FlowStore.plano_em_prova`: o plano do próprio fluxo com os
        parâmetros do comando; `None` se o fluxo sumiu, foi desligado ou o molde não casa). Fora do fluxo, `True`. Um
        plano ilegível não é caminho (a execução de prova o recusaria do mesmo modo)."""
        kind, _, ref = item_ref.partition(":")
        if kind != "fluxo":
            return True
        try:
            return FlowStore(self._db).plano_em_prova(ref, comando) is not None
        except ValueError:                       # `pydantic.ValidationError` é `ValueError`
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
