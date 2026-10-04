"""As rotas dos anexos dos canais (item 28.24): `GET /api/canais/anexos/{id}` e `.../conteudo` (só leitura, F1),
`POST /api/canais/anexos/{id}/trello` (F2: anexa ao cartão do Trello a imagem que o dono mandou, com confirmação) e
`GET /api/canais/anexos` (F4: a lista paginada da tela Anexos do painel).

Passam pelo mesmo portão de toda rota `/api/` do painel (`main.guarda`: sessão ou credencial; sem elas, 401), como a
`/api/canais/estado`. Os metadados nunca trazem caminho de disco nem o nome que o remetente deu (o produto nunca o
guarda). O conteúdo sai como DOWNLOAD (`Content-Disposition: attachment`, nome neutro `anexo-<id>.<ext>`), com
`X-Content-Type-Options: nosniff` e `Cache-Control: no-store`, no mime que o conteúdo mostrou na entrada: o navegador não
abre nem interpreta o arquivo no site do painel (a `<img>` do painel o mostra mesmo assim). Só imagem e PDF saem: o texto
guardado (`text/plain`) não tem prévia (415), e o anexo de convidado não tem conteúdo (404).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.modules.avisos.adapters.trello import FalhaDoTrello
from app.modules.avisos.domain.anexos import EXTENSAO
from app.modules.avisos.infrastructure.anexos import ArmazemDeAnexos
from app.modules.avisos.infrastructure.anexos_trello import AnexoNaoPodeIrAoCartao, anexar_ao_cartao
from app.util import parse_iso, to_iso

router = APIRouter(prefix="/api/canais/anexos")

_CAMPOS = ("id", "canal", "entrada_id", "direcao", "sha256", "mime", "bytes", "estado", "motivo_recusa", "criado_em",
           "apagado_em")


#: O que o painel mostra: imagem (na `<img>`) e PDF (abre pelo link). O resto da lista de tipos fica guardado e não sai por aqui.
COM_CONTEUDO = frozenset({"image/jpeg", "image/png", "image/webp", "application/pdf"})


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


def _instante(valor: str | None, nome: str) -> str | None:
    """O limite do período como o banco o guarda (`criado_em` é ISO em UTC); data sozinha vale como 00:00Z."""
    if not valor:
        return None
    try:
        dt = parse_iso(valor)
    except ValueError:
        dt = None
    if dt is None:
        raise HTTPException(422, detail={"code": "periodo_invalido", "message": f"'{nome}' não é uma data ISO válida."})
    return to_iso(dt)


def _item(linha: dict[str, object]) -> dict[str, object]:
    """Uma linha da lista: os campos do painel e dois fatos já resolvidos, para a tela não repetir a regra."""
    guardado = linha.get("estado") == "guardado"
    do_dono = bool(linha.get("do_dono"))
    return {
        "id": linha["id"], "canal": linha["canal"], "direcao": linha["direcao"], "mime": linha["mime"],
        "bytes": linha["bytes"], "estado": linha["estado"], "motivo_recusa": linha["motivo_recusa"],
        "criado_em": linha["criado_em"], "apagado_em": linha["apagado_em"], "do_dono": do_dono,
        # O conteúdo sai para imagem/PDF guardados, nunca o do convidado (a entrada de quem não é o dono).
        "tem_conteudo": guardado and linha.get("mime") in COM_CONTEUDO and (do_dono or linha["direcao"] == "saida"),
        # A mesma regra de `conferir_origem`: só a mensagem do dono, guardada.
        "pode_ir_ao_cartao": guardado and bool(linha.get("de_mensagem_do_dono")),
    }


@router.get("")
async def lista_de_anexos(
    request: Request,
    canal: str | None = Query(None, max_length=32),
    direcao: Literal["entrada", "saida"] | None = None,
    do_dono: bool | None = None,
    estado: Literal["guardado", "recusado", "apagado"] | None = None,
    desde: str | None = Query(None, max_length=40),
    ate: str | None = Query(None, max_length=40),
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    """A página de anexos, do mais novo ao mais velho (a tela Anexos). Só metadados: sem caminho, sem `sha256`, sem
    referência do canal e sem o nome do remetente. `desde`/`ate` (ISO, `ate` exclusivo) delimitam o período."""
    linhas, total = _armazem(request).listar(
        canal=canal, direcao=direcao, do_dono=do_dono, estado=estado, desde=_instante(desde, "desde"),
        ate=_instante(ate, "ate"), limite=limit, deslocamento=offset)
    return {"items": [_item(x) for x in linhas], "total": total, "limit": limit, "offset": offset}


@router.get("/{ident}")
async def metadados_do_anexo(ident: int, request: Request) -> dict[str, object]:
    linha = _linha(request, ident)
    return {c: linha.get(c) for c in _CAMPOS}


@router.get("/{ident}/conteudo")
async def conteudo_do_anexo(ident: int, request: Request) -> FileResponse:
    linha = _linha(request, ident)
    estado = linha.get("estado")
    if _armazem(request).origem_e_de_convidado(linha):
        # Defensivo: o convidado nunca tem anexo baixado (28.24), então não deveria haver linha. Se houver, não sai.
        raise HTTPException(404, detail={"code": "anexo_de_convidado",
                                         "message": "Anexo de convidado não é baixado: não há conteúdo para mostrar."})
    if estado == "apagado":
        raise HTTPException(410, detail={"code": "anexo_apagado",
                                         "message": "O arquivo venceu a retenção e foi apagado."})
    aberto = _armazem(request).abrir(ident) if estado == "guardado" else None
    if aberto is None:
        raise HTTPException(404, detail={"code": "anexo_sem_arquivo",
                                         "message": "Este anexo não tem arquivo guardado (foi recusado ou sumiu do disco)."})
    meta, caminho = aberto
    mime = str(meta["mime"])
    if mime not in COM_CONTEUDO:
        raise HTTPException(415, detail={"code": "tipo_sem_previa",
                                         "message": "Só imagem e PDF saem pelo painel; este arquivo fica guardado na Central."})
    nome = f"anexo-{ident}.{EXTENSAO.get(mime, 'bin')}"
    return FileResponse(caminho, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="{nome}"', "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store"})


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
