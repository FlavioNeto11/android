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
from app.modules.learning.domain.ensino_da_falha import (intencao_da_exploracao, intencao_sugerida, pergunta_da_etapa,
                                                         pergunta_da_exploracao)
from app.modules.learning.infrastructure.rendimento_sql import LeitorDoRendimento
from app.modules.skills.presentation.schemas import TrainingDeFalhaBody, TrainingStopBody, TrainingUndoBody
from app.planning.provider import AIError
from app.training import exibicao
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
    return _err(exc.status, exc.code, exc.message, **exc.extra)


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
                                operator=getattr(request.state, "operator", None), profile_id=body.profile_id,
                                nascido_de_prova=body.nascido_de_prova)
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
        # 31.312: a exploração que parou sugere ensinar o caminho (pela chave, sem o pedido); a falha de sempre, corrigir.
        intent = body.intent or (intencao_da_exploracao(origem.step_key) if origem.exploratoria
                                 else intencao_sugerida(origem.titulo, diagnostico))
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
    if origem.exploratoria:
        # 31.312 (adendo v1.138): a IA explorou e não chegou lá; a causa vem do diagnóstico (quando houver), a pergunta é a da exploração
        return {"intent": intencao_da_exploracao(origem.step_key),
                "pergunta": pergunta_da_exploracao(origem.parou_no_teto),
                "rotulo": diagnostico.get("rotulo") if diagnostico else None,
                "causa": diagnostico.get("causa") if diagnostico else None,
                "exploracao": True, "parou_no_teto": origem.parou_no_teto}
    # v1.82: `causa` é o código do diagnóstico (o painel escolhe ícone e texto por ele); a pergunta é a do ESTADO da etapa
    return {"intent": intencao_sugerida(origem.titulo, diagnostico),
            "pergunta": pergunta_da_etapa(origem.status, diagnostico),
            "rotulo": diagnostico.get("rotulo") if diagnostico else None,
            "causa": diagnostico.get("causa") if diagnostico else None}


def _exibir(request: Request, resposta: object) -> object:
    """31.183: toda resposta que traz a sessão (ou `{"session": ...}`) ganha `proposal_exibicao`, a cópia da proposta
    só para exibir, com o dado da persona mascarado; a `proposal` (que o painel devolve na prévia e no salvar) não
    muda."""
    if isinstance(resposta, dict) and isinstance(resposta.get("session"), dict):
        return {**resposta, "session": _exibir(request, resposta["session"])}
    if not isinstance(resposta, dict) or "proposal" not in resposta:
        return resposta
    persona = _st(request).repo.variaveis_da_persona(resposta.get("profile_id"))
    return {**resposta, "proposal_exibicao": exibicao.proposta(resposta.get("proposal"), persona)}


def _relatorio_exibido(request: Request, session_id: str, resposta: object) -> object:
    """31.183 (achado da Portal no 31.189): o `steps[]` da prévia, do salvar e do refazer receitas com o título e o
    motivo mascarados; o relatório só é exibido. E os `warnings` também (achado da Portal no 31.182: o aviso cita a
    etapa pelo título ou pela `key`)."""
    if not isinstance(resposta, dict) or not any(isinstance(resposta.get(k), list) for k in ("steps", "warnings")):
        return resposta
    perfil = _st(request).db.scalar("SELECT profile_id FROM training_sessions WHERE id=?", (session_id,))
    persona = _st(request).repo.variaveis_da_persona(str(perfil) if perfil else None)
    return {**resposta, **({"steps": exibicao.relatorio(resposta["steps"], persona)}
                           if isinstance(resposta.get("steps"), list) else {}),
            **({"warnings": exibicao.avisos(resposta["warnings"], persona)}
               if isinstance(resposta.get("warnings"), list) else {})}


@router.get("/training", response_model=None)
async def list_training(request: Request, instance_id: str | None = None, limit: int = Query(30, ge=1, le=200),
                        nascido_de_prova: bool | None = None) -> object:
    return [_exibir(request, s) for s in
            _st(request).training.list(instance_id=instance_id, limit=limit, nascido_de_prova=nascido_de_prova)]


