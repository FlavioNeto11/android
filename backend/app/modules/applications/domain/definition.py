"""`AppDefinition`: o manifesto declarativo de um aplicativo (fase K1; design §2.5, §16).

É o que o núcleo pergunta ao registro de apps no lugar de `if package == "com.instagram.android"` ou
`session_provider == "instagram"`: quem é o app, se ele tem conta gerenciada (e de que tipo), se toda tarefa nele
exige perfil e internet, como chamá-lo numa mensagem, e o que as capabilities dele significam para o histórico do
perfil (que tipo de texto cada uma escreve, que leitura vira fala de outra pessoa).

Só dado, sem comportamento: o catálogo de capabilities, as leituras de tela e a fábrica do provedor de sessão são
código do app e moram no `AppManifest` (`infrastructure/registry.py`), porque o domínio não enxerga `UiTree`,
`CapabilityCatalog` nem o que um provedor de sessão precisa para existir (regra D2).

Era `planning.catalog.AppCapabilities`, que continua sendo o mesmo tipo pelo nome antigo.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppDefinition:
    """O que este aplicativo declara sobre si.

    `session_provider` é o TIPO do provedor de sessão de conta (hoje só `"instagram"`). `None` quer dizer que o app
    não tem conta gerenciada: a porta de sessão do despacho nem abre, e instalar o app não invalida sessão nenhuma.
    `needs_profile` diz se uma tarefa naquele app exige perfil vinculado ao aparelho.
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
    #: capability → tipo de texto que ela escreve na voz do perfil (`dm_initiate`, `post_comment`...). Muda o
    #: enquadramento do rascunho: responder alguém não é comentar uma publicação nem puxar conversa do zero.
    text_kinds: tuple[tuple[str, str], ...] = ()
    #: capability de LEITURA de conversa → tipo da interação de entrada que o que ela coletou vira no histórico.
    conversation_reads: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", self.name or self.package)

    def text_kind(self, capability: str | None) -> str | None:
        """Que texto esta capability escreve; `None` = o app não declarou (quem pergunta decide o padrão)."""
        return next((tipo for chave, tipo in self.text_kinds if chave == capability), None)

    def conversation_read(self, capability: str | None) -> str | None:
        """Se o que esta capability coleta é fala de outra pessoa, e de que tipo; `None` = não é."""
        return next((tipo for chave, tipo in self.conversation_reads if chave == capability), None)


def neutral(package: str | None) -> AppDefinition:
    """Aplicativo sem registro: sem catálogo, sem sessão, sem perfil. É o caminho livre de sempre."""
    return AppDefinition(package=package or "", name=package or "aplicativo")
