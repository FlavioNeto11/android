"""O que as rotas de releases e as que ficaram em `app/api.py` dividem (15.15 F4d): achar o aparelho pelo id e recusar a loja
como destino de um aplicativo. Mora aqui, e não em `api.py`, para o módulo de rotas não importar `app.api` (ciclo): `api.py`
importa estes dois nomes daqui, como importa o resto dos módulos."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from app.devices.manager import DeviceRuntime

if TYPE_CHECKING:
    from app.state import AppState


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def device(state: AppState, instance_id: str) -> DeviceRuntime:
    try:
        return state.devices.get(instance_id)
    except KeyError:
        raise _err(404, "not_found", f"Instância {instance_id} não existe.") from None


def recusa_loja_como_alvo(rt: DeviceRuntime) -> None:
    """A loja é a FONTE do aplicativo, nunca o destino: nela o app vem da Play Store, não do nosso catálogo."""
    if rt.store:
        raise _err(409, "store_instance", f"{rt.id} é a loja (Play Store): nela o aplicativo vem da própria loja. "
                                          "Instale, prove e reverta releases nos aparelhos do parque.")
