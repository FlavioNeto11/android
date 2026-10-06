"""O que as rotas de execução e as que ficaram em `app/api.py` dividem (15.15 F4, corte 3): quem fez o gesto e a recusa de
uma execução como HTTP. Mora aqui, e não em `api.py`, para o módulo de rotas não importar `app.api` (ciclo): `api.py` importa
estes dois nomes daqui, como importa o resto dos módulos."""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.shared.costuras import autor_do_gesto
from app.taskqueue.service import RunError


def autor_do_sinal(request: Request) -> str:
    """Quem fez um gesto que vira sinal do aprendizado (ADR-054): o operador da SESSÃO, ou `panel` — a regra das
    rotas do livro. Diferente de `quem`, o `requested_by` do corpo não entra: qualquer chamador o escreve, e um
    `sistema` ali tiraria o gesto da conta das pessoas na régua diária. Por isso pode diferir do `resolved_by` que o
    comando grava (aquele aceita o corpo sem sessão); o do comando vai em `data` do sinal, para cruzar os dois."""
    return autor_do_gesto(getattr(request.state, "operador", None))


def run_error(exc: RunError) -> HTTPException:
    # `details` carrega o que o painel precisa para OFERECER a saída — no pré-voo, a lista por aparelho e quais
    # seguem aptos. Sem isso a recusa seria só uma frase, e "seguir só com os aptos" não teria como existir.
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message, **exc.details})
