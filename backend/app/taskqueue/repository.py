"""Persistência da fila: execuções, objetivos por dispositivo, etapas, tentativas, ações e evidências.

Tudo o que o scheduler decide é gravado ANTES de ser executado; as transições passam por
`states.check_transition` e as operações críticas (assumir etapa + registrar tentativa) são uma
única transação.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..db import Database, INTEGRITY_ERRORS, Row, dumps, loads
from ..events import EventBus
from ..models import (RUN_TERMINAL, ActionDTO, ActionStatus, AttemptDTO, AttemptStatus, DecisionDTO, DeliveryLevel,
                      EvidenceDTO, ObjectiveDTO, ObjectiveStatus, Plan, PlanStep, PlanVersionDTO, Postcondition,
                      RunCounts, RunCreate, RunDetail, RunStatus, RunSummary, StepDTO, StepResult, StepStatus)
from ..planning.provider import Usage
from ..storage import DiskStorage, Storage, put_async
from ..util import new_run_id, now_iso, truncate
from .recipes import para_hash, step_template_hash
from .states import STEP_ACTIVE, STEP_OPEN, check_transition

#: Tipo do conteúdo por extensão de evidência. O disco não guarda tipo (quem serve o decide pela extensão), mas
#: o S3 guarda — e sem isto toda captura de tela chegaria ao navegador como `application/octet-stream`.
CONTENT_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "txt": "text/plain",
                 "xml": "application/xml", "json": "application/json", "log": "text/plain"}

TEMPLATE_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
# Prefixo de `status_detail` das etapas canceladas por uma REJEIÇÃO. Vocabulário, não frase solta: `recovery_steps`
# o lê para saber que aquela chave foi decidida por uma pessoa — e só essa origem de `cancelled` é definitiva.
MOTIVO_REJEICAO = "rejeitado por quem aprova"


class Sentinel:
    """Tipo do sentinela de `clear_wait_reason` — só para o `Any`/anotação ficar legível."""


#: "Nenhum valor de restauro foi passado" — distinto de `None` (que é um valor de restauro VÁLIDO: o objetivo
#: não tinha nenhum texto de espera antes da chamada de IA). Usar `None` como padrão confundia os dois.
_SEM_RESTAURO = Sentinel()


def _col(row: Any, nome: str) -> Any:
    """Coluna que pode não existir naquela linha (banco de uma versão anterior, consulta parcial)."""
    try:
        return row[nome] if nome in row.keys() else None
    except (KeyError, IndexError, AttributeError):
        return None


def resolve_templates(text: str | None, variables: dict[str, str]) -> str | None:
    """Substitui apenas variáveis conhecidas ({instance_id}, {run_id}, parâmetros…); o resto fica como está."""
    if text is None:
        return None
    return TEMPLATE_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


#: Validade da posse de uma etapa. Generoso de propósito: o preço de um lease longo é demorar a retomar o
#: trabalho de um backend que morreu; o preço de um lease curto é DOIS backends executando a mesma etapa no mesmo
#: aparelho. O segundo erro é caro e o primeiro não, então o lease é renovado a cada `RENOVAR_POSSE_S` e vale
#: muito mais que isso.
#:
#: **Depende de relógio sincronizado entre as máquinas.** O vencimento é escrito com o relógio de QUEM assumiu e
#: comparado com o relógio de QUEM pergunta. Um backend adiantado alguns minutos veria todo lease vivo como vencido
#: e adotaria etapas em plena execução — exatamente o que estes 120 s existem para impedir. NTP não é detalhe de
#: operação aqui, é pré-requisito, e está dito em `docs/banco.md`. Os 120 s também compram folga para desvio
#: pequeno: alguns segundos de diferença não chegam perto de virar adoção indevida.
POSSE_TTL_S = 120
#: Com que frequência o dono renova o que é dele. Uma escrita por aparelho ativo a cada 20 s.
RENOVAR_POSSE_S = 20


class PosseDaEtapaPerdida(RuntimeError):
    """Tentei escrever numa etapa que já não é minha (item 5.3, achado #32).

    O caso real: este backend ficou lento (pausa longa da VM, partição com o banco), parou de renovar o lease, e
    outro adotou a etapa. Sem cerca na escrita, o lento continuava gravando o desfecho por cima de quem agora
    executa — o lease protegia a ADOÇÃO e não protegia a ESCRITA, que é onde o estrago aparece. Quem recebe isto
    aborta o trabalho daquele aparelho: perdeu a posse, não manda mais nele.
    """

    def __init__(self, step_id: str, dono: str | None, eu: str):
        super().__init__(f"a etapa {step_id} agora é de '{dono}' (eu sou '{eu}'): escrita recusada")
        self.step_id, self.dono, self.eu = step_id, dono, eu


class Repository:
    def __init__(self, db: Database, bus: EventBus, evidence_dir: Path, *, owner_id: str = "local",
                 storage: Storage | None = None):
        self.db = db
        self.bus = bus
        self.evidence_dir = evidence_dir
        #: Quem assume etapas por este processo. Ver `Config.owner_id`: é a máquina, não o PID.
        self.owner_id = owner_id
        #: Onde a evidência é gravada (item 5.7). Sem argumento, é a pasta local de sempre.
        self.storage: Storage = storage or DiskStorage(evidence_dir)

    # ================================================================== execuções
    def create_run(self, req: RunCreate, *, simulated: bool) -> tuple[Row, bool]:
        """Cria a execução. A chave de idempotência é UNIQUE: repetição devolve a mesma execução."""
        run_id = new_run_id()
        try:
            with self.db.tx():
                self.db.execute(
                    "INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, req.idempotency_key, req.command.strip(), req.mode, RunStatus.planning.value,
                     int(simulated), dumps(req.instance_ids), now_iso()))
        except INTEGRITY_ERRORS:
            row = self.db.one("SELECT * FROM runs WHERE idempotency_key=?", (req.idempotency_key,))
            assert row is not None
            return row, False
        row = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        assert row is not None
        self.emit_run(run_id, f"Execução {run_id} criada; planejando…")
        return row, True

    def run_row(self, run_id: str) -> Row | None:
        return self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))

    def set_run_status(self, run_id: str, status: RunStatus, detail: str | None = None, *, message: str | None = None,
                       level: str = "info") -> None:
        fields, params = ["status=?", "status_detail=?"], [status.value, detail]
        if status == RunStatus.running:
            fields.append("started_at=COALESCE(started_at, ?)")
            params.append(now_iso())
        if status in RUN_TERMINAL:
            fields.append("finished_at=COALESCE(finished_at, ?)")
            params.append(now_iso())
        self.db.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id=?", (*params, run_id))
        self.emit_run(run_id, message or f"Execução {run_id}: {status.value}", level=level)

    def request_pause(self, run_id: str, reason: str) -> None:
        """Pausa automaticamente (disjuntor de conta de IA): idempotente e sem checar quem pediu — ao contrário
        de `RunService.pause`, que é o pedido do usuário pelo painel e recusa fora do estado 'running'."""
        run = self.run_row(run_id)
        if run is None or run["status"] != RunStatus.running.value or run["pause_requested"]:
            return
        self.db.execute("UPDATE runs SET pause_requested=1 WHERE id=?", (run_id,))
        self.set_run_status(run_id, RunStatus.paused, reason,
                            message=f"Execução {run_id} pausada automaticamente: {reason}", level="error")

    def save_plan(self, run_id: str, plan: Plan) -> None:
        apps = [a for a in dict.fromkeys([plan.app_id, *(s.app_id for s in plan.steps)]) if a]
        self.db.execute("UPDATE runs SET plan=?, app_ids=? WHERE id=?", (plan.model_dump_json(), dumps(apps), run_id))

    def materialize(self, run_id: str, plan: Plan, instances: list[dict[str, Any]]) -> None:
        """Persiste objetivos e etapas (versão 1) de cada aparelho ANTES de qualquer execução."""
        with self.db.tx():
            for inst in instances:
                iid = inst["instance_id"]
                oid = f"{run_id}:{iid}"
                if self.db.one("SELECT id FROM objectives WHERE id=?", (oid,)):
                    continue
                base = {"instance_id": iid, "run_id": run_id, "account_label": inst.get("account_label") or ""}
                params = {k: resolve_templates(v, base) or "" for k, v in plan.parameters.items()}
                self.db.execute(
                    "INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id,"
                    " worker_id, hosted_by, device_serial, physical_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (oid, run_id, iid, ObjectiveStatus.pending.value, 1, dumps(params), inst.get("profile_id"),
                     # ONDE isto vai rodar, fotografado junto com o perfil: o vínculo id lógico → aparelho físico
                     # muda por configuração, e sem a fotografia o relatório de amanhã não sabe dizer de que
                     # aparelho falava. Re-fotografado no despacho por `stamp_location`.
                     inst.get("worker_id"), inst.get("hosted_by"), inst.get("device_serial"),
                     inst.get("physical_id")))
                self._insert_steps(run_id, oid, iid, 1, plan.steps, {**params, **base}, "Plano inicial")

    def stamp_location(self, objective_id: str, *, worker_id: str | None, hosted_by: str | None,
                       device_serial: str | None, physical_id: str | None) -> None:
        """Re-fotografa ONDE o objetivo está rodando, no instante do despacho.

        A fotografia do plano pode ter envelhecido: entre materializar e despachar, o aparelho pode ter mudado de
        worker (`PATCH /instances/{id}`), ganhado outro endereço de ADB, ou o id lógico pode ter sido remapeado
        para outro aparelho físico. Quem conta a verdade sobre onde o trabalho aconteceu é o despacho.

        Nunca APAGA o que já se sabia: identidade física ainda não observada (`physical_id=None`) não desfaz a
        que estava gravada — o que não se sabe não invalida nada (migração 020).
        """
        self.db.execute(
            "UPDATE objectives SET worker_id=?, hosted_by=?, device_serial=?,"
            " physical_id=COALESCE(?, physical_id) WHERE id=?",
            (worker_id, hosted_by, device_serial, physical_id, objective_id))

    def _insert_steps(self, run_id: str, oid: str, iid: str, version: int, steps: list[PlanStep],
                      variables: dict[str, str], reason: str) -> None:
        resolved: list[PlanStep] = []
        # Identidade da etapa ANTES de resolver variáveis — e com os valores que o planejador escreveu por extenso
        # devolvidos ao nome do parâmetro, senão a receita de "@nasa" nunca serve para "@outro".
        hashes = {s.key: step_template_hash(para_hash(s, variables)) for s in steps}
        for s in steps:
            v = {**variables, **s.variables}                       # cópia de for_each: {item} é desta etapa
            post = s.postcondition.model_copy(update={
                "value": resolve_templates(s.postcondition.value, v),
                "description": resolve_templates(s.postcondition.description, v)})
            resolved.append(s.model_copy(update={
                "title": resolve_templates(s.title, v), "goal": resolve_templates(s.goal, v),
                "precondition": resolve_templates(s.precondition, v), "postcondition": post,
                "commit_guard": [resolve_templates(g, v) or "" for g in s.commit_guard],
                "band_guard": [resolve_templates(g, v) or "" for g in s.band_guard],
                "bindings": {k: resolve_templates(val, v) or "" for k, val in s.bindings.items()}}))
        self.db.execute("INSERT INTO plan_versions(objective_id, version, reason, steps, created_at) VALUES (?,?,?,?,?)",
                        (oid, version, reason, dumps([s.model_dump(mode="json") for s in resolved]), now_iso()))
        for seq, s in enumerate(resolved, start=1):
            self.db.execute(
                "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
                " side_effect, commit_guard, precondition, postcondition, timeout_s, max_attempts, status, template_hash,"
                " variables, for_each, capability, template_key, commit_selector, band_guard, bindings, app_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{run_id}:{iid}:v{version}:{s.key}", run_id, oid, iid, version, seq, s.key, s.title, s.goal,
                 dumps(s.depends_on), int(s.side_effect), dumps(s.commit_guard), s.precondition,
                 s.postcondition.model_dump_json(), s.timeout_s, s.max_attempts, StepStatus.pending.value,
                 hashes[s.key], dumps(s.variables) if s.variables else None, s.for_each,
                 s.capability, s.template_key, s.commit_selector, dumps(s.band_guard) if s.band_guard else None,
                 dumps(s.bindings) if s.bindings else None, s.app_id))

    # ================================================================== etapas
    def step_row(self, step_id: str) -> Row:
        row = self.db.one("SELECT * FROM steps WHERE id=?", (step_id,))
        if row is None:
            raise KeyError(step_id)
        return row

    def transition_step(self, step_id: str, target: StepStatus, *, detail: str | None = None,
                        result: StepResult | None = None, next_retry_at: str | None = None,
                        message: str | None = None, level: str = "info") -> None:
        with self.db.tx():
            row = self.step_row(step_id)
            check_transition(row["status"], target)
            # CERCA (item 5.3). Só vale para etapa EM EXECUÇÃO e com dono registrado: em `ready`/`pending` o
            # `claimed_by` é resto de uma tentativa anterior, e cercar por ele recusaria escrita legítima.
            dono = _col(row, "claimed_by")
            cercar = row["status"] in (StepStatus.running.value, StepStatus.verifying.value) and dono is not None
            if cercar and dono != self.owner_id:
                raise PosseDaEtapaPerdida(step_id, dono, self.owner_id)
            fields = ["status=?", "status_detail=?", "next_retry_at=?"]
            params: list[Any] = [target.value, truncate(detail, 600), next_retry_at]
            if result is not None:
                fields.append("result=?")
                params.append(result.model_dump_json())
            if target in (StepStatus.succeeded, StepStatus.failed, StepStatus.cancelled, StepStatus.skipped,
                          StepStatus.uncertain, StepStatus.waiting_user):
                fields.append("finished_at=?")
                params.append(now_iso())
            sql = f"UPDATE steps SET {', '.join(fields)} WHERE id=?"
            alvo: tuple[Any, ...] = (*params, step_id)
            if cercar:
                # A leitura acima e o UPDATE estão na mesma transação, mas em READ COMMITTED outro backend pode
                # ter adotado a etapa entre as duas. O `AND claimed_by=?` fecha essa fresta.
                sql += " AND claimed_by=?"
                alvo = (*alvo, self.owner_id)
            cur = self.db.execute(sql, alvo)
            if cercar and (cur.rowcount or 0) != 1:
                atual = self.db.one("SELECT claimed_by FROM steps WHERE id=?", (step_id,))
                raise PosseDaEtapaPerdida(step_id, (atual or {}).get("claimed_by"), self.owner_id)
        self.emit_step(step_id, message or f"Etapa '{row['title']}': {target.value}" + (f" — {detail}" if detail else ""),
                       level=level)

    def promote(self, run_id: str) -> int:
        """pending→ready quando as dependências estão comprovadas; retry_wait→ready quando vence o intervalo."""
        changed = 0
        now = now_iso()
        rows = self.db.query(
            "SELECT s.* FROM steps s JOIN objectives o ON o.id = s.objective_id AND o.plan_version = s.plan_version"
            " WHERE s.run_id=? AND s.status IN ('pending','retry_wait')" + self.so_meu("s.instance_id")
            + " ORDER BY s.seq", (run_id, self.owner_id))
        for r in rows:
            if r["status"] == "retry_wait":
                if (r["next_retry_at"] or "") <= now:
                    self.transition_step(r["id"], StepStatus.ready, message=f"Etapa '{r['title']}': nova tentativa liberada")
                    changed += 1
                continue
            if r["for_each"]:                       # etapa-modelo: só roda depois de expandida (coleta feita)
                continue
            deps = loads(r["depends_on"], [])
            if deps and not self._dependencias_comprovadas(r["objective_id"], r["plan_version"], deps):
                continue
            self.transition_step(r["id"], StepStatus.ready)
            changed += 1
        return changed

    def _dependencias_comprovadas(self, objective_id: str, plan_version: int, deps: list[str]) -> bool:
        """Dependência que EXISTE nesta versão do plano só conta comprovada nesta versão; a comprovação de versão
        anterior vale apenas para etapa que o replano não trouxe de volta (fronteira de efeito já comprovado).

        Antes a versão anterior valia sempre: em eda77f o replano recolocou 'abrir perfil' (que falhava) e, como a
        v1 dele tinha sucesso, 'abrir publicação' v2 ficou pronta ao lado — as etapas correram fora de ordem e a
        execução fechou como falha faltando só o comentário."""
        marcas = ",".join("?" * len(deps))
        atuais = {row["key"]: row["status"] for row in self.db.query(
            f"SELECT key, status FROM steps WHERE objective_id=? AND plan_version=? AND key IN ({marcas})",
            (objective_id, plan_version, *deps))}
        for dep in deps:
            if dep in atuais:
                if atuais[dep] != "succeeded":
                    return False
                continue
            if not self.db.scalar("SELECT COUNT(*) FROM steps WHERE objective_id=? AND key=? AND status='succeeded'",
                                  (objective_id, dep)):
                return False
        return True

    def next_ready_step(self, objective_id: str) -> Row | None:
        return self.db.one(
            "SELECT s.* FROM steps s JOIN objectives o ON o.id=s.objective_id AND o.plan_version=s.plan_version"
            " WHERE s.objective_id=? AND s.status='ready' ORDER BY s.seq LIMIT 1", (objective_id,))

    def claim_step(self, step_id: str) -> Row | None:
        """Assume a etapa e registra a tentativa numa única transação.

        Garante: (1) só uma etapa ativa por aparelho (dono único do executor); (2) a mesma tentativa
        nunca é disparada duas vezes (id estável `<step>:a<n>` com UNIQUE).

        A contagem abaixo não bastava: em PostgreSQL (READ COMMITTED) dois backends leem o MESMO zero e os dois
        assumem. Quem garante de verdade é o índice único parcial da migração 018 — e por isso a violação dele é
        tratada aqui como "outro já assumiu", não como erro. O `try` fica FORA da transação de propósito: no
        PostgreSQL a transação já está abortada quando o erro chega, e qualquer instrução seguinte falharia.
        """
        try:
            with self.db.tx():
                row = self.db.one("SELECT * FROM steps WHERE id=?", (step_id,))
                if row is None or row["status"] != StepStatus.ready.value or row["attempts"] >= row["max_attempts"]:
                    return None
                busy = self.db.scalar(
                    "SELECT COUNT(*) FROM steps WHERE instance_id=? AND status IN ('running','verifying')",
                    (row["instance_id"],))
                if busy:
                    return None
                # `attempts` conta tentativas CONSUMIDAS; o número da tentativa vem do histórico (interrupções
                # por pausa/controle manual/reinício não consomem tentativa, mas ficam registradas)
                number = int(self.db.scalar("SELECT COALESCE(MAX(number),0)+1 FROM attempts WHERE step_id=?",
                                            (step_id,)))
                attempt_id = f"{step_id}:a{number}"
                cur = self.db.execute(
                    "UPDATE steps SET status='running', attempts=attempts+1, started_at=COALESCE(started_at, ?),"
                    " status_detail=NULL, claimed_by=?, claim_expires_at=? WHERE id=? AND status='ready'",
                    # O vencimento é escrito com o relógio do BANCO (item 5.3): escrito com o daqui e lido com o
                    # de outra máquina, um desvio de minutos vira adoção de etapa em plena execução.
                    (now_iso(), self.owner_id, self.db.prazo_iso(POSSE_TTL_S), step_id))
                if cur.rowcount != 1:
                    return None
                try:
                    self.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                                    (attempt_id, step_id, number, AttemptStatus.running.value, now_iso()))
                except INTEGRITY_ERRORS:
                    raise RuntimeError(f"tentativa {attempt_id} já existe — disparo duplicado bloqueado") from None
        except INTEGRITY_ERRORS:
            # `idx_steps_um_ativo_por_aparelho`: outro executor assumiu uma etapa deste aparelho entre a contagem
            # e o UPDATE. Não é erro — é a corrida sendo perdida, e quem perde simplesmente não assume.
            return None
        self.emit_step(step_id, f"Etapa '{row['title']}': tentativa {number} iniciada")
        attempt = self.db.one("SELECT * FROM attempts WHERE id=?", (attempt_id,))
        self.emit_attempt(attempt_id, row)
        return attempt

    def note_attempt(self, attempt_id: str, *, error: str | None = None, recovery: str | None = None) -> None:
        """Anota erro original/recuperação numa tentativa ainda em andamento."""
        self.db.execute("UPDATE attempts SET error=COALESCE(?, error), recovery=COALESCE(?, recovery) WHERE id=?",
                        (truncate(error, 800), truncate(recovery, 800), attempt_id))

    def refund_attempt(self, step_id: str) -> None:
        """Interrupção sem culpa da etapa (pausa, controle manual, reinício): não consome tentativa."""
        self.db.execute("UPDATE steps SET attempts=CASE WHEN attempts > 1 THEN attempts - 1 ELSE 0 END WHERE id=?", (step_id,))

    def finish_attempt(self, attempt_id: str, status: AttemptStatus, *, error: str | None = None,
                       recovery: str | None = None, observed: str | None = None) -> None:
        """Fecha a tentativa. **Cercada pela posse da etapa** (item 5.3): no `_apply` do scheduler a tentativa é
        fechada ANTES da transição da etapa, então sem cerca aqui um dono que já perdeu a posse ainda gravaria o
        desfecho da tentativa por cima de quem agora executa — a cerca da etapa chegaria tarde demais."""
        cur = self.db.execute(
            "UPDATE attempts SET status=?, finished_at=?, error=COALESCE(?, error), recovery=COALESCE(?, recovery),"
            " observed_result=COALESCE(?, observed_result) WHERE id=? AND EXISTS"
            " (SELECT 1 FROM steps s WHERE s.id=attempts.step_id AND (s.claimed_by IS NULL OR s.claimed_by=?))",
            (status.value, now_iso(), truncate(error, 800), truncate(recovery, 800), truncate(observed, 800),
             attempt_id, self.owner_id))
        if (cur.rowcount or 0) != 1:
            linha = self.db.one("SELECT s.id, s.claimed_by FROM steps s JOIN attempts a ON a.step_id=s.id"
                                " WHERE a.id=?", (attempt_id,))
            if linha is not None:
                raise PosseDaEtapaPerdida(linha["id"], linha["claimed_by"], self.owner_id)
        step = self.db.one("SELECT s.* FROM steps s JOIN attempts a ON a.step_id=s.id WHERE a.id=?", (attempt_id,))
        self.emit_attempt(attempt_id, step)

    # ================================================================== ações (diário intenção → resultado)
    def log_intent(self, attempt_id: str, tool: str, args: dict[str, Any], rationale: str | None,
                   *, side_effect: bool, source: str = "ai") -> int:
        seq = int(self.db.scalar("SELECT COALESCE(MAX(seq),0)+1 FROM actions WHERE attempt_id=?", (attempt_id,)))
        action_id = int(self.db.inserted_id(
            "INSERT INTO actions(attempt_id, seq, tool, args, rationale, status, side_effect, intent_at, source)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (attempt_id, seq, tool, dumps(args), truncate(rationale, 400), ActionStatus.intended.value,
             int(side_effect), now_iso(), source)) or 0)
        self.emit_action(action_id)
        return action_id

    def finish_action(self, action_id: int, status: ActionStatus, *, result: dict[str, Any] | None = None,
                      error: str | None = None, effect_possible: bool = False,
                      target: dict[str, Any] | None = None) -> None:
        self.db.execute("UPDATE actions SET status=?, done_at=?, result=?, error=?, effect_possible=?,"
                        " target=COALESCE(?, target) WHERE id=?",
                        (status.value, now_iso(), dumps(result) if result is not None else None, truncate(error, 600),
                         int(effect_possible), dumps(target) if target is not None else None, action_id))
        self.emit_action(action_id)

    def commit_state(self, step_id: str) -> tuple[bool, bool]:
        """(efeito_disparado, resultado_desconhecido) considerando TODAS as tentativas da etapa."""
        rows = self.db.query(
            "SELECT a.status, a.effect_possible FROM actions a JOIN attempts t ON t.id=a.attempt_id"
            " WHERE t.step_id=? AND a.side_effect=1", (step_id,))
        fired = any(r["status"] in ("done", "unknown", "intended") or r["effect_possible"] for r in rows)
        unknown = any(r["status"] in ("unknown", "intended") for r in rows)
        return fired, unknown

    # ================================================================== evidências / uso de IA
    @staticmethod
    def chave_de_evidencia(*, run_id: str, instance_id: str, step_id: str | None, attempt_id: str | None,
                           kind: str, ts: str, ext: str) -> str:
        """A chave de storage. Barra normal SEMPRE, nos dois back-ends e nos dois sistemas operacionais — até
        aqui ela saía de `Path.relative_to`, e no Windows nascia com a barra invertida, que nenhum bucket
        entende."""
        safe_step = re.sub(r"[^A-Za-z0-9_.-]", "_", (attempt_id or step_id or "run").split(":", 2)[-1])
        nome = f"{ts.replace(':', '').replace('.', '')}_{safe_step}_{kind}.{ext}"
        return f"{run_id}/{instance_id}/{nome}"

    def add_evidence(self, *, run_id: str, instance_id: str, step_id: str | None, attempt_id: str | None, kind: str,
                     note: str | None, data: bytes | None = None, ext: str = "jpg", redacted: bool = False) -> int:
        """Grava a evidência. **Bloqueante**: de código assíncrono, use `add_evidence_async`."""
        path: str | None = None
        ts = now_iso()
        if data is not None and not redacted:
            path = self.storage.put(
                self.chave_de_evidencia(run_id=run_id, instance_id=instance_id, step_id=step_id,
                                        attempt_id=attempt_id, kind=kind, ts=ts, ext=ext),
                data, content_type=CONTENT_TYPES.get(ext, "application/octet-stream"))
        return self._registrar_evidencia(run_id=run_id, instance_id=instance_id, step_id=step_id,
                                         attempt_id=attempt_id, ts=ts, kind=kind, note=note, path=path,
                                         redacted=redacted)

    async def add_evidence_async(self, *, run_id: str, instance_id: str, step_id: str | None,
                                 attempt_id: str | None, kind: str, note: str | None, data: bytes | None = None,
                                 ext: str = "jpg", redacted: bool = False) -> int:
        """A mesma coisa, com a ESCRITA fora do laço de eventos (item 5.7).

        Em disco local a escrita síncrona era inofensiva. Com o destino na rede — que é o ponto do item — ela
        vira uma ida e volta de rede dentro do laço, a cada captura de tela, com o scheduler inteiro parado
        esperando. O registro no banco continua aqui: ele é curto, e é o que ordena o evento `evidence.added`.
        """
        path: str | None = None
        ts = now_iso()
        if data is not None and not redacted:
            path = await put_async(
                self.storage,
                self.chave_de_evidencia(run_id=run_id, instance_id=instance_id, step_id=step_id,
                                        attempt_id=attempt_id, kind=kind, ts=ts, ext=ext),
                data, content_type=CONTENT_TYPES.get(ext, "application/octet-stream"))
        return self._registrar_evidencia(run_id=run_id, instance_id=instance_id, step_id=step_id,
                                         attempt_id=attempt_id, ts=ts, kind=kind, note=note, path=path,
                                         redacted=redacted)

    def _registrar_evidencia(self, *, run_id: str, instance_id: str, step_id: str | None, attempt_id: str | None,
                             ts: str, kind: str, note: str | None, path: str | None, redacted: bool) -> int:
        # `storage`/`stored_by` dizem ONDE o arquivo está e QUEM o gravou: sem isso a retenção de uma réplica
        # apaga do banco compartilhado a linha de um arquivo que está no disco da OUTRA (achado #172).
        eid = int(self.db.inserted_id(
            "INSERT INTO evidence(run_id, instance_id, step_id, attempt_id, ts, kind, note, path, redacted,"
            " storage, stored_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, instance_id, step_id, attempt_id, ts, kind, truncate(note, 600), path, int(redacted),
             self.storage.name if path else None, self.owner_id if path else None)) or 0)
        ev = self.evidence_dto(self.db.one("SELECT * FROM evidence WHERE id=?", (eid,)))
        self.bus.emit("evidence.added", f"Evidência registrada: {note or kind}", run_id=run_id, instance_id=instance_id,
                      step_id=step_id, attempt_id=attempt_id, data={"evidence": ev.model_dump(mode="json")})
        return eid

    def add_usage(self, run_id: str | None, objective_id: str | None, usage: Usage, *, step_id: str | None = None,
                  ok: bool = True, error_kind: str | None = None, error_status: int | None = None,
                  error_message: str | None = None) -> None:
        """`run_id` nulo é uso de IA fora de execução (ex.: gerar uma resposta social pelo portal): entra no
        relatório de custo por função e não soma a execução nenhuma.

        `error_kind`/`error_status`/`error_message` (migração 033, achado #101): só em linhas `ok=False`. Sem
        eles a chamada com erro só dizia "deu erro", sem tipo nem modelo — e o pseudo-modelo antigo ('(erro)')
        entrava na lista de "modelo sem preço" do relatório de custo, fazendo um total real virar "parcial".
        `usage.model` numa linha de erro é o modelo REALMENTE pedido (`_ai` resolve isso antes de chamar aqui).
        """
        if not usage.calls and not usage.input_tokens:
            return
        if usage.role:        # uma linha por chamada: função, modelo e cache — base do relatório de custo
            fresh = max(0, usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
            # `requested_model`/`fallback`/`provider` (migração 032): o que foi PEDIDO, por que houve troca e qual
            # endpoint cobrou. Sem eles, "respondeu o fallback" era indistinguível de "estava configurado assim".
            self.db.execute(
                "INSERT INTO ai_calls(ts, run_id, objective_id, step_id, role, model, tier, input_tokens, cache_read,"
                " cache_write, output_tokens, with_image, ms, ok, requested_model, fallback, provider,"
                " error_kind, error_status, error_message)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), run_id, objective_id, step_id, usage.role, usage.model, usage.tier, fresh,
                 usage.cache_read_tokens, usage.cache_write_tokens, usage.output_tokens, int(usage.with_image),
                 usage.ms, int(ok), usage.requested_model or usage.model, usage.fallback, usage.provider or None,
                 None if ok else error_kind, None if ok else error_status,
                 None if ok else truncate(error_message, 500)))
        self.db.execute("UPDATE runs SET ai_input_tokens=ai_input_tokens+?, ai_output_tokens=ai_output_tokens+? WHERE id=?",
                        (usage.input_tokens, usage.output_tokens, run_id))
        if objective_id:
            self.db.execute(
                "UPDATE objectives SET ai_calls=ai_calls+?, ai_input_tokens=ai_input_tokens+?,"
                " ai_output_tokens=ai_output_tokens+? WHERE id=?",
                (usage.calls, usage.input_tokens, usage.output_tokens, objective_id))

    def decision(self, text: str, *, run_id: str, instance_id: str | None = None, step_id: str | None = None) -> None:
        self.bus.emit("decision", text, run_id=run_id, instance_id=instance_id, step_id=step_id, data={"text": text})

    # ================================================================== objetivos
    def objective_row(self, objective_id: str) -> Row:
        row = self.db.one("SELECT * FROM objectives WHERE id=?", (objective_id,))
        if row is None:
            raise KeyError(objective_id)
        return row

    def set_objective(self, objective_id: str, status: ObjectiveStatus, *, detail: str | None = None,
                      blocked_reason: str | None = None, needs: str | None = None,
                      delivery_level: DeliveryLevel | None = None, message: str | None = None,
                      level: str = "info", blocked_kind: str | None = None) -> None:
        # `wait_reason` sempre volta a NULL aqui (item 7.3): toda chamada a `set_objective` é uma transição de
        # ESTADO do objetivo — a espera tipada (device_slot/profile_limit/ai_capacity/model_response), que só o
        # scheduler e o `_ai` escrevem via `note_waiting`/coluna direta, sempre termina numa destas transições.
        # `waiting_user` continua sem escrever nada aqui: o motivo "pessoa" é DERIVADO do próprio status no
        # frontend, não precisa de coluna.
        fields = ["status=?", "status_detail=?", "blocked_reason=?", "needs=?", "wait_reason=NULL"]
        params: list[Any] = [status.value, truncate(detail, 600), truncate(blocked_reason, 600), truncate(needs, 600)]
        if blocked_kind is not None:
            fields.append("blocked_kind=?")
            params.append(blocked_kind)
        if status == ObjectiveStatus.running:
            fields.append("started_at=COALESCE(started_at, ?)")
            params.append(now_iso())
            fields.append("finished_at=NULL")
        if status in (ObjectiveStatus.succeeded, ObjectiveStatus.failed, ObjectiveStatus.cancelled,
                      ObjectiveStatus.uncertain, ObjectiveStatus.waiting_user):
            fields.append("finished_at=?")
            params.append(now_iso())
        if delivery_level is not None:
            fields.append("delivery_level=?")
            params.append(delivery_level.value)
        self.db.execute(f"UPDATE objectives SET {', '.join(fields)} WHERE id=?", (*params, objective_id))
        row = self.objective_row(objective_id)
        self.bus.emit("objective.updated", message or f"{row['instance_id']}: objetivo {status.value}"
                      + (f" — {detail}" if detail else ""), level=level, run_id=row["run_id"],
                      instance_id=row["instance_id"], objective_id=objective_id,
                      data={"objective": self.objective_dto(row).model_dump(mode="json")})

    def emit_objective(self, objective_id: str, message: str | None = None) -> None:
        """Publica o objetivo sem mudar o estado (progresso de etapas, nível de entrega)."""
        row = self.objective_row(objective_id)
        dto = self.objective_dto(row)
        self.bus.emit("objective.updated", message or f"{row['instance_id']}: {dto.steps_done}/{dto.steps_total} etapas comprovadas",
                      run_id=row["run_id"], instance_id=row["instance_id"], objective_id=objective_id,
                      data={"objective": dto.model_dump(mode="json")})

    def add_effect(self, objective_id: str, text: str) -> None:
        row = self.objective_row(objective_id)
        effects = loads(row["effects"], [])
        effects.append(f"{now_iso()} — {text}")
        self.db.execute("UPDATE objectives SET effects=? WHERE id=?", (dumps(effects), objective_id))

    def revise_plan(self, objective_id: str, reason: str, steps: list[PlanStep]) -> int:
        """Nova versão do plano para o objetivo: etapas abertas da versão atual viram `skipped`, as já
        comprovadas permanecem no histórico e as novas nascem com ids estáveis da nova versão."""
        with self.db.tx():
            obj = self.objective_row(objective_id)
            version = obj["plan_version"] + 1
            for r in self.db.query("SELECT id, status FROM steps WHERE objective_id=? AND plan_version=?",
                                   (objective_id, obj["plan_version"])):
                if r["status"] in ("pending", "ready", "retry_wait", "waiting_user"):
                    self.db.execute("UPDATE steps SET status='skipped', status_detail=?, finished_at=? WHERE id=?",
                                    (f"plano revisado (v{version})", now_iso(), r["id"]))
                    # A etapa morreu; o pedido de aprovação dela também. Deixá-lo pendente punha DOIS cartões
                    # iguais na tela — o desta versão e o da nova —, e editar o antigo gravava o texto numa etapa
                    # que nunca roda: a pessoa aprovava uma frase e o aparelho digitava outra.
                    self.db.execute(
                        "UPDATE pending_approvals SET status='expired', decided_at=?, decided_note=?"
                        " WHERE step_id=? AND status='pending'",
                        (now_iso(), f"plano revisado (v{version}): esta etapa não vai mais acontecer", r["id"]))
            self.db.execute("UPDATE objectives SET plan_version=? WHERE id=?", (version, objective_id))
            params = loads(obj["parameters"], {})
            account = self.db.scalar("SELECT account_label FROM instances WHERE id=?", (obj["instance_id"],)) or ""
            base = {"instance_id": obj["instance_id"], "run_id": obj["run_id"], "account_label": account}
            self._insert_steps(obj["run_id"], objective_id, obj["instance_id"], version, steps, {**params, **base}, reason)
        self.bus.emit("plan.revised", f"{obj['instance_id']}: plano revisado (v{version}) — {reason}", level="warn",
                      run_id=obj["run_id"], instance_id=obj["instance_id"], objective_id=objective_id,
                      data={"objective_id": objective_id, "version": version, "reason": reason})
        return version

    def resume_objective(self, objective_id: str, detail: str, *, esperou_s: int = 0) -> None:
        """Volta um objetivo bloqueado para a fila, sem revisar plano: usado quando o motivo do bloqueio saiu
        (aprovação decidida, limite vencido). O que já foi feito continua feito.

        `esperou_s` é o tempo em que o objetivo ficou parado esperando ALGUÉM, e ele entra em `paused_s`: o prazo
        total do objetivo mede a demora da máquina, não a de quem decide. Sem isso, uma aprovação que demore mais
        do que `objective_timeout_s` (15 min por padrão) é aceita e descartada no mesmo segundo — o objetivo
        volta à fila, o scheduler vê o relógio estourado e mata a etapa antes de digitar qualquer coisa.
        """
        self.db.execute("UPDATE objectives SET blocked_kind=NULL, finished_at=NULL, paused_s=paused_s+?"
                        " WHERE id=?", (max(0, esperou_s), objective_id))
        self.set_objective(objective_id, ObjectiveStatus.pending, detail=detail, blocked_reason=None, needs=None)

    def cancel_target_steps(self, objective_id: str, step_id: str, *, item: str | None, note: str | None = None) -> int:
        """Cancela a etapa e, quando ela é a cópia de um bloco `for_each`, as outras etapas DAQUELE item.

        Rejeitar uma resposta não pode cancelar o objetivo inteiro: os outros alvos continuam valendo.

        O motivo é fixo (`MOTIVO_REJEICAO` + a observação de quem decidiu) porque não é só texto de tela: é ele
        que faz `recovery_steps` distinguir "uma pessoa recusou isto" de "isto foi cancelado porque o item falhou".
        """
        reason = MOTIVO_REJEICAO + (f": {note}" if note else "")
        abertos = self.db.query(
            "SELECT * FROM steps WHERE objective_id=? AND status IN ('pending','ready','retry_wait','waiting_user')"
            " ORDER BY seq", (objective_id,))
        n = 0
        for r in abertos:
            proprio = r["id"] == step_id
            mesmo_item = bool(item) and (loads(r["variables"], {}) or {}).get("item") == item
            if proprio or mesmo_item:
                self.transition_step(r["id"], StepStatus.cancelled, detail=reason)
                n += 1
        return n

    def recompute_run(self, run_id: str) -> RunStatus | None:
        """Deriva o estado da execução a partir dos objetivos. Só sucesso comprovado conta como sucesso."""
        run = self.run_row(run_id)
        if run is None or run["status"] in ("planning", "needs_input", "planned"):
            return None
        counts = self._counts(run_id)
        total = sum(counts.model_dump().values())
        used = int(self.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=? AND started_at IS NOT NULL", (run_id,)) or 0)
        self.db.execute("UPDATE runs SET instances_used=? WHERE id=?", (used, run_id))
        active = counts.running + counts.pending
        current = RunStatus(run["status"])
        if active == 0 and total > 0:
            if run["cancel_requested"] and counts.cancelled:
                new = RunStatus.cancelled
            elif counts.succeeded == total:
                new = RunStatus.completed
            else:
                new = RunStatus.completed_with_issues
            if counts.waiting_user or counts.uncertain:
                # há itens aguardando decisão do usuário: a execução segue "em aberto" para permitir retomada
                new = RunStatus.completed_with_issues
            detail = self._status_detail(counts, total)
            if new != current or detail != run["status_detail"]:
                self.set_run_status(run_id, new, detail, message=f"Execução {run_id} finalizada: {detail}",
                                    level="info" if new == RunStatus.completed else "warn")
            return new
        if current in RUN_TERMINAL and active > 0:          # retomada de itens
            self.db.execute("UPDATE runs SET finished_at=NULL WHERE id=?", (run_id,))
            self.set_run_status(run_id, RunStatus.paused if run["pause_requested"] else RunStatus.running,
                                "itens retomados")
            return RunStatus.running
        self.emit_run(run_id, None)
        return current

    @staticmethod
    def _status_detail(c: RunCounts, total: int) -> str:
        parts = [f"{c.succeeded} de {total} com sucesso comprovado"]
        for n, label in ((c.failed, "falha"), (c.waiting_user, "bloqueio aguardando usuário"),
                         (c.uncertain, "resultado incerto"), (c.cancelled, "cancelado")):
            if n:
                parts.append(f"{n} {label}")
        return "; ".join(parts)

    def _counts(self, run_id: str) -> RunCounts:
        c = RunCounts()
        for r in self.db.query("SELECT status, COUNT(*) n FROM objectives WHERE run_id=? GROUP BY status", (run_id,)):
            setattr(c, r["status"], r["n"])
        return c

    # ================================================================== consultas do scheduler
    def so_meu(self, coluna: str) -> str:
        """Fragmento de SQL: "…e o aparelho desta linha não é de outro backend" (item 5.1, migração 027).

        Um `AND NOT EXISTS`, e não um `JOIN … hosted_by=?`, porque `NULL` e linha ausente precisam continuar
        valendo como MINHAS: é o estado de um banco anterior à 027 e o de produção, com um backend só. Quem usa
        passa `self.owner_id` como o parâmetro seguinte na tupla.
        """
        return (f" AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id={coluna}"
                " AND i.hosted_by IS NOT NULL AND i.hosted_by<>?)")

    def active_runs(self) -> list[Row]:
        return self.db.query("SELECT * FROM runs WHERE status IN ('running','cancelling') ORDER BY created_at")

    def dispatchable_objectives(self) -> list[Row]:
        """Objetivos com etapa pronta, de execuções em andamento e não pausadas — a execução mais antiga primeiro.

        **Só o que ESTE backend hospeda** (item 5.1, achado #171). Sem o filtro, um segundo backend no mesmo banco
        via o objetivo de um aparelho que ele não tem e fazia coisa destrutiva com ele: bloqueava com "Instância
        não existe na configuração atual", ou — com o rodízio ligado — criava no próprio disco um AVD com o mesmo
        id lógico para atendê-lo. O objetivo alheio é IGNORADO aqui; quem o despacha é quem hospeda o aparelho.

        `hosted_by IS NULL` continua sendo meu: é o estado de um banco anterior à migração 027 e o de um aparelho
        que ninguém reivindicou. Com um backend só — que é a produção — nada muda.
        """
        return self.db.query(
            "SELECT o.*, r.created_at AS run_created FROM objectives o JOIN runs r ON r.id=o.run_id"
            " LEFT JOIN instances i ON i.id=o.instance_id"
            " WHERE r.status='running' AND r.pause_requested=0 AND r.cancel_requested=0"
            " AND o.status IN ('pending','running')"
            " AND (i.hosted_by IS NULL OR i.hosted_by=?)"
            " AND EXISTS (SELECT 1 FROM steps s WHERE s.objective_id=o.id AND s.plan_version=o.plan_version AND s.status='ready')"
            " ORDER BY r.created_at, o.instance_id", (self.owner_id,))

    def instances_with_open_work(self) -> set[str]:
        """Aparelhos com objetivo ainda por fazer em execução ativa (inclui etapas em retry_wait, que
        `dispatchable_objectives` não enxerga) — o rodízio nunca desliga um destes."""
        return {r["instance_id"] for r in self.db.query(
            "SELECT DISTINCT o.instance_id FROM objectives o JOIN runs r ON r.id=o.run_id"
            " WHERE r.status IN ('running','cancelling') AND o.status IN ('pending','running')")}

    def instances_needing_user(self) -> set[str]:
        """Aparelhos cuja TELA o usuário precisa ver agora: item bloqueado ou incerto de execução ainda aberta."""
        return {r["instance_id"] for r in self.db.query(
            "SELECT DISTINCT o.instance_id FROM objectives o JOIN runs r ON r.id=o.run_id"
            " WHERE r.status IN ('running','cancelling') AND o.status IN ('waiting_user','uncertain')")}

    def note_waiting(self, objective_id: str, detail: str, *, wait_reason: str | None = None) -> None:
        """Motivo de espera do objetivo (ex.: aguardando vaga). Só grava/emite quando muda.

        `wait_reason` (migração 033, achado #68) é o motivo ESTRUTURADO — device_slot | profile_limit |
        ai_capacity | model_response — para o frontend parar de adivinhar por regex sobre `detail` (texto livre,
        que continua existindo para o operador ler). `None` deixa o campo como estava: quem não sabe o motivo
        tipado não apaga o que uma chamada anterior gravou.
        """
        row = self.objective_row(objective_id)
        mudou_detail = row["status_detail"] != detail
        mudou_wait = wait_reason is not None and _col(row, "wait_reason") != wait_reason
        if not mudou_detail and not mudou_wait:
            return
        if mudou_wait:
            self.db.execute("UPDATE objectives SET status_detail=?, wait_reason=? WHERE id=?",
                            (truncate(detail, 600), wait_reason, objective_id))
        else:
            self.db.execute("UPDATE objectives SET status_detail=? WHERE id=?", (truncate(detail, 600), objective_id))
        self.emit_objective(objective_id, f"{row['instance_id']}: {detail}")

    def clear_wait_reason(self, objective_id: str, *, restore_detail: str | Sentinel = _SEM_RESTAURO) -> None:
        """Fim de uma espera TIPADA (item 7.3: vaga de IA ou resposta do modelo) que não terminou noutra
        transição de `set_objective` — `_ai` chama isto ao sair do limiter, sucesso ou erro.

        `restore_detail` devolve `status_detail` ao texto de ANTES da espera: sem isto, o campo estruturado
        (`wait_reason`) voltava a `NULL` mas o texto livre ficava preso em "aguardando resposta do modelo" até a
        PRÓXIMA chamada de IA — a etapa já batendo na tela, a interface ainda dizendo que espera o modelo. O
        valor de antes PODE ser `None` (nenhuma espera em curso naquele momento) — por isso o padrão é um
        sentinela distinto de `None`, não `None` em si: sem ele, "restaurar para None" e "não restaurar nada"
        eram indistinguíveis, e o restauro nunca acontecia na primeira chamada de IA de cada etapa.
        """
        row = self.objective_row(objective_id)
        tem_restauro = restore_detail is not _SEM_RESTAURO
        mudou_wait = _col(row, "wait_reason") is not None
        mudou_detail = tem_restauro and row["status_detail"] != restore_detail
        if not mudou_wait and not mudou_detail:
            return
        if tem_restauro:
            novo_detail = truncate(restore_detail, 600) if restore_detail else None
            self.db.execute("UPDATE objectives SET wait_reason=NULL, status_detail=? WHERE id=?",
                            (novo_detail, objective_id))
        else:
            self.db.execute("UPDATE objectives SET wait_reason=NULL WHERE id=?", (objective_id,))
        self.emit_objective(objective_id)

    def interrupted_steps(self) -> list[Row]:
        """Etapas em execução que são MINHAS — mais as sem dono, que só existem de antes da posse existir.

        Um segundo backend chamando isto não recebe as etapas vivas do primeiro: era exatamente o que acontecia
        antes, e teria destruído o trabalho dele no reinício."""
        return self.db.query(
            "SELECT * FROM steps WHERE status IN ('running','verifying') AND (claimed_by IS NULL OR claimed_by=?)"
            " ORDER BY run_id, seq", (self.owner_id,))

    def abandoned_steps(self) -> list[Row]:
        """Etapas de OUTRO dono cujo lease venceu: o dono parou de renovar, então caiu.

        Não é o mesmo que `interrupted_steps`. Ali a prova de que o dono morreu é o próprio processo ter reiniciado;
        aqui a prova é o tempo. É por isso que o lease é longo: reconciliar cedo demais significaria dois backends
        no mesmo aparelho."""
        return self.db.query(
            "SELECT * FROM steps WHERE status IN ('running','verifying') AND claimed_by IS NOT NULL AND claimed_by<>?"
            " AND claim_expires_at IS NOT NULL AND claim_expires_at < ? ORDER BY run_id, seq",
            # Pergunta e resposta no MESMO relógio, o do banco: é o que tira o NTP da lista de pré-requisitos.
            (self.owner_id, self.db.agora_iso()))

    def renew_claims(self) -> int:
        """Renova a posse do que este backend está executando. Enquanto ele respira, ninguém mais mexe."""
        cur = self.db.execute(
            "UPDATE steps SET claim_expires_at=? WHERE claimed_by=? AND status IN ('running','verifying')",
            (self.db.prazo_iso(POSSE_TTL_S), self.owner_id))
        return int(cur.rowcount or 0)

    def take_over(self, step: Row) -> bool:
        """Assume uma etapa abandonada para reconciliá-la em nome próprio. `True` só para quem ganhou.

        É compare-and-swap, não `UPDATE` cego, e o motivo é o cenário de TRÊS backends: A morre, B e C estão vivos,
        os dois veem a etapa em `abandoned_steps()` e os dois tentam adotá-la. Com escrita cega os dois "conseguem" e
        os dois reconciliam — a segunda passagem devolveria a tentativa duas vezes e só então esbarraria numa
        transição `ready → ready`. Condicionando ao dono e ao vencimento que EU li, só um ganha; o outro recebe
        `False` e não faz nada.
        """
        cur = self.db.execute(
            "UPDATE steps SET claimed_by=?, claim_expires_at=? WHERE id=? AND claimed_by=? AND claim_expires_at=?"
            " AND status IN ('running','verifying')",
            (self.owner_id, self.db.prazo_iso(POSSE_TTL_S), step["id"], step["claimed_by"], step["claim_expires_at"]))
        return (cur.rowcount or 0) == 1

    def cancel_open_steps(self, run_id: str, *, objective_id: str | None = None, reason: str) -> int:
        q = "SELECT id, status FROM steps WHERE run_id=? AND status IN ('pending','ready','retry_wait','waiting_user')"
        params: list[Any] = [run_id]
        if objective_id:
            q += " AND objective_id=?"
            params.append(objective_id)
        n = 0
        for r in self.db.query(q, tuple(params)):
            self.transition_step(r["id"], StepStatus.cancelled, detail=reason)
            n += 1
        return n

    # ================================================================== DTOs e eventos
    def run_summary(self, row: Row, *, deduplicated: bool | None = None) -> RunSummary:
        ids = loads(row["instance_ids"], [])
        counts = self._counts(row["id"])
        total = sum(counts.model_dump().values())
        return RunSummary(
            id=row["id"], short_id=row["id"].rsplit("-", 1)[-1], command=row["command"], status=RunStatus(row["status"]),
            simulated=bool(row["simulated"]), instance_ids=ids, instances_requested=len(ids),
            instances_used=row["instances_used"], created_at=row["created_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], counts=counts, progress=(counts.succeeded / total) if total else 0.0,
            status_detail=row["status_detail"], deduplicated=deduplicated,
            app_ids=loads(_col(row, "app_ids"), []) or [])

    def objective_dto(self, row: Row) -> ObjectiveDTO:
        done, total = self._step_progress(row["id"], row["plan_version"])
        return ObjectiveDTO(
            id=row["id"], run_id=row["run_id"], instance_id=row["instance_id"], status=ObjectiveStatus(row["status"]),
            worker_id=_col(row, "worker_id"), hosted_by=_col(row, "hosted_by"),
            device_serial=_col(row, "device_serial"), physical_id=_col(row, "physical_id"),
            status_detail=row["status_detail"], blocked_reason=row["blocked_reason"], needs=row["needs"],
            blocked_kind=_col(row, "blocked_kind"), wait_reason=_col(row, "wait_reason"),
            plan_version=row["plan_version"], parameters=loads(row["parameters"], {}), steps_done=done, steps_total=total,
            delivery_level=DeliveryLevel(row["delivery_level"]) if row["delivery_level"] else None,
            effects=loads(row["effects"], []), started_at=row["started_at"], finished_at=row["finished_at"],
            ai_calls=row["ai_calls"], ai_input_tokens=row["ai_input_tokens"], ai_output_tokens=row["ai_output_tokens"])

    def _step_progress(self, objective_id: str, version: int) -> tuple[int, int]:
        r = self.db.one("SELECT SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) d, COUNT(*) t FROM steps WHERE objective_id=? AND plan_version=?",
                        (objective_id, version))
        return int(r["d"] or 0), int(r["t"] or 0)

    @staticmethod
    def step_dto(r: Row) -> StepDTO:
        return StepDTO(
            id=r["id"], run_id=r["run_id"], objective_id=r["objective_id"], instance_id=r["instance_id"],
            plan_version=r["plan_version"], seq=r["seq"], key=r["key"], title=r["title"], goal=r["goal"],
            depends_on=loads(r["depends_on"], []), side_effect=bool(r["side_effect"]),
            commit_guard=loads(r["commit_guard"], []), precondition=r["precondition"],
            postcondition=Postcondition.model_validate_json(r["postcondition"]), timeout_s=r["timeout_s"],
            max_attempts=r["max_attempts"], attempts=r["attempts"], status=StepStatus(r["status"]),
            status_detail=r["status_detail"], next_retry_at=r["next_retry_at"], started_at=r["started_at"],
            finished_at=r["finished_at"], result=StepResult.model_validate_json(r["result"]) if r["result"] else None,
            claimed_by=_col(r, "claimed_by"),
            driven_by=r["driven_by"] if "driven_by" in r.keys() else None,
            capability=r["capability"], commit_selector=r["commit_selector"],
            band_guard=loads(r["band_guard"], []) or [], bindings=loads(r["bindings"], {}) or {},
            for_each=r["for_each"], variables=loads(r["variables"], {}) or {}, app_id=_col(r, "app_id"))

    @staticmethod
    def action_dto(r: Row) -> ActionDTO:
        return ActionDTO(id=r["id"], attempt_id=r["attempt_id"], seq=r["seq"], tool=r["tool"], args=loads(r["args"], {}),
                         rationale=r["rationale"], status=ActionStatus(r["status"]), side_effect=bool(r["side_effect"]),
                         intent_at=r["intent_at"], done_at=r["done_at"], result=loads(r["result"]), error=r["error"],
                         source=r["source"] if "source" in r.keys() else "ai")

    def attempt_dto(self, r: Row, *, with_actions: bool = True) -> AttemptDTO:
        actions = ([self.action_dto(a) for a in self.db.query("SELECT * FROM actions WHERE attempt_id=? ORDER BY seq",
                                                             (r["id"],))] if with_actions else [])
        return AttemptDTO(id=r["id"], step_id=r["step_id"], number=r["number"], status=AttemptStatus(r["status"]),
                          started_at=r["started_at"], finished_at=r["finished_at"], error=r["error"],
                          recovery=r["recovery"], observed_result=r["observed_result"], actions=actions)

    @staticmethod
    def evidence_dto(r: Row) -> EvidenceDTO:
        has_file = bool(r["path"]) and not r["redacted"]
        return EvidenceDTO(id=r["id"], run_id=r["run_id"], instance_id=r["instance_id"], step_id=r["step_id"],
                           attempt_id=r["attempt_id"], ts=r["ts"], kind=r["kind"], note=r["note"],
                           url=f"/api/evidence/{r['id']}" if has_file else None, redacted=bool(r["redacted"]))

    def run_detail(self, run_id: str) -> RunDetail | None:
        row = self.run_row(run_id)
        if row is None:
            return None
        summary = self.run_summary(row)
        objectives = [self.objective_dto(o) for o in
                      self.db.query("SELECT * FROM objectives WHERE run_id=? ORDER BY instance_id", (run_id,))]
        steps = [self.step_dto(s) for s in
                 self.db.query("SELECT * FROM steps WHERE run_id=? ORDER BY instance_id, plan_version, seq", (run_id,))]
        attempts = [self.attempt_dto(a) for a in self.db.query(
            "SELECT a.* FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=? ORDER BY a.started_at", (run_id,))]
        evidence = [self.evidence_dto(e) for e in self.db.query("SELECT * FROM evidence WHERE run_id=? ORDER BY id", (run_id,))]
        versions = [PlanVersionDTO(objective_id=v["objective_id"], version=v["version"], reason=v["reason"],
                                   created_at=v["created_at"], steps=[PlanStep.model_validate(s) for s in loads(v["steps"], [])])
                    for v in self.db.query(
                        "SELECT v.* FROM plan_versions v JOIN objectives o ON o.id=v.objective_id WHERE o.run_id=?"
                        " ORDER BY v.objective_id, v.version", (run_id,))]
        decisions = [DecisionDTO(ts=d["ts"], instance_id=d["instance_id"], text=d["message"]) for d in self.db.query(
            "SELECT ts, instance_id, message FROM events WHERE run_id=? AND kind='decision' ORDER BY id", (run_id,))]
        return RunDetail(**summary.model_dump(), plan=Plan.model_validate_json(row["plan"]) if row["plan"] else None,
                         objectives=objectives, steps=steps, attempts=attempts, evidence=evidence,
                         plan_versions=versions, decisions=decisions)

    def emit_run(self, run_id: str, message: str | None, *, level: str = "info") -> None:
        row = self.run_row(run_id)
        if row is None:
            return
        summary = self.run_summary(row)
        self.bus.emit("run.updated", message or f"Execução {run_id}: {summary.status.value}", level=level, run_id=run_id,
                      data={"run": summary.model_dump(mode="json")})

    def emit_step(self, step_id: str, message: str, *, level: str = "info") -> None:
        r = self.step_row(step_id)
        self.bus.emit("step.updated", f"{r['instance_id']}: {message}", level=level, run_id=r["run_id"],
                      instance_id=r["instance_id"], objective_id=r["objective_id"], step_id=step_id,
                      data={"step": self.step_dto(r).model_dump(mode="json")})

    def emit_attempt(self, attempt_id: str, step: Row | None) -> None:
        a = self.db.one("SELECT * FROM attempts WHERE id=?", (attempt_id,))
        if a is None or step is None:
            return
        self.bus.emit("attempt.updated", f"{step['instance_id']}: tentativa {a['number']} — {a['status']}",
                      run_id=step["run_id"], instance_id=step["instance_id"], objective_id=step["objective_id"],
                      step_id=step["id"], attempt_id=attempt_id,
                      data={"attempt": self.attempt_dto(a, with_actions=False).model_dump(mode="json")})

    def emit_action(self, action_id: int) -> None:
        r = self.db.one(
            "SELECT a.*, s.run_id, s.instance_id, s.objective_id, s.id AS sid FROM actions a"
            " JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id WHERE a.id=?", (action_id,))
        if r is None:
            return
        dto = self.action_dto(r)
        label = dto.rationale or dto.tool
        self.bus.emit("action.logged", f"{r['instance_id']}: {dto.tool} [{dto.status.value}] — {label}",
                      level="warn" if dto.status in (ActionStatus.failed, ActionStatus.unknown, ActionStatus.rejected) else "info",
                      run_id=r["run_id"], instance_id=r["instance_id"], objective_id=r["objective_id"], step_id=r["sid"],
                      attempt_id=r["attempt_id"],
                      data={"action": dto.model_dump(mode="json"), "instance_id": r["instance_id"], "step_id": r["sid"]})
