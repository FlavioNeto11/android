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
        # Lido da hierarquia real do app 447 em 24/09 (`GET /api/instances/android-01/hierarchy`): a barra do feed é
        # `main_feed_action_bar` (com `action_bar_title_view` e `title_logo` dentro). O `action_bar_title_logo` de
        # antes não existe mais e derrubava a etapa 6 vezes seguidas (ig-abrir-mensagens, r-…-e2a48a).
        post_kind="element_present", post_value="id=main_feed_action_bar",
        post_description="A barra superior do feed está visível."),
    Capability(
        key="OPEN_PROFILE", title="Abrir o perfil de {username}", bindings=("username",),
        goal="Abrir o perfil de {username} pela busca ou por um link na tela.",
        post_kind="model_judged", post_value="perfil de {username} aberto",
        post_description="A tela mostra o perfil de {username}, com seguidores e publicações.",
        # Medido no app 447: o perfil aberto traz o username no `action_bar_title` (a tela mostra "capitaotarcisio…",
        # sem a arroba). Prova positiva dispensa o verificador; se o título não casar, o modelo julga como antes.
        local_proof="selector:id=action_bar_title|text=={username}"),
    Capability(
        key="OPEN_POST", title="Abrir a publicação", bindings=("target",),
        goal="Abrir a publicação identificada por {target}.",
        # Medido em eda77f: publicação (ou reel) aberta a partir da grade do perfil tem `action_bar_title` "Posts";
        # o feed inicial tem `action_bar_title_logo` e o perfil tem o username no título — então "Posts" exato só
        # casa a publicação aberta. NÃO usar `row_feed_button_like`: existe em todo cartão do feed, e um `press_back`
        # do perfil cai no feed — a etapa "passaria" lá e a curtida seguinte iria num post qualquer.
        post_kind="element_present", post_value="id=action_bar_title|text==Posts",
        post_description="A publicação está aberta (título \"Posts\"), com curtidas e comentários visíveis."),
    Capability(
        key="OPEN_COMMENTS", title="Abrir os comentários", precondition="Uma publicação está aberta.",
        goal="Abrir a lista de comentários da publicação aberta.",
        # Medido em eda77f: a folha de comentários tem `title_text_view` "Comments".
        post_kind="element_present", post_value="id=title_text_view|text==Comments",
        post_description="Os comentários da publicação estão visíveis (folha \"Comments\")."),
    Capability(
        key="OPEN_INBOX", title="Abrir as mensagens", goal="Abrir a caixa de mensagens diretas.",
        post_kind="model_judged", post_value="caixa de mensagens aberta",
        # Caixa VAZIA é caixa aberta: uma conta nova mostra "No messages yet" e nenhuma conversa. O que a etapa
        # precisa comprovar é que a tela já terminou de carregar — "Loading…" não passa, lista vazia passa.
        post_description="A caixa de mensagens está aberta e terminou de carregar: a lista de conversas aparece, "
                         "mesmo que vazia (\"No messages yet\"). Ainda carregando (\"Loading…\") não conta."),
    Capability(
        key="OPEN_THREAD", title="Abrir a conversa com {username}", bindings=("username",),
        precondition="A caixa de mensagens está aberta.",
        goal="Abrir a conversa com {username}, seja uma conversa existente na lista, seja uma nova.",
        post_kind="model_judged", post_value="conversa com {username} aberta",
        # Exigir o cabeçalho era impossível de cumprir em conversa NOVA: o Instagram mostra "New message" no topo e
        # o destinatário em "To:". A etapa era reprovada com a conversa aberta e o compositor pronto, e o envio
        # acabava cancelado (visto em r-20260918213007-a353d8). O que importa é o destinatário certo e poder
        # escrever — não onde o nome aparece.
        post_description="A conversa com {username} está aberta e pronta para escrever: {username} aparece como "
                         "destinatário (no cabeçalho de uma conversa existente, ou em \"To:\"/\"Para:\" de uma "
                         "conversa nova) e o campo de escrever mensagem está disponível.",
        # Atalho positivo: o username visível junto do campo de escrita da conversa já é a conversa certa aberta (com
        # ou sem arroba). Sem isso, o modelo julga como antes. Até 27/09 a prova só pedia o username — e uma linha da
        # caixa de entrada com o mesmo nome passava sem a conversa aberta. O id do compositor foi lido das telas reais
        # julgadas na bateria de 24–25/09 (`row_thread_composer_edittext`, "Message…", em conversa nova e existente).
        local_proof="selector:text=={username}&id=row_thread_composer_edittext"),
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
        collect_limit=20, collect_from_top=False, collect_rewind=True,
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
        # `desc==Like` EXATO: por substring, "Like" também casava "Liked" — o toque num post já curtido era aceito
        # como commit e DESCURTIA. A prova local é o mesmo botão marcado depois do toque.
        side_effect=True, risk="medium", commit_selector="desc==Like", limit_bucket="likes",
        default_policy="autonomous", local_proof="selector:desc==Liked",
        reconciliation="Depois do toque, observar o ícone: marcado = curtido. Nunca tocar de novo por timeout."),
    Capability(
        key="UNLIKE_POST", interaction_type="post_unliked", title="Descurtir a publicação", precondition="A publicação alvo está aberta na tela.",
        goal="Remover a curtida da publicação aberta.",
        post_kind="model_judged", post_value="curtida removida",
        post_description="O ícone de curtir aparece desmarcado.",
        side_effect=True, risk="medium", commit_selector="desc==Liked", limit_bucket="likes",
        default_policy="approval_required", local_proof="selector:desc==Like",
        reconciliation="Observar o ícone depois do toque; desmarcado = removida."),
    Capability(
        key="LIKE_COMMENT", interaction_type="comment_liked", title="Curtir o comentário de {username}", bindings=("username",),
        precondition="A lista de comentários está aberta.",
        goal="Curtir o comentário de {username}, uma única vez.",
        post_kind="model_judged", post_value="comentário de {username} curtido",
        post_description="O comentário de {username} aparece com a curtida marcada.",
        side_effect=True, risk="medium", commit_selector="desc==Like", commit_guard=("{username}",),
        band_guard=("{username}",), limit_bucket="likes",
        # O coração marcado tem de estar na FAIXA do comentário de {username}: o de cima não prova o de baixo.
        local_proof="selector_band:desc==Liked",
        reconciliation="Observar o ícone do comentário alvo depois do toque."),
    Capability(
        key="CREATE_COMMENT", interaction_type="comment_replied", title="Comentar na publicação",
        optional_bindings=("content_brief", "content", "content_verbatim"),
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
        key="REPLY_COMMENT", interaction_type="comment_replied", title="Responder o comentário de {username}",
        bindings=("username",), optional_bindings=("content_brief", "content", "content_verbatim"),
        precondition="A lista de comentários está aberta, com a resposta já escrita e aprovada.",
        goal="Responder o comentário de {username} com o conteúdo aprovado.",
        post_kind="model_judged", post_value="resposta publicada",
        post_description="A resposta aparece abaixo do comentário de {username}.",
        side_effect=True, risk="high", commit_selector="id=layout_comment_thread_post_button_icon",
        commit_guard=("{username}", "{content}"),
        band_guard=("{username}",), limit_bucket="comments", default_policy="approval_required", needs_draft=True,
        reconciliation="A resposta precisa aparecer na conversa do comentário; nunca reenviar por timeout."),
    Capability(
        key="SEND_MESSAGE", interaction_type="dm_sent", title="Enviar a mensagem para {username}",
        bindings=("username",), optional_bindings=("content_brief", "content", "content_verbatim"),
        precondition="A conversa com {username} está aberta e o texto já foi escrito e aprovado.",
        goal="Enviar o conteúdo aprovado na conversa aberta com {username}.",
        post_kind="model_judged", post_value="mensagem enviada para {username}",
        # Medido no app real: o Instagram NAO mostra rotulo "Sent"/"Delivered" numa DM recem-enviada. Exigir
        # "indicacao de envio" fazia todo envio bem-sucedido voltar como "nao foi possivel comprovar".
        # A prova observavel e a TRANSICAO: o texto sai do campo de escrita e vira mensagem no fio.
        post_description=("A mensagem aparece na conversa com {username} como mensagem enviada — ela saiu do "
                          "campo de escrita, que volta vazio — e não há marca de falha (como 'Not delivered' "
                          "ou 'Tap to retry')."),
        side_effect=True, risk="high", commit_selector="desc=Send", commit_guard=("{username}", "{content}"),
        limit_bucket="dms", default_policy="approval_required", needs_draft=True,
        failure_marks=("Not delivered", "Tap to retry", "Failed to send", "Message not sent",
                       "Não entregue", "Toque para tentar novamente"),
        local_proof="sent_text",
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
