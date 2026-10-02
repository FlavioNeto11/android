"""O barramento e o catálogo, vistos pela porta do aprendizado (30.21). Só adaptação: a decisão de avisar e o conteúdo
do aviso moram em `application/espera.py` e `domain/espera.py`."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.integrations.app_declarado.conhecimento import PASTA_DOS_APPS
from app.modules.applications.infrastructure import registry
from app.modules.learning.domain.espera import AvisoDeEspera, FatosDoCatalogo
from app.planning.capabilities import UnknownCapability

TIPO_DO_EVENTO = "learning.needs_person"


class Barramento(Protocol):
    """`events.EventBus.emit`, só com os argumentos que o aprendizado usa."""

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object: ...


class EventosNoBarramento:
    """`PortaDeEventos` sobre o `EventBus`: evento persistido (não está em `EPHEMERAL_KINDS`), sem aparelho. O
    payload é só o que `AvisoDeEspera.como_dados` monta (lista fechada). A falha SOBE: cada chamador decide."""

    def __init__(self, bus: Barramento) -> None:
        self._bus = bus

    def esperando_a_pessoa(self, aviso: AvisoDeEspera) -> None:
        self._bus.emit(TIPO_DO_EVENTO, aviso.mensagem(), level=aviso.nivel, data=aviso.como_dados())


class RiscoDoRegistro:
    """`CatalogoDeRisco` sobre o registro de apps: os FATOS de risco da capability (nunca o texto da ação)."""

    def __init__(self, pasta: Path = PASTA_DOS_APPS) -> None:
        self._pasta = pasta

    def tem_catalogo(self, app: str) -> bool:
        return bool(app) and (self._pasta / app / "catalogo.yaml").is_file()

    def da_capability(self, app: str, capability: str) -> FatosDoCatalogo | None:
        catalogo = registry.get(app)
        if catalogo is None:
            return None
        try:
            c = catalogo.get(capability)
        except UnknownCapability:
            return None
        return FatosDoCatalogo(risco=c.risk, politica=c.default_policy, precisa_rascunho=bool(c.needs_draft),
                               efeito_externo=bool(c.side_effect))


__all__ = ["TIPO_DO_EVENTO", "Barramento", "EventosNoBarramento", "RiscoDoRegistro"]
