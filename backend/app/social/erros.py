"""Erro do serviço de contas e normalização do `host`: o que `service.py` e `provisionamento.py` dividem.

Moravam em `service.py`; o módulo da conta planejada (31.281) precisa dos dois e não pode importar o serviço (fecharia um
ciclo de execução). `service.py` reexporta os nomes, então quem importava `SocialError` dali continua importando.
"""
from __future__ import annotations


class SocialError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409, details: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        #: Dados estruturados da recusa (ex.: `estado_atual` do 409 `estado_inesperado`); nunca segredo.
        self.details = details


def normalizar_host(valor: str | None) -> str | None:
    """O `host` de uma conta de portal, normalizado como a barra de endereço o mostra: minúsculo, sem esquema, sem
    caminho, sem porta. Vazio vira `None` (conta do app inteiro), que a unicidade trata como ''."""
    if not valor:
        return None
    t = valor.strip().casefold()
    t = t.split("://", 1)[1] if "://" in t else t
    t = t.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
    return t or None
