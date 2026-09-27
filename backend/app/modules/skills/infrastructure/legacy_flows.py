"""O fluxo legado visto como habilidade: `flow:<flows.id>@1`, só leitura (§15.1, ADR-034).

Embrulha o `FlowStore` em vez de reimplementá-lo: a resolução DELEGA a `FlowStore.match`, e com isso a ordem
(`uses DESC, created_at`), o escopo por perfil e grupo (`flow_scope`), a extração dos valores e o "faltou valor,
não é este fluxo" são exatamente os de hoje. Nada aqui escreve em `flows`.

Como o fluxo vira versão:
- estado `published` se `flows.status='active'`, senão `disabled`;
- conteúdo `{schema_version: 0, command_template, plan, required_apps}`, com os apps de `flow_required_apps` (a
  tabela manda, como em `FlowStore.match`). O hash é calculado na leitura, nunca gravado: reflete o fluxo de agora;
- proveniência `legacy_flow`, com `flows.source` e `flows.source_run_id` nas notas.

A compilação de conteúdo legado é passagem direta (`legacy_plan`): o plano congelado já é um `Plan`.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.db import Database, Row
from app.models import Plan, PlannerInfo
from app.modules.skills.domain.document import JsonObject, JsonValue, parse_json_object
from app.modules.skills.domain.lifecycle import InvalidDocument, SkillNotFound, SkillState
from app.modules.skills.domain.matching import bind_template_parameters, extract_parameters
from app.modules.skills.domain.refs import SkillRef, is_legacy_skill_id
from app.modules.skills.domain.versions import (SCHEMA_LEGACY_PLAN, Provenance, ResolvedSkill, SkillDefinition,
                                                SkillSummary, SkillVersion, SourceKind, legacy_plan_parameters)
from app.modules.skills.infrastructure.rows import texto, texto_ou_nulo
from app.taskqueue.flows import FlowStore


def legacy_content(row: Row, required_apps: Sequence[str]) -> JsonObject:
    """O conteúdo de um fluxo como versão congelada. Usado também na ADOÇÃO: a v1 da habilidade é este conteúdo."""
    apps: list[JsonValue] = [a for a in sorted(required_apps)]
    return {"schema_version": SCHEMA_LEGACY_PLAN, "command_template": texto(row, "command_template"),
            "plan": parse_json_object(texto(row, "plan")), "required_apps": apps}


def legacy_provenance(row: Row) -> Provenance:
    notas = tuple((k, v) for k in ("source", "source_run_id") if (v := texto_ou_nulo(row, k)) is not None)
    return Provenance(kind=SourceKind.LEGACY_FLOW, ref=texto(row, "id"), notes=notas)


def required_apps(db: Database, flow_id: str) -> list[str]:
    return [texto(r, "app_id") for r in db.query(
        "SELECT app_id FROM flow_required_apps WHERE flow_id=? ORDER BY app_id", (flow_id,))]


class LegacyFlowAdapter:
    def __init__(self, db: Database, flows: FlowStore | None = None) -> None:
        self._db = db
        self._flows = flows if flows is not None else FlowStore(db)

    # ------------------------------------------------------------------ SkillSource
    def resolve(self, command: str, profile_ids: Sequence[str | None] | None) -> ResolvedSkill | None:
        casado = self._flows.match(command, list(profile_ids) if profile_ids is not None else None)
        if casado is None:
            return None
        row, plano = casado
        versao = self._version(row)
        valores = extract_parameters(texto(row, "command_template"), command)
        if valores is None:
            # Não acontece enquanto as duas extrações forem a mesma (teste de equivalência); se um dia divergirem,
            # os valores saem do plano que o próprio `FlowStore` montou, e o `Plan` final continua idêntico.
            modelo = legacy_plan_parameters(versao.document())
            valores = {k: plano.parameters[k] for k, v in modelo.items()
                       if v == "{" + k + "}" and k in plano.parameters}
        return ResolvedSkill(definition=self._definition(row), version=versao, parameters=valores)

    def get(self, ref: SkillRef) -> SkillVersion:
        flow_id = ref.legacy_flow_id
        row = self._db.one("SELECT * FROM flows WHERE id=?", (flow_id,)) if flow_id else None
        if row is None:
            raise SkillNotFound(f"Fluxo não encontrado: {ref}.")
        return self._version(row)

    def published(self, skill_id: str) -> SkillVersion | None:
        if not is_legacy_skill_id(skill_id):
            return None
        row = self._db.one("SELECT * FROM flows WHERE id=? AND status='active'",
                           (SkillRef(skill_id, 1).legacy_flow_id,))
        return self._version(row) if row else None

    def definition(self, skill_id: str) -> SkillDefinition | None:
        if not is_legacy_skill_id(skill_id):
            return None
        row = self._db.one("SELECT * FROM flows WHERE id=?", (SkillRef(skill_id, 1).legacy_flow_id,))
        return self._definition(row) if row else None

    def list(self, *, app_id: str | None = None, state: SkillState | None = None) -> list[SkillSummary]:
        resumos = []
        for row in self._db.query("SELECT * FROM flows ORDER BY id"):
            versao = self._version(row)
            if state is not None and versao.state is not state:
                continue
            if app_id is not None and app_id != texto_ou_nulo(row, "app_id") and app_id not in versao.app_ids:
                continue
            resumos.append(SkillSummary(ref=versao.ref, name=texto(row, "name"), app_id=texto_ou_nulo(row, "app_id"),
                                        state=versao.state, command_template=versao.command_template,
                                        schema_version=versao.schema_version, content_hash=versao.content_hash,
                                        intact=True, state_at=versao.state_at))
        return resumos

    # ------------------------------------------------------------------ mapeamento
    def _version(self, row: Row) -> SkillVersion:
        apps = required_apps(self._db, texto(row, "id"))
        estado = SkillState.PUBLISHED if texto(row, "status") == "active" else SkillState.DISABLED
        return SkillVersion.frozen(SkillRef.legacy(texto(row, "id")), legacy_content(row, apps), state=estado,
                                   schema_version=SCHEMA_LEGACY_PLAN,
                                   command_template=texto(row, "command_template"), app_ids=apps,
                                   provenance=legacy_provenance(row), at=texto(row, "created_at"), by=None,
                                   detail=None)

    @staticmethod
    def _definition(row: Row) -> SkillDefinition:
        return SkillDefinition(id=SkillRef.legacy(texto(row, "id")).skill_id, name=texto(row, "name"),
                               description="", app_id=texto_ou_nulo(row, "app_id"),
                               legacy_flow_id=texto(row, "id"), created_by=None, created_at=texto(row, "created_at"),
                               updated_at=texto_ou_nulo(row, "last_used_at") or texto(row, "created_at"))


def legacy_plan(resolved: ResolvedSkill) -> Plan:
    """Conteúdo legado (`schema_version` 0) + valores do comando → o `Plan` da execução, sem compilador e sem IA.

    Para `flow:<id>@1` o resultado é o MESMO `Plan` que `FlowStore.match` devolve hoje (planejador `fluxo:<id>`); para
    a v1 de um fluxo adotado, o planejador passa a dizer `skill:<id>@<n>` e o resto é igual.
    """
    versao = resolved.version
    if versao.schema_version != SCHEMA_LEGACY_PLAN:
        raise InvalidDocument(f"{versao.ref}: conteúdo v{versao.schema_version} é do compilador, não do legado.")
    documento = versao.document()
    plano = Plan.model_validate(documento.get("plan"))
    ligados = bind_template_parameters(plano.parameters, resolved.parameters)
    if ligados is None:
        raise InvalidDocument(f"{versao.ref}: o comando não dá valor a todos os parâmetros do plano.")
    plano.parameters = ligados
    if versao.ref.is_legacy:
        plano.planner = PlannerInfo(provider="fluxo", model=f"fluxo:{versao.ref.legacy_flow_id}",
                                    simulated=plano.planner.simulated)
    else:
        plano.planner = PlannerInfo(provider="skill", model=f"skill:{versao.ref}", simulated=plano.planner.simulated)
    exigidos = documento.get("required_apps")
    apps = [a for a in exigidos if isinstance(a, str)] if isinstance(exigidos, list) else []
    plano.required_apps = apps or plano.required_apps
    return plano
