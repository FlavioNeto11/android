"""Rotas do D2 (ADR-054, decisão 2): o botão "deu certo / deu errado + motivo" e a aba Sinais.

- `POST /api/runs/{run_id}/feedback {objective_id?, verdict, reason?, note?}` → 201 `{signal, efeitos, resumo}`.
  Sem `objective_id` o voto vale para a execução (`run:<id>`); um voto por pessoa e por item (votar de novo troca o
  voto, sem reativar o que foi desligado). 404 execução ou objetivo inexistente; 422 fora do vocabulário ('errado'
  exige motivo; 'certo' não leva); 409 `note_looks_secret` — a nota com cara de credencial NÃO é gravada, nem o voto;
- `GET /api/runs/{run_id}/feedback`: os votos por item, os sinais implícitos da execução e o bloco `aprendizado` — o
  que ela ensinou ao livro e o que usou dele, em cinco listas (`receitas`, `fluxos`, `falhas`, `candidatas`, `licoes`;
  o formato que o painel lê em `model.ts::lerAprendizado`), vazias quando nada mudou; `null` só quando a leitura dele
  falhou (os votos e os sinais saem mesmo assim);
- `GET /api/aprendizado/sinais?dias=&kind=&app=`: a aba Sinais, com `app_nome` e `capability_nome` em cada sinal
  (`presentation/nomes.py`; nulos quando não se sabe).

Cada efeito diz o que mudou (`de` → `para`) e, quando o voto desligou algo que ESTAVA publicado, o `desfazer`: a
chamada exata do `POST /api/aprendizado/{kind}/{ref}/status` que o reativa (a única volta da tabela do D1 é
`published`, e é de pessoa). Desligado do `candidate`/`validated`, o `desfazer` vem `null`: aquela volta não
desfaria, publicaria o que nunca foi aprovado (a regra é `reativar_desfaz`, no domínio).

A ORDEM importa duas vezes: estas rotas entram antes do livro (`router.py`), cuja `{kind}/{ref}` casaria com
`/sinais`; e o roteador do aprendizado entra antes do `api.py` (`main.py`), cujo `POST /runs/{run_id}/{op}` casaria com
`/feedback`. Regra nenhuma mora aqui.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.learning.application.aprendido import AprendizadoDaExecucao
from app.modules.learning.application.feedback import (EfeitoAplicado, ServicoDeFeedback, SinalGravado, Voto)
from app.modules.learning.application.servico import NOTA_MAX, LearningService
from app.modules.learning.domain.aprendido import CHAVE_DO_GRUPO, ItemAprendido
from app.modules.learning.domain.vocabulario import MotivoDoVoto, SignalKind, Veredito
from app.modules.learning.infrastructure.aprendido_sql import montar_aprendizado
from app.modules.learning.infrastructure.feedback_sql import montar_feedback
from app.modules.learning.presentation.livro import _chamar, _quem
from app.modules.learning.presentation.nomes import nomear
from app.modules.skills.domain.document import JsonObject, JsonValue

router = APIRouter(prefix="/api")
log = logging.getLogger("poc.aprendizado")


def _feedback(request: Request) -> ServicoDeFeedback:
    poc: object = getattr(request.app.state, "poc", None)
    servico: object = getattr(poc, "learning", None)
    feedback = montar_feedback(getattr(poc, "db", None), servico) if isinstance(servico, LearningService) else None
    if feedback is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "O aprendizado ainda não foi composto."})
    return feedback


def _aprendizado(request: Request) -> AprendizadoDaExecucao | None:
    poc: object = getattr(request.app.state, "poc", None)
    servico: object = getattr(poc, "learning", None)
    return montar_aprendizado(getattr(poc, "db", None), servico) if isinstance(servico, LearningService) else None


# ------------------------------------------------------------------ JSON
def _sinal(s: SinalGravado) -> JsonObject:
    return {"id": s.id, "kind": s.kind.value, "polarity": s.polarity.value, "verdict": s.verdict, "reason": s.reason,
            "note": s.note, "note_refused": s.note_refused, "source_ref": s.source_ref, "created_by": s.created_by,
            "run_id": s.run_id, "objective_id": s.objective_id, "step_id": s.step_id, "attempt_id": s.attempt_id,
            "instance_id": s.instance_id, "profile_id": s.profile_id, "app_package": s.app_package,
            "capability": s.capability, "step_hash": s.step_hash, "failure_kind": s.failure_kind,
            "step_verified": s.step_verified, "data": s.data, "simulated": s.simulated, "created_at": s.created_at,
            "updated_at": s.updated_at}


def _efeito(a: EfeitoAplicado) -> JsonObject:
    e = a.efeito
    desfazer: JsonObject | None = None
    if a.reativavel:
        desfazer = {"method": "POST", "href": f"/api/aprendizado/{e.kind}/{e.ref}/status",
                    "body": {"to": "published", "reason": "reativado depois do voto"}}
    return {"acao": e.acao.value, "kind": e.kind, "ref": e.ref, "uso": e.uso.value if e.uso else None,
            "de": e.de.value if e.de else None, "para": e.para.value if e.para else None, "aplicado": a.aplicado,
            "erro": a.erro, "desfazer": desfazer}


def _aprendido(i: ItemAprendido) -> JsonObject:
    """Uma linha do bloco, com os nomes que `model.ts::lerAprendizado` lê. Sem `titulo`, o painel escreve o rótulo da
    falha pelo `failure_kind` e pelo `n`, ou mostra o ref."""
    return {"kind": i.kind.value if i.kind else None, "ref": i.ref, "titulo": i.titulo,
            "estado": i.estado.value if i.estado else None, "papel": i.papel,
            "braco": i.braco.value if i.braco else None, "failure_kind": i.failure_kind, "n": i.n}


def _bloco(itens: tuple[ItemAprendido, ...]) -> JsonObject:
    grupos: dict[str, list[JsonValue]] = {chave: [] for chave in CHAVE_DO_GRUPO.values()}
    for i in itens:
        grupos[CHAVE_DO_GRUPO[i.grupo]].append(_aprendido(i))
    return {chave: lista for chave, lista in grupos.items()}


class CorpoDoVoto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_id: str | None = Field(default=None, min_length=1, max_length=300)
    verdict: Veredito
    reason: MotivoDoVoto | None = None
    note: str | None = Field(default=None, max_length=NOTA_MAX)


# ------------------------------------------------------------------ rotas
@router.post("/runs/{run_id}/feedback", status_code=201, response_model=None)
async def votar(request: Request, run_id: str, corpo: CorpoDoVoto) -> JsonObject:
    feedback = _feedback(request)
    quem = _quem(request)
    voto = Voto(verdict=corpo.verdict, reason=corpo.reason, note=corpo.note, objective_id=corpo.objective_id)
    resultado = _chamar(lambda: feedback.votar(run_id, voto, by=quem))
    return {"signal": _sinal(resultado.sinal), "efeitos": [_efeito(a) for a in resultado.efeitos],
            "resumo": resultado.resumo}


@router.get("/runs/{run_id}/feedback", response_model=None)
async def ler_votos(request: Request, run_id: str) -> JsonObject:
    feedback = _feedback(request)
    lidos = _chamar(lambda: feedback.da_execucao(run_id))
    return {"run_id": run_id, "votos": [_sinal(s) for s in lidos.votos], "sinais": [_sinal(s) for s in lidos.sinais],
            "aprendizado": _aprendizado_da_execucao(request, run_id)}


def _aprendizado_da_execucao(request: Request, run_id: str) -> JsonObject | None:
    """O bloco informa: uma leitura que quebra (linha legada estranha) não derruba os votos nem os sinais."""
    servico = _aprendizado(request)
    if servico is None:
        return None
    try:
        return _bloco(servico.da_execucao(run_id))
    except Exception:  # noqa: BLE001 - o bloco é informativo; o voto e os sinais continuam saindo
        log.exception("aprendizado da execução %s", run_id)
        return None


@router.get("/aprendizado/sinais", response_model=None)
async def sinais(request: Request, dias: int = Query(14, ge=1, le=400), kind: SignalKind | None = None,
                 app: str | None = None) -> JsonObject:
    lista = _feedback(request).sinais(dias=dias, kind=kind, app=app)
    por_tipo: dict[str, int] = {}
    for s in lista:
        por_tipo[s.kind.value] = por_tipo.get(s.kind.value, 0) + 1
    contagem: JsonObject = {k: n for k, n in por_tipo.items()}
    linhas = [_sinal(s) for s in lista]
    # Só na aba Sinais (P3 do deploy 3): os votos de uma execução seguem com o pacote e o código.
    poc: object = getattr(request.app.state, "poc", None)
    servico: object = getattr(poc, "learning", None)
    nomear(linhas, servico if isinstance(servico, LearningService) else None, app="app_package")
    sinais: list[JsonValue] = list(linhas)
    return {"sinais": sinais, "total": len(lista), "contagem": contagem, "dias": dias}
