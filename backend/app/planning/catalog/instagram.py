"""Catálogo de capabilities do Instagram.

Cada entrada é um contrato: o que a etapa faz, o que precisa antes, o que comprova depois, se tem efeito externo,
qual o risco, quem dispara o efeito e como reconciliar. Os textos de tela são da variante `en-US`, que é a suportada
nesta rodada (medido no emulador desta máquina) — outra variante entra como tabela de sinais própria, não como
remendo espalhado pelo código.

Três decisões que valem explicação:

* **Login e verificação de conta são `internal`.** Quem autentica é código determinístico, fora do laço da IA, e a
  porta de sessão do despacho já garante que nenhuma etapa roda sem conta verificada. Oferecê-las ao planejador
  criaria um segundo caminho para digitar senha — exatamente o que o canal sensível existe para impedir.
* **Toda ação com efeito declara `commit_selector`.** Sem ele, o efeito seria adivinhado pelo texto do elemento, e
  numa lista de sugestões qualquer linha viraria commit. Commit falso é irreversível.
* **Seguir, comentar e mandar mensagem nascem em `approval_required`.** É o padrão conservador do §17; o perfil pode
  afrouxar de propósito, nunca por omissão.
"""
from __future__ import annotations

from ..capabilities import Capability, CapabilityCatalog

PACKAGE = "com.instagram.android"

