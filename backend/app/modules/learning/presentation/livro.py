"""Rotas do livro de aprendizado (ADR-054, fluxo §8): a leitura única e o status com trilha.

- `GET  /api/aprendizado?kind=&state=&app=&origem=`: a união (receita, fluxo, habilidade, memória e os itens do
  livro), com a contagem por tipo e estado. `app=` é o PACOTE (chave canônica) ou `nao_resolvido` (o balde do fluxo e
  da habilidade cujo app não resolve, 30.2); a memória é da persona e não entra em nenhum dos dois;
- `GET  /api/aprendizado/pendentes`: a fila do D1 ("Para aprovar") e a contagem da barra do topo;
- `GET  /api/aprendizado/revisar`: receitas e fluxos ATIVOS com efeito externo que nenhuma pessoa decidiu pelo livro
  (o legado anterior ao D1);
- `GET  /api/aprendizado/{kind}/{ref}`: o item com a evidência e a trilha (memória: só a contagem);
- `POST /api/aprendizado/{kind}/{ref}/status {to, reason}`: em receita e fluxo, CAS no status nativo e trilha; em
  habilidade, 409 com o endereço da rota das habilidades; motivo obrigatório (e nunca no formato reservado
  `evidencia_invalida:…`, que tem ação própria);
- `POST /api/aprendizado/{kind}/{ref}/evidencia-invalida {run_id}` (30.23): a receita ou o fluxo foi aprendido de um
  sucesso falso. Desliga com o motivo estruturado (o já desligado ganha a linha que reclassifica); só a execução de
  origem do item.

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
from app.modules.learning.domain.evidencia_invalida import Reaprendizado, run_invalidada
from app.modules.learning.domain.livro import (EntradaDoLivro, Transicao, acoes_da_pessoa, evidencia_a_invalidar,
                                               por_que_o_sistema_nao_publica)
from app.modules.learning.domain.promocao import Evidencia
from app.modules.learning.domain.saude import Saude
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
def _saude(s: Saude | None) -> JsonObject | None:
    """A saúde do item (30.4): o rótulo, os motivos (vocabulário fechado) e as dimensões medidas. `valor: null` +
    `estado: desconhecida` = sem dado (nunca zero). O texto em português é do painel."""
    if s is None:
        return None
    return {"rotulo": s.rotulo.value,
            "motivos": [{"codigo": m.codigo.value, "dimensao": m.dimensao.value if m.dimensao else None,
                         "valor": m.valor, "limite": m.limite, "detalhe": m.detalhe} for m in s.motivos],
            "dimensoes": [{"nome": d.nome.value, "estado": "desconhecida" if d.desconhecida else "medida",
                           "valor": d.valor, "amostra": d.amostra, "fonte": d.fonte} for d in s.dimensoes]}


def _entrada(e: EntradaDoLivro, servico: LearningService | None = None, saude: Saude | None = None,
             capability: str | None = None, capability_nome: str | None = None) -> JsonObject:
    """`acoes` e `por_que_nao_publica` vêm do domínio (§5.4 do aprendizado vivo): o painel não espelha o `ciclo.py`.
    Com o `servico`, o motivo conhece o modo do tipo e do pacote e o veto (`vetado`, `modo_desligado`); sem ele, só o
    que o próprio item diz (efeito externo, texto de pessoa, habilidade). `capability`: a da hierarquia App → Capability
    → Item (`servico.capabilities`, em lote); `None` quando a linha não tem ou não se sabe. `capability_nome`: o nome
    dela em português, do catálogo do app (`servico.nomes_das_capabilities`); `None` sem catálogo."""
    if servico is None:
        motivo = por_que_o_sistema_nao_publica(e)
    else:
        modo_publica, veto = servico.contexto_de_publicacao(e)
        motivo = por_que_o_sistema_nao_publica(e, modo_publica=modo_publica, veto=veto)
    return {"kind": e.kind.value, "ref": e.ref, "state": e.state.value if e.state else None,
            "native_status": e.native_status, "title": e.title, "app": e.app, "app_ref": e.app_ref,
            "capability": capability, "capability_nome": capability_nome,
            "origin": e.origin.value,
            "side_effect": e.side_effect, "human_origin": e.human_origin, "requires_owner": e.requires_owner,
            "created_at": e.created_at, "state_at": e.state_at, "last_used_at": e.last_used_at, "uses": e.uses,
            "evidence": {"for": e.a_favor, "against": e.contra}, "count": e.count, "detail": e.detail,
            "acoes": [{"to": a.to.value, "rotulo": a.rotulo, "exige_motivo": a.exige_motivo}
                      for a in acoes_da_pessoa(e)],
            "por_que_nao_publica": None if motivo is None else {
                "codigo": motivo.codigo, "espera_o_dono": motivo.espera_o_dono, "detalhe": motivo.detalhe},
            "saude": _saude(saude), "nasceu_de": e.nasceu_de, "reaprendido": _reaprendido(e.reaprendido)}


def _reaprendido(r: Reaprendizado | None) -> JsonObject | None:
    """30.23: a execução da evidência inválida e o item que ela desligou (`kind`/`ref` do detalhe dele)."""
    if r is None:
        return None
    kind, _, ref = r.item_invalidado.partition(":")
    return {"run_invalidada": r.run_invalidada, "item": {"kind": kind, "ref": ref}}


def _evidencia(e: Evidencia, invalidas: frozenset[str] = frozenset()) -> JsonObject:
    """`invalidada` (30.23): a execução desta evidência foi marcada como evidência inválida no item; ela fica no
    histórico, mas não prova nada (a sombra a ignora)."""
    return {"stance": e.stance.value, "origin_ref": e.origin_ref, "run_id": e.run_id, "instance_id": e.instance_id,
            "app_version": e.app_version, "simulated": e.simulated, "detail": e.detail, "observed_at": e.observed_at,
            "invalidada": e.run_id is not None and e.run_id in invalidas}


def _transicao(t: Transicao) -> JsonObject:
    """`tipo` e `run_invalidada` (30.23): o desligamento por evidência inválida já vem lido; o painel nunca interpreta
    o formato do motivo."""
    run = run_invalidada(t.reason)
    return {"id": t.id, "from": t.from_state.value if t.from_state else None, "to": t.to_state.value,
            "reason": t.reason, "decided_by": t.decided_by, "decided_at": t.decided_at, "run_id": t.run_id,
            "tipo": "evidencia_invalida" if run is not None else None, "run_invalidada": run}


def _detalhe(d: DetalheDoLivro, servico: LearningService) -> JsonObject:
    capability = servico.capabilities([d.entrada]).get(d.entrada.trail_ref)
    nome = servico.nome_da_capability(d.entrada.app, capability)
    invalidas = frozenset(r for t in d.trilha if (r := run_invalidada(t.reason)) is not None)
    a_invalidar = evidencia_a_invalidar(d.entrada, d.trilha)
    return {"item": _entrada(d.entrada, servico, d.saude, capability, nome),
            "evidencias": [_evidencia(e, invalidas) for e in d.evidencias],
            "trilha": [_transicao(t) for t in d.trilha], "exposicoes": list(d.exposicoes),
            "conteudo": d.conteudo, "versao": d.versao, "relacoes": list(d.relacoes),
            "invalidar_evidencia": None if a_invalidar is None else {"run_id": a_invalidar}}


def _lista(entradas: tuple[EntradaDoLivro, ...], servico: LearningService) -> JsonObject:
    saudes = servico.saudes(entradas)
    capabilities = servico.capabilities(entradas)
    nomes = servico.nomes_das_capabilities(entradas, capabilities)
    return {"itens": [_entrada(e, servico, saudes.get(e.trail_ref), capabilities.get(e.trail_ref),
                               nomes.get(e.trail_ref)) for e in entradas],
            "total": len(entradas)}


class CorpoDeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: SkillState
    reason: str = Field(min_length=1, max_length=500)


class CorpoDeEvidenciaInvalida(BaseModel):
    """Só a execução: o motivo é estruturado (`evidencia_invalida:<run>`) e quem decide é o operador da sessão."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64)


