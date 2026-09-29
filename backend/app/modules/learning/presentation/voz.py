"""Rotas do pacote A9 do ADR-054: a prévia da voz e as sugestões de preferência. Só leitura, sem IA.

- `GET /api/aprendizado/voz/previa?profile_id=`: o que a voz aprendida daquele perfil levaria ao texto — as aprovações
  editadas (a origem), as vozes esperando o dono, as publicadas e, por ação, o bloco EXATO que iria ao contexto
  social, com os tokens. Responde inclusive que não há aprovação editada. 404 perfil inexistente; 422 sem o perfil;
- `GET /api/aprendizado/preferencias/sugestoes?run_id=`: o que PRÉ-PREENCHER nas perguntas abertas de uma execução
  em `needs_input`. Nunca responde, nunca cria a sucessora, nunca grava: a pessoa confirma. 404 execução inexistente.

As duas entram ANTES do livro (`router.py`): a rota genérica `{kind}/{ref}` casaria com elas.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.modules.learning.application.preferencias import ServicoDePreferencias
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.voz import PreviaDaVoz, ServicoDeVoz
from app.modules.learning.infrastructure.ligar_voz import montar_voz
from app.modules.learning.infrastructure.preferencias_sql import montar_preferencias
from app.modules.learning.presentation.livro import _chamar
from app.modules.skills.domain.document import JsonObject, JsonValue

router = APIRouter(prefix="/api/aprendizado")

_NAO_PRONTO = {"code": "not_ready", "message": "O aprendizado ainda não foi composto."}


def _estado(request: Request) -> tuple[object, object]:
    poc: object = getattr(request.app.state, "poc", None)
    servico: object = getattr(poc, "learning", None)
    if not isinstance(servico, LearningService):
        raise HTTPException(503, detail=_NAO_PRONTO)
    return getattr(poc, "db", None), servico


def _voz(request: Request) -> ServicoDeVoz:
    voz = montar_voz(*_estado(request))
    if voz is None:
        raise HTTPException(503, detail=_NAO_PRONTO)
    return voz


def _preferencias(request: Request) -> ServicoDePreferencias:
    preferencias = montar_preferencias(*_estado(request))
    if preferencias is None:
        raise HTTPException(503, detail=_NAO_PRONTO)
    return preferencias


def _previa(p: PreviaDaVoz) -> JsonObject:
    blocos: list[JsonValue] = [
        {"capability": b.capability, "tokens": b.tokens, "texto": b.texto,
         "pares": [{"gerado": x.gerado, "editado": x.editado} for x in b.pares]} for b in p.blocos]
    return {"profile_id": p.profile_id, "modo": p.modo.value, "vai_ao_prompt": p.vai_ao_prompt,
            "aprovacoes_editadas": p.aprovacoes_editadas, "candidatas": p.candidatas, "publicadas": p.publicadas,
            "blocos": blocos, "mensagem": p.mensagem}


@router.get("/voz/previa", response_model=None)
async def previa_da_voz(request: Request, profile_id: str = Query(min_length=1, max_length=120)) -> JsonObject:
    voz = _voz(request)
    return _previa(_chamar(lambda: voz.previa(profile_id)))


@router.get("/preferencias/sugestoes", response_model=None)
async def sugestoes(request: Request, run_id: str = Query(min_length=1, max_length=120)) -> JsonObject:
    preferencias = _preferencias(request)
    achadas = _chamar(lambda: preferencias.sugestoes(run_id))
    lista: list[JsonValue] = [{"campo": s.campo, "valor": s.valor, "item_id": s.item_id} for s in achadas]
    modo: str = preferencias.modo.value
    return {"run_id": run_id, "modo": modo, "sugestoes": lista}
