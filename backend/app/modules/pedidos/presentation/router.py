"""Rotas de pedidos (item 28.9; adendo v0.45 de `docs/api-contract.md`), todas sob `/api/pedidos`.

`/previa` e `/avisos` são declaradas ANTES de `/{pedido_id}` (o mesmo cuidado de `POST /api/runs/targets/suggest`). A
API solicita e acompanha: nenhuma rota materializa, despacha ou fecha ocorrência. Não implementadas no 28.9 (ver o
adendo): `executar` e `backfill`."""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypeVar

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.modules.pedidos.infrastructure.servico import ESTADOS, ErroDeApi, PedidosApi
from app.modules.pedidos.presentation.schemas import (AtivarCorpo, BuscaCorpo, CancelarCorpo, CriarCorpo, EdicaoCorpo,
                                                      LerAvisosCorpo, PausarCorpo, PreviaCorpo, ResolverIncertaCorpo, RetomarCorpo, selecao, utc)
from app.security.sessions import operador_atual

router = APIRouter(prefix="/api/pedidos")
T = TypeVar("T")


def _api(request: Request) -> PedidosApi:
    poc: object = getattr(request.app.state, "poc", None)
    api = getattr(poc, "pedidos_api", None)
    if not isinstance(api, PedidosApi):
        raise HTTPException(503, detail={"code": "not_ready", "message": "A API de pedidos ainda não foi composta."})
    return api


def _chamar(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ErroDeApi as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message, **e.details}) from e


@router.post("/previa")
async def previa(request: Request, corpo: PreviaCorpo) -> dict[str, object]:
    """Sem efeito, sem gravação e sem chamada de IA: devolve o que a criação decidiria."""
    return _chamar(lambda: _api(request).previa(corpo.para_corpo(), corpo.proximas))


@router.get("/avisos")
async def avisos(request: Request, pedido_id: str | None = None, requer_pessoa: bool | None = None,
                 lido: bool | None = None, limit: int = Query(50, ge=1, le=200),
                 cursor: str | None = None) -> dict[str, object]:
    return _chamar(lambda: _api(request).avisos(pedido_id=pedido_id, requer_pessoa=requer_pessoa, lido=lido,
                                                limit=limit, cursor=cursor))


@router.post("/avisos/ler")
async def ler_avisos(request: Request, corpo: LerAvisosCorpo) -> dict[str, object]:
    """Idempotente: repetir não muda a data de leitura e devolve `lidos: 0`."""
    return _chamar(lambda: _api(request).ler_avisos(ids=corpo.ids, todos=corpo.todos, pedido_id=corpo.pedido_id))


@router.post("")
async def criar(request: Request, corpo: CriarCorpo) -> JSONResponse:
    view, repetida = _chamar(lambda: _api(request).criar(
        corpo.para_corpo(), idempotency_key=corpo.idempotency_key, titulo=corpo.titulo,
        confirmacao=corpo.confirmacao, operador=operador_atual()))
    if repetida:
        return JSONResponse(status_code=200, content=jsonable_encoder({**view, "deduplicated": True}))
    return JSONResponse(status_code=201, content=jsonable_encoder(view))


def _listar(request: Request, *, estado: str | None, autonomia: str | None, tipo: str | None, profile_id: str | None,
            q: str | None, pede_atencao: bool, ordem: str, limit: int, cursor: str | None) -> dict[str, object]:
    estados = [e.strip() for e in estado.split(",") if e.strip()] if estado else None
    for e in estados or []:
        if e not in ESTADOS:
            raise HTTPException(422, detail={"code": "estado_invalido", "message": f"Estado desconhecido: {e!r}."})
    return _chamar(lambda: _api(request).listar(estado=estados, autonomia=autonomia, tipo=tipo, profile_id=profile_id,
                                                q=q, pede_atencao=pede_atencao, ordem=ordem, limit=limit,
                                                cursor=cursor))


@router.get("")
async def listar(request: Request, estado: str | None = None,
                 autonomia: Literal["observar", "preparar", "agir"] | None = None,
                 tipo: Literal["agora", "horario", "recorrencia", "evento", "condicao", "persona"] | None = None,
                 profile_id: str | None = None, pede_atencao: int = 0,
                 ordem: Literal["atualizado", "proxima", "criado"] = "atualizado",
                 limit: int = Query(50, ge=1, le=200), cursor: str | None = None) -> dict[str, object]:
    # 29.26: o termo de busca é texto livre e não vai mais na query. Ignorar `q` em silêncio devolveria a lista inteira
    # como se fosse o resultado da busca; por isso a recusa explícita, com o caminho novo.
    if "q" in request.query_params:
        raise HTTPException(422, detail={"code": "busca_no_corpo", "message": "O termo de busca vai no corpo: use POST /api/pedidos/busca."})
    return _listar(request, estado=estado, autonomia=autonomia, tipo=tipo, profile_id=profile_id, q=None,
                   pede_atencao=bool(pede_atencao), ordem=ordem, limit=limit, cursor=cursor)


