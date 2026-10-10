"""O que as rotas de personas e as que ficaram em `app/api.py` (perfis do Instagram, evidências) dividem (15.15 F4h): o erro de
social como HTTP, o aparelho pelo id, o tipo de mídia de uma chave de imagem e servir um artefato pela interface de storage. Moram aqui, e não em
`api.py`, para o módulo de rotas não importar `app.api` (ciclo): `api.py` os importa daqui e continua exportando os nomes."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from app.devices.manager import DeviceRuntime
from app.security.sessions import operador_atual
from app.shared.costuras import PAINEL
from app.social.service import SocialError
from app.storage import Storage, StorageError

if TYPE_CHECKING:
    from app.state import AppState

log = logging.getLogger("poc.api")


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def device(state: AppState, instance_id: str) -> DeviceRuntime:
    """O aparelho pelo id, ou 404. O mesmo achado de `applications.presentation.comum.device`, repetido aqui porque `identity` não
    pode importar `applications` (os dois contextos fechariam um ciclo; `test_contextos_novos_formam_um_dag`)."""
    try:
        return state.devices.get(instance_id)
    except KeyError:
        raise _err(404, "not_found", f"Instância {instance_id} não existe.") from None


def quem(request: Request | None = None, informado: str | None = None) -> str:
    """Quem está pedindo: a sessão vence o que o cliente diz (a regra de `fleet.presentation.comum.quem`, repetida aqui porque `identity`
    importar `fleet` fecharia um ciclo de contextos). `panel` é o último recurso: "veio do painel, e ninguém se identificou"."""
    da_sessao = getattr(request.state, "operador", None) if request is not None else None
    return da_sessao or operador_atual() or (informado or "").strip() or PAINEL


def social_error(exc: SocialError) -> HTTPException:
    if exc.details:
        return HTTPException(status_code=exc.status,
                             detail={"code": exc.code, "message": exc.message, "details": exc.details})
    return _err(exc.status, exc.code, exc.message)


def mime_da_chave(chave: str) -> str:
    return "image/png" if chave.lower().endswith(".png") else "image/jpeg"


def servir_do_storage(armazem: Storage, chave: str, media_type: str,
                      *, ausente: tuple[str, str]) -> FileResponse | RedirectResponse | StreamingResponse:
    """Serve um artefato PELA INTERFACE de storage, e não pelo disco deste processo (item 5.7).

    Três caminhos, nesta ordem, porque cada um é o barato do seu back-end:

    1. arquivo local → `FileResponse`, como sempre foi (envio por partes, `Range`, tudo de graça);
    2. URL pré-assinada → redireciona, e os bytes nem passam pelo backend;
    3. streaming pela interface — o que sobra quando o cliente do bucket não assina URL.
    """
    try:
        if (local := armazem.local_path(chave)) is not None:
            return FileResponse(local, media_type=media_type)
        if (link := armazem.url(chave)) is not None:
            return RedirectResponse(link, status_code=307)
        if (corpo := armazem.stream(chave)) is not None:
            return StreamingResponse(corpo, media_type=media_type)
    except StorageError as exc:
        log.warning("chave de storage recusada (%s): %s", chave, exc)
    raise _err(404, ausente[0], ausente[1])
