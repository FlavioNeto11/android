"""Rotas do rótulo de intenção (item 30.25). Sem IA, sem custo.

- `GET /api/aprendizado/intencao`: as perguntas abertas, da mais recente para a mais antiga, e o total. Cada uma traz o
  comando da execução (lido agora, nunca do dossiê), a cadeia (`sem_casamento` ou `empate`), os candidatos com o nome do
  catálogo de agora e os empatados. 503 antes da composição;
- `POST /api/aprendizado/execucao/{run_id}/intencao` (`{"escolha": "<skill_id>" | "nenhum"}`): a resposta da pessoa,
  com o operador da sessão. 404 sem pergunta; 422 fora do catálogo gravado; 409 já respondida.

As duas entram ANTES do livro (`router.py`): a rota genérica `{kind}/{ref}` casaria com elas.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.learning.application.intencao import PENDENTES_MAX, PerguntaAberta, RotuloGravado, ServicoDeRotulos
from app.modules.learning.application.servico import LearningService
from app.modules.learning.presentation.livro import _chamar, _quem
from app.modules.learning.presentation.nomes import nomear
from app.modules.skills.domain.document import JsonObject, JsonValue

router = APIRouter(prefix="/api/aprendizado")


def _servicos(request: Request) -> tuple[LearningService, ServicoDeRotulos]:
    poc: object = getattr(request.app.state, "poc", None)
    servico: object = getattr(poc, "learning", None)
    rotulos = servico.extensao(ServicoDeRotulos) if isinstance(servico, LearningService) else None
    if not isinstance(servico, LearningService) or rotulos is None:
        raise HTTPException(503, detail={"code": "not_ready",
                                         "message": "O rótulo de intenção ainda não foi composto."})
    return servico, rotulos


def _rotulo(r: RotuloGravado) -> JsonObject:
    return {"review_id": r.id, "run_id": r.run_id, "criado_em": r.criado_em, "app": r.app or None,
            "decisao_final": r.decisao_final, "decidido_por": r.decidido_por}


def _pergunta(p: PerguntaAberta) -> JsonObject:
    d = p.rotulo.dossie
    candidatos: list[JsonValue] = [{"skill_id": c, "nome": p.nomes.get(c, c)} for c in (d.candidatos if d else ())]
    return {**_rotulo(p.rotulo), "comando": p.comando, "terminou_em": p.terminou_em,
            "cadeia": d.cadeia.value if d else None, "candidatos": candidatos,
            "empatados": list(d.empatados) if d else []}


class CorpoDoRotulo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    escolha: str = Field(min_length=1, max_length=200)


@router.get("/intencao", response_model=None)
async def perguntas_abertas(request: Request, limite: int = Query(default=50, ge=1, le=PENDENTES_MAX)) -> JsonObject:
    servico, rotulos = _servicos(request)
    perguntas, total = rotulos.pendentes(limite)
    itens = [_pergunta(p) for p in perguntas]
    nomear(itens, servico)
    return {"itens": list[JsonValue](itens), "total": total}


@router.post("/execucao/{run_id}/intencao", response_model=None)
async def rotular(request: Request, run_id: str, corpo: CorpoDoRotulo) -> JsonObject:
    _, rotulos = _servicos(request)
    quem = _quem(request)
    return _rotulo(_chamar(lambda: rotulos.rotular(run_id, corpo.escolha, by=quem)))
