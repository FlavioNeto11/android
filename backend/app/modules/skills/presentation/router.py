"""Rotas HTTP de habilidades e do ensino v2 (§15.3), atrás de `skills.enabled` (decisão P1).

Com o interruptor desligado, TODA rota daqui responde 404 `skills_disabled` — explícito, para o painel (que olha
`health.features.skills`) e para quem chama a API saber que a rota existe e está desligada, em vez de um 404 de rota
inexistente. As rotas legadas (`/api/training*`, `/api/flows`) não passam por aqui e não mudam.

- `/api/skills`: lista, detalhe, versão, status (transição), escopo e rollback — o `SqlSkillRepository`.
- `/api/teaching-sessions`: iniciar, anexar demonstração (gravação v1 ou execução), correção, pedir candidata,
  responder, descartar — o `TeachingService`.
- `/api/skill-candidates/{id}`: ler, compilar, validar (estática) e publicar (= virar rascunho de versão).
- `/api/flows/{id}/adopt` e `/api/flows/{id}/release` (fase J): converter um fluxo em habilidade (v1 publicada = o
  plano do fluxo, v2 rascunho = o documento descompilado, fluxo desligado; uma transação) e desfazer; e
  `/api/skills/{id}/versions/{n}/decompile`, a v1 → v2 de quem adotou um fluxo antes do descompilador existir.

É a camada de apresentação: fala FastAPI, traduz as recusas do domínio (`SkillError.code`) para o `{code, message}`
de sempre da API e monta o JSON. Regra nenhuma de negócio mora aqui. `/api/skills/resolve` é da fase I e mora em
`api.py`, registrado ANTES deste roteador — `/api/skills/{skill_id}` não o sombreia.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.skills.application.teaching import CandidateDetail, TeachingService, TeachingView
from app.modules.skills.domain.document import JsonObject, JsonValue
from app.modules.skills.domain.lifecycle import (InvalidDocument, SkillError, SkillNotFound, SkillState,
                                                 ValidationPending)
from app.modules.skills.domain.refs import InvalidSkillRef, SkillRef
from app.modules.skills.domain.teaching import (CredentialInText, Demonstration, GeneralizerFailed, SkillCandidate,
                                                TeachingInputInvalid, TeachingNotFound, TeachingSession,
                                                TeachingStatus, TeachingTurn)
from app.modules.skills.domain.versions import DocumentFacts, SkillSummary, SkillVersion, TransitionRecord
from app.modules.skills.infrastructure.decompiler import DecompileIssue, PlanDecompiler
from app.modules.skills.infrastructure.flow_conversion import FlowConverter
from app.modules.skills.infrastructure.run_planning import SkillRunPlanner
from app.modules.skills.infrastructure.sql_repository import SecretInParameters, SqlSkillRepository
from app.shared.costuras import autor_do_gesto

T = TypeVar("T")


# ------------------------------------------------------------------ acesso ao estado
def _poc_attr(request: Request, nome: str) -> object:
    poc: object = getattr(request.app.state, "poc", None)
    valor: object = getattr(poc, nome, None)
    return valor


def _ensino(request: Request) -> TeachingService:
    servico = _poc_attr(request, "teaching")
    if not isinstance(servico, TeachingService):
        raise HTTPException(503, detail={"code": "not_ready", "message": "O ensino ainda não foi composto."})
    return servico


def _habilidades(request: Request) -> SqlSkillRepository:
    repo = _poc_attr(request, "skill_repo")
    if not isinstance(repo, SqlSkillRepository):
        raise HTTPException(503, detail={"code": "not_ready", "message": "O repositório de habilidades não subiu."})
    return repo


def _conversor(request: Request) -> FlowConverter:
    """O descompilador confere o documento pelo compilador da EXECUÇÃO (`skill_planner.compiler`), não por um
    montado à parte: "converteu equivalente" e "roda igual" não divergem por caminho."""
    planejador = _poc_attr(request, "skill_planner")
    if not isinstance(planejador, SkillRunPlanner):
        raise HTTPException(503, detail={"code": "not_ready", "message": "O planejador de habilidades não subiu."})
    return FlowConverter(_habilidades(request), PlanDecompiler(planejador.compiler))


def _exige_habilidades(request: Request) -> None:
    """O interruptor, lido a CADA requisição: a configuração muda com o processo no ar."""
    if not _ensino(request).enabled:
        raise HTTPException(404, detail={"code": "skills_disabled",
                                         "message": "As habilidades versionadas estão desligadas nesta instalação "
                                                    "(skills.enabled: false). O treino e os fluxos continuam como "
                                                    "sempre."})


def _quem(request: Request) -> str:
    """Quem decide: o operador da sessão do painel, ou `panel`. Nunca o ator de sistema (ele tem outras permissões
    na tabela de transições). A regra é a do kernel (`autor_do_gesto`): a mesma do livro e dos sinais de gesto."""
    return autor_do_gesto(getattr(request.state, "operador", None))


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidSkillRef):
        return HTTPException(400, detail={"code": "invalid_ref", "message": str(exc)})
    assert isinstance(exc, SkillError)
    detalhe: dict[str, JsonValue] = {"code": exc.code, "message": str(exc)}
    if isinstance(exc, (SkillNotFound, TeachingNotFound)):
        status = 404
    elif isinstance(exc, (TeachingInputInvalid, CredentialInText, SecretInParameters)):
        status = 400
    elif isinstance(exc, InvalidDocument):
        status, detalhe["errors"] = 422, list(exc.errors)
    elif isinstance(exc, GeneralizerFailed):
        status = 502
    else:
        status = 409
        if isinstance(exc, ValidationPending):
            detalhe["pending"] = list(exc.pending)
    return HTTPException(status, detail=detalhe)


def _chamar(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except (SkillError, InvalidSkillRef) as exc:
        raise _http(exc) from exc


router = APIRouter(prefix="/api", dependencies=[Depends(_exige_habilidades)])


# ================================================================== corpos
class _Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransitionBody(_Corpo):
    to: SkillState
    reason: str = Field(default="", max_length=500)
    manual: bool = False


class ScopeBody(_Corpo):
    profile_ids: list[str] = Field(default_factory=list, max_length=500)
    group_ids: list[str] = Field(default_factory=list, max_length=100)


class RollbackBody(_Corpo):
    version: int = Field(ge=1)
    reason: str = Field(default="", max_length=500)


class TeachingStartBody(_Corpo):
    instruction: str = Field(default="", max_length=2000)
    skill_id: str | None = Field(default=None, max_length=64)
    base_version: int | None = Field(default=None, ge=1)
    app_id: str | None = Field(default=None, max_length=64)
    profile_id: str | None = Field(default=None, max_length=64)


class DemonstrationBody(_Corpo):
    training_session_id: str | None = Field(default=None, max_length=80)
    run_id: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class CorrectionBody(_Corpo):
    body: str = Field(min_length=1, max_length=2000)
    run_id: str = Field(min_length=1, max_length=80)
    step_id: str = Field(min_length=1, max_length=200)
    payload: dict[str, str] = Field(default_factory=dict)


class ProposeBody(_Corpo):
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class AnswerBody(_Corpo):
    question_id: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=2000)


class ValidateBody(_Corpo):
    mode: Literal["static"] = "static"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class PublishBody(_Corpo):
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class AdoptBody(_Corpo):
    #: Sem id, a habilidade que já adotou o fluxo (readoção) ou `<app>.<fluxo>`.
    skill_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=500)


class ReleaseBody(_Corpo):
    reason: str = Field(default="", max_length=500)


# ================================================================== /api/skills
@router.get("/skills", response_model=None)
async def list_skills(request: Request, app_id: str | None = None, state: SkillState | None = None
                      ) -> list[JsonObject]:
    return [_resumo(s) for s in _habilidades(request).list(app_id=app_id, state=state)]


@router.get("/skills/{skill_id}", response_model=None)
async def get_skill(request: Request, skill_id: str) -> JsonObject:
    repo = _habilidades(request)
    definicao = repo.definition(skill_id)
    if definicao is None:
        raise HTTPException(404, detail={"code": "not_found", "message": f"Habilidade não encontrada: {skill_id}."})
    escopo = repo.scope(skill_id)
    versoes = [s for s in repo.list() if s.ref.skill_id == skill_id]
    return {"id": definicao.id, "name": definicao.name, "description": definicao.description,
            "app_id": definicao.app_id, "legacy_flow_id": definicao.legacy_flow_id,
            "created_by": definicao.created_by, "created_at": definicao.created_at,
            "updated_at": definicao.updated_at,
            "scope": {"profile_ids": list(escopo.profile_ids), "group_ids": list(escopo.group_ids)},
            "versions": [_resumo(s) for s in versoes]}


@router.get("/skills/{skill_id}/versions/{version}", response_model=None)
async def get_skill_version(request: Request, skill_id: str, version: int) -> JsonObject:
    repo = _habilidades(request)
    return _chamar(lambda: _versao(repo.get(SkillRef(skill_id, version)),
                                   repo.history(SkillRef(skill_id, version))))


@router.post("/skills/{skill_id}/versions/{version}/status", response_model=None)
async def transition_skill_version(request: Request, skill_id: str, version: int, body: TransitionBody) -> JsonObject:
    repo = _habilidades(request)
    ref = _chamar(lambda: SkillRef(skill_id, version))
    versao = _chamar(lambda: repo.transition(ref, body.to, by=_quem(request), reason=body.reason, manual=body.manual))
    return _versao(versao, repo.history(ref))


@router.put("/skills/{skill_id}/scope", response_model=None)
async def set_skill_scope(request: Request, skill_id: str, body: ScopeBody) -> JsonObject:
    repo = _habilidades(request)
    _chamar(lambda: repo.set_scope(skill_id, profile_ids=body.profile_ids, group_ids=body.group_ids))
    escopo = repo.scope(skill_id)
    return {"skill_id": skill_id, "profile_ids": list(escopo.profile_ids), "group_ids": list(escopo.group_ids)}


@router.post("/skills/{skill_id}/rollback", response_model=None)
async def rollback_skill(request: Request, skill_id: str, body: RollbackBody) -> JsonObject:
    """Volta a publicar uma versão `deprecated` (§10.3): a publicada atual é depreciada na mesma transação."""
    repo = _habilidades(request)
    ref = _chamar(lambda: SkillRef(skill_id, body.version))
    versao = _chamar(lambda: repo.transition(ref, SkillState.PUBLISHED, by=_quem(request),
                                             reason=body.reason or "rollback"))
    return _versao(versao, repo.history(ref))


# ================================================================== conversão de fluxo (fase J)
@router.post("/flows/{flow_id}/adopt", status_code=201, response_model=None)
async def adopt_flow(request: Request, flow_id: str, body: AdoptBody | None = None) -> JsonObject:
    """Converte o fluxo ATIVO em habilidade, numa transação: v1 publicada com o plano do fluxo (as execuções seguem
    idênticas), v2 em rascunho com o documento descompilado, fluxo desligado. O que a ida e volta pelo compilador não
    reproduz recusa tudo (422, `errors`); os avisos voltam na resposta."""
    repo, conversor = _habilidades(request), _conversor(request)
    corpo = body or AdoptBody()
    c = _chamar(lambda: conversor.convert(flow_id, by=_quem(request), skill_id=corpo.skill_id, reason=corpo.reason))
    return {"flow_id": flow_id, "skill_id": c.published.ref.skill_id,
            "published": _versao(c.published, repo.history(c.published.ref)),
            "draft": _versao(c.draft, repo.history(c.draft.ref)), "warnings": [_problema(w) for w in c.warnings]}


@router.post("/flows/{flow_id}/release", response_model=None)
async def release_flow(request: Request, flow_id: str, body: ReleaseBody | None = None) -> JsonObject:
    """Desfaz a conversão: a versão publicada desabilitada, o fluxo religado como era, os rascunhos da conversão
    apagados — uma transação."""
    repo, conversor = _habilidades(request), _conversor(request)
    motivo = body.reason if body is not None else ""
    d = _chamar(lambda: conversor.undo(flow_id, by=_quem(request), reason=motivo))
    return {"flow_id": flow_id, "skill_id": d.skill_id, "flow_status": "active",
            "discarded_drafts": [str(r) for r in d.discarded_drafts],
            "versions": [_resumo(s) for s in repo.list() if s.ref.skill_id == d.skill_id]}


@router.post("/skills/{skill_id}/versions/{version}/decompile", status_code=201, response_model=None)
async def decompile_skill_version(request: Request, skill_id: str, version: int) -> JsonObject:
    """v1 → v2: o rascunho da DSL a partir de uma versão de conteúdo legado (a adoção feita antes da fase J)."""
    repo, conversor = _habilidades(request), _conversor(request)
    ref = _chamar(lambda: SkillRef(skill_id, version))
    rascunho, avisos = _chamar(lambda: conversor.draft_from(ref, by=_quem(request)))
    return {"draft": _versao(rascunho, repo.history(rascunho.ref)), "warnings": [_problema(w) for w in avisos]}


# ================================================================== /api/teaching-sessions
@router.post("/teaching-sessions", status_code=201, response_model=None)
async def start_teaching(request: Request, body: TeachingStartBody) -> JsonObject:
    servico = _ensino(request)
    return _visao(_chamar(lambda: servico.start(body.instruction, skill_id=body.skill_id,
                                                base_version=body.base_version, app_id=body.app_id,
                                                profile_id=body.profile_id, operator=_quem(request))))


@router.get("/teaching-sessions", response_model=None)
async def list_teaching(request: Request, status: TeachingStatus | None = None,
                        limit: int = Query(50, ge=1, le=200),
                        training_session_id: str | None = Query(None, max_length=80)) -> list[JsonObject]:
    """Resumos. Com `training_session_id`, o ensino que usa aquela gravação (zero ou um)."""
    return [_resumo_do_ensino(v) for v in _ensino(request).list_sessions(
        status=status, limit=limit, training_session_id=training_session_id)]


@router.get("/teaching-sessions/{teaching_id}", response_model=None)
async def get_teaching(request: Request, teaching_id: str) -> JsonObject:
    servico = _ensino(request)
    return _visao(_chamar(lambda: servico.get(teaching_id)))


@router.post("/teaching-sessions/{teaching_id}/demonstrations", response_model=None)
async def attach_demonstration(request: Request, teaching_id: str, body: DemonstrationBody) -> JsonObject:
    servico = _ensino(request)
    if (body.training_session_id is None) == (body.run_id is None):
        raise HTTPException(400, detail={"code": "invalid_input",
                                         "message": "Informe exatamente um: training_session_id (gravação) ou "
                                                    "run_id (execução comprovada)."})
    if body.training_session_id is not None:
        gravacao = body.training_session_id
        return _visao(_chamar(lambda: servico.attach_recording(teaching_id, gravacao, note=body.note,
                                                               by=_quem(request))))
    execucao = body.run_id or ""
    return _visao(_chamar(lambda: servico.attach_run(teaching_id, execucao, note=body.note, by=_quem(request))))


@router.post("/teaching-sessions/{teaching_id}/corrections", response_model=None)
async def add_correction(request: Request, teaching_id: str, body: CorrectionBody) -> JsonObject:
    servico = _ensino(request)
    dados: JsonObject = {k: v for k, v in body.payload.items()}
    return _visao(_chamar(lambda: servico.add_correction(teaching_id, body.body, run_id=body.run_id,
                                                         step_id=body.step_id, payload=dados, by=_quem(request))))


@router.post("/teaching-sessions/{teaching_id}/candidates", response_model=None)
async def propose_candidate(request: Request, teaching_id: str, body: ProposeBody | None = None) -> JsonObject:
    """Pede a candidata ao generalizador. Com IA real é UMA chamada paga do planejador; no simulado, zero."""
    servico = _ensino(request)
    try:
        visao = await servico.propose(teaching_id, by=_quem(request),
                                      idempotency_key=body.idempotency_key if body is not None else None)
    except (SkillError, InvalidSkillRef) as exc:
        raise _http(exc) from exc
    return _visao(visao)


@router.post("/teaching-sessions/{teaching_id}/answers", response_model=None)
async def answer_question(request: Request, teaching_id: str, body: AnswerBody) -> JsonObject:
    servico = _ensino(request)
    return _visao(_chamar(lambda: servico.answer(teaching_id, body.question_id, body.body, by=_quem(request))))


@router.post("/teaching-sessions/{teaching_id}/discard", response_model=None)
async def discard_teaching(request: Request, teaching_id: str) -> JsonObject:
    servico = _ensino(request)
    return _visao(_chamar(lambda: servico.discard(teaching_id, by=_quem(request))))


# ================================================================== /api/skill-candidates
@router.get("/skill-candidates/{candidate_id}", response_model=None)
async def get_candidate(request: Request, candidate_id: str) -> JsonObject:
    servico = _ensino(request)
    return _detalhe(_chamar(lambda: servico.candidate(candidate_id)))


@router.post("/skill-candidates/{candidate_id}/compile", response_model=None)
async def compile_candidate(request: Request, candidate_id: str) -> JsonObject:
    servico = _ensino(request)
    return _fatos(_chamar(lambda: servico.compile(candidate_id)))


@router.post("/skill-candidates/{candidate_id}/validate", response_model=None)
async def validate_candidate(request: Request, candidate_id: str, body: ValidateBody | None = None) -> JsonObject:
    servico = _ensino(request)
    modo = body.mode if body is not None else "static"
    return _visao(_chamar(lambda: servico.validate(candidate_id, mode=modo, by=_quem(request))))


@router.post("/skill-candidates/{candidate_id}/publish", response_model=None)
async def publish_candidate(request: Request, candidate_id: str, body: PublishBody | None = None) -> JsonObject:
    """A candidata validada vira `skill_versions` em DRAFT. Repetir devolve a mesma versão (idempotente pela
    própria candidata). Publicar a habilidade é outra decisão, em `/api/skills/.../status`."""
    servico = _ensino(request)
    return _visao(_chamar(lambda: servico.publish(candidate_id, by=_quem(request))))


# ================================================================== JSON
def _resumo(s: SkillSummary) -> JsonObject:
    return {"ref": str(s.ref), "skill_id": s.ref.skill_id, "version": s.ref.version, "name": s.name,
            "app_id": s.app_id, "state": s.state.value, "command_template": s.command_template,
            "schema_version": s.schema_version, "content_hash": s.content_hash, "intact": s.intact,
            "state_at": s.state_at, "legacy_flow_id": s.legacy_flow_id}


def _problema(i: DecompileIssue) -> JsonObject:
    return {k: v for k, v in i.as_dict().items()}


def _versao(v: SkillVersion, historico: Sequence[TransitionRecord]) -> JsonObject:
    return {"ref": str(v.ref), "skill_id": v.ref.skill_id, "version": v.ref.version, "state": v.state.value,
            "schema_version": v.schema_version, "content": v.document(), "content_hash": v.content_hash,
            "command_template": v.command_template, "app_ids": list(v.app_ids), "parent_version": v.parent_version,
            "source_kind": v.provenance.kind.value, "source_ref": v.provenance.ref,
            "provenance": v.provenance.to_json(), "created_by": v.created_by, "created_at": v.created_at,
            "state_at": v.state_at, "state_by": v.state_by, "state_detail": v.state_detail,
            "history": [{"from": t.from_state.value if t.from_state else None, "to": t.to_state.value,
                         "reason": t.reason, "by": t.decided_by, "at": t.decided_at} for t in historico]}


def _sessao(s: TeachingSession) -> JsonObject:
    return {"id": s.id, "instruction": s.instruction, "skill_id": s.skill_id, "base_version": s.base_version,
            "app_id": s.app_id, "profile_id": s.profile_id, "status": s.status.value,
            "validation_status": s.validation_status.value, "result_version_id": s.result_version_id,
            "operator": s.operator, "created_at": s.created_at, "updated_at": s.updated_at,
            "closed_at": s.closed_at}


def _demo(d: Demonstration) -> JsonObject:
    return {"id": d.id, "seq": d.seq, "kind": d.kind.value, "training_session_id": d.training_session_id,
            "run_id": d.run_id, "instance_id": d.instance_id, "app_snapshot": d.app_snapshot, "note": d.note,
            "created_at": d.created_at}


def _turno(t: TeachingTurn) -> JsonObject:
    return {"id": t.id, "kind": t.kind.value, "author": t.author.value, "reply_to": t.reply_to, "target": t.target,
            "body": t.body, "payload": t.payload, "candidate_id": t.candidate_id, "created_by": t.created_by,
            "created_at": t.created_at}


def _pergunta(t: TeachingTurn) -> JsonObject:
    return {"id": t.id, "kind": t.question_kind.value, "key": t.question_key, "origin": t.author.value,
            "text": t.body, "target": t.target, "candidate_id": t.candidate_id}


def _candidata(c: SkillCandidate) -> JsonObject:
    return {"id": c.id, "teaching_id": c.teaching_id, "seq": c.seq, "status": c.status.value,
            "validation_status": c.validation_status.value, "generated_by": c.generated_by,
            "content_hash": c.content_hash, "version_id": c.version_id, "document": c.envelope.document,
            "annotations": c.envelope.annotations.to_json(), "created_at": c.created_at, "updated_at": c.updated_at}


def _visao(v: TeachingView) -> JsonObject:
    return {**_sessao(v.session), "source": v.source.value, "demonstrations": [_demo(d) for d in v.demonstrations],
            "turns": [_turno(t) for t in v.turns], "candidates": [_candidata(c) for c in v.candidates],
            "current_candidate": _candidata(v.current) if v.current is not None else None,
            "open_questions": [_pergunta(t) for t in v.open_questions], "errors": list(v.errors)}


def _resumo_do_ensino(v: TeachingView) -> JsonObject:
    return {**_sessao(v.session), "source": v.source.value, "demonstration_count": len(v.demonstrations),
            "candidate_count": len(v.candidates), "open_question_count": len(v.open_questions),
            "current_candidate_id": v.current.id if v.current is not None else None}


def _fatos(f: DocumentFacts) -> JsonObject:
    return {"skill_id": f.skill_id, "name": f.name, "app_id": f.app_id, "command_template": f.command_template,
            "app_ids": list(f.app_ids), "errors": list(f.errors), "ok": not f.errors}


def _detalhe(d: CandidateDetail) -> JsonObject:
    return {**_candidata(d.candidate), "teaching_status": d.session.status.value, "compile": _fatos(d.facts),
            "open_questions": [_pergunta(t) for t in d.open_questions]}
