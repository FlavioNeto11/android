"""Shim: o registro de aplicativos mora em `app/modules/applications/infrastructure/registry.py` (fase K1).

Continua aqui porque o legado inteiro pergunta por `planning.catalog` (`capabilities_of`, `register`, `get`...), e
o que se move deixa o nome antigo funcionando (design §3). Os nomes são os MESMOS objetos: registrar por aqui ou
por lá é registrar no mesmo lugar. `AppCapabilities` é o mesmo tipo que `AppDefinition`.

O catálogo de ações de um app não mora mais aqui em Python: é dado, em `app/conhecimento/apps/<pacote>/catalogo.yaml`,
lido por `planning/capabilities.py::catalogo_do_pacote` e entregue ao registro pelo manifesto do app (ADR-052, fatia
2). Este pacote só guarda o shim; `tests/test_catalogo_como_dado.py` reprova um `.py` de app que volte para cá.
"""
from __future__ import annotations

from ...modules.applications.infrastructure import registry as _registro
from ...modules.applications.infrastructure.registry import (AppCapabilities, AppDefinition, AppManifest,
                                                             capabilities_of, definition_of, get,
                                                             package_of_provider, register, register_manifest,
                                                             registered, screen_reader_of, session_factory_of,
                                                             session_provider_of, unregister)

#: A tabela de embutidos, pelo mesmo objeto: `tests/test_registro_de_apps.py` confere que os caminhos são relativos.
_BUILTINS = _registro._BUILTINS

__all__ = ["AppCapabilities", "AppDefinition", "AppManifest", "register", "register_manifest", "unregister", "get",
           "capabilities_of", "definition_of", "screen_reader_of", "session_factory_of", "registered",
           "session_provider_of", "package_of_provider"]