# ------------------------------------------------------------------ rotas
@router.get("", response_model=None)
async def ler_livro(request: Request, kind: LivroKind | None = None, state: SkillState | None = None,
                    app: str | None = None, origem: Origem | None = None) -> JsonObject:
    servico = _servico(request)
    livro = servico.livro(kind=kind, state=state, app=app, origem=origem)
    contagem: JsonObject = {k: {estado: n for estado, n in v.items()} for k, v in livro.contagem.items()}
    capabilities = servico.capabilities(livro.itens)
    nomes = servico.nomes_das_capabilities(livro.itens, capabilities)
    return {"itens": [_entrada(e, servico, livro.saudes.get(e.trail_ref), capabilities.get(e.trail_ref),
                               nomes.get(e.trail_ref)) for e in livro.itens],
            "total": len(livro.itens), "contagem": contagem}


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


@router.post("/{kind}/{ref}/evidencia-invalida", response_model=None)
async def invalidar_evidencia(request: Request, kind: LivroKind, ref: str, corpo: CorpoDeEvidenciaInvalida) -> JsonObject:
    servico = _servico(request)
    quem = _quem(request)
    entrada = _chamar(lambda: servico.invalidar_evidencia(kind, ref, corpo.run_id, by=quem))
    return _detalhe(_chamar(lambda: servico.detalhe(entrada.kind, entrada.ref)), servico)
