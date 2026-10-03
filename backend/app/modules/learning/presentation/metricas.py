"""As métricas do aprendizado e a lista de revisões do curador (30.8; §10 e §11.3 do desenho).

`GET /api/aprendizado/metricas?app=&dias=`: um bloco por linha da tabela do §10; ausente é `null`, nunca zero.
`GET /api/aprendizado/revisoes?app=&decisao=&desde=&limite=&cursor=`: a lista chata dos pareceres, sem o dossiê nem a
saída inteira (o detalhe do item já os entrega).
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request

from app.modules.learning.application.metricas import RevisaoNaLista, ServicoDeMetricas
from app.modules.learning.presentation.livro import _servico
from app.modules.skills.domain.document import JsonObject
from app.util import to_iso

router = APIRouter(prefix="/api/aprendizado")


def _metricas(request: Request) -> ServicoDeMetricas:
    metricas = _servico(request).extensao(ServicoDeMetricas)
    if metricas is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "As métricas do aprendizado não foram compostas."})
    return metricas


def _revisao(x: RevisaoNaLista) -> JsonObject:
    r, p = x.revisao, x.revisao.parecer
    return {"id": r.id, "criado_em": r.criado_em, "item_ref": r.item_ref, "item_kind": r.item_kind, "app": x.app or None,
            "gatilho": r.gatilho, "validade": r.validade, "simulado": r.simulated, "provedor": r.provedor or None,
            "modelo": r.modelo or None, "usd": round(x.usd, 6), "classe": r.classe.value if r.classe else None,
            "decisao": p.decisao.value if p is not None else None,
            "confianca": p.confianca.value if p is not None and p.confianca is not None else None,
            "decisao_final": r.decisao_final, "decidido_por": r.decidido_por, "override": r.override}


def _desde(d: datetime | None) -> str | None:
    """A data sem fuso é UTC (o banco grava em UTC; `astimezone` a leria no fuso da máquina)."""
    if d is None:
        return None
    return to_iso(d if d.tzinfo is not None else d.replace(tzinfo=UTC))


def _cursor(texto: str | None) -> tuple[str, str] | None:
    if not texto:
        return None
    criado, _, review_id = texto.partition("|")
    if not criado or not review_id:
        raise HTTPException(422, detail={"code": "cursor_invalido", "message": "Cursor inválido."})
    return criado, review_id


@router.get("/metricas", response_model=None)
async def metricas_do_aprendizado(request: Request, app: str | None = None,
                                  dias: int = Query(14, ge=1, le=90)) -> JsonObject:
    m = _metricas(request).metricas(app=app or None, dias=dias)
    corpo: JsonObject = asdict(m)
    return corpo


@router.get("/revisoes", response_model=None)
async def revisoes_do_curador(request: Request, app: str | None = None, decisao: str | None = None,
                              desde: datetime | None = None, limite: int = Query(50, ge=1, le=200),
                              cursor: str | None = None) -> JsonObject:
    pagina = _metricas(request).revisoes(app=app or None, decisao=decisao or None,
                                         desde=_desde(desde), limite=limite,
                                         cursor=_cursor(cursor))
    proximo = "|".join(pagina.proximo) if pagina.proximo is not None else None
    return {"revisoes": [_revisao(x) for x in pagina.revisoes], "proximo": proximo}


__all__ = ["router"]
