"""Formatos de saída estruturada e a conversão JSON → objeto do domínio, comuns a TODOS os provedores.

Por que saiu de `anthropic_provider.py`: com um segundo provedor real (o compatível com OpenAI), a tradução de
"o que o modelo devolveu" para `Plan`/`Verdict`/`SocialDraftDTO` passou a ter dois chamadores. Copiada, ela
viraria duas verdades — e a diferença só apareceria como plano válido num provedor e inválido no outro.

O que fica com cada provedor: o TRANSPORTE (como se pede saída estruturada, como se mandam ferramentas e imagem)
e o tratamento de erro da API. Isso sim é específico de cada um.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from ..models import (DeliveryLevel, MissingInfo, Plan, PlannerInfo, PlanStep, Postcondition, SocialDraftDTO)
from .capabilities import CapabilityNode, compose
from .provider import AIError, PlanRequest, Verdict


# ---- formatos de saída estruturada (compatíveis com strict) -------------------
class _ParamOut(BaseModel):
    name: str
    value: str


class _PostOut(BaseModel):
    kind: Literal["text_visible", "app_foreground", "element_present", "model_judged", "items_collected"]
    value: str
    description: str
    required_delivery_level: DeliveryLevel | None


class _StepOut(BaseModel):
    key: str
    title: str
    goal: str
    depends_on: list[str]
    side_effect: bool
    commit_guard: list[str]
    precondition: str | None
    postcondition: _PostOut
    timeout_s: int
    max_attempts: int
    for_each: str | None


class _PlanOut(BaseModel):
    summary: str
    app_id: str | None
    parameters: list[_ParamOut]
    success_criteria: list[str]
    steps: list[_StepOut]
    missing: list[MissingInfo]


# Formato do planejamento COM catálogo: por etapa, só o que o modelo realmente decide. Esquema pequeno é esquema
# que valida; a etapa em si é montada pelo backend, com texto revisado por gente.
class _BindingOut(BaseModel):
    name: str
    value: str


class _CapStepOut(BaseModel):
    key: str
    capability: str
    depends_on: list[str]
    bindings: list[_BindingOut]
    for_each: str | None


class _CapPlanOut(BaseModel):
    summary: str
    parameters: list[_ParamOut]
    success_criteria: list[str]
    steps: list[_CapStepOut]
    missing: list[MissingInfo]


def norm_key(key: str) -> str:
    """O schema estrito não carrega o `pattern` da chave; normaliza 'Open-App' → 'open_app' em vez de rejeitar o plano."""
    k = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")[:40]
    return k if re.match(r"^[a-z]", k) and len(k) >= 2 else f"step_{k or 'x'}"


def loads_json(raw: str, what: str) -> Any:
    """JSON do modelo. Um provedor sem `json_schema` costuma embrulhar em cerca de código — desembrulhar aqui é
    mais barato e muito mais previsível do que pedir de novo."""
    texto = (raw or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto).strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise AIError(f"{what} inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc


def plan_from_json(raw: str, req: PlanRequest, *, provider: str, model: str, max_steps: int) -> Plan:
    """Planejamento LIVRE (app sem catálogo)."""
    try:
        out = _PlanOut.model_validate(loads_json(raw, "Plano"))
    except ValidationError as exc:
        raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
    app = next((a for a in req.apps if a.id == out.app_id), None)
    try:
        plan = Plan(
            summary=out.summary, app_id=app.id if app else None, app_package=app.package if app else None,
            parameters={p.name: p.value for p in out.parameters}, success_criteria=out.success_criteria,
            steps=[PlanStep(key=norm_key(s.key), title=s.title, goal=s.goal,
                            depends_on=[norm_key(d) for d in s.depends_on],
                            side_effect=s.side_effect, commit_guard=s.commit_guard, precondition=s.precondition,
                            postcondition=Postcondition(**s.postcondition.model_dump()),
                            timeout_s=max(30, min(s.timeout_s, 600)),
                            max_attempts=1 if s.side_effect else max(1, min(s.max_attempts, 5)),
                            for_each=norm_key(s.for_each) if s.for_each else None)
                   for s in out.steps[:max_steps]],
            missing=out.missing, planner=PlannerInfo(provider=provider, model=model, simulated=False))
    except (ValidationError, ValueError) as exc:
        raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
    if out.app_id and app is None:
        plan.missing.append(MissingInfo(field="app", question=f"O app '{out.app_id}' não está configurado. "
                                                              "Qual aplicativo configurado deve ser usado?"))
    return plan


def catalog_plan_from_json(raw: str, req: PlanRequest, *, provider: str, model: str, max_steps: int) -> Plan:
    """Planejamento COM catálogo: o modelo escolhe ações e argumentos; o backend monta as etapas."""
    try:
        out = _CapPlanOut.model_validate(loads_json(raw, "Plano"))
    except ValidationError as exc:
        raise AIError(f"Plano inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc
    app = next((a for a in req.apps if a.package == req.catalog.package), None)
    nodes = [CapabilityNode(key=norm_key(s.key), capability=s.capability,
                            depends_on=[norm_key(d) for d in s.depends_on],
                            bindings={b.name: b.value for b in s.bindings},
                            for_each=norm_key(s.for_each) if s.for_each else None)
             for s in out.steps[:max_steps]]
    steps, missing = compose(req.catalog, nodes)
    return Plan(summary=out.summary, app_id=app.id if app else None,
                app_package=app.package if app else req.catalog.package,
                parameters={p.name: p.value for p in out.parameters},
                success_criteria=out.success_criteria, steps=[] if missing else steps,
                missing=out.missing + missing,
                planner=PlannerInfo(provider=provider, model=model, simulated=False))


def verdict_from_json(raw: str) -> Verdict:
    try:
        return Verdict.model_validate(loads_json(raw, "Veredito"))
    except ValidationError as exc:
        raise AIError(f"Veredito inválido devolvido pelo modelo: {exc}", kind="invalid_output") from exc


def social_from_json(raw: str, max_length: int) -> SocialDraftDTO:
    try:
        draft = SocialDraftDTO.model_validate(loads_json(raw, "Resposta social"))
    except ValidationError as exc:
        raise AIError(f"Resposta social inválida devolvida pelo modelo: {exc}", kind="invalid_output") from exc
    if len(draft.content) > max_length:
        # Cortar aqui é mais barato e mais previsível do que pedir de novo; o limite é do app, não do modelo.
        draft.content = draft.content[:max_length].rstrip()
    return draft
