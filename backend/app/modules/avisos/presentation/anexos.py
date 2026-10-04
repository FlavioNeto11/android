"""As rotas dos anexos dos canais (item 28.24): `GET /api/canais/anexos/{id}` e `.../conteudo` (só leitura, F1) e
`POST /api/canais/anexos/{id}/trello` (F2: anexa ao cartão do Trello a imagem que o dono mandou, com confirmação).

Passam pelo mesmo portão de toda rota `/api/` do painel (`main.guarda`: sessão ou credencial; sem elas, 401), como a
`/api/canais/estado`. Os metadados nunca trazem caminho de disco nem o nome que o remetente deu (o produto nunca o
guarda). O conteúdo sai como DOWNLOAD (`Content-Disposition: attachment`, nome neutro `anexo-<id>.<ext>`) e com
`X-Content-Type-Options: nosniff`, no mime que o conteúdo mostrou na entrada: o navegador não abre nem interpreta o
arquivo no site do painel.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.modules.avisos.adapters.trello import FalhaDoTrello
from app.modules.avisos.domain.anexos import EXTENSAO
from app.modules.avisos.infrastructure.anexos import ArmazemDeAnexos
from app.modules.avisos.infrastructure.anexos_trello import AnexoNaoPodeIrAoCartao, anexar_ao_cartao

router = APIRouter(prefix="/api/canais/anexos")

_CAMPOS = ("id", "canal", "entrada_id", "direcao", "sha256", "mime", "bytes", "estado", "motivo_recusa", "criado_em",
           "apagado_em")


def _armazem(request: Request) -> ArmazemDeAnexos:
    armazem = getattr(getattr(request.app.state, "poc", None), "anexos_canal", None)
    if not isinstance(armazem, ArmazemDeAnexos):
        raise HTTPException(503, detail={"code": "not_ready", "message": "Os anexos dos canais ainda não foram compostos."})
    return armazem


class AnexarAoCartao(BaseModel):
    card: str
    confirmar: bool = False


def _linha(request: Request, ident: int) -> dict[str, object]:
    linha = _armazem(request).linha(ident)
    if linha is None:
        raise HTTPException(404, detail={"code": "anexo_desconhecido", "message": "Não há anexo com esse id."})
    return linha


@router.get("/{ident}")
async def metadados_do_anexo(ident: int, request: Request) -> dict[str, object]:
    linha = _linha(request, ident)
    return {c: linha.get(c) for c in _CAMPOS}


@router.get("/{ident}/conteudo")
async def conteudo_do_anexo(ident: int, request: Request) -> FileResponse:
    linha = _linha(request, ident)
    estado = linha.get("estado")
    if estado == "apagado":
        raise HTTPException(410, detail={"code": "anexo_apagado",
                                         "message": "O arquivo venceu a retenção e foi apagado."})
    aberto = _armazem(request).abrir(ident) if estado == "guardado" else None
    if aberto is None:
        raise HTTPException(404, detail={"code": "anexo_sem_arquivo",
                                         "message": "Este anexo não tem arquivo guardado (foi recusado ou sumiu do disco)."})
    meta, caminho = aberto
    mime = str(meta["mime"])
    nome = f"anexo-{ident}.{EXTENSAO.get(mime, 'bin')}"
    return FileResponse(caminho, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="{nome}"', "X-Content-Type-Options": "nosniff"})


@router.post("/{ident}/trello")
async def anexar_ao_cartao_do_trello(ident: int, corpo: AnexarAoCartao, request: Request) -> dict[str, object]:
    """Anexa ao cartão a imagem que o DONO mandou (exceção (b) do dono, 04/10). Pede `confirmar: true`: é um efeito em
    sistema externo. Só anexo de entrada do dono; cartão de quadro configurado; a falha do Trello vira 502."""
    if not corpo.confirmar:
        raise HTTPException(400, detail={"code": "confirmacao_necessaria",
                                         "message": "Anexar ao cartão do Trello pede confirmar: true."})
    armazem = _armazem(request)
    poc = request.app.state.poc
    espelho = getattr(poc, "trello_espelho", None)
    try:
        return await anexar_ao_cartao(poc.db, poc.cfg, armazem, espelho.cliente() if espelho is not None else None,
                                      ident, corpo.card)
    except AnexoNaoPodeIrAoCartao as recusa:
        raise HTTPException(recusa.status, detail={"code": recusa.codigo, "message": recusa.motivo}) from None
    except FalhaDoTrello as falha:
        raise HTTPException(502, detail={"code": "trello_falhou", "message": falha.motivo}) from None
