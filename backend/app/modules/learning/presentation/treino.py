"""O modo treinamento (`/api/instances/{id}/training` e `/api/training*`, itens 13.1 a 13.3): abrir a gravação, listar e ler as
sessões, parar, propor, prever, salvar, refazer as receitas, descartar e desfazer a última entrada. Saíram de `api.py` no
15.15 F4 (corte 2) sem mudar caminho, método, corpo nem resposta.

Montado em `main.py` logo depois do router dos fluxos (o mesmo lugar em que `api.router` entra). `/training/from-run` vem
ANTES de `/training/{session_id}`, que a engoliria. Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`) e o erro é o mesmo `{code, message}` de `api.err`.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request

from app.models import TrainingSaveBody, TrainingStartBody
from app.modules.learning.domain.ensino_da_falha import intencao_sugerida
from app.modules.skills.presentation.schemas import TrainingDeFalhaBody, TrainingStopBody, TrainingUndoBody
from app.planning.provider import AIError
from app.training.recorder import TrainingError

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _training_error(exc: TrainingError) -> HTTPException:
    return _err(exc.status, exc.code, exc.message)


@router.post("/instances/{instance_id}/training", status_code=201)
async def start_training(request: Request, instance_id: str, body: TrainingStartBody) -> object:
    s = _st(request)
    try:
        s.devices.get(instance_id)
    except KeyError as exc:
        raise _err(404, "not_found", "Instância não encontrada.") from exc
    if body.profile_id and body.profile_id not in s.social.profiles_of(instance_id):
        raise _err(400, "profile_not_on_device", f"A persona {body.profile_id} não está vinculada a {instance_id}: "
                                                "o treino é de uma persona deste aparelho.")
    try:
        return s.training.start(instance_id, intent=body.intent, lease_id=body.lease_id, app_id=body.app_id,
                                operator=getattr(request.state, "operator", None), profile_id=body.profile_id)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/from-run", status_code=201)
async def start_training_from_run(request: Request, body: TrainingDeFalhaBody) -> dict[str, object]:
    """31.111 F1: abre a sessão de ensino já ligada à etapa que falhou. Só a pessoa com o controle do aparelho abre;
    as travas são as do treino de hoje (nada automático, a loja não é aparelho de treino)."""
    s = _st(request)
    try:
        origem = s.training.origem_da_falha(body.run_id, body.step_id)
        try:
            s.devices.get(origem.instance_id)
        except KeyError as exc:
            raise _err(404, "not_found", "O aparelho desta etapa não existe mais.") from exc
        if body.profile_id and body.profile_id not in s.social.profiles_of(origem.instance_id):
            raise _err(400, "profile_not_on_device", f"A persona {body.profile_id} não está vinculada a "
                                                    f"{origem.instance_id}: o treino é de uma persona deste aparelho.")
        # 31.111 F4: sem intenção escrita pela pessoa, a sugerida leva a causa provável da tentativa (30.13, sem IA)
        diagnostico = (s.training.diagnostico_da_falha(origem.attempt_id)
                       if origem.attempt_id and s.training.diagnostico_da_falha is not None else None)
        intent = body.intent or intencao_sugerida(origem.titulo, diagnostico)
        return s.training.start(origem.instance_id, intent=intent, lease_id=body.lease_id, app_id=body.app_id,
                                operator=getattr(request.state, "operator", None), profile_id=body.profile_id,
                                origem=origem)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.get("/runs/{run_id}/steps/{step_id}/ensino-sugerido", response_model=None)
async def ensino_sugerido(request: Request, run_id: str, step_id: str) -> dict[str, object] | None:
    """31.116 (parte 2): o que o formulário de "Ensinar a corrigir" pré-preenche ANTES da sessão existir: a intenção
    sugerida, a pergunta e o rótulo da causa provável, pelo mesmo diagnóstico do 31.111 F4. Só leitura: sem IA, sem
    gravar, sem pedir o controle. As recusas são as do `from-run` (404 `step_not_found`, 409 `step_not_failed`); sem
    tentativa, `null`. A intenção que a pessoa escrever vence (é ela que vai no `POST /training/from-run`)."""
    s = _st(request)
    try:
        origem = s.training.origem_da_falha(run_id, step_id)
    except TrainingError as exc:
        raise _training_error(exc) from exc
    if not origem.attempt_id:
        return None
    diagnostico = (s.training.diagnostico_da_falha(origem.attempt_id)
                   if s.training.diagnostico_da_falha is not None else None)
    return {"intent": intencao_sugerida(origem.titulo, diagnostico),
            "pergunta": diagnostico.get("pergunta") if diagnostico else None,
            "rotulo": diagnostico.get("rotulo") if diagnostico else None}


@router.get("/training", response_model=None)
async def list_training(request: Request, instance_id: str | None = None, limit: int = Query(30, ge=1, le=200)) -> object:
    return _st(request).training.list(instance_id=instance_id, limit=limit)


@router.get("/training/{session_id}", response_model=None)
async def get_training(request: Request, session_id: str) -> object:
    try:
        return _st(request).training.get(session_id)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/stop", response_model=None)
async def stop_training(request: Request, session_id: str, body: TrainingStopBody | None = None) -> object:
    try:
        return _st(request).training.stop(session_id, lease_id=body.lease_id if body else None)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/propose", response_model=None)
async def propose_training(request: Request, session_id: str) -> object:
    """A IA lê a gravação e propõe a habilidade (comando com parâmetros, etapas, descartes). Uma chamada do modelo
    do planejador; a proposta fica guardada para a pessoa revisar. Corpo OPCIONAL `{"answers": [{question, answer}]}`
    (31.91): as respostas da pessoa às perguntas da proposta anterior; lido à mão para o erro de forma ser 400
    `invalid_answers` (e não o 422 do FastAPI)."""
    cru = await request.body()
    corpo: object = None
    if cru.strip():
        try:
            corpo = json.loads(cru)
        except ValueError:
            raise _err(400, "invalid_answers", "O corpo tem de ser um JSON {\"answers\": [...]}.") from None
    try:
        return await _st(request).skills.propose(session_id, corpo)
    except TrainingError as exc:
        raise _training_error(exc) from exc
    except AIError as exc:
        raise _err(502, "ai_error", f"A IA não conseguiu propor a habilidade: {exc}") from exc


@router.post("/training/{session_id}/save", response_model=None)
async def save_training(request: Request, session_id: str, body: TrainingSaveBody) -> object:
    try:
        return await _st(request).skills.save(session_id, proposal=body.proposal, profile_ids=body.profile_ids,
                                             group_ids=body.group_ids, scope_on_proof=body.scope_on_proof)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/preview")
async def preview_training(request: Request, session_id: str, body: TrainingSaveBody) -> dict[str, object]:
    """O que o `save` faria com esta proposta, sem gravar nada (31.86): os mesmos erros e, por etapa, se vira receita e
    por que não. A pessoa corrige a proposta ANTES de salvar, em vez de descobrir o motivo depois."""
    try:
        return await _st(request).skills.preview(session_id, proposal=body.proposal, profile_ids=body.profile_ids,
                                                group_ids=body.group_ids, scope_on_proof=body.scope_on_proof)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/recipes")
async def redo_training_recipes(request: Request, session_id: str) -> dict[str, object]:
    """Refaz a destilação de uma habilidade JÁ salva e grava a receita das etapas que ficaram sem (31.86): o reparo do
    que foi salvo com o aparelho fora do ar. Idempotente; sessão não salva: 409 `sessao_nao_salva`."""
    try:
        return await _st(request).skills.refazer_receitas(session_id)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/discard", response_model=None)
async def discard_training(request: Request, session_id: str, body: TrainingStopBody | None = None) -> object:
    try:
        return _st(request).training.stop(session_id, discard=True, lease_id=body.lease_id if body else None)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/undo")
async def undo_training_input(request: Request, session_id: str, body: TrainingUndoBody) -> dict[str, object]:
    """31.90-D: tira a ÚLTIMA entrada da gravação VIVA (o toque errado) sem descartar a sessão. Exige o controle do
    aparelho (`lease_id`); `seq` opcional confere que a última ainda é a que a pessoa viu. O aparelho não volta."""
    try:
        return _st(request).training.desfazer_a_ultima(session_id, lease_id=body.lease_id, seq=body.seq)
    except TrainingError as exc:
        raise _training_error(exc) from exc
