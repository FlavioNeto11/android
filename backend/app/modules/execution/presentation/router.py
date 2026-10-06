"""As execuções (`/api/runs*`): criar, prévia de alvos e de distribuição, listar, ler, projeção, eventos, relatório, a
porta do plano (30.61), a sucessora, as operações (iniciar, pausar, retomar, cancelar, repetir) e resolver um objetivo.
Saíram de `api.py` no 15.15 F4 (corte 3) sem mudar caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra, DEPOIS dos routers de aprendizado e de pedidos (o voto do
D2, `POST /runs/{id}/feedback`, mora no aprendizado e precisa casar antes do coringa). A ORDEM destas rotas importa e é a de
antes: as literais (`/runs/targets/*`, `/runs/distribution`) e as específicas de `/runs/{run_id}/...` ANTES de
`POST /runs/{run_id}/{op}`, que fica por último. O `GET /runs/distribution` escondido do OpenAPI (405 `metodo_removido`)
tem de vir antes de `GET /runs/{run_id}`, que o engoliria com um 404.

Este módulo não importa `app.api` (ciclo): o estado é `request.app.state.poc` (`AppState` só em `TYPE_CHECKING`).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from app.db import loads
from app.models import (DistributeSpec, Plan, ResolveBody, RunCreate, RunSummary, RunTargetsPreview,
                        RunTargetsResolveBody)
from app.modules.execution.presentation.comum import autor_do_sinal, run_error
from app.porta_do_plano import (AprovarPlanoBody, PortaIndisponivel, PreviaDoItemBody, aprovar_plano, previa_da_porta,
                                previa_do_item, renovar_plano)
from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
from app.taskqueue.orquestrador import Orquestrador, RunTargetsSuggestBody, RunTargetsSuggestion
from app.taskqueue.service import RunError

if TYPE_CHECKING:
    from app.state import AppState

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.post("/runs", response_model=None)
async def create_run(request: Request, body: RunCreate) -> object:
    """Cria a execução. Com `mode=plan`, a resposta leva também `plan_report`: o relatório dos recursos declarados
    (design §14.2) — o que está certo, o que diverge, o que seria feito e o que só uma pessoa resolve —, lido sem
    aplicar nada. Aditivo: o resumo de sempre continua igual, campo a campo."""
    runs = _st(request).runs
    try:
        resumo = runs.create(body)
    except RunError as exc:
        raise run_error(exc) from exc
    if body.mode != "plan":
        return resumo
    try:
        relatorio: dict[str, object] = runs.relatorio_de_recursos(resumo.id)
    except Exception as exc:  # noqa: BLE001 - a execução já existe: o relatório ao lado não pode virar um 500
        log.exception("relatório de recursos da execução %s", resumo.id)
        relatorio = {"source": "error", "detail": f"o relatório dos recursos não pôde ser montado: {exc}"}
    return {**jsonable_encoder(resumo), "plan_report": relatorio}


@router.post("/runs/targets/resolve")
async def resolve_run_targets(request: Request, body: RunTargetsResolveBody) -> RunTargetsPreview:
    """Prévia OBRIGATÓRIA dos alvos (onda C; design persona-e-parque §7.6): para quem e onde a execução aconteceria,
    com a origem de cada alvo (`ui`, `texto`, `vinculo`, `balanceamento`), as perguntas e o comando sem os destinos.
    Não cria execução, não grava nada e não chama o planejador. Declarada antes de `/runs/{run_id}/{op}`."""
    try:
        return _st(request).runs.previa_de_alvos(body)
    except RunError as exc:
        raise run_error(exc) from exc


@router.post("/runs/targets/suggest")
async def suggest_run_targets(request: Request, body: RunTargetsSuggestBody) -> RunTargetsSuggestion:
    """Modo Automático (ADR-050): quem faz e onde, pelo pedido. Não cria execução. Sem IA quando o texto já diz o
    destino ou quando o app não usa conta (distribuição pela carga); senão uma chamada do papel `plan` escolhe as
    personas pelo perfil e o resolvedor põe cada uma no aparelho dela. Declarada antes de `/runs/{run_id}/{op}`."""
    state = _st(request)
    try:
        return await Orquestrador(state.runs, state.social_repo).sugerir(body)
    except RunError as exc:
        raise run_error(exc) from exc


@router.get("/runs")
async def list_runs(request: Request, limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
                    instance_id: str | None = None, worker_id: str | None = None) -> object:
    """A lista de execuções, paginada e filtrável por ONDE rodou.

    Sem paginação, o painel pedia 50 e as execuções mais antigas simplesmente sumiam — não havia como chegar
    nelas por nenhum caminho. Os filtros vêm da mesma fotografia do objetivo (migração 022): "o que rodou naquele
    servidor" e "o que rodou naquele aparelho" passam a ser perguntas que a tela sabe fazer.

    O filtro por aparelho também olha `runs.instance_ids` porque uma execução em `planning` ainda não tem
    objetivo materializado — e some-la da lista seria esconder justamente a que está acontecendo agora.
    """
    s = _st(request)
    where, params = [], []
    if instance_id:
        where.append("(EXISTS (SELECT 1 FROM objectives o WHERE o.run_id=r.id AND o.instance_id=?)"
                     " OR r.instance_ids LIKE ?)")
        params += [instance_id, f'%"{instance_id}"%']
    if worker_id:
        where.append("EXISTS (SELECT 1 FROM objectives o WHERE o.run_id=r.id AND o.worker_id=?)")
        params.append(worker_id)
    sql = "SELECT r.* FROM runs r" + (" WHERE " + " AND ".join(where) if where else "")
    total = s.db.scalar("SELECT COUNT(*) FROM (" + sql + ") x", tuple(params)) or 0
    rows = s.db.query(sql + " ORDER BY r.created_at DESC LIMIT ? OFFSET ?", tuple(params) + (limit, offset))
    return {"runs": s.repo.run_summaries(rows), "total": int(total), "limit": limit, "offset": offset}


class DistributionPreviewBody(BaseModel):
    """Corpo de `POST /runs/distribution` (29.26): o comando, que pode trazer e-mail e nunca deve ir para a URL (query
    string vira linha de log de acesso). O teto é o do comando de uma execução, de `/skills/resolve` e de `/flows/match`."""
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=1, le=64)
    app_id: str | None = Field(default=None, min_length=1, max_length=80)
    command: str | None = Field(default=None, min_length=1, max_length=4000)


@router.post("/runs/distribution", response_model=None)
async def preview_distribution(request: Request, body: DistributionPreviewBody) -> object:
    """Quais aparelhos uma execução distribuída pegaria AGORA, por servidor — sem criar nada.

    Item 24.6: `app_id` ficou opcional. Sem ele, os apps são os que o `command` usa (a mesma leitura da criação);
    um dos dois é obrigatório. Comando com credencial recebe a mesma recusa da criação. Item 29.26: era `GET` com tudo
    na query; o texto do comando agora vai no corpo."""
    count, app_id, command = body.count, body.app_id, body.command
    if app_id is None and command is None:
        raise _err(422, "distribution_sem_alvo", "Informe o app (`app_id`) ou o comando (`command`) da distribuição.")
    s = _st(request)
    try:
        if command is not None:
            s.runs._recusar_credencial(command)
        return s.runs.previa_de_distribuicao(DistributeSpec(count=count, app_id=app_id), command)
    except RunError as exc:
        raise run_error(exc) from exc


@router.get("/runs/distribution", include_in_schema=False)
async def preview_distribution_get_removido() -> None:
    """Sem isto, o GET antigo cairia em `/runs/{run_id}` e responderia 404 "Execução não encontrada" (29.26)."""
    raise HTTPException(405, detail={"code": "metodo_removido", "message": "A prévia da distribuição agora é POST /api/runs/distribution, "
                                     "com o comando no corpo."}, headers={"Allow": "POST"})


@router.get("/runs/{run_id}", response_model=None)
async def get_run(request: Request, run_id: str) -> object:
    detail = _st(request).repo.run_detail(run_id)
    if detail is None:
        raise _err(404, "not_found", "Execução não encontrada.")
    return detail


@router.get("/runs/{run_id}/projection")
async def run_projection(request: Request, run_id: str) -> dict[str, object]:
    """Item 18.3: o normal medido de cada etapa do plano (chamadas de IA, segundos e US$ — mediana e p90 por ação,
    nas etapas concluídas da janela configurada), somado. Não chama IA. 409 `no_plan` enquanto o plano não existe."""
    s = _st(request)
    run = s.repo.run_row(run_id)
    if run is None:
        raise _err(404, "not_found", "Execução não encontrada.")
    bruto = loads(run["plan"], None) if run["plan"] else None
    if not bruto:
        raise _err(409, "no_plan", "A execução ainda não tem plano.")
    return s.runs.projecao(Plan.model_validate(bruto))


@router.get("/runs/{run_id}/events", response_model=None)
async def run_events(request: Request, run_id: str, after: int = 0, limit: int = Query(500, ge=1, le=5000)) -> object:
    return _st(request).bus.since(after, run_id=run_id, limit=limit)


@router.get("/runs/{run_id}/report", response_model=None)
async def run_report(request: Request, run_id: str) -> object:
    try:
        return _st(request).runs.report(run_id)
    except RunError as exc:
        raise run_error(exc) from exc


@router.get("/runs/{run_id}/porta")
async def run_porta(request: Request, run_id: str) -> dict[str, object]:
    """30.61: a prévia da porta do despacho numa execução `planned`: o selo de cada etapa com efeito (permitido,
    aprovacao, adiado, recusado, na_execucao), a chave dos itens aprováveis e o que só se decide na execução. Só leitura:
    não grava decisão, não abre pedido, não chama IA. 404 sem execução; 409 `invalid_state` fora de `planned`."""
    try:
        return previa_da_porta(_st(request), run_id)
    except PortaIndisponivel as exc:
        raise _err(exc.status, exc.codigo, exc.mensagem, **exc.extra) from exc


@router.post("/runs/{run_id}/aprovar-plano")
async def run_aprovar_plano(request: Request, run_id: str, body: AprovarPlanoBody) -> dict[str, object]:
    """30.61: "Aprovar N e iniciar". 409 `plano_mudou` (com `mudaram` e a `previa` nova) quando algum item não é mais o
    que o dono viu; nada é gravado. Senão grava os sins de origem `plano`, cancela as tiradas e inicia."""
    try:
        return aprovar_plano(_st(request), run_id, body, por=autor_do_sinal(request))
    except PortaIndisponivel as exc:
        raise _err(exc.status, exc.codigo, exc.mensagem, **exc.extra) from exc
    except RunError as exc:
        raise run_error(exc) from exc


@router.post("/runs/{run_id}/porta/item")
async def run_porta_item(request: Request, run_id: str, body: PreviaDoItemBody) -> dict[str, object]:
    """30.68: a prévia de UM item com o texto proposto no cartão (selo, motivo e chave). Só leitura: não grava e não
    chama IA. 409 `plano_mudou` se a etapa saiu do plano; 422 para o texto vazio, longo ou com variável."""
    try:
        return previa_do_item(_st(request), run_id, body)
    except PortaIndisponivel as exc:
        raise _err(exc.status, exc.codigo, exc.mensagem, **exc.extra) from exc


@router.post("/runs/{run_id}/porta/renovar")
async def run_porta_renovar(request: Request, run_id: str) -> dict[str, object]:
    """30.61 "Renovar": a validade dos sins do plano em aberto volta a contar de agora, sem reabrir os itens."""
    try:
        return renovar_plano(_st(request), run_id)
    except PortaIndisponivel as exc:
        raise _err(exc.status, exc.codigo, exc.mensagem, **exc.extra) from exc


@router.post("/runs/{run_id}/successor")
async def run_successor(request: Request, run_id: str, body: RunSuccessorBody) -> RunSummary:
    """Responde a uma execução em `needs_input`: cria a execução com o comando respondido e o mesmo pedido de alvos
    e cancela a antiga, que aponta para a nova. Declarada antes de `/runs/{run_id}/{op}`."""
    try:
        nova, _ = ComandoAssistido(_st(request).runs).sucessora(run_id, body, por=autor_do_sinal(request))
    except RunError as exc:
        raise run_error(exc) from exc
    return nova


@router.post("/runs/{run_id}/{op}", response_model=None)
async def run_op(request: Request, run_id: str, op: str) -> object:
    runs = _st(request).runs
    # Cancelar pela rota é o GESTO de uma pessoa (sinal `cancelou_execucao`); o cancelamento que a sucessora faz não é.
    # Repetir também é gesto (`repetiu_execucao`): os dois levam o operador da sessão ao sinal. Iniciar leva a pessoa ao
    # evento (`iniciada_por`, P12): é o que separa a prévia iniciada de propósito do início automático do `mode=execute`.
    ops: dict[str, Callable[[str], object]] = {"start": lambda rid: runs.start(rid, por=autor_do_sinal(request)), "pause": runs.pause,
           "resume": runs.resume,
           "cancel": lambda rid: runs.cancel(rid, por=autor_do_sinal(request)),
           "retry_failed": lambda rid: runs.retry_failed(rid, por=autor_do_sinal(request))}
    if op not in ops:
        raise _err(404, "not_found", "Operação desconhecida.")
    try:
        return ops[op](run_id)
    except RunError as exc:
        raise run_error(exc) from exc


@router.post("/runs/{run_id}/objectives/{objective_id}/resolve", response_model=None)
async def resolve(request: Request, run_id: str, objective_id: str, body: ResolveBody) -> object:
    try:
        return _st(request).runs.resolve(run_id, objective_id, body, por=autor_do_sinal(request))
    except RunError as exc:
        raise run_error(exc) from exc
