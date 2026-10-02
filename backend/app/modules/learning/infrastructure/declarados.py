"""As fontes da visão por app (30.1): o DECLARADO lido do registro de apps e da pasta do repositório, e a LOJA lida da
tabela `apps`. Só leitura, a cada pedido: nada é copiado para o banco (sem segunda verdade). Os arquivos da pasta
são conferidos por existência; as ações vêm do catálogo que o registro já carregou e as telas, do `telas.yaml`
(carregado uma vez por processo pelo motor)."""
from __future__ import annotations

from pathlib import Path

from app.automation import conhecimento_de_telas as telas_
from app.db import Database
from app.integrations.app_declarado.conhecimento import PASTA_DOS_APPS
from app.modules.applications.infrastructure import registry
from app.modules.learning.application.apps import AppDaLoja, Declarado
from app.modules.learning.infrastructure import linhas


class DeclaradosDoRegistro:
    def __init__(self, pasta: Path = PASTA_DOS_APPS) -> None:
        self._pasta = pasta

    def declarados(self) -> list[Declarado]:
        return [self._de(d.package, d.name, d.session_provider is not None) for d in registry.registered()]

    def _de(self, pacote: str, nome: str, login: bool) -> Declarado:
        pasta = self._pasta / pacote
        catalogo = registry.get(pacote)
        return Declarado(package=pacote, name=nome, tem_app=(pasta / "app.yaml").is_file(),
                         tem_catalogo=(pasta / "catalogo.yaml").is_file(), tem_telas=(pasta / "telas.yaml").is_file(),
                         tem_sessao=(pasta / "sessao.yaml").is_file(),
                         acoes=len(catalogo.capabilities) if catalogo is not None else None,
                         telas=_telas(pasta), login_gerenciado=login)


def _telas(pasta: Path) -> int | None:
    try:
        k = telas_.da_pasta(pasta)
    except telas_.ConhecimentoInvalido:
        return None                         # arquivo inválido: o app aparece, mas sem contagem inventada
    return len(k.telas) if k is not None else None


class LojaSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def apps(self) -> list[AppDaLoja]:
        return [AppDaLoja(package=linhas.texto(r, "package"), name=linhas.texto_ou_nulo(r, "name") or "",
                          nav_hints=bool(linhas.texto_ou_nulo(r, "nav_hints")),
                          known_selectors=bool(linhas.texto_ou_nulo(r, "known_selectors")))
                for r in self._db.query("SELECT package, name, nav_hints, known_selectors FROM apps ORDER BY id")]