CAPABILITIES = [
    # ---------------------------------------------------------------- sessão (resolvidas por código)
    Capability(
        key="AUTHENTICATE_INSTAGRAM", title="Autenticar no Instagram", internal=True,
        goal="Abrir o Instagram e garantir a sessão da conta esperada.",
        post_kind="model_judged", post_value="sessão autenticada na conta esperada",
        post_description="O app mostra a conta conectada esperada.",
        reconciliation="Observar a tela depois do envio: feed, erro de credencial, desafio ou outra conta.",
        risk="high", default_policy="manual_only"),
    Capability(
        key="VERIFY_ACCOUNT", title="Confirmar a conta conectada", internal=True,
        goal="Ler na tela qual conta está conectada e comparar com a esperada.",
        post_kind="model_judged", post_value="conta conectada confirmada",
        post_description="A tela de perfil mostra o nome de usuário esperado.",
        reconciliation="A conta lida da tela é a única prova; nunca presumir pelo estado salvo."),
    Capability(
        key="LOGOUT", title="Sair da conta", goal="Sair da conta conectada neste aparelho.",
        post_kind="model_judged", post_value="sessão encerrada",
        post_description="O app volta à tela de entrada.",
        side_effect=True, risk="high", default_policy="manual_only",
        commit_selector="text=Log out", commit_guard=("Log out",),
        reconciliation="Depois de sair, a tela de login precisa aparecer; sessão salva deixa de valer."),

    # ---------------------------------------------------------------- navegação (sem efeito)
    Capability(
        key="OPEN_FEED", title="Abrir o feed", goal="Ir para a aba inicial do Instagram.",
        post_kind="element_present", post_value="id=action_bar_title_logo",
        post_description="A barra superior do feed está visível."),
    Capability(
        key="OPEN_PROFILE", title="Abrir o perfil de {username}", bindings=("username",),
        goal="Abrir o perfil de {username} pela busca ou por um link na tela.",
        post_kind="model_judged", post_value="perfil de {username} aberto",
        post_description="A tela mostra o perfil de {username}, com seguidores e publicações."),
    Capability(
        key="OPEN_POST", title="Abrir a publicação", bindings=("target",),
        goal="Abrir a publicação identificada por {target}.",
        post_kind="model_judged", post_value="publicação {target} aberta",
        post_description="A publicação está aberta, com curtidas e comentários visíveis."),
    Capability(
        key="OPEN_COMMENTS", title="Abrir os comentários", precondition="Uma publicação está aberta.",
        goal="Abrir a lista de comentários da publicação aberta.",
        post_kind="model_judged", post_value="lista de comentários aberta",
        post_description="Os comentários da publicação estão visíveis."),
    Capability(
        key="OPEN_INBOX", title="Abrir as mensagens", goal="Abrir a caixa de mensagens diretas.",
        post_kind="model_judged", post_value="caixa de mensagens aberta",
        post_description="A lista de conversas está visível."),
    Capability(
        key="OPEN_THREAD", title="Abrir a conversa com {username}", bindings=("username",),
        precondition="A caixa de mensagens está aberta.",
        goal="Abrir a conversa com {username} na lista de conversas.",
        post_kind="model_judged", post_value="conversa com {username} aberta",
        post_description="O cabeçalho da conversa mostra {username}."),
    Capability(
        key="OPEN_FOLLOW_REQUESTS", title="Abrir os pedidos para seguir",
        goal="Abrir a lista de pedidos de seguidores pendentes.",
        post_kind="model_judged", post_value="lista de pedidos aberta",
        post_description="A lista de pedidos para seguir está visível."),

    # ---------------------------------------------------------------- leitura e coleta
    Capability(
        key="READ_MESSAGES", title="Ler as mensagens da conversa", collect=True,
        collect_limit=20, collect_from_top=False,
        precondition="Uma conversa está aberta.",
        goal="Ler as mensagens recentes da conversa aberta, sem responder.",
        post_kind="items_collected", post_value="texto de cada mensagem recente da conversa",
        post_description="As mensagens recentes foram lidas."),
    Capability(
        key="COLLECT_THREADS", title="Levantar as conversas da caixa de entrada", collect=True,
        collect_limit=20, collect_from_top=False,
        precondition="A caixa de mensagens está aberta.",
        goal="Levantar o nome de cada conversa da lista.",
        post_kind="items_collected", post_value="nome de cada conversa da lista",
        post_description="As conversas da lista foram levantadas."),
    Capability(
        key="COLLECT_COMMENTS", title="Levantar os comentários da publicação", collect=True,
        collect_limit=20, collect_from_top=False,
        # A linha do comentário é lida como "autor said texto…". Quem identifica o alvo é o autor: é ele que precisa
        # virar `{username}` nas etapas do bloco, senão `commit_guard`/`band_guard` exigiriam a frase inteira
        # (com emojis) visível na tela — e a curtida do comentário nunca fecharia.
        item_key=r"^(\S+)\s+said\s",
        precondition="A lista de comentários está aberta.",
        goal="Levantar o autor e o texto de cada comentário visível.",
        post_kind="items_collected", post_value="autor e texto de cada comentário da lista",
        post_description="Os comentários foram levantados."),

    # ---------------------------------------------------------------- efeito externo
    Capability(
        key="LIKE_POST", interaction_type="post_liked", title="Curtir a publicação", precondition="A publicação alvo está aberta na tela.",
        goal="Curtir a publicação aberta, uma única vez.",
        post_kind="model_judged", post_value="publicação curtida",
        post_description="O ícone de curtir aparece marcado na publicação aberta.",
        side_effect=True, risk="medium", commit_selector="desc=Like", limit_bucket="likes",
        default_policy="autonomous",
        reconciliation="Depois do toque, observar o ícone: marcado = curtido. Nunca tocar de novo por timeout."),
    Capability(
        key="UNLIKE_POST", interaction_type="post_unliked", title="Descurtir a publicação", precondition="A publicação alvo está aberta na tela.",
        goal="Remover a curtida da publicação aberta.",
        post_kind="model_judged", post_value="curtida removida",
        post_description="O ícone de curtir aparece desmarcado.",
        side_effect=True, risk="medium", commit_selector="desc=Liked", limit_bucket="likes",
        default_policy="approval_required",
        reconciliation="Observar o ícone depois do toque; desmarcado = removida."),
    Capability(
        key="LIKE_COMMENT", interaction_type="comment_liked", title="Curtir o comentário de {username}", bindings=("username",),
        precondition="A lista de comentários está aberta.",
        goal="Curtir o comentário de {username}, uma única vez.",
        post_kind="model_judged", post_value="comentário de {username} curtido",
        post_description="O comentário de {username} aparece com a curtida marcada.",
        side_effect=True, risk="medium", commit_selector="desc=Like", commit_guard=("{username}",),
        band_guard=("{username}",), limit_bucket="likes",
        reconciliation="Observar o ícone do comentário alvo depois do toque."),
    Capability(
        key="CREATE_COMMENT", interaction_type="comment_replied", title="Comentar na publicação", bindings=("content",),
        precondition="A lista de comentários está aberta e o texto já foi escrito e aprovado.",
        goal="Publicar o comentário com o conteúdo aprovado.",
        post_kind="model_judged", post_value="comentário publicado",
        post_description="O comentário aparece na lista, atribuído à conta conectada.",
        # Medido no app real (447): o botão de enviar só EXISTE depois que há texto no campo, e sua descrição é
        # "Post", não "Post comment". Com o seletor errado o commit nunca casava, o executor rejeitava o toque e a
        # etapa morria em "o efeito externo foi tentado no elemento errado" — com a IA fazendo tudo certo.
        side_effect=True, risk="high", commit_selector="id=layout_comment_thread_post_button_icon",
        commit_guard=("{content}",),
        limit_bucket="comments", default_policy="approval_required", needs_draft=True,
        reconciliation="O comentário precisa aparecer na lista; enviar de novo criaria dois comentários."),
    Capability(
        key="REPLY_COMMENT", interaction_type="comment_replied", title="Responder o comentário de {username}", bindings=("username", "content"),
        precondition="A lista de comentários está aberta, com a resposta já escrita e aprovada.",
        goal="Responder o comentário de {username} com o conteúdo aprovado.",
        post_kind="model_judged", post_value="resposta publicada",
        post_description="A resposta aparece abaixo do comentário de {username}.",
        side_effect=True, risk="high", commit_selector="id=layout_comment_thread_post_button_icon",
        commit_guard=("{username}", "{content}"),
        band_guard=("{username}",), limit_bucket="comments", default_policy="approval_required", needs_draft=True,
        reconciliation="A resposta precisa aparecer na conversa do comentário; nunca reenviar por timeout."),
    Capability(
        key="SEND_MESSAGE", interaction_type="dm_sent", title="Enviar a mensagem para {username}", bindings=("username", "content"),
        precondition="A conversa com {username} está aberta e o texto já foi escrito e aprovado.",
        goal="Enviar o conteúdo aprovado na conversa aberta com {username}.",
        post_kind="model_judged", post_value="mensagem enviada para {username}",
        post_description="A mensagem aparece na conversa com {username}, com indicação de envio.",
        side_effect=True, risk="high", commit_selector="desc=Send", commit_guard=("{username}", "{content}"),
        limit_bucket="dms", default_policy="approval_required", needs_draft=True,
        reconciliation="Observar a conversa: a mensagem aparece uma vez. Reenviar por timeout duplicaria."),
    Capability(
        key="FOLLOW", interaction_type="followed", title="Seguir {username}", bindings=("username",),
        precondition="O perfil de {username} está aberto.",
        goal="Seguir {username} a partir do perfil aberto.",
        post_kind="model_judged", post_value="{username} seguido ou pedido enviado",
        post_description="O botão mostra 'Following' ou 'Requested' no perfil de {username}.",
        side_effect=True, risk="high", commit_selector="text=Follow", commit_guard=("{username}",),
        limit_bucket="follows", default_policy="approval_required",
        reconciliation="Ler o botão depois do toque: Following, Requested ou ainda Follow."),
    Capability(
        key="UNFOLLOW", interaction_type="unfollowed", title="Deixar de seguir {username}", bindings=("username",),
        precondition="O perfil de {username} está aberto.",
        goal="Deixar de seguir {username} a partir do perfil aberto.",
        post_kind="model_judged", post_value="{username} deixou de ser seguido",
        post_description="O botão volta a mostrar 'Follow' no perfil de {username}.",
        side_effect=True, risk="high", commit_selector="text=Following", commit_guard=("{username}",),
        limit_bucket="follows", default_policy="manual_only",
        reconciliation="Ler o botão depois da confirmação; o app costuma pedir confirmação em diálogo."),
    Capability(
        key="ACCEPT_FOLLOW_REQUEST", interaction_type="follow_request_accepted", title="Aceitar o pedido de {username}", bindings=("username",),
        precondition="A lista de pedidos para seguir está aberta.",
        goal="Aceitar o pedido de {username} na lista de pedidos.",
        post_kind="model_judged", post_value="pedido de {username} aceito",
        post_description="A linha de {username} deixa de mostrar o botão de aceitar.",
        side_effect=True, risk="high", commit_selector="text=Confirm", commit_guard=("{username}",),
        band_guard=("{username}",), limit_bucket="follows", default_policy="approval_required",
        reconciliation="A linha do pedido some ou muda de estado; não tocar de novo."),
    Capability(
        key="DECLINE_FOLLOW_REQUEST", interaction_type="follow_request_declined", title="Recusar o pedido de {username}", bindings=("username",),
        precondition="A lista de pedidos para seguir está aberta.",
        goal="Recusar o pedido de {username} na lista de pedidos.",
        post_kind="model_judged", post_value="pedido de {username} recusado",
        post_description="A linha de {username} sai da lista de pedidos.",
        side_effect=True, risk="high", commit_selector="text=Delete", commit_guard=("{username}",),
        band_guard=("{username}",), limit_bucket="follows", default_policy="approval_required",
        reconciliation="A linha some da lista; recusar duas vezes não é possível, mas tocar de novo acerta outro pedido."),
]

INSTAGRAM_CATALOG = CapabilityCatalog(PACKAGE, CAPABILITIES)
