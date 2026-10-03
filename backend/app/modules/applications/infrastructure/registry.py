"""Registro de aplicativos: o ponto de extensão que faltava para um segundo app existir sem tocar no núcleo.

Antes disto o núcleo de planejamento decidia por `if package == "com.instagram.android"` (capabilities.py), a
porta de sessão do despacho era do Instagram sem perguntar de que app era a tarefa, e instalar QUALQUER pacote
invalidava a sessão do Instagram. Acrescentar WhatsApp ou TikTok exigia editar o núcleo.

Aqui um app se registra com o seu MANIFESTO (`AppManifest`): a definição declarativa (`AppDefinition`, no domínio),
o catálogo de capabilities, as leituras de tela que a execução usa para escrever na voz do perfil e a fábrica do
provedor de sessão da conta. Quem não se registra continua no caminho livre de sempre — é o que mantém o QA
Messenger intacto. O núcleo pergunta aqui (e a `SessionProviders`, que fabrica o provedor por composição) e não
conhece nome de app nenhum.

Desde o ADR-052 um app é DADO: uma pasta em `app/conhecimento/apps/<pacote>/`, que o descobridor embutido
(`integrations/app_declarado/pacote.descobrir`) transforma em manifesto com os motores genéricos. Adicionar um app =
criar a pasta; não há Python por app. `register_manifest()` continua valendo para teste e extensão.

O descobridor entra por tabela preguiçosa (`_BUILTINS`) e não por importação no topo: ele importa o carregador do
catálogo (`planning/capabilities.py`, que pergunta a este registro) e o motor de sessão. Importá-los aqui em cima
fecharia o ciclo.

Morava em `planning/catalog/__init__.py`, que ficou como shim (fase K1, design §16: "comparações com 'instagram' →
modules/identity + registro de SessionProvider").
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, TypeVar

from app.modules.applications.domain.definition import AppDefinition, neutral

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from app.automation.hierarchy import UiTree
    from app.modules.identity.infrastructure.sessions import SessionProviderFactory
    from app.planning.capabilities import CapabilityCatalog

#: Nome antigo (`planning.catalog.AppCapabilities`): é o MESMO tipo.
AppCapabilities = AppDefinition

_T = TypeVar("_T")


class ScreenReader(Protocol):
    """Leituras de tela que a execução pede ao app para escrever na voz do perfil (`AppState._draft_gate`).

    O que está visível (a legenda que vai ser comentada), a fala de uma pessoa num comentário e numa conversa. Cada
    app sabe onde isso aparece na árvore dele; o núcleo só pede. Sem leitor, o rascunho sai sem contexto de tela.
    """

    def visible_content(self, tree: UiTree) -> str: ...

    def comment_of(self, tree: UiTree, author: str) -> str: ...

    def message_of(self, tree: UiTree, author: str) -> str: ...


@dataclass(frozen=True, slots=True)
class AppManifest:
    """O manifesto de um app: a definição declarativa e as peças com comportamento que ele traz para o núcleo."""

    definition: AppDefinition
    catalog: CapabilityCatalog | None = None
    screen: ScreenReader | None = None
    #: Fábrica do provedor de sessão da conta. Exige `definition.session_provider` (o tipo): um sem o outro deixaria
    #: a porta de sessão e a invalidação respondendo coisas diferentes para o mesmo app.
    session: SessionProviderFactory | None = None


#: Descobridores embutidos: "módulo (RELATIVO a este pacote)", função sem argumentos que devolve os manifestos.
#: Relativo de propósito: o backend já rodou como `backend.app` (partida à mão da raiz do repositório) e o nome
#: absoluto `app.planning…` derrubou o planejamento com "No module named 'app'" (execução 8a9ffc).
#: Relativo a `__package__` (`app.modules.applications.infrastructure`): quatro pontos sobem até `app`.
_BUILTINS: tuple[tuple[str, str], ...] = (
    ("....integrations.app_declarado.pacote", "descobrir"),
)

_CATALOGS: dict[str, CapabilityCatalog] = {}
_CAPS: dict[str, AppDefinition] = {}
_SCREENS: dict[str, ScreenReader] = {}
_SESSIONS: dict[str, SessionProviderFactory] = {}
#: pacote → manifesto embutido (descoberto), guardado para voltar ao registro depois de um `unregister`.
_EMBUTIDOS: dict[str, AppManifest] = {}


def _guardar(tabela: dict[str, _T], package: str, valor: _T | None) -> None:
    if valor is None:
        tabela.pop(package, None)
    else:
        tabela[package] = valor


def register(package: str, catalog: CapabilityCatalog | None = None,
             capabilities: AppDefinition | None = None, *, screen: ScreenReader | None = None,
             session: SessionProviderFactory | None = None) -> AppDefinition:
    """Registra um aplicativo. Chamar de novo substitui — é o que torna o registro testável sem estado preso.

    Devolve o que ficou valendo. `has_catalog` é derivado do catálogo recebido, nunca declarado à mão: o registro
    não pode dizer que tem catálogo e devolver `None` em `get()`.
    """
    if not package:
        raise ValueError("pacote é obrigatório para registrar um aplicativo")
    caps = capabilities or _CAPS.get(package) or neutral(package)
    caps = replace(caps, package=package, has_catalog=catalog is not None)
    if session is not None and caps.session_provider is None:
        raise ValueError(f"{package}: fábrica de provedor de sessão sem declarar o tipo (`session_provider`)")
    if catalog is not None and catalog.package != package:
        raise ValueError(f"{package}: o catálogo recebido é de {catalog.package}")
    _guardar(_CATALOGS, package, catalog)
    _guardar(_SCREENS, package, screen)
    _guardar(_SESSIONS, package, session)
    _CAPS[package] = caps
    return caps


def register_manifest(manifest: AppManifest) -> AppDefinition:
    """Registra um app pelo manifesto inteiro: definição, catálogo, leituras de tela e provedor de sessão."""
    return register(manifest.definition.package, manifest.catalog, manifest.definition, screen=manifest.screen,
                    session=manifest.session)


def unregister(package: str) -> None:
    """Tira o app do registro. Existe para o teste que prova que nada do núcleo depende de um app específico."""
    _CATALOGS.pop(package, None)
    _SCREENS.pop(package, None)
    _SESSIONS.pop(package, None)
    _CAPS.pop(package, None)


def _importar(modulo: str, atributo: str) -> tuple[AppManifest, ...]:
    descobrir = getattr(import_module(modulo, package=__package__), atributo)
    if not callable(descobrir):
        raise TypeError(f"{modulo}:{atributo} não é um descobridor de manifestos")
    achados = descobrir()
    if not isinstance(achados, tuple) or not all(isinstance(m, AppManifest) for m in achados):
        raise TypeError(f"{modulo}:{atributo} não devolveu uma tupla de AppManifest")
    return tuple(m for m in achados if isinstance(m, AppManifest))


def _descobrir_embutidos() -> None:
    """Na primeira vez, descobre os manifestos embutidos. Um app já registrado à mão com o mesmo pacote vale mais
    que o embutido, como antes."""
    if _EMBUTIDOS:
        return
    for modulo, atributo in _BUILTINS:
        for manifesto in _importar(modulo, atributo):
            _EMBUTIDOS[manifesto.definition.package] = manifesto
            if manifesto.definition.package not in _CAPS:
                register_manifest(manifesto)


def _carregar_embutido(package: str) -> None:
    _descobrir_embutidos()
    manifesto = _EMBUTIDOS.get(package)
    if manifesto is None or package in _CAPS:
        return
    register_manifest(manifesto)


def get(package: str | None) -> CapabilityCatalog | None:
    """Catálogo do app, quando existe. Sem registro, `None` — e o planejamento livre continua valendo."""
    if not package:
        return None
    if package not in _CAPS:
        _carregar_embutido(package)
    return _CATALOGS.get(package)


def capabilities_of(package: str | None) -> AppDefinition:
    """O que este app declara. Pacote desconhecido devolve o perfil neutro — nunca `None`, nunca exceção.

    Devolver neutro em vez de levantar é deliberado: o núcleo pergunta isto em caminho quente (porta de sessão,
    invalidação, despacho), e um app não registrado tem de seguir como sempre seguiu, não parar o parque.
    """
    if not package:
        return neutral(package)
    if package not in _CAPS:
        _carregar_embutido(package)
    return _CAPS.get(package) or neutral(package)


#: O nome novo da mesma pergunta: a definição (manifesto declarativo) deste app.
definition_of = capabilities_of


def screen_reader_of(package: str | None) -> ScreenReader | None:
    """As leituras de tela que o app declara, quando declara."""
    if not package:
        return None
    if package not in _CAPS:
        _carregar_embutido(package)
    return _SCREENS.get(package)


def session_factory_of(package: str | None) -> SessionProviderFactory | None:
    """A fábrica do provedor de sessão deste pacote, quando o app tem conta gerenciada."""
    if not package:
        return None
    if package not in _CAPS:
        _carregar_embutido(package)
    return _SESSIONS.get(package)


def registered() -> list[AppDefinition]:
    """Todos os apps registrados, embutidos inclusive. É o que a interface usa para oferecer a escolha do app."""
    _descobrir_embutidos()
    for pacote in _EMBUTIDOS:
        if pacote not in _CAPS:
            _carregar_embutido(pacote)
    return sorted(_CAPS.values(), key=lambda c: c.label.lower())


def nomes_e_apelidos() -> tuple[str, ...]:
    """Como o dono chama cada app registrado: nome, rótulo e apelidos do `app.yaml` ("Instagram", "insta"). É a fonte dos
    nomes de app do filtro da sombra da intenção (`decisao_fechada.entidades`, ADR-052), registrada pela fila na subida."""
    return tuple(n for d in registered() for n in (d.name, d.label, *d.aliases) if n)


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


def pacote_ancora() -> str | None:
    """O pacote do app âncora do perfil: o que declara `profile_anchor` (`ancora_do_perfil: true` no `app.yaml`).

    Substitui `package_of_provider(<tipo de conta do app>)`: o núcleo perguntava pelo TIPO de conta de um app
    específico para achar "o app da conta da persona", e com isso sabia o nome do app. Hoje a persona tem um app
    âncora só; dois registrados é erro de configuração, não escolha que o registro faça sozinho (item 12.3).
    """
    ancoras = sorted(c.package for c in registered() if c.profile_anchor)
    if len(ancoras) > 1:
        raise ValueError(f"mais de um app âncora do perfil ({', '.join(ancoras)}): a persona tem um só (item 12.3)")
    return ancoras[0] if ancoras else None


def _reset_para_teste() -> None:
    """Devolve o registro ao estado de fábrica. Só os testes chamam."""
    _CATALOGS.clear()
    _SCREENS.clear()
    _SESSIONS.clear()
    _CAPS.clear()


__all__ = ["AppCapabilities", "AppDefinition", "AppManifest", "ScreenReader", "register", "register_manifest",
           "unregister", "get", "capabilities_of", "definition_of", "screen_reader_of", "session_factory_of",
           "registered", "session_provider_of", "package_of_provider", "pacote_ancora", "nomes_e_apelidos"]
