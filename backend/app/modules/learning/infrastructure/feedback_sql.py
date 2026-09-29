"""A leitura do voto do D2 (`LeituraDoVoto`) sobre as tabelas da execução, e a composição do `ServicoDeFeedback`.

Só LEITURA: o que o item votado usou e aprendeu sai das colunas que já existiam — `runs.flow_id` e
`flows.source_run_id` (fluxo), `attempts.recipe_id` e `recipes.learned_from_step` (receita), `runs/steps.skill_*`
(versão de habilidade) e `learning_exposures` no braço `with` (lição). O estado de agora e toda escrita (status com
trilha, evidência, sinal) são do livro.

A etapa de referência de um objetivo é onde ele PAROU (a primeira da versão corrente do plano que não terminou bem),
ou a última quando todas concluíram; dela saem a ação, o tipo da falha (gravado ou, no legado, classificado na leitura
pelo mesmo classificador puro, sem gravar) e a tentativa. O voto da execução inteira não tem etapa.

O serviço é montado por requisição (`montar_feedback`), como o `FlowConverter` das habilidades: não guarda estado, e a
apresentação não precisa enxergar o banco.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.db import Database, Row
from app.modules.learning.application.feedback import (ItemLido, RefDoItem, ServicoDeFeedback, SinalGravado)
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.falhas import classificar_falha
from app.modules.learning.domain.vocabulario import LivroKind, Polaridade, SignalKind
from app.modules.learning.domain.voto import Uso
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.taskqueue.projecao import QUALQUER, app_da_etapa
from app.util import now

#: Status de etapa em que o objetivo NÃO parou (a etapa de referência é a primeira fora deles).
_ETAPA_BEM = frozenset({"succeeded", "skipped"})
#: Parâmetros por consulta `IN (...)`: abaixo do limite do SQLite antigo (999) com folga.
_LOTE = 400


class LeituraDoVotoSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ o item votado
    def existe_execucao(self, run_id: str) -> bool:
        return self._db.one("SELECT id FROM runs WHERE id=?", (run_id,)) is not None

    def item(self, run_id: str, objective_id: str | None) -> ItemLido | None:
        run = self._db.one("SELECT id, status, simulated, flow_id, skill_id, skill_version, app_ids FROM runs"
                           " WHERE id=?", (run_id,))
        if run is None:
            return None
        objetivo: Row | None = None
        if objective_id is not None:
            objetivo = self._db.one("SELECT id, instance_id, status, profile_id FROM objectives WHERE id=? AND run_id=?",
                                    (objective_id, run_id))
            if objetivo is None:
                return None
        filtro = ""
        params: tuple[str, ...] = (run_id,)
        if objective_id is not None:
            filtro, params = " AND s.objective_id=?", (run_id, objective_id)
        etapas = self._db.query(
            "SELECT s.id, s.plan_version, s.seq, s.status, s.result, s.capability, s.app_id, s.template_hash,"
            " s.skill_id, s.skill_version, s.failure_kind FROM steps s WHERE s.run_id=?" + filtro
            + " ORDER BY s.objective_id, s.plan_version, s.seq", params)
        tentativas = self._db.query(
            "SELECT a.id, a.step_id, a.number, a.status, a.error, a.recipe_id, a.failure_kind FROM attempts a"
            " JOIN steps s ON s.id = a.step_id WHERE s.run_id=?" + filtro + " ORDER BY a.step_id, a.number", params)
        concluidas = [e for e in etapas if linhas.texto(e, "status") == "succeeded"]
        comprovadas = sum(1 for e in concluidas if _verificada(e))
        referencia = _referencia(etapas) if objetivo is not None else None
        ultima = _ultima_tentativa(tentativas, linhas.texto(referencia, "id")) if referencia is not None else None
        return ItemLido(
            run_id=run_id, objective_id=objective_id,
            status=linhas.texto(objetivo if objetivo is not None else run, "status"),
            simulado=bool(linhas.inteiro(run, "simulated")), etapas_comprovadas=comprovadas,
            etapas_a_mao=len(concluidas) - comprovadas, refs=self._refs(run, etapas, tentativas, objective_id),
            instance_id=linhas.texto(objetivo, "instance_id") if objetivo is not None else None,
            profile_id=linhas.texto_ou_nulo(objetivo, "profile_id") if objetivo is not None else None,
            app_package=self._pacote(referencia["app_id"] if referencia is not None else None, run["app_ids"]),
            capability=(linhas.texto_ou_nulo(referencia, "capability") or "") if referencia is not None else "",
            step_id=linhas.texto(referencia, "id") if referencia is not None else None,
            attempt_id=linhas.texto(ultima, "id") if ultima is not None else None,
            step_hash=linhas.texto_ou_nulo(referencia, "template_hash") if referencia is not None else None,
            failure_kind=_tipo_da_falha(referencia, ultima))

    def _refs(self, run: Row, etapas: list[Row], tentativas: list[Row],
              objective_id: str | None) -> tuple[RefDoItem, ...]:
        refs: list[RefDoItem] = []
        fluxo = linhas.texto_ou_nulo(run, "flow_id")
        if fluxo:
            refs.append(RefDoItem(LivroKind.FLUXO, fluxo, Uso.USOU))
        refs.extend(RefDoItem(LivroKind.FLUXO, linhas.texto(r, "id"), Uso.APRENDEU) for r in self._db.query(
            "SELECT id FROM flows WHERE source_run_id=? ORDER BY id", (linhas.texto(run, "id"),)))
        for t in tentativas:
            receita = linhas.inteiro_ou_nulo(t, "recipe_id")
            if receita is not None:
                refs.append(RefDoItem(LivroKind.RECEITA, str(receita), Uso.USOU))
        refs.extend(RefDoItem(LivroKind.RECEITA, str(rid), Uso.APRENDEU)
                    for rid in self._aprendidas([linhas.texto(e, "id") for e in etapas]))
        habilidades = [(linhas.texto_ou_nulo(run, "skill_id"), linhas.inteiro_ou_nulo(run, "skill_version"))]
        habilidades += [(linhas.texto_ou_nulo(e, "skill_id"), linhas.inteiro_ou_nulo(e, "skill_version")) for e in etapas]
        refs.extend(RefDoItem(LivroKind.HABILIDADE, f"{sid}@{versao}", Uso.USOU)
                    for sid, versao in habilidades if sid and versao is not None)
        sql = "SELECT DISTINCT item_id FROM learning_exposures WHERE run_id=? AND arm='with'"
        params: tuple[str, ...] = (linhas.texto(run, "id"),)
        if objective_id is not None:
            sql += " AND (objective_id=? OR objective_id IS NULL)"      # a lição do planejador é da execução
            params += (objective_id,)
        refs.extend(RefDoItem(LivroKind.LICAO, linhas.texto(r, "item_id"), Uso.EXPOSTA)
                    for r in self._db.query(sql + " ORDER BY item_id", params))
        return tuple(refs)

    def _aprendidas(self, etapas: Sequence[str]) -> list[int]:
        saida: list[int] = []
        for i in range(0, len(etapas), _LOTE):
            lote = tuple(etapas[i:i + _LOTE])
            marcas = ",".join("?" for _ in lote)
            saida.extend(linhas.inteiro(r, "id") for r in self._db.query(
                f"SELECT id FROM recipes WHERE learned_from_step IN ({marcas}) ORDER BY id", lote))
        return saida

    def _pacote(self, app_id: object, app_ids: object) -> str:
        app = app_da_etapa(app_id, app_ids)
        if app == QUALQUER:
            return ""
        linha = self._db.one("SELECT package FROM apps WHERE id=?", (app,))
        pacote = linhas.texto_ou_nulo(linha, "package") if linha is not None else None
        return pacote or app

    # ------------------------------------------------------------------ sinais
    def sinal(self, signal_id: int) -> SinalGravado | None:
        row = self._db.one("SELECT * FROM learning_signals WHERE id=?", (signal_id,))
        return _sinal(row) if row is not None else None

    def sinais_da_execucao(self, run_id: str) -> list[SinalGravado]:
        return [_sinal(r) for r in self._db.query("SELECT * FROM learning_signals WHERE run_id=? ORDER BY id",
                                                   (run_id,))]

    def sinais(self, *, desde: str, kind: SignalKind | None, app: str | None, limite: int) -> list[SinalGravado]:
        sql, params = "SELECT * FROM learning_signals WHERE created_at >= ?", [desde]
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind.value)
        if app:
            sql += " AND app_package=?"
            params.append(app)
        return [_sinal(r) for r in self._db.query(sql + " ORDER BY created_at DESC, id DESC LIMIT ?",
                                                   (*params, int(limite)))]


def montar_feedback(db: object, servico: LearningService) -> ServicoDeFeedback | None:
    """O serviço do D2 sobre o banco do central, ou `None` quando o banco ainda não está composto."""
    if not isinstance(db, Database):
        return None
    return ServicoDeFeedback(servico, SqlLearningRepository(db), LeituraDoVotoSql(db), relogio=now)


# ------------------------------------------------------------------ apoio
def _verificada(etapa: Row) -> bool:
    """A etapa concluiu com prova da tela (`StepResult.verified`)? Sem resultado legível, não."""
    resultado = linhas.json_legado(linhas.texto_ou_nulo(etapa, "result"))
    return isinstance(resultado, dict) and resultado.get("verified") is True


def _referencia(etapas: list[Row]) -> Row | None:
    """Onde o objetivo parou: na versão corrente do plano, a primeira etapa que não terminou bem; senão a última."""
    if not etapas:
        return None
    versao = max(linhas.inteiro(e, "plan_version") for e in etapas)
    correntes = [e for e in etapas if linhas.inteiro(e, "plan_version") == versao]
    return next((e for e in correntes if linhas.texto(e, "status") not in _ETAPA_BEM), correntes[-1])


def _ultima_tentativa(tentativas: list[Row], step_id: str) -> Row | None:
    da_etapa = [t for t in tentativas if linhas.texto(t, "step_id") == step_id]
    return max(da_etapa, key=lambda t: linhas.inteiro(t, "number")) if da_etapa else None


def _tipo_da_falha(etapa: Row | None, tentativa: Row | None) -> str | None:
    """O tipo gravado (A2) ou, no legado, o do erro final classificado na leitura — nunca gravado daqui."""
    if etapa is None:
        return None
    gravado = linhas.texto_ou_nulo(etapa, "failure_kind")
    if gravado:
        return gravado
    if tentativa is None:
        return None
    gravado = linhas.texto_ou_nulo(tentativa, "failure_kind")
    if gravado:
        return gravado
    tipo = classificar_falha(linhas.texto_ou_nulo(tentativa, "error"), linhas.texto_ou_nulo(tentativa, "status"))
    return tipo.value if tipo is not None else None


def _sinal(r: Row) -> SinalGravado:
    verificada = linhas.inteiro_ou_nulo(r, "step_verified")
    return SinalGravado(
        id=linhas.inteiro(r, "id"), kind=SignalKind(linhas.texto(r, "kind")),
        polarity=Polaridade(linhas.texto(r, "polarity")), verdict=linhas.texto_ou_nulo(r, "verdict"),
        reason=linhas.texto_ou_nulo(r, "reason"), note=linhas.texto_ou_nulo(r, "note"),
        note_refused=bool(linhas.inteiro(r, "note_refused")), source_ref=linhas.texto(r, "source_ref"),
        created_by=linhas.texto(r, "created_by"), run_id=linhas.texto_ou_nulo(r, "run_id"),
        objective_id=linhas.texto_ou_nulo(r, "objective_id"), step_id=linhas.texto_ou_nulo(r, "step_id"),
        attempt_id=linhas.texto_ou_nulo(r, "attempt_id"), instance_id=linhas.texto_ou_nulo(r, "instance_id"),
        profile_id=linhas.texto_ou_nulo(r, "profile_id"), app_package=linhas.texto(r, "app_package"),
        capability=linhas.texto(r, "capability"), step_hash=linhas.texto_ou_nulo(r, "step_hash"),
        failure_kind=linhas.texto_ou_nulo(r, "failure_kind"),
        step_verified=None if verificada is None else bool(verificada), data=linhas.json_objeto(r, "data"),
        simulated=bool(linhas.inteiro(r, "simulated")), created_at=linhas.texto(r, "created_at"),
        updated_at=linhas.texto_ou_nulo(r, "updated_at"))


__all__ = ["LeituraDoVotoSql", "montar_feedback"]
