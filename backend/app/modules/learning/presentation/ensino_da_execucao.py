"""31.221: o ensino a partir de uma execução que deu certo (adendo v1.120). Sem IA, sem aparelho.

- `GET /api/aprendizado/execucao/{run_id}/ensino`: por etapa, a receita candidata que nasceu dela ou o motivo fechado
  de não haver o que ensinar (`domain/ensino_da_execucao.Motivo`); no `caminho_nao_reproduzivel`, as ferramentas que
  impediram a receita. Só ids, chaves, ação do catálogo, status e contagens: nenhum argumento, texto ou nome.
- `POST /api/aprendizado/execucao/{run_id}/ensino`: a pessoa (o operador da sessão) promove num gesto as candidatas
  ensináveis, pelo caminho do Livro (`mudar_status_nativo` → candidate → validated → published, com a trilha). O motivo
  é `ensino_da_execucao:<run> persona:<id>`. Cada receita que o Livro recusar volta em `recusadas` com o código; as
  outras seguem. Sem candidata, 200 com `promovidas` vazio e as etapas com os motivos.

404 `execucao_desconhecida`; 503 antes da composição. Entram ANTES do livro (`router.py`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.db import Database
from app.modules.learning.application.pareceres import ServicoDePareceres
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import ErroDeAprendizado
from app.modules.learning.domain.ensino_da_execucao import bloqueadoras, motivo_da_promocao
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure.ensino_da_execucao_sql import EtapaLida, ExecucaoLida, LeitorDoEnsinoDaExecucao
from app.modules.learning.presentation.livro import _quem
from app.modules.skills.domain.document import JsonObject, JsonValue

router = APIRouter(prefix="/api/aprendizado")


def _compostos(request: Request) -> tuple[Database, LearningService]:
    poc: object = getattr(request.app.state, "poc", None)
    db = getattr(poc, "db", None)
    servico = getattr(poc, "learning", None)
    if not isinstance(db, Database) or not isinstance(servico, LearningService):
        raise HTTPException(503, detail={"code": "not_ready", "message": "O aprendizado ainda não foi composto."})
    return db, servico


def _ler(db: Database, run_id: str) -> ExecucaoLida:
    lida = LeitorDoEnsinoDaExecucao(db).ler(run_id)
    if lida is None:
        raise HTTPException(404, detail={"code": "execucao_desconhecida", "message": "Execução não encontrada."})
    return lida


def _etapa(e: EtapaLida) -> JsonObject:
    receita: JsonValue = (None if e.receita is None else
                          {"id": e.receita.id, "status": e.receita.status, "replay_ok": e.receita.replay_ok})
    saida: JsonObject = {"step_id": e.etapa.step_id, "key": e.etapa.key, "capability": e.etapa.capability or None,
                         "status": e.etapa.status, "driven_by": e.etapa.driven_by or None,
                         "persona": e.etapa.profile_id or None, "receita": receita,
                         "ensinavel": e.motivo is None and e.receita is not None,
                         "motivo": e.motivo.value if e.motivo is not None else None}
    if bloqueio := bloqueadoras(e.etapa):
        saida["ferramentas_nao_reproduziveis"] = list[JsonValue](bloqueio)
    return saida


def _corpo(lida: ExecucaoLida) -> JsonObject:
    return {"run_id": lida.run_id, "status": lida.status, "simulada": lida.simulada,
            "etapas": [_etapa(e) for e in lida.etapas], "ensinaveis": len(lida.ensinaveis)}


@router.get("/execucao/{run_id}/ensino", response_model=None)
def ensino_da_execucao(request: Request, run_id: str) -> JsonObject:
    db, _ = _compostos(request)
    return _corpo(_ler(db, run_id))


@router.post("/execucao/{run_id}/ensino", response_model=None)
def ensinar_da_execucao(request: Request, run_id: str) -> JsonObject:
    db, servico = _compostos(request)
    quem = _quem(request)
    pareceres = servico.extensao(ServicoDePareceres)
    mover = pareceres.mudar_status_nativo if pareceres is not None else servico.mudar_status_nativo
    promovidas: list[JsonValue] = []
    recusadas: list[JsonValue] = []
    for e in _ler(db, run_id).ensinaveis:
        if e.receita is None:
            continue
        try:
            mover(LivroKind.RECEITA, str(e.receita.id), "active", by=quem,
                  reason=motivo_da_promocao(run_id, e.etapa.profile_id))
        except ErroDeAprendizado as exc:
            recusadas.append({"recipe_id": e.receita.id, "step_key": e.etapa.key, "code": exc.code,
                              "message": str(exc)})
            continue
        promovidas.append({"recipe_id": e.receita.id, "step_key": e.etapa.key})
    return {**_corpo(_ler(db, run_id)), "promovidas": promovidas, "recusadas": recusadas}
