"""Habilidade (`SkillDefinition`) e versão (`SkillVersion`): §10.1, §10.2, §10.8 e §15.1.

A versão guarda o conteúdo na forma canônica (texto) e o hash dela. É imutável em memória (dataclass congelada, e
o documento sai sempre como cópia) e só muda de conteúdo em `draft`: `revise` recusa fora dele, e a versão nova é
outro número, com `parent_version`. O congelamento vale ao SAIR de `draft`, e não só ao publicar: senão a
validação de `validated` provaria um conteúdo e a publicação publicaria outro (decisão 3).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from app.modules.skills.domain.document import (JsonObject, JsonValue, NotJson, as_json_object, canonical_json,
                                                content_hash, parse_json_object)
from app.modules.skills.domain.lifecycle import (EDITABLE, ContentTampered, FrozenVersion, InvalidDocument,
                                                 SkillState, check_transition)
from app.modules.skills.domain.matching import normalize_command
from app.modules.skills.domain.refs import SkillRef

#: `schema_version` do conteúdo: 1 = `automation/v1alpha1`; 0 = plano legado congelado (fluxo, ou fluxo adotado).
SCHEMA_DSL_V1 = 1
SCHEMA_LEGACY_PLAN = 0


class SourceKind(StrEnum):
    LEGACY_FLOW = "legacy_flow"
    TEACHING = "teaching"
    RUN = "run"
    MANUAL = "manual"
    IMPORT = "import"


@dataclass(frozen=True, slots=True)
class UsesLock:
    """Trava de composição: a versão EXATA de cada habilidade usada, com o hash do conteúdo dela (§12.3)."""

    skill: str
    version: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class Provenance:
    """De onde a versão veio (§10.8). `kind`/`ref` viram colunas; o resto vai para o JSON `provenance`."""

    kind: SourceKind
    ref: str | None = None
    candidate_id: str | None = None
    teaching_id: str | None = None
    generated_by: str | None = None              # ai:<modelo> | person | merge
    compiler_version: str | None = None
    uses_lock: tuple[UsesLock, ...] = ()
    reviewed_by: str | None = None
    #: O que a origem legada tinha e não tem campo próprio (ex.: `flows.source`, `flows.source_run_id`).
    notes: tuple[tuple[str, str], ...] = ()

    def to_json(self) -> JsonObject:
        doc: JsonObject = {}
        for chave, valor in (("candidate_id", self.candidate_id), ("teaching_id", self.teaching_id),
                             ("generated_by", self.generated_by), ("compiler_version", self.compiler_version),
                             ("reviewed_by", self.reviewed_by)):
            if valor is not None:
                doc[chave] = valor
        if self.uses_lock:
            doc["uses_lock"] = [{"skill": u.skill, "version": u.version, "content_hash": u.content_hash}
                                for u in self.uses_lock]
        if self.notes:
            doc["notes"] = {k: v for k, v in self.notes}
        return doc

    @classmethod
    def from_json(cls, kind: SourceKind, ref: str | None, doc: JsonObject) -> Provenance:
        def texto(chave: str) -> str | None:
            valor = doc.get(chave)
            return valor if isinstance(valor, str) else None

        travas: list[UsesLock] = []
        bruto = doc.get("uses_lock")
        for item in bruto if isinstance(bruto, list) else []:
            if isinstance(item, dict):
                skill, version, hash_ = item.get("skill"), item.get("version"), item.get("content_hash")
                if isinstance(skill, str) and isinstance(version, int) and isinstance(hash_, str):
                    travas.append(UsesLock(skill, version, hash_))
        notas = doc.get("notes")
        return cls(kind=kind, ref=ref, candidate_id=texto("candidate_id"), teaching_id=texto("teaching_id"),
                   generated_by=texto("generated_by"), compiler_version=texto("compiler_version"),
                   uses_lock=tuple(travas), reviewed_by=texto("reviewed_by"),
                   notes=tuple((k, v) for k, v in sorted(notas.items()) if isinstance(v, str))
                   if isinstance(notas, dict) else ())


@dataclass(frozen=True, slots=True)
class DocumentFacts:
    """O que o validador do documento extrai dele (§12.2): metadados, comando-modelo, apps exigidos e erros.

    Vem da porta `DocumentValidator` (o schema `automation/v1alpha1` e o compilador são de outra peça). `errors`
    vazio é condição para `draft → candidate`; um rascunho com erro ainda pode ser salvo — é trabalho em andamento.
    """

    skill_id: str | None
    name: str | None
    description: str = ""
    app_id: str | None = None
    command_template: str | None = None
    app_ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    schema_version: int = SCHEMA_DSL_V1


@dataclass(frozen=True, slots=True)
class SkillScope:
    """A quem a habilidade vale (mesma semântica de `flow_scope`): vazio = todos."""

    profile_ids: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()

    @property
    def everyone(self) -> bool:
        return not self.profile_ids and not self.group_ids


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    app_id: str | None
    legacy_flow_id: str | None
    created_by: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SkillVersion:
    ref: SkillRef
    state: SkillState
    schema_version: int
    content_json: str                      # forma canônica; `document()` devolve a cópia parseada
    content_hash: str
    command_template: str | None
    match_key: str | None
    app_ids: tuple[str, ...]
    parent_version: int | None
    provenance: Provenance
    created_by: str | None
    created_at: str
    state_at: str
    state_by: str | None
    state_detail: str | None

    # -------------------------------------------------------------- construção
    @classmethod
    def new_draft(cls, ref: SkillRef, document: JsonObject, facts: DocumentFacts, *, provenance: Provenance,
                  parent_version: int | None, by: str | None, now: str) -> SkillVersion:
        texto, hash_ = _canonical(document)
        return cls(ref=ref, state=SkillState.DRAFT, schema_version=facts.schema_version, content_json=texto,
                   content_hash=hash_, command_template=facts.command_template,
                   match_key=_match_key(facts.command_template), app_ids=_apps(facts), parent_version=parent_version,
                   provenance=provenance, created_by=by, created_at=now, state_at=now, state_by=by,
                   state_detail="rascunho criado")

    @classmethod
    def frozen(cls, ref: SkillRef, document: JsonObject, *, state: SkillState, schema_version: int,
               command_template: str | None, app_ids: Sequence[str], provenance: Provenance, at: str,
               by: str | None, detail: str | None) -> SkillVersion:
        """Uma versão que nasce fora de `draft`: o fluxo legado (`flow:<id>@1`) e a v1 de um fluxo adotado."""
        texto, hash_ = _canonical(document)
        return cls(ref=ref, state=state, schema_version=schema_version, content_json=texto, content_hash=hash_,
                   command_template=command_template, match_key=_match_key(command_template),
                   app_ids=tuple(sorted({a for a in app_ids if a})), parent_version=None,
                   provenance=provenance, created_by=by, created_at=at, state_at=at, state_by=by, state_detail=detail)

    # -------------------------------------------------------------- leitura
    def document(self) -> JsonObject:
        return parse_json_object(self.content_json)

    @property
    def editable(self) -> bool:
        return self.state in EDITABLE

    def verify_integrity(self) -> None:
        """O hash gravado confere com o conteúdo? Como `Database.divergencias` faz com migração, na leitura."""
        try:
            atual = content_hash(self.document())
        except NotJson as exc:
            raise ContentTampered(f"{self.ref}: conteúdo gravado não é JSON ({exc}).") from exc
        if atual != self.content_hash:
            raise ContentTampered(f"{self.ref}: o conteúdo não bate com o hash gravado — versão alterada por fora.")

    # -------------------------------------------------------------- mudança
    def revise(self, document: JsonObject, facts: DocumentFacts) -> SkillVersion:
        """Novo conteúdo para ESTE rascunho. Fora de `draft`, recusa: a edição vira uma versão nova."""
        if not self.editable:
            raise FrozenVersion(f"{self.ref} está em '{self.state}': o conteúdo não muda mais. "
                                "Crie uma nova versão a partir dela.")
        if facts.schema_version != self.schema_version:
            raise InvalidDocument(f"{self.ref}: o formato do documento não pode mudar num rascunho "
                                  f"({self.schema_version} → {facts.schema_version}).")
        texto, hash_ = _canonical(document)
        return replace(self, content_json=texto, content_hash=hash_, command_template=facts.command_template,
                       match_key=_match_key(facts.command_template), app_ids=_apps(facts))

    def moved_to(self, to: SkillState, *, by: str, detail: str | None, at: str) -> SkillVersion:
        check_transition(self.state, to, by)
        return replace(self, state=to, state_at=at, state_by=by, state_detail=detail)


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    ref: SkillRef
    from_state: SkillState | None
    to_state: SkillState
    reason: str | None
    decided_by: str | None
    decided_at: str


@dataclass(frozen=True, slots=True)
class SkillSummary:
    """Uma linha de listagem: sem o conteúdo, com a integridade conferida."""

    ref: SkillRef
    name: str
    app_id: str | None
    state: SkillState
    command_template: str | None
    schema_version: int
    content_hash: str
    intact: bool
    state_at: str
    #: O fluxo que a habilidade adotou (fase J), para o painel saber que fluxo "virou" qual habilidade.
    legacy_flow_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedSkill:
    """O comando casou com esta versão. `parameters` são os valores que o COMANDO deu a cada `{nome}` do modelo."""

    definition: SkillDefinition
    version: SkillVersion
    parameters: Mapping[str, str] = field(default_factory=dict)

    @property
    def ref(self) -> SkillRef:
        return self.version.ref

    @property
    def legacy_flow_id(self) -> str | None:
        """O fluxo por trás da resolução: o próprio (`flow:<id>@1`) ou o que a habilidade adotou."""
        return self.version.ref.legacy_flow_id or self.definition.legacy_flow_id


# ------------------------------------------------------------------ auxiliares
def _canonical(document: JsonObject) -> tuple[str, str]:
    copia = as_json_object(document)
    return canonical_json(copia), content_hash(copia)


def _match_key(template: str | None) -> str | None:
    return normalize_command(template) if template and template.strip() else None


def _apps(facts: DocumentFacts) -> tuple[str, ...]:
    return tuple(sorted({a for a in (facts.app_id, *facts.app_ids) if a}))


def legacy_plan_parameters(document: JsonObject) -> dict[str, str]:
    """`plan.parameters` de um conteúdo legado (`schema_version` 0), só com valores texto (como `Plan` exige)."""
    plano: JsonValue = document.get("plan")
    params = plano.get("parameters") if isinstance(plano, dict) else None
    if not isinstance(params, dict):
        return {}
    return {k: v for k, v in params.items() if isinstance(v, str)}
