"""Registro de aplicativos: o ponto de extensão que faltava para um segundo app existir sem tocar no núcleo.

Antes disto o núcleo de planejamento decidia por `if package == "com.instagram.android"` (capabilities.py), a
porta de sessão do despacho era do Instagram sem perguntar de que app era a tarefa, e instalar QUALQUER pacote
invalidava a sessão do Instagram. Acrescentar WhatsApp ou TikTok exigia editar o núcleo.

Aqui um app declara o que tem: catálogo de capabilities, se alguém provê sessão de conta naquele pacote, e se ele
trabalha com perfil. Quem não se registra continua no caminho livre de sempre — é o que mantém o QA Messenger
intacto.

Os embutidos entram por tabela preguiçosa (`_BUILTINS`) e não por importação no topo: `catalog/instagram.py`
importa `Capability` de `..capabilities`, que importa este módulo. Importar o catálogo aqui em cima fecharia o
ciclo. `register()` é o ponto de extensão de verdade; o embutido só evita que o registro dependa de alguém ter
importado `integrations.instagram` antes.

Morava em `planning/catalog/__init__.py`, que ficou como shim (fase K1, design §16: "comparações com 'instagram' →
modules/identity + registro de SessionProvider"). O conteúdo veio literal; só os caminhos relativos mudaram.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from app.planning.capabilities import CapabilityCatalog


@dataclass(frozen=True, slots=True)
class AppCapabilities:
    """O que este aplicativo declara sobre si — lido pelo núcleo no lugar do `if` por pacote.

    `session_provider` é o nome do provedor de sessão de conta (hoje só `"instagram"`). `None` quer dizer que o
    app não tem conta gerenciada: a porta de sessão do despacho nem abre, e instalar o app não invalida sessão
    nenhuma. `needs_profile` diz se uma tarefa naquele app exige perfil vinculado ao aparelho.
    """

    package: str
    name: str
    has_catalog: bool = False
    session_provider: str | None = None
    needs_profile: bool = False
    #: O app só funciona com internet DENTRO do aparelho (login, feed, envio). O despacho não entrega tarefa dele a
    #: um aparelho `online` cuja `connectivity` não esteja `healthy`. Falso por padrão: app sem registro (ou local,
    #: como Configurações) segue sem essa porta — "online ≠ internet", mas nem toda tarefa precisa de internet.
    requires_internet: bool = False
    #: Rótulo do app para as mensagens do despacho, no lugar do texto fixo "Instagram".
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", self.name or self.package)


#: Aplicativo sem registro: sem catálogo, sem sessão, sem perfil. É o caminho livre de sempre.
def _neutro(package: str | None) -> AppCapabilities:
    return AppCapabilities(package=package or "", name=package or "aplicativo")


#: pacote -> "módulo (RELATIVO a este pacote):atributo" do catálogo embutido, importado na primeira consulta.
#: Relativo de propósito: o backend já rodou como `backend.app` (partida à mão da raiz do repositório) e o nome
#: absoluto `app.planning…` derrubou o planejamento com "No module named 'app'" (execução 8a9ffc).
#: Relativo a `__package__` (`app.modules.applications.infrastructure`): quatro pontos sobem até `app`.
_BUILTINS: dict[str, tuple[str, str, AppCapabilities]] = {
    "com.instagram.android": (
        "....planning.catalog.instagram", "INSTAGRAM_CATALOG",
        AppCapabilities(package="com.instagram.android", name="Instagram", has_catalog=True,
                        session_provider="instagram", needs_profile=True, requires_internet=True,
                        label="Instagram"),
    ),
}

_CATALOGS: dict[str, "CapabilityCatalog"] = {}
_CAPS: dict[str, AppCapabilities] = {}


def register(package: str, catalog: "CapabilityCatalog | None" = None,
             capabilities: AppCapabilities | None = None) -> AppCapabilities:
    """Registra um aplicativo. Chamar de novo substitui — é o que torna o registro testável sem estado preso.

    Devolve o que ficou valendo. `has_catalog` é derivado do catálogo recebido, nunca declarado à mão: o registro
    não pode dizer que tem catálogo e devolver `None` em `get()`.
    """
    if not package:
        raise ValueError("pacote é obrigatório para registrar um aplicativo")
    caps = capabilities or _CAPS.get(package) or _neutro(package)
    caps = replace(caps, package=package, has_catalog=catalog is not None)
    if catalog is not None:
        _CATALOGS[package] = catalog
    else:
        _CATALOGS.pop(package, None)
    _CAPS[package] = caps
    return caps


def unregister(package: str) -> None:
    """Tira o app do registro. Existe para o teste que prova que nada do núcleo depende de um app específico."""
    _CATALOGS.pop(package, None)
    _CAPS.pop(package, None)


def _carregar_embutido(package: str) -> None:
    alvo = _BUILTINS.get(package)
    if alvo is None:
        return
    modulo, atributo, caps = alvo
    catalogo = getattr(import_module(modulo, package=__package__), atributo)
    register(package, catalogo, caps)


def get(package: str | None) -> "CapabilityCatalog | None":
    """Catálogo do app, quando existe. Sem registro, `None` — e o planejamento livre continua valendo."""
    if not package:
        return None
    if package not in _CAPS and package in _BUILTINS:
        _carregar_embutido(package)
    return _CATALOGS.get(package)


def capabilities_of(package: str | None) -> AppCapabilities:
    """O que este app declara. Pacote desconhecido devolve o perfil neutro — nunca `None`, nunca exceção.

    Devolver neutro em vez de levantar é deliberado: o núcleo pergunta isto em caminho quente (porta de sessão,
    invalidação, despacho), e um app não registrado tem de seguir como sempre seguiu, não parar o parque.
    """
    if not package:
        return _neutro(package)
    if package not in _CAPS and package in _BUILTINS:
        _carregar_embutido(package)
    return _CAPS.get(package) or _neutro(package)


def registered() -> list[AppCapabilities]:
    """Todos os apps registrados, embutidos inclusive. É o que a interface usa para oferecer a escolha do app."""
    for pacote in _BUILTINS:
        if pacote not in _CAPS:
            _carregar_embutido(pacote)
    return sorted(_CAPS.values(), key=lambda c: c.label.lower())


def session_provider_of(package: str | None) -> str | None:
    """Atalho do caminho quente: quem provê sessão de conta neste pacote, se alguém provê."""
    return capabilities_of(package).session_provider


def package_of_provider(provider: str) -> str | None:
    """O pacote do app que provê ESTE tipo de conta. É a pergunta inversa, e existe por um motivo prático.

    Um perfil (credencial, sessão, persona) é o conceito de um app com conta gerenciada. Onde o código precisava
    de "o pacote deste perfil", havia um literal `"com.instagram.android"` embutido em assinatura de função. Aqui
    a resposta vem do registro: quando um segundo app com conta se registrar, o literal não precisa existir.
    """
    for caps in registered():
        if caps.session_provider == provider:
            return caps.package
    return None


def _reset_para_teste() -> None:
    """Devolve o registro ao estado de fábrica. Só os testes chamam."""
    _CATALOGS.clear()
    _CAPS.clear()


__all__ = ["AppCapabilities", "register", "unregister", "get", "capabilities_of", "registered",
           "session_provider_of", "package_of_provider"]
