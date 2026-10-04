"""As rotas das decisões automáticas (item 28.25): listar e desfazer. Contrato em `docs/api-contract.md` (v1.22).

Passam pelo portão de toda rota `/api/` do painel (`main.guarda`: sessão ou credencial; sem elas, 401): é a mesma
autorização das rotas de decisão do livro e das pendências. Quem desfez é o operador da SESSÃO (`autor_do_gesto`), nunca
um campo do corpo.

Apresentação só traduz: o prazo, a idempotência e a inversa moram em `application/desfazer.py`.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.decisoes.application.desfazer import (DecisaoNaoEncontrada, DesfazerDecisoes, PrazoVencido,
                                                         SemInversaSegura, rotulo_do_desfazer)
from app.modules.decisoes.domain.decisao import Decisao
from app.modules.decisoes.domain.leitura import efeito_legivel, fatos_legiveis
from app.modules.decisoes.infrastructure.registro_sql import LIMITE_MAX, LIMITE_PADRAO, RegistroSql
from app.shared.costuras import autor_do_gesto
from app.shared.decisoes import FILAS
from app.util import parse_iso

router = APIRouter(prefix="/api/decisoes-automaticas")

MOTIVO_MAX = 300


class CorpoDoDesfazer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Desfazer é um gesto com efeito na fila dona: o dono confirma por extenso (sem isto, 400).
    confirmar: bool = False
    motivo: str | None = Field(None, max_length=MOTIVO_MAX)


def _estado(request: Request) -> tuple[RegistroSql, DesfazerDecisoes, float]:
    poc: object = getattr(request.app.state, "poc", None)
    registro = getattr(poc, "decisoes_registro", None)
    desfazer = getattr(poc, "decisoes_desfazer", None)
    cfg = getattr(poc, "cfg", None)
    if not isinstance(registro, RegistroSql) or not isinstance(desfazer, DesfazerDecisoes) or cfg is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "As decisões automáticas ainda não foram compostas."})
    return registro, desfazer, float(cfg.file.avisos.decisoes_automaticas.desfazer_dias)


def _item(d: Decisao, desfazer: DesfazerDecisoes) -> dict[str, object]:
    s = desfazer.situacao(d)
    descricao = desfazer.descrever(d)
    fatos = fatos_legiveis(d.fatos)
    return {"id": d.id, "fila": d.fila, "item_ref": d.item_ref, "regra": d.regra,
            "efeito": efeito_legivel(d.fila, d.efeito, d.fatos), "fatos": fatos,
            "item_nome": descricao.nome, "run_id": descricao.run_id or _texto(fatos.get("run_id")),
            "decidida_em": d.decidida_em, "resumida_em": d.resumida_em,
            "desfeita": d.desfeita, "desfeita_em": d.desfeita_em, "desfeita_por": d.desfeita_por,
            "motivo_do_desfazer": d.motivo_do_desfazer,
            "pode_desfazer": s.pode, "acao_do_desfazer": rotulo_do_desfazer(d.fila), "por_que_nao": s.por_que_nao, "prazo_ate": s.prazo_ate}


def _texto(valor: object) -> str | None:
    return valor if isinstance(valor, str) and valor else None


def _instante(valor: str | None, campo: str) -> str | None:
    if not valor:
        return None
    try:
        quando = parse_iso(valor)
    except ValueError:
        quando = None
    if quando is None:
        raise HTTPException(400, detail={"code": "invalid", "message": f"'{campo}' precisa ser uma data ISO (UTC)."})
    return valor


@router.get("", response_model=None)
async def listar(request: Request, regra: str | None = Query(None, max_length=120),
                 fila: str | None = Query(None, max_length=20), desde: str | None = Query(None, max_length=40),
                 ate: str | None = Query(None, max_length=40),
                 desfeitas: Literal["todas", "sim", "nao"] = "todas",
                 limite: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAX)) -> dict[str, object]:
    registro, desfazer, dias = _estado(request)
    if fila is not None and fila not in FILAS:
        raise HTTPException(400, detail={"code": "invalid", "message": f"'fila' precisa ser uma de: {', '.join(FILAS)}."})
    itens = registro.listar(regra=regra, fila=fila, desde=_instante(desde, "desde"), ate=_instante(ate, "ate"),
                            desfeitas=desfeitas, limite=limite)
    # 28.29: o registro mostra o estado de agora do item; o que foi desfeito por outro caminho sai do filtro "não".
    agora = [desfazer.reconciliar(d) for d in itens]
    if desfeitas == "nao":
        agora = [d for d in agora if not d.desfeita]
    return {"itens": [_item(d, desfazer) for d in agora], "total": len(agora), "regras": registro.regras(),
            "desfazer_dias": dias}


@router.post("/{decisao_id}/desfazer", response_model=None)
async def desfazer_decisao(request: Request, decisao_id: int, corpo: CorpoDoDesfazer) -> dict[str, object]:
    registro, desfazer, _ = _estado(request)
    if not corpo.confirmar:
        raise HTTPException(400, detail={"code": "confirmation_required",
                                          "message": "Desfazer exige confirmar: envie {\"confirmar\": true}."})
    quem = autor_do_gesto(getattr(request.state, "operador", None))
    try:
        d, fez = desfazer.desfazer(decisao_id, por=quem, motivo=(corpo.motivo or "").strip() or None)
    except DecisaoNaoEncontrada as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "Não há essa decisão automática."}) from exc
    except PrazoVencido as exc:
        raise HTTPException(409, detail={"code": "prazo_vencido", "message": str(exc)}) from exc
    except SemInversaSegura as exc:
        raise HTTPException(409, detail={"code": "sem_inversa_segura", "message": exc.motivo}) from exc
    return {**_item(d, desfazer), "desfeita_agora": fez}
