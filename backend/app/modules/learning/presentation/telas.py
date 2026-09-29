"""Pacote A8 do ADR-054: `GET /api/aprendizado/export?kind=tela&app=<pacote>` — o fragmento YAML das telas aprendidas
(validadas e publicadas) de um app, com a proveniência em comentário, para uma sessão de desenvolvimento commitar no
`telas.yaml` do repositório. O fragmento sai conferido pelo carregador do motor: acrescentado ao arquivo do app, ele
carrega e cada regra volta igual. Só leitura: exportar não muda nada no livro (a absorção é da curadoria, depois do
commit implantado).

Só a tela exporta: receita, fluxo e habilidade têm casa nativa, e lição, voz e preferência não viram arquivo.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.modules.learning.application.telas import servico_de_telas
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.presentation.livro import _chamar, _servico

router = APIRouter(prefix="/api/aprendizado")


@router.get("/export", response_model=None)
async def exportar(request: Request, app: str = Query(min_length=3, max_length=200),
                   kind: LivroKind = LivroKind.TELA) -> Response:
    if kind is not LivroKind.TELA:
        raise HTTPException(422, detail={"code": "invalid", "message": "Só a tela aprendida exporta para o "
                                         "repositório: as outras têm casa nativa ou não viram arquivo."})
    telas = servico_de_telas(_servico(request))
    if telas is None:
        raise HTTPException(503, detail={"code": "not_ready",
                                         "message": "As telas aprendidas ainda não foram ligadas."})
    texto = _chamar(lambda: telas.exportar(app))
    return Response(content=texto, media_type="text/yaml; charset=utf-8")