@router.post("/busca")
async def buscar(request: Request, corpo: BuscaCorpo) -> dict[str, object]:
    """A listagem com o termo `q` no corpo (29.26). Só lê: é POST apenas para o texto da busca não ir para a URL."""
    return _listar(request, estado=corpo.estado, autonomia=corpo.autonomia, tipo=corpo.tipo, profile_id=corpo.profile_id,
                   q=corpo.q, pede_atencao=corpo.pede_atencao, ordem=corpo.ordem, limit=corpo.limit, cursor=corpo.cursor)


@router.get("/{pedido_id}")
async def detalhe(request: Request, pedido_id: str) -> dict[str, object]:
    return _chamar(lambda: _api(request).detalhe(pedido_id))


@router.patch("/{pedido_id}")
async def editar(request: Request, pedido_id: str, corpo: EdicaoCorpo) -> dict[str, object]:
    enviados = corpo.model_fields_set
    mud: dict[str, object] = {}
    for c in ("titulo", "contexto", "criterios_sucesso", "autonomia", "fuso", "max_ocorrencias", "orcamento_total_usd",
              "orcamento_ocorrencia_usd", "sobreposicao", "janela_recuperacao_s", "coalescer", "max_tentativas",
              "pausa_por_falha", "objetivo"):
        if c in enviados:
            mud[c] = getattr(corpo, c)
    for c in ("inicio_em", "fim_em"):
        if c in enviados:
            mud[c] = utc(getattr(corpo, c))
    sel = selecao(corpo.objetivo or "pedido", corpo.alvos) if corpo.alvos is not None else None
    gat = [(g.tipo, g.spec) for g in corpo.gatilhos] if corpo.gatilhos is not None else None
    return _chamar(lambda: _api(request).editar(pedido_id, mud, versao=corpo.versao, dry_run=corpo.dry_run,
                                                confirmacao=corpo.confirmacao, selecao=sel, gatilhos=gat))


@router.post("/{pedido_id}/ativar")
async def ativar(request: Request, pedido_id: str, corpo: AtivarCorpo) -> dict[str, object]:
    return _chamar(lambda: _api(request).ativar(pedido_id, corpo.confirmacao))


@router.post("/{pedido_id}/pausar")
async def pausar(request: Request, pedido_id: str, corpo: PausarCorpo | None = None) -> dict[str, object]:
    return _chamar(lambda: _api(request).pausar(pedido_id, corpo.motivo if corpo else None))


@router.post("/{pedido_id}/retomar")
async def retomar(request: Request, pedido_id: str, corpo: RetomarCorpo | None = None) -> dict[str, object]:
    return _chamar(lambda: _api(request).retomar(pedido_id, corpo.modo if corpo else None))


@router.post("/{pedido_id}/cancelar")
async def cancelar(request: Request, pedido_id: str, corpo: CancelarCorpo | None = None) -> dict[str, object]:
    c = corpo or CancelarCorpo()
    return _chamar(lambda: _api(request).cancelar(pedido_id, c.confirmar, c.motivo, operador_atual()))


@router.get("/{pedido_id}/ocorrencias")
async def ocorrencias(request: Request, pedido_id: str, estado: str | None = None, origem: str | None = None,
                      de: str | None = None, ate: str | None = None, limit: int = Query(50, ge=1, le=500),
                      antes_de: str | None = None) -> dict[str, object]:
    return _chamar(lambda: _api(request).ocorrencias(pedido_id, estado=estado, origem=origem, de=de, ate=ate,
                                                     limit=limit, antes_de=antes_de))


@router.post("/{pedido_id}/ocorrencias/{ocorrencia_id}/resolver")
async def resolver_incerta(request: Request, pedido_id: str, ocorrencia_id: str, corpo: ResolverIncertaCorpo) -> dict[str, object]:
    """A pessoa conferiu uma ocorrência `incerta` e a dá por resolvida (28.21): grava quem, quando e a nota (obrigatória).
    NÃO reexecuta e NÃO muda o estado (segue `incerta`); só a tira das pendências, e então o `retomar` do pedido vale.
    Repetir é idempotente (200, sem regravar). Devolve a ocorrência."""
    return _chamar(lambda: _api(request).resolver_incerta(pedido_id, ocorrencia_id, corpo.nota, operador_atual()))


@router.get("/{pedido_id}/execucoes")
async def execucoes(request: Request, pedido_id: str, limit: int = Query(20, ge=1, le=200)) -> list[object]:
    linhas = _chamar(lambda: _api(request).execucoes(pedido_id, limit))
    repo = request.app.state.poc.repo
    return [repo.run_summary(r) for r in linhas]


@router.get("/{pedido_id}/relatorios")
async def relatorios(request: Request, pedido_id: str, limit: int = Query(20, ge=1, le=200),
                     cursor: str | None = None) -> dict[str, object]:
    return _chamar(lambda: _api(request).relatorios(pedido_id, limit, cursor))


@router.get("/{pedido_id}/observacoes")
async def observacoes(request: Request, pedido_id: str, limit: int = Query(50, ge=1, le=500),
                      cursor: str | None = None) -> dict[str, object]:
    return _chamar(lambda: _api(request).observacoes(pedido_id, limit, cursor))
