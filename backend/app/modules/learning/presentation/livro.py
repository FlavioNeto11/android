"""Rotas do livro de aprendizado (ADR-054, fluxo §8): a leitura única e o status com trilha.

- `GET  /api/aprendizado?kind=&state=&app=&origem=`: a união (receita, fluxo, habilidade, memória e os itens do
  livro), com a contagem por tipo e estado. `app=` é o PACOTE (chave canônica) ou `nao_resolvido` (o balde do fluxo e
  da habilidade cujo app não resolve, 30.2); a memória é da persona e não entra em nenhum dos dois;
- `GET  /api/aprendizado/pendentes`: a fila do D1 ("Para aprovar") e a contagem da barra do topo;
- `GET  /api/aprendizado/revisar`: receitas e fluxos ATIVOS com efeito externo que nenhuma pessoa decidiu pelo livro
  (o legado anterior ao D1);
- `GET  /api/aprendizado/{kind}/{ref}`: o item com a evidência e a trilha (memória: só a contagem);
- `POST /api/aprendizado/{kind}/{ref}/status {to, reason}`: em receita e fluxo, CAS no status nativo e trilha; em
  habilidade, 409 com o endereço da rota das habilidades; motivo obrigatório.

`mudar_status_legado` leva ao mesmo serviço as rotas antigas `PUT /api/flows/{id}` e `PUT /api/recipes/{id}` (que moram
em `app/api.py`, com o vocabulário nativo): a mesma trilha, o mesmo quem, o mesmo veto.

Camada de apresentação: fala FastAPI, traduz as recusas do domínio (`ErroDeAprendizado.code`) para o
`{code, message}` de sempre e monta o JSON. Regra nenhuma mora aqui. As rotas ESPECÍFICAS dos pacotes seguintes
(`/api/aprendizado/falhas`, `/licoes/previa`...) entram ANTES destas em `router.py`: `{kind}/{ref}` casaria com elas.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.learning.application.servico import DetalheDoLivro, LearningService
from app.modules.learning.domain.ciclo import (EntradaInvalida, ErroDeAprendizado, NaoEncontrado, SkillState,
                                               UseARotaDasHabilidades)
from app.modules.learning.domain.livro import (EntradaDoLivro, Transicao, acoes_da_pessoa,
                                               por_que_o_sistema_nao_publica)
from app.modules.learning.domain.promocao import Evidencia
from app.modules.learning.domain.vocabulario import LivroKind, Origem
from app.modules.skills.domain.document import JsonObject, JsonValue
from app.shared.costuras import autor_do_gesto

T = TypeVar("T")

router = APIRouter(prefix="/api/aprendizado")


# ------------------------------------------------------------------ acesso ao estado
def _servico(request: Request) -> LearningService:
    poc: object = getattr(request.app.state, "poc", None)
    servico: object = getattr(poc, "learning", None)
    if not isinstance(servico, LearningService):
        raise HTTPException(503, detail={"code": "not_ready", "message": "O aprendizado ainda não foi composto."})
    return servico


def _quem(request: Request) -> str:
    """Quem decide: o operador da sessão do painel, ou `panel`. Nunca o ator de sistema (a regra `_quem` das
    habilidades): pela rota decide sempre uma pessoa. A regra é a do kernel (`autor_do_gesto`), a mesma dos sinais
    de gesto que chegam pelas costuras."""
    return autor_do_gesto(getattr(request.state, "operador", None))


def mudar_status_legado(request: Request, kind: LivroKind, ref: str, status: str, *, reason: str) -> EntradaDoLivro:
    """O que `PUT /api/flows/{id}` e `PUT /api/recipes/{id}` (em `app/api.py`) decidem, pelo livro: o mesmo quem
    (`_quem`), o mesmo serviço e as mesmas recusas (`{code, message}`; 404, 422 ou 409). Sem isso, o que uma pessoa
    desligava por ali não entrava na trilha — não vetava, e o sistema podia reaprender o mesmo caminho."""
    servico = _servico(request)
    quem = _quem(request)
    return _chamar(lambda: servico.mudar_status_nativo(kind, ref, status, by=quem, reason=reason))


def _http(exc: ErroDeAprendizado) -> HTTPException:
    detalhe: dict[str, JsonValue] = {"code": exc.code, "message": str(exc)}
    if isinstance(exc, NaoEncontrado):
        return HTTPException(404, detail=detalhe)
    if isinstance(exc, EntradaInvalida):
        return HTTPException(422, detail=detalhe)
    if isinstance(exc, UseARotaDasHabilidades):
        detalhe["href"] = exc.href
    return HTTPException(409, detail=detalhe)


def _chamar(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ErroDeAprendizado as exc:
        raise _http(exc) from exc


# ------------------------------------------------------------------ JSON
def _entrada(e: EntradaDoLivro, servico: LearningService | None = None) -> JsonObject:
    """`acoes` e `por_que_nao_publica` vêm do domínio (§5.4 do aprendizado vivo): o painel não espelha o `ciclo.py`.
    Com o `servico`, o motivo conhece o modo do tipo e do pacote e o veto (`vetado`, `modo_desligado`); sem ele, só o
    que o próprio item diz (efeito externo, texto de pessoa, habilidade)."""
    if servico is None:
        motivo = por_que_o_sistema_nao_publica(e)
    else:
        modo_publica, veto = servico.contexto_de_publicacao(e)
        motivo = por_que_o_sistema_nao_publica(e, modo_publica=modo_publica, veto=veto)
    return {"kind": e.kind.value, "ref": e.ref, "state": e.state.value if e.state else None,
            "native_status": e.native_status, "title": e.title, "app": e.app, "app_ref": e.app_ref,
            "origin": e.origin.value,
            "side_effect": e.side_effect, "human_origin": e.human_origin, "requires_owner": e.requires_owner,
            "created_at": e.created_at, "state_at": e.state_at, "last_used_at": e.last_used_at, "uses": e.uses,
            "evidence": {"for": e.a_favor, "against": e.contra}, "count": e.count, "detail": e.detail,
            "acoes": [{"to": a.to.value, "rotulo": a.rotulo, "exige_motivo": a.exige_motivo}
                      for a in acoes_da_pessoa(e)],
            "por_que_nao_publica": None if motivo is None else {
                "codigo": motivo.codigo, "espera_o_dono": motivo.espera_o_dono, "detalhe": motivo.detalhe}}


def _evidencia(e: Evidencia) -> JsonObject:
    return {"stance": e.stance.value, "origin_ref": e.origin_ref, "run_id": e.run_id, "instance_id": e.instance_id,
            "app_version": e.app_version, "simulated": e.simulated, "detail": e.detail, "observed_at": e.observed_at}


def _transicao(t: Transicao) -> JsonObject:
    return {"id": t.id, "from": t.from_state.value if t.from_state else None, "to": t.to_state.value,
            "reason": t.reason, "decided_by": t.decided_by, "decided_at": t.decided_at, "run_id": t.run_id}


def _detalhe(d: DetalheDoLivro, servico: LearningService) -> JsonObject:
    return {"item": _entrada(d.entrada, servico), "evidencias": [_evidencia(e) for e in d.evidencias],
            "trilha": [_transicao(t) for t in d.trilha], "exposicoes": list(d.exposicoes),
            "conteudo": d.conteudo}


def _lista(entradas: tuple[EntradaDoLivro, ...], servico: LearningService) -> JsonObject:
    return {"itens": [_entrada(e, servico) for e in entradas], "total": len(entradas)}


class CorpoDeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: SkillState
    reason: str = Field(min_length=1, max_length=500)


# ------------------------------------------------------------------ rotas
@router.get("", response_model=None)
async def ler_livro(request: Request, kind: LivroKind | None = None, state: SkillState | None = None,
                    app: str | None = None, origem: Origem | None = None) -> JsonObject:
    servico = _servico(request)
    livro = servico.livro(kind=kind, state=state, app=app, origem=origem)
    contagem: JsonObject = {k: {estado: n for estado, n in v.items()} for k, v in livro.contagem.items()}
    return {"itens": [_entrada(e, servico) for e in livro.itens], "total": len(livro.itens), "contagem": contagem}


@router.get("/pendentes", response_model=None)
async def pendentes(request: Request) -> JsonObject:
    servico = _servico(request)
    return _lista(servico.pendentes(), servico)


@router.get("/revisar", response_model=None)
async def revisar(request: Request) -> JsonObject:
    servico = _servico(request)
    return _lista(servico.revisar(), servico)


@router.get("/{kind}/{ref}", response_model=None)
async def ler_item(request: Request, kind: LivroKind, ref: str) -> JsonObject:
    servico = _servico(request)
    return _detalhe(_chamar(lambda: servico.detalhe(kind, ref)), servico)


@router.post("/{kind}/{ref}/status", response_model=None)
async def mudar_status(request: Request, kind: LivroKind, ref: str, corpo: CorpoDeStatus) -> JsonObject:
    servico = _servico(request)
    quem = _quem(request)
    entrada = _chamar(lambda: servico.mudar_estado(kind, ref, corpo.to, by=quem, reason=corpo.reason))
    return _detalhe(_chamar(lambda: servico.detalhe(entrada.kind, entrada.ref)), servico)
