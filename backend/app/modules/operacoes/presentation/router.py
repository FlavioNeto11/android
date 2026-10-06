"""A operação com N agentes (`/api/operacoes*`, 31.154, adendo v1.94): criar, listar, ler, cancelar e liberar.

Montado em `main.py` junto dos outros routers de contexto. Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`), e o serviço é montado aqui com as peças que ele já expõe.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.fleet.presentation.comum import quem
from app.modules.operacoes.infrastructure.servico import (AlvoPedido, OperacaoError, PedidoDeOperacao,
                                                          ServicoDeOperacoes)

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


class AlvoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=100)
    account_id: str | None = Field(default=None, min_length=1, max_length=100)
    instance_id: str | None = Field(default=None, min_length=1, max_length=64)


class OperacaoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=3, max_length=4000)
    app_id: str = Field(min_length=1, max_length=80)
    alvos: list[AlvoIn] = Field(min_length=1, max_length=64)
    acao_final: Literal["preparar", "executar"] = "preparar"
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    #: Obrigatório (decisão da orquestradora, 06/10): a operação para de gastar ao atingir o teto.
    max_usd: float = Field(gt=0, le=100)
    assunto: str | None = Field(default=None, min_length=3, max_length=500)
    fontes: list[str] = Field(default_factory=list, max_length=10)
    #: Adendo v1.95: parâmetros fixos de cada execução de alvo (`username`, `caption_contains`). O serviço confere
    #: nomes e valores (`_conferir_parametros`).
    parametros: dict[str, str] | None = Field(default=None, max_length=10)

    @field_validator("alvos")
    @classmethod
    def _sem_repetir(cls, v: list[AlvoIn]) -> list[AlvoIn]:
        ids = [a.profile_id for a in v]
        if len(set(ids)) != len(ids):
            raise ValueError("a mesma persona aparece duas vezes em alvos")
        return v

    @field_validator("fontes")
    @classmethod
    def _so_https(cls, v: list[str]) -> list[str]:
        for u in v:
            partes = urlsplit(u)
            # Sem query nem usuário na URL: um `?token=` ou `usuario:senha@` levaria credencial à pesquisa externa.
            if (partes.scheme != "https" or not partes.hostname or partes.query or partes.username or partes.password
                    or len(u) > 500 or any(c.isspace() for c in u)):
                raise ValueError("fonte inválida: só https://, sem parâmetros (?…), sem usuário e até 500 caracteres")
        return v


class ItemDeLiberacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=100)
    #: O texto que a pessoa LEU (31.49): diferente do pedido de aprovação, o item é recusado com `texto_divergente`.
    texto: str = Field(min_length=1, max_length=4000)


class LiberarBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    itens: list[ItemDeLiberacao] = Field(min_length=1, max_length=64)


class CancelarAlvosBody(BaseModel):
    """Os filtros se somam (E). Nenhum filtro dá 422 `filtro_vazio`: a operação inteira é `POST …/cancelar`."""
    model_config = ConfigDict(extra="forbid")
    profile_ids: list[str] = Field(default_factory=list, max_length=200)
    estados: list[str] = Field(default_factory=list, max_length=8)
    estagios: list[str] = Field(default_factory=list, max_length=20)
    instance_ids: list[str] = Field(default_factory=list, max_length=200)


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _servico(request: Request) -> ServicoDeOperacoes:
    st = _st(request)
    return ServicoDeOperacoes(st.db, st.runs, st.social_repo, st.approval_service, st.settings.get, st.bus,
                              st.cfg.file.ai.prices)


def _erro(exc: OperacaoError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message, **exc.extra})


@router.post("/operacoes", status_code=201, response_model=None)
async def criar_operacao(request: Request, body: OperacaoCreate) -> object:
    try:
        return _servico(request).criar(PedidoDeOperacao(
            command=body.command, app_id=body.app_id, acao_final=body.acao_final, idempotency_key=body.idempotency_key,
            max_usd=body.max_usd, assunto=body.assunto, fontes=tuple(body.fontes), parametros=body.parametros,
            alvos=tuple(AlvoPedido(a.profile_id, a.account_id, a.instance_id) for a in body.alvos)), quem=quem(request))
    except OperacaoError as exc:
        raise _erro(exc) from exc


@router.get("/operacoes", response_model=None)
async def listar_operacoes(request: Request, limite: int = Query(50, ge=1, le=200)) -> object:
    return _servico(request).listar(limite)


@router.get("/operacoes/{operacao_id}", response_model=None)
async def ler_operacao(request: Request, operacao_id: str) -> object:
    try:
        return _servico(request).ler(operacao_id)
    except OperacaoError as exc:
        raise _erro(exc) from exc


@router.post("/operacoes/{operacao_id}/cancelar", response_model=None)
async def cancelar_operacao(request: Request, operacao_id: str) -> object:
    try:
        return _servico(request).cancelar(operacao_id, quem=quem(request))
    except OperacaoError as exc:
        raise _erro(exc) from exc


@router.post("/operacoes/{operacao_id}/cancelar-alvos", response_model=None)
async def cancelar_alvos_da_operacao(request: Request, operacao_id: str, body: CancelarAlvosBody) -> object:
    try:
        return _servico(request).cancelar_alvos(operacao_id, profile_ids=body.profile_ids, estados=body.estados,
                                                estagios=body.estagios, instance_ids=body.instance_ids,
                                                quem=quem(request))
    except OperacaoError as exc:
        raise _erro(exc) from exc


@router.post("/operacoes/{operacao_id}/liberar", response_model=None)
async def liberar_operacao(request: Request, operacao_id: str, body: LiberarBody) -> object:
    try:
        return _servico(request).liberar(operacao_id, [(i.profile_id, i.texto) for i in body.itens],
                                         quem=quem(request))
    except OperacaoError as exc:
        raise _erro(exc) from exc
