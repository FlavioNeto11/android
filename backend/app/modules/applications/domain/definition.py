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
    #: É o app da CONTA do perfil (ADR-052, fatia 4): onde vivem a credencial e a sessão que o resto do sistema lê
    #: como "a conta da persona". No máximo um app registrado é âncora; persona com login gerenciado em mais de um
    #: app é o item 12.3.
    profile_anchor: bool = False
    #: Links de perfil do app, para o parâmetro `handle` das skills: os domínios, e os primeiros segmentos de caminho
    #: que NÃO são perfil (post, reel...). Vazio = link deste app não vira nome de usuário, vira pergunta.
    profile_link_hosts: tuple[str, ...] = ()
    profile_link_reserved: tuple[str, ...] = ()
    #: Renderizadores do EMULADOR em que este app não roda (`renderizador_recusado` no `app.yaml`, 29.11), pelo nome
    #: canônico (`swiftshader`, `host`). Não é preferência: é o app que derruba o processo do emulador naquele
    #: renderizador. A plataforma recusa instalar e abrir o app num aparelho que o usa (`devices/compatibilidade.py`).
    #: Vazio = o app não declarou nada, e nada muda para ele.
    refused_renderers: tuple[str, ...] = ()
    #: Atividades (`pacote/atividade` completo, minúsculo) que, EM FOCO, provam a conta PERDIDA no app
    #: (`atividades_de_conta_perdida` no `app.yaml`, 29.23/ADR-068). É o sinal forte da retirada automática: app que não
    #: declara nenhuma não retira conta sozinho (a conta travada fica para a pessoa, e a retirada é só pela rota).
    lost_account_activities: tuple[str, ...] = ()
    #: A conta retirada por bloqueio confirmado leva junto os dados DESTE app nos aparelhos onde estava logada
    #: (`limpar_ao_retirar: true` no `app.yaml`, 29.27/emenda do ADR-068): `pm clear` só desse pacote, com captura de tela
    #: antes e depois, sem toque na tela. Só limpa quem declara; o padrão (falso) deixa o app como estava.
    clear_on_account_retire: bool = False
    #: Como o dono chama o app num comando, além do nome e do rótulo ("insta"; `apelidos` no `app.yaml`). É o que o filtro
    #: da sombra da intenção (31.9) reconhece como o app, sem lista de app em Python (ADR-052).
    aliases: tuple[str, ...] = ()
    #: Prova de 07/10 (31.154, adendo v1.94): o rótulo do estágio "app aberto" de um alvo da operação (`instagram_aberto`)
    #: e que ação do catálogo, concluída, marca qual estágio (`OPEN_POST` → `post_localizado`). `operacao` no `app.yaml`;
    #: vazio = o alvo deste app só tem os estágios que não dependem do app (o nome do app nunca fica no código).
    operation_opening: str = "app_aberto"
    operation_stages: tuple[tuple[str, str], ...] = ()
    #: O app declara como sair da conta aberta (`troca` no `sessao.yaml`, 31.155/ADR-080): o motor de sessão troca de
    #: conta sozinho, e mais de uma persona pode servir a este app no mesmo aparelho. Derivado do `sessao.yaml` na
    #: descoberta, nunca escrito no `app.yaml`; falso = conta errada é caso de pessoa (achado #115).
    account_switch: bool = False

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
