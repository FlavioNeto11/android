"""A rota da tela Canais (item 32.5): `GET /api/canais/estado`, só leitura.

Passa pelo mesmo portão de toda rota `/api/` do painel (`main.guarda`: sessão ou credencial; sem elas, 401). Ao contrário do
webhook do Trello, vizinho de caminho, esta rota NÃO tem exceção de login.

A regra do que pode sair mora em `EstadoDosCanais` (`infrastructure/estado_sql.py`): números, horas e códigos de lista
fechada, nunca o conteúdo de aviso, mensagem ou cartão. Aqui só se liga a consulta às fontes de saúde do `AppState`.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from fastapi import APIRouter, HTTPException, Request

from app.models import Problem
from app.modules.avisos.infrastructure.estado_sql import EstadoDosCanais
from app.modules.avisos.infrastructure.trello_saude import problemas_do_trello

router = APIRouter(prefix="/api/canais")


def _fontes(poc: object, *nomes: str) -> list[Callable[[], Iterable[Problem]]]:
    """As `problemas()` dos serviços que existirem no estado. O que faltar (estado parcial, como nos testes) é calado: a
    tela mostra o que sabe, e a saúde (`/api/health`) segue sendo a fonte dos problemas."""
    fontes: list[Callable[[], Iterable[Problem]]] = []
    for nome in nomes:
        problemas = getattr(getattr(poc, nome, None), "problemas", None)
        if callable(problemas):
            fontes.append(problemas)
    return fontes


@router.get("/estado")
async def estado_dos_canais(request: Request) -> dict[str, object]:
    poc: object = getattr(request.app.state, "poc", None)
    db, cfg = getattr(poc, "db", None), getattr(poc, "cfg", None)
    if db is None or cfg is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "O estado dos canais ainda não foi composto."})
    return EstadoDosCanais(
        db, cfg,
        problemas_do_aviso=_fontes(poc, "avisos"),
        problemas_da_conversa=_fontes(poc, "telegram_entrada"),
        # A recusa do Trello é uma só para o espelho e o leitor (o mesmo token): `_codigos` já não repete o código.
        problemas_do_trello=[*_fontes(poc, "trello_espelho", "trello_leitor", "trello_webhook", "trello_cadastro"),
                             lambda: problemas_do_trello(cfg)],
    ).ler()
