"""A leitura dos pedidos de validação automática (item 30.38 b; adendo v1.02). Só leitura, sem IA, sem custo.

- `GET /api/aprendizado/validacoes?estado=&item=&run=&limite=50&antes=`: os pedidos de `learning_validations`, dos mais novos para
  os mais antigos (`antes`: o `created_at` do último da página anterior), a contagem por estado de todos os pedidos, o
  total e o `modo` do despachante agora (`off` = pausado: o painel explica a pausa). Cada item leva o motivo em código e
  `motivo_humano` (a tabela do domínio), o nome do app (`app_nome`) e o comando de origem cortado em 200.

O `comando` é para o PAINEL. Esta rota não é fonte para espelho externo: Trello e Telegram nunca recebem texto de comando
(a leitura de conhecimento do 32.2 é outra rota, da frente Canais).

Entra ANTES do livro (`router.py`): a rota genérica `{kind}/{ref}` casaria com `validacoes`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.validacao import LISTA_MAX, PedidoListado, ServicoDeValidacao
from app.modules.learning.domain.validacao import EstadoDoPedido, motivo_humano
from app.modules.learning.presentation.livro import _servico
from app.modules.learning.presentation.nomes import nomear_apps
from app.modules.skills.domain.document import JsonObject, JsonValue

router = APIRouter(prefix="/api/aprendizado")


def _servicos(request: Request) -> tuple[LearningService, ServicoDeValidacao]:
    servico = _servico(request)
    validacao = servico.extensao(ServicoDeValidacao)
    if validacao is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "A validação automática não foi composta."})
    return servico, validacao


def pedido_json(p: PedidoListado) -> JsonObject:
    return {"id": p.id, "estado": p.estado, "motivo": p.motivo, "motivo_humano": motivo_humano(p.motivo),
            "item_ref": p.item_ref, "item_kind": p.item_kind, "app": p.scope_app or None, "grupo": p.grupo,
            "run_id": p.run_id, "run_origem": p.run_origem, "aparelho": p.aparelho, "usd": round(p.usd, 4),
            "teto_usd": p.teto_usd, "created_at": p.created_at, "feito_em": p.feito_em, "expira_em": p.expira_em or None,
            "revisao_nova_id": p.revisao_nova_id, "comando": p.comando,
            "invalida_depois": None if p.invalida_depois is None else {
                "motivo": p.invalida_depois, "motivo_humano": motivo_humano(p.invalida_depois)}}


@router.get("/validacoes", response_model=None)
async def validacoes(request: Request, estado: EstadoDoPedido | None = None,
                     limite: int = Query(default=50, ge=1, le=LISTA_MAX),
                     antes: str | None = Query(default=None, max_length=40),
                     item: str | None = Query(default=None, max_length=200),
                     run: str | None = Query(default=None, max_length=64)) -> JsonObject:
    """30.43: `item` (`<kind>:<ref>`, o `item_ref`) e `run` filtram a lista; a `contagem` e o `total` seguem sendo de
    TODOS os pedidos (o painel da fila os usa como estão). Um filtro na rota de sempre, e não uma rota por item: o
    item é só mais uma coluna da mesma lista."""
    servico, validacao = _servicos(request)
    itens = [pedido_json(p) for p in validacao.listar(estado, limite, antes, item=item, run=run)]
    nomear_apps(itens, servico)
    contagem = validacao.contagens()
    return {"itens": list[JsonValue](itens), "contagem": dict[str, JsonValue](contagem),
            "total": sum(contagem.values()), "modo": validacao.modo().value}
