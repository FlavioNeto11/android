"""Rota das lições (ADR-054, decisão 5; pacote A7): `GET /api/aprendizado/licoes/previa?app=&acao=&papel=&etapa=`.

Devolve o bloco EXATO que iria ao prompt do ator ou do planejador — todas as lições publicadas que cabem no teto do
papel, como se estivessem no braço `with` (a prévia não tem unidade para sortear) — e os tokens, sem IA e sem gravar
nada. Diz também o modo: no `shadow` de fábrica, nada disso vai ao prompt ainda. Para cada lição em prova, "faltam N"
unidades no braço mais curto até o veredito. O verificador não tem papel aqui: lição nunca vai ao juiz (ADR-024).

Registrada ANTES do livro (`router.py`): `{kind}/{ref}` casaria com `/licoes/previa`.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.modules.learning.application.licoes import Previa, ServicoDeLicoes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.licoes import Escolhida
from app.modules.learning.domain.tokens import estimar_tokens
from app.modules.learning.domain.vocabulario import Modo, Papel
from app.modules.learning.infrastructure.ligar_licoes import licoes_de
from app.modules.skills.domain.document import JsonObject

router = APIRouter(prefix="/api/aprendizado")


def _licoes(request: Request) -> ServicoDeLicoes:
    poc: object = getattr(request.app.state, "poc", None)
    servico: object = getattr(poc, "learning", None)
    licoes = licoes_de(servico) if isinstance(servico, LearningService) else None
    if licoes is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "As lições ainda não foram compostas."})
    return licoes


def _licao(e: Escolhida, licoes: ServicoDeLicoes) -> JsonObject:
    medida = licoes.medida(e.item)
    return {"id": e.item.id, "texto": e.item.summary, "tokens": estimar_tokens(e.item.summary),
            "nivel": e.nivel.name.lower(), "detalhe": e.item.state_detail,
            "escopo": {"app": e.item.escopo.app, "acao": e.item.escopo.capability, "etapa": e.item.escopo.step_hash},
            "evidencia": {"for": e.item.evidence_for, "against": e.item.evidence_against,
                          "execucoes": e.item.distinct_runs},
            "faltam": medida.faltam if medida is not None else None}


def _previa(p: Previa, licoes: ServicoDeLicoes) -> JsonObject:
    return {"app": p.app, "papel": p.papel.value, "acao": p.acao, "etapa": p.etapa, "modo": p.modo.value,
            "vai_ao_prompt": p.modo is Modo.ON and bool(p.bloco), "bloco": p.bloco,
            "tokens": {"bloco": p.tokens_do_bloco, "licoes": p.escolha.tokens},
            "teto": {"tokens": p.teto.tokens, "itens": p.teto.itens, "caracteres_por_item": p.teto.caracteres_por_item},
            "licoes": [_licao(e, licoes) for e in p.escolha.escolhidas], "cortadas": p.escolha.cortadas}


@router.get("/licoes/previa", response_model=None)
async def previa(request: Request, app: str = Query(min_length=3, max_length=200),
                 papel: Literal["actor", "planner"] = "actor", acao: str = Query("", max_length=64),
                 etapa: str = Query("", max_length=128)) -> JsonObject:
    licoes = _licoes(request)
    return _previa(licoes.previa(app=app, papel=Papel(papel), acao=acao, etapa=etapa), licoes)
