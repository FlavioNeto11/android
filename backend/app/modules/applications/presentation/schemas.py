"""Corpos de requisição HTTP de aplicativos: registro de app, release, assinatura, instalação, verificação,
loja e ciclo de vida da release.

Movidos literalmente de `app/models.py` (design §16, fase K: "primeiro os corpos"); `app.models` reexporta os
MESMOS objetos, e o nome de cada classe continua sendo o nome do esquema no contrato HTTP. Este módulo não
importa `app.models`: o reexport importa daqui, e o caminho de volta fecharia um ciclo de import de topo.

`APP_CATEGORIES`/`AppCategory` vieram junto porque só estes corpos os usam.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReleaseImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_reference: str | None = Field(default=None, max_length=300)   # de onde veio, informado por quem importou
    expected_package: str | None = Field(default=None, max_length=120)


class SignatureApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=300)


class AppInstallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_id: str = Field(min_length=3, max_length=200)
    #: Chave de idempotência do COMANDO: reenviar a mesma requisição devolve o comando original em vez de
    #: abrir um efeito novo. Opcional — sem ela, cada chamada é um pedido novo.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class AppVerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: str = Field(min_length=3, max_length=120)
    #: Chave de idempotência do COMANDO: reenviar a mesma requisição devolve o comando original em vez de
    #: abrir um efeito novo. Opcional — sem ela, cada chamada é um pedido novo.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class StoreBody(BaseModel):
    """Ações sobre o aparelho-loja. O pacote é obrigatório: a loja não assume um aplicativo por omissão."""

    model_config = ConfigDict(extra="forbid")
    package: str | None = Field(default=None, min_length=3, max_length=120)
    #: Chave de idempotência do COMANDO: reenviar a mesma requisição devolve o comando original em vez de
    #: abrir um efeito novo. Opcional — sem ela, cada chamada é um pedido novo.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class ReleaseLifecycleBody(BaseModel):
    """Um verbo por chamada, no mesmo formato das aprovações — quatro rotas diriam a mesma coisa em quatro lugares."""

    model_config = ConfigDict(extra="forbid")
    verb: Literal["canary", "promote", "quarantine", "rollback", "distribute"]
    instance_id: str | None = Field(default=None, max_length=120)   # obrigatório em canary e rollback
    note: str | None = Field(default=None, max_length=300)
    # Rollback preservando dados pode ser recusado pelo Android. Reinstalar resolve, mas APAGA a sessão — então
    # quem chama tem de dizer isso de propósito. A API nunca escolhe esse caminho sozinha.
    confirm_reinstall: bool = False
    # Só para `distribute`: "instalar em todos agora". O rodízio liga os aparelhos pendentes dentro das vagas, em vez de
    # esperar que cada um pegue uma tarefa.
    eager: bool = False
    #: Chave de idempotência do COMANDO: reenviar a mesma requisição devolve o comando original em vez de
    #: abrir um efeito novo. Opcional — sem ela, cada chamada é um pedido novo.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)
    #: Só para `distribute` (loja de apps, 26/09): PARA QUEM. Sem nenhum dos dois, o parque inteiro, como sempre foi.
    #: `instance_ids` = os aparelhos escolhidos; `count` = N aparelhos escolhidos pelo backend entre os que podem
    #: receber e ainda não estão na versão. Os dois juntos são recusados: "estes 5" e "quaisquer 3" não se somam.
    instance_ids: list[str] | None = Field(default=None, max_length=200)
    count: int | None = Field(default=None, ge=1, le=200)
    #: Prévia: devolve, aparelho por aparelho, o que ACONTECERIA — sem gravar versão desejada nem instalar nada.
    dry_run: bool = False


#: Categorias da vitrine, lista fixa decidida pelo dono em 26/09. A ordem é a dos filtros no painel.
APP_CATEGORIES = ("social", "mensagens", "email", "rede", "utilitario", "qa")

AppCategory = Literal["social", "mensagens", "email", "rede", "utilitario", "qa"]

_PKG = r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$"


class AppInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    package: str = Field(pattern=_PKG, max_length=200)
    activity: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.$]*$")
    apk_path: str | None = Field(default=None, max_length=400)
    nav_hints: str | None = Field(default=None, max_length=4000)
    known_selectors: dict[str, str] | None = None
    category: AppCategory | None = None


class AppPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    package: str | None = Field(default=None, pattern=_PKG, max_length=200)
    activity: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.$]*$")
    apk_path: str | None = Field(default=None, max_length=400)
    nav_hints: str | None = Field(default=None, max_length=4000)
    known_selectors: dict[str, str] | None = None
    category: AppCategory | None = None
