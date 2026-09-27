"""Shim: o registro de aplicativos mora em `app/modules/applications/infrastructure/registry.py` (fase K1).

Continua aqui porque o legado inteiro pergunta por `planning.catalog` (`capabilities_of`, `register`, `get`...), e
o que se move deixa o nome antigo funcionando (design §3). Os nomes são os MESMOS objetos: registrar por aqui ou
por lá é registrar no mesmo lugar.

`catalog/instagram.py` (o catálogo do Instagram) continua neste pacote: é dado do app, não do registro.
"""
from __future__ import annotations

from ...modules.applications.infrastructure import registry as _registro
from ...modules.applications.infrastructure.registry import (AppCapabilities, capabilities_of, get,
                                                             package_of_provider, register, registered,
                                                             session_provider_of, unregister)

#: A tabela de embutidos, pelo mesmo objeto: `tests/test_registro_de_apps.py` confere que os caminhos são relativos.
_BUILTINS = _registro._BUILTINS

__all__ = ["AppCapabilities", "register", "unregister", "get", "capabilities_of", "registered",
           "session_provider_of", "package_of_provider"]
