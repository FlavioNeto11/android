"""Manifesto do Instagram: tudo que o núcleo sabe dele, ele sabe por aqui (fase K1; design §2.5).

É a primeira implementação completa de `AppManifest`: a definição (conta gerenciada do tipo `"instagram"`, perfil e
internet obrigatórios), o catálogo de capabilities, as leituras de tela que o rascunho usa, os mapas de tipo de texto
e de leitura de conversa, e a fábrica do provedor de sessão — o `InstagramAuthenticator` de sempre, agora atrás da
porta `modules/identity/application/ports.SessionProvider`.

Antes, cada um desses pedaços estava no núcleo: `state.py` importava o autenticador e os extratores de
`navigation`, criava `self.instagram` concreto e mantinha `_TIPO_DE_TEXTO`/`_LEITURA_DE_CONVERSA` com chaves do
catálogo do Instagram; a porta de sessão, a invalidação e o painel comparavam com `"instagram"`. O registro carrega
este módulo sozinho (embutido preguiçoso), então ninguém precisa importá-lo antes.
"""
from __future__ import annotations

from ...automation.hierarchy import UiTree
from ...modules.applications.domain.definition import AppDefinition
from ...modules.applications.infrastructure.registry import AppManifest
from ...modules.identity.infrastructure.sessions import SessionDeps
from ...planning.catalog.instagram import INSTAGRAM_CATALOG, PACKAGE
from .authentication import InstagramAuthenticator
from .navigation import comentario_de, conteudo_visivel, mensagem_de

# Que tipo de escrita é cada ação do catálogo. Muda o enquadramento do texto: responder alguém não é o mesmo que
# comentar uma publicação nem que puxar conversa do zero.
#
# `SEND_MESSAGE` é o único que DEPENDE do fio: mandar mensagem numa conversa em que a outra pessoa acabou de
# falar é responder, não puxar assunto. Quem decide é `_draft_gate`, olhando a última fala dela.
TIPO_DE_TEXTO: dict[str, str] = {
    "CREATE_COMMENT": "post_comment",
    "REPLY_COMMENT": "comment_reply",
    "SEND_MESSAGE": "dm_initiate",
}
# Ações de LEITURA de conversa: o que elas coletam é fala de outra pessoa, e é por aqui que o perfil finalmente
# ouve. `COLLECT_THREADS` fica de fora de propósito — ela levanta NOMES de conversa na caixa de entrada, não
# mensagens; gravar aquilo como fala seria inventar que a pessoa disse o próprio nome.
LEITURA_DE_CONVERSA: dict[str, str] = {
    "READ_MESSAGES": "dm_received",
}


class LeituraDeTela:
    """`ScreenReader` do Instagram: as leituras de `navigation` que o rascunho usa para falar do que está na tela."""

    def visible_content(self, tree: UiTree) -> str:
        return conteudo_visivel(tree)

    def comment_of(self, tree: UiTree, author: str) -> str:
        return comentario_de(tree, author)

    def message_of(self, tree: UiTree, author: str) -> str:
        return mensagem_de(tree, author)


def sessao(deps: SessionDeps) -> InstagramAuthenticator:
    """Login determinístico, fora do laço da IA: a senha só passa pelo canal de entrada sensível."""
    return InstagramAuthenticator(deps.cfg, deps.devices, deps.repo, deps.secrets, deps.sensitive_input, deps.bus)


DEFINICAO = AppDefinition(package=PACKAGE, name="Instagram", session_provider="instagram", needs_profile=True,
                          requires_internet=True, label="Instagram", text_kinds=tuple(TIPO_DE_TEXTO.items()),
                          conversation_reads=tuple(LEITURA_DE_CONVERSA.items()))

INSTAGRAM = AppManifest(definition=DEFINICAO, catalog=INSTAGRAM_CATALOG, screen=LeituraDeTela(), session=sessao)
