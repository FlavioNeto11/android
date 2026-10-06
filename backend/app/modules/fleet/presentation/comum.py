"""O que as rotas de rede por aparelho e as que ficaram em `app/api.py` dividem (15.15 F4f): quem está pedindo. Mora aqui, e
não em `api.py`, para o módulo de rotas não importar `app.api` (ciclo): `api.py` importa `quem` daqui, como importa o resto
dos módulos, e continua exportando o nome."""
from __future__ import annotations

from fastapi import Request

from app.security.sessions import operador_atual
from app.shared.costuras import PAINEL


def quem(request: Request | None = None, informado: str | None = None) -> str:
    """Quem está pedindo, na ordem em que uma trilha de auditoria precisa que seja.

    **A sessão vence o que o cliente diz.** `requested_by` sempre foi um campo do CORPO: qualquer chamador
    escrevia ali o nome que quisesse, e era o único "quem" que o banco guardava. Com sessão, o nome vem do
    cookie — que o JavaScript da página não lê e o navegador não deixa forjar — e o campo do corpo vira o que
    sempre deveria ter sido: um rótulo de quem chama a API sem sessão (script, ferramenta, worker).

    `panel` continua existindo como último recurso, e agora quer dizer o que parecia querer: "veio do painel, e
    ninguém se identificou".
    """
    da_sessao = getattr(request.state, "operador", None) if request is not None else None
    return da_sessao or operador_atual() or (informado or "").strip() or PAINEL