@router.get("/training/{session_id}", response_model=None)
async def get_training(request: Request, session_id: str) -> object:
    try:
        return _exibir(request, _st(request).training.get(session_id))
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.get("/training/{session_id}/rendimento", response_model=None)
async def rendimento_do_treino(request: Request, session_id: str) -> object:
    """O que esta sessão de ensino gerou e quanto disso foi usado (receitas, fluxo, lições, vizinhos), com a régua do
    uso real de 06/10. Só leitura; nenhuma IA."""
    st = _st(request)
    leitor = LeitorDoRendimento(st.db, liberada=st.scheduler.executor.recipes.liberada_fora_do_ensino,
                                em_uso_real_desde=st.scheduler.flows.em_uso_real_desde,
                                precos=lambda: st.cfg.file.ai.prices)
    rendimento = leitor.ler(session_id)
    if rendimento is None:
        raise _err(404, "not_found", f"Sessão de treino {session_id} não existe.")
    return rendimento.como_dict()


@router.post("/training/{session_id}/stop", response_model=None)
async def stop_training(request: Request, session_id: str, body: TrainingStopBody | None = None) -> object:
    try:
        return _exibir(request, _st(request).training.stop(session_id, lease_id=body.lease_id if body else None))
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
        return _exibir(request, await _st(request).skills.propose(session_id, corpo))
    except TrainingError as exc:
        raise _training_error(exc) from exc
    except AIError as exc:
        raise _err(502, "ai_error", f"A IA não conseguiu propor a habilidade: {exc}") from exc


@router.post("/training/{session_id}/save", response_model=None)
async def save_training(request: Request, session_id: str, body: TrainingSaveBody) -> object:
    try:
        return _relatorio_exibido(request, session_id, _exibir(request, await _st(request).skills.save(
            session_id, proposal=body.proposal, profile_ids=body.profile_ids, group_ids=body.group_ids,
            scope_on_proof=body.scope_on_proof)))
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/preview")
async def preview_training(request: Request, session_id: str, body: TrainingSaveBody) -> dict[str, object]:
    """O que o `save` faria com esta proposta, sem gravar nada (31.86): os mesmos erros e, por etapa, se vira receita e
    por que não. A pessoa corrige a proposta ANTES de salvar, em vez de descobrir o motivo depois. O comando repetido
    vem no corpo (`code: duplicate_command`, 31.142, adendo v1.91), junto do resto; o 409 dele é só do `save`."""
    try:
        previa = await _st(request).skills.preview(session_id, proposal=body.proposal, profile_ids=body.profile_ids,
                                                   group_ids=body.group_ids, scope_on_proof=body.scope_on_proof)
        exibida = _relatorio_exibido(request, session_id, previa)
        return exibida if isinstance(exibida, dict) else previa
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/recipes")
async def redo_training_recipes(request: Request, session_id: str) -> dict[str, object]:
    """Refaz a destilação de uma habilidade JÁ salva e grava a receita das etapas que ficaram sem (31.86): o reparo do
    que foi salvo com o aparelho fora do ar. Idempotente; sessão não salva: 409 `sessao_nao_salva`."""
    try:
        resposta = _relatorio_exibido(request, session_id,
                                      _exibir(request, await _st(request).skills.refazer_receitas(session_id)))
        return resposta if isinstance(resposta, dict) else {}
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/discard", response_model=None)
async def discard_training(request: Request, session_id: str, body: TrainingStopBody | None = None) -> object:
    try:
        return _exibir(request, _st(request).training.stop(session_id, discard=True,
                                                           lease_id=body.lease_id if body else None))
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/undo")
async def undo_training_input(request: Request, session_id: str, body: TrainingUndoBody) -> dict[str, object]:
    """31.90-D: tira a ÚLTIMA entrada da gravação VIVA (o toque errado) sem descartar a sessão. Exige o controle do
    aparelho (`lease_id`); `seq` opcional confere que a última ainda é a que a pessoa viu. O aparelho não volta."""
    try:
        resposta = _exibir(request, _st(request).training.desfazer_a_ultima(session_id, lease_id=body.lease_id,
                                                                             seq=body.seq))
        return resposta if isinstance(resposta, dict) else {}
    except TrainingError as exc:
        raise _training_error(exc) from exc
