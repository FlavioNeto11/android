"""Classificação das telas do Instagram por sinais ESTRUTURAIS.

Os ids do Instagram são ofuscados e mudam a cada versão, então nada aqui depende de um id fixo. A âncora é o
atributo `password` da hierarquia, que vem direto do Android (`AccessibilityNodeInfo.isPassword()`), e as posições
relativas entre os campos. Texto localizado é sinal COMPLEMENTAR, nunca a decisão: ele mora numa tabela por idioma
e só desempata.

Idioma: `en-US` é a variante suportada nesta rodada (é o que o emulador do projeto usa). Acrescentar outro idioma é
acrescentar uma linha na tabela, não espalhar texto pelo código.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from ...automation.hierarchy import UiElement, UiTree

PACKAGE = "com.instagram.android"


class Screen(StrEnum):
    FEED = "feed"
    LOGIN = "login"
    TWO_FACTOR = "two_factor"
    CHALLENGE = "challenge"
    SAVE_LOGIN_PROMPT = "save_login_prompt"
    ACCOUNT_SWITCHER = "account_switcher"
    PROFILE = "profile"
    INBOX = "inbox"
    LOADING = "loading"
    UNKNOWN = "unknown"


# Sinais por idioma. Cada entrada é um padrão aplicado ao texto/descrição da tela inteira.
SIGNALS: dict[str, dict[str, re.Pattern[str]]] = {
    "en": {
        "login_button": re.compile(r"^\s*(log ?in|sign ?in)\s*$", re.IGNORECASE),
        "facebook": re.compile(r"facebook", re.IGNORECASE),
        "two_factor": re.compile(r"(two[- ]factor|security code|confirmation code|6[- ]digit)", re.IGNORECASE),
        "challenge": re.compile(r"(we detected|suspicious|unusual|confirm it'?s you|help us confirm|verify your account"
                                r"|enter the code we sent|captcha|i'?m not a robot"
                                # achado #104: redação vista em produção (r-20260920143652-132c2e) e ausente
                                # daqui — sem ela a tela caía em UNKNOWN e o perfil ficava preso reobservando.
                                # Ancorado em "confirm you're human" (nunca só "to use your account" sozinho,
                                # que sozinho poderia aparecer numa tela de onboarding comum e não é challenge).
                                r"|confirm you'?re human)", re.IGNORECASE),
        "save_login": re.compile(r"save (your )?login info", re.IGNORECASE),
        "save_dismiss": re.compile(r"^\s*not now\s*$", re.IGNORECASE),
        "wrong_password": re.compile(r"(incorrect password|password (you )?entered .* incorrect|wrong password)",
                                     re.IGNORECASE),
        "user_not_found": re.compile(r"(couldn'?t find|user not found|no account found)", re.IGNORECASE),
        # Diálogo genérico do app depois de Entrar ("Unable to log in / An unexpected error occurred"). Visto no
        # android-06 com uma conta que entra pelo navegador: a tela NÃO diz a causa, então isto nunca é "senha
        # errada" — só um fato observado, registrado com nome em vez de "tela não classificada".
        "login_error": re.compile(r"(unable to log ?in|an unexpected error occurred|please try logging in again)",
                                  re.IGNORECASE),
        "switcher": re.compile(r"(switch accounts?|log into another account)", re.IGNORECASE),
        "inbox": re.compile(r"^\s*(messages|direct)\s*$", re.IGNORECASE),
    },
    "pt": {
        "login_button": re.compile(r"^\s*(entrar|fazer login)\s*$", re.IGNORECASE),
        "facebook": re.compile(r"facebook", re.IGNORECASE),
        "two_factor": re.compile(r"(autentica[çc][ãa]o de dois fatores|c[óo]digo de seguran[çc]a|c[óo]digo de confirma)",
                                 re.IGNORECASE),
        "challenge": re.compile(r"(detectamos|suspeit|confirme que [ée] voc[êe]|ajude a confirmar|verifique sua conta"
                                r"|insira o c[óo]digo|captcha|n[ãa]o sou um rob[ôo]"
                                # achado #104: "confirme que você é humano/uma pessoa" tem outra ordem de
                                # palavras que o padrão de cima não cobre.
                                r"|confirme que (voc[êe] )?[ée] (um[ae]? pessoa|humano))", re.IGNORECASE),
        "save_login": re.compile(r"salvar (suas )?informa[çc][õo]es de login", re.IGNORECASE),
        "save_dismiss": re.compile(r"^\s*agora n[ãa]o\s*$", re.IGNORECASE),
        "wrong_password": re.compile(r"(senha incorreta|senha .* incorreta)", re.IGNORECASE),
        "user_not_found": re.compile(r"(n[ãa]o foi poss[íi]vel encontrar|usu[áa]rio n[ãa]o encontrado)", re.IGNORECASE),
        "login_error": re.compile(r"(n[ãa]o foi poss[íi]vel (entrar|fazer login)|ocorreu um erro inesperado"
                                  r"|tente (entrar|fazer login) novamente)", re.IGNORECASE),
        "switcher": re.compile(r"(trocar de conta|entrar em outra conta)", re.IGNORECASE),
        "inbox": re.compile(r"^\s*(mensagens|direct)\s*$", re.IGNORECASE),
    },
}
DEFAULT_LOCALE = "en"

# Um `@usuario` mostrado na tela. Serve para ler a conta observada sem depender de id.
USERNAME_TEXT = re.compile(r"^@?([A-Za-z0-9._]{1,30})$")


@dataclass(slots=True)
class LoginForm:
    """Os três elementos do formulário, quando a tela é de login."""

    password: UiElement
    username: UiElement | None = None
    submit: UiElement | None = None

    @property
    def complete(self) -> bool:
        return self.username is not None and self.submit is not None


@dataclass(slots=True)
class Classification:
    screen: Screen
    reason: str
    form: LoginForm | None = None
    evidence: list[str] = field(default_factory=list)


def signals(locale: str | None) -> dict[str, re.Pattern[str]]:
    """`pt-BR` -> tabela `pt`; idioma não suportado cai no inglês, que é a variante desta rodada."""
    code = (locale or "").split("-")[0].split("_")[0].lower()
    return SIGNALS.get(code, SIGNALS[DEFAULT_LOCALE])


def _screen_text(tree: UiTree) -> str:
    return "\n".join(f"{e.text} {e.desc}".strip() for e in tree.elements if e.text or e.desc)


def password_field(tree: UiTree) -> UiElement | None:
    """Âncora do formulário. `password` vem do Android; `editable` não serve, porque o Instagram usa widgets
    próprios que a heurística de classe não reconhece."""
    campos = [e for e in tree.elements if e.password and e.enabled]
    return campos[0] if len(campos) == 1 else None


def username_field(tree: UiTree, password: UiElement) -> UiElement | None:
    """O campo de usuário é o campo de texto imediatamente ACIMA do de senha, na mesma faixa horizontal."""
    px1, py1, px2, _ = password.bounds
    candidatos = [
        e for e in tree.elements
        if not e.password and e.enabled and e.bounds[3] <= py1                     # inteiramente acima
        and e.bounds[2] > px1 and e.bounds[0] < px2                                # sobreposto horizontalmente
        and (e.editable or e.class_name.endswith("EditText") or e.clickable)
        and (e.bounds[2] - e.bounds[0]) >= (px2 - px1) * 0.6                       # largura parecida com a do campo
    ]
    if not candidatos:
        return None
    return max(candidatos, key=lambda e: e.bounds[1])                              # o mais próximo, logo acima


def submit_button(tree: UiTree, password: UiElement, locale: str | None) -> UiElement | None:
    """Clicável, habilitado, ABAIXO do campo de senha, sem 'facebook', e exatamente um candidato.

    Mais de um candidato é incerteza, não escolha: 'Log in' também aparece em 'Already have an account? Log in',
    em 'Log in with Facebook' e no seletor de contas.
    """
    sig = signals(locale)
    _, _, _, py2 = password.bounds
    candidatos = []
    for e in tree.elements:
        if not (e.clickable and e.enabled) or e.bounds[1] < py2:
            continue
        rotulo = f"{e.text} {e.desc}".strip()
        if not rotulo or sig["facebook"].search(rotulo):
            continue
        if sig["login_button"].match(rotulo.strip()):
            candidatos.append(e)
    return candidatos[0] if len(candidatos) == 1 else None


def login_form(tree: UiTree, locale: str | None) -> LoginForm | None:
    password = password_field(tree)
    if password is None:
        return None
    return LoginForm(password=password, username=username_field(tree, password),
                     submit=submit_button(tree, password, locale))


# Telas benignas que o Instagram intercala depois de entrar: dicas ("Got it"), passos de onboarding ("Skip") e
# diálogos com um par aceitar/recusar ("Yes, follow friends" / "No, skip"). Só a RECUSA entra aqui — jamais
# "Allow"/"Permitir"/"Next"/"Yes", que liberariam contatos, notificações, sincronização ou sairiam seguindo gente.
_DISPENSAR = re.compile(
    r"^\s*(?:(?:no|n[ãa]o)[,\s]+)?(?:skip|pular)\s*$"          # "Skip", "No, skip", "Não, pular"
    r"|^\s*(?:got it|entendi|ok)\s*$"
    r"|^\s*(?:not now|agora n[ãa]o)\s*$"
    r"|^\s*(?:maybe later|talvez mais tarde)\s*$",
    re.IGNORECASE)
# Botão de RECUSA dos diálogos do Instagram. Vale como dispensa quando o rótulo não bate: é o lado que não concede.
_RECUSA_ID = re.compile(r"(alert_dialog_cancel|dialog_secondary|negative_button)", re.IGNORECASE)


def dismiss_button(tree: UiTree) -> UiElement | None:
    """Botão que fecha uma tela intermediária benigna sem conceder permissão nem seguir ninguém."""
    for e in tree.elements:
        if e.clickable and _DISPENSAR.search(f"{e.text} {e.desc}".strip()):
            return e
    for e in tree.elements:
        if e.clickable and _RECUSA_ID.search(e.resource_id):
            return e
    return None


def save_login_dismiss(tree: UiTree, locale: str | None = None) -> UiElement | None:
    """Botão que dispensa o "Salvar dados de login?" sem salvá-los na nuvem — "Agora não" / "Not now".

    É a escolha que preserva privacidade (o login não vai para o backup da conta Google do aparelho). Se o texto do
    botão mudar, devolve None e o fluxo segue sem tocar em nada: falha para o lado seguro, nunca no botão errado.
    """
    sig = signals(locale)
    for e in tree.elements:
        if e.clickable and sig["save_dismiss"].search(f"{e.text} {e.desc}".strip()):
            return e
    return None


_USERNAME_IDS = ("username", "action_bar_title", "profile_header_username", "row_profile_header_textview_username")


def header_username(tree: UiTree) -> str | None:
    """Conta aberta, lida SÓ de um id de cabeçalho de perfil conhecido — a fonte confiável de QUEM está logado.

    O feed mostra o @ de outras contas (autores de reels, stories que a conta segue); pegar o "primeiro @ da tela"
    confundia um desses com a conta própria e disparava "conta errada" falso. Para decidir identidade, só o
    cabeçalho vale; quem precisa disso navega até a aba de perfil primeiro.
    """
    for e in tree.elements:
        if e.resource_id.rsplit("/", 1)[-1].lower() in _USERNAME_IDS:
            achado = USERNAME_TEXT.match((e.text or "").strip())
            if achado:
                return achado.group(1).lower()
    return None


def observed_username(tree: UiTree) -> str | None:
    """Conta aberta, lida da tela. Prefere um id conhecido; se não houver, usa o `@usuario` mais evidente.

    O palpite pelo `@` só é seguro em telas onde o único @ é o da conta (perfil, troca de contas). Para IDENTIFICAR
    quem está logado a partir do feed, use `header_username`, que ignora esse palpite.
    """
    achado = header_username(tree)
    if achado:
        return achado
    for e in tree.elements:
        texto = (e.text or "").strip()
        if texto.startswith("@"):
            achado = USERNAME_TEXT.match(texto)
            if achado:
                return achado.group(1).lower()
    return None


# Rótulos de interface que aparecem em quase toda tela do app e não dizem nada sobre o CONTEÚDO. Ficam fora da
# leitura para que o gerador receba a legenda e os comentários, não a barra de navegação.
_CHROME = re.compile(
    r"^\s*(curtir|curtidas?|comentar|coment[áa]rios?|compartilhar|enviar|salvar|seguir|seguindo|seguidores?"
    r"|publica[çc][õo]es|in[íi]cio|pesquisa|explorar|reels|perfil|mais|op[çc][õo]es|voltar|fechar|adicionar"
    r"|ver tradu[çc][ãa]o|ver mais|ver todos.*|responder|h[áa] \d+.*|\d+ ?[a-z]?|like[sd]?|comments?|share|send"
    r"|save|follow(ing)?|followers?|posts?|home|search|explore|profile|more|options|back|close|add|reply"
    r"|see translation|see more|view all.*|\d+ ?[wdhm]|now|agora)\s*$", re.IGNORECASE)
# Texto curto demais é rótulo, não conteúdo. Uma legenda ou comentário de verdade não cabe em 15 caracteres.
_MIN_CONTEUDO = 15


def conteudo_visivel(tree: UiTree, *, limite: int = 600, max_linhas: int = 8) -> str:
    """O que está ESCRITO na tela agora: legenda da publicação, comentários, mensagem da conversa.

    Serve para o texto que o perfil vai escrever falar do que está ali, em vez de elogiar no vácuo. É leitura
    heurística de propósito: os ids do Instagram são ofuscados e mudam a cada versão, então filtrar por rótulo de
    interface e por tamanho envelhece melhor do que depender de um id que some na próxima atualização.

    Tela sensível (campo de senha) devolve vazio: nada dela sai daqui, nem para o modelo.

    O que sai daqui é DADO de terceiro — quem monta o prompt precisa marcar como tal, nunca como instrução.
    """
    if tree.sensitive:
        return ""
    linhas: list[str] = []
    vistos: set[str] = set()
    for e in tree.elements:
        texto = (e.text or "").strip()
        if len(texto) < _MIN_CONTEUDO or _CHROME.match(texto):
            continue
        chave = texto.lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        linhas.append(texto)
        if len(linhas) >= max_linhas:
            break
    saida = "\n".join(linhas)
    return saida[:limite].rstrip() if len(saida) > limite else saida


# A linha de um comentário chega como "autor said texto…" (a mesma forma que `COLLECT_COMMENTS` usa para recortar
# o autor). Aqui o interesse é o oposto: o TEXTO, já que o autor é quem se sabe de antemão.
def _padrao_do_autor(username: str) -> re.Pattern[str]:
    arroba = re.escape(username.strip().lstrip("@"))
    return re.compile(rf"^@?{arroba}\s+(?:said|disse|comentou)\s+(.+)$", re.IGNORECASE | re.DOTALL)


def comentario_de(tree: UiTree, username: str, *, limite: int = 400) -> str:
    """O que ESTA pessoa escreveu no comentário visível — e nada do que as outras escreveram.

    Responder sem ler o comentário é responder no escuro. Mas pegar "um texto qualquer da tela" seria pior: numa
    lista de comentários, atribuir a fala do vizinho a quem se está respondendo produz resposta sem sentido e,
    pior, memória falsa no nome da pessoa errada. Por isso o casamento exige o autor na MESMA linha do texto.

    Devolve vazio quando não há certeza — e vazio significa "escreva sem isto", nunca "invente".
    """
    # Testa o valor JÁ sem a arroba: "@" sozinho passaria no teste e produziria um padrão que casa com qualquer
    # linha "@ disse …" — fala de ninguém entrando como fala da contraparte, no único bloco de onde nasce memória.
    if tree.sensitive or not (username or "").strip().lstrip("@"):
        return ""
    padrao = _padrao_do_autor(username)
    for e in tree.elements:
        achado = padrao.match((e.text or "").strip())
        if achado:
            texto = achado.group(1).strip()
            return texto[:limite].rstrip() if len(texto) > limite else texto
    return ""


# Numa conversa aberta, a bolha da outra pessoa é anunciada pela acessibilidade com o autor junto do texto. As
# formas vistas em campo e nas traduções do app: "fulano said oi", "fulano disse oi", "Message from fulano: oi",
# "Mensagem de fulano: oi". Casar o AUTOR é a única maneira honesta de saber de quem é a fala — bolha sem autor
# pode ser desta própria conta, e atribuir à outra pessoa o que nós mesmos escrevemos é memória falsa no nome
# errado.
def _padroes_de_mensagem(username: str) -> tuple[re.Pattern[str], ...]:
    arroba = re.escape(username.strip().lstrip("@"))
    return (
        re.compile(rf"^@?{arroba}\s+(?:said|disse|sent|enviou|escreveu)\s*:?\s+(.+)$", re.IGNORECASE | re.DOTALL),
        re.compile(rf"^(?:message|mensagem)\s+(?:from|de)\s+@?{arroba}\s*[:\-]\s*(.+)$", re.IGNORECASE | re.DOTALL),
    )


def mensagem_de(tree: UiTree, username: str, *, limite: int = 400) -> str:
    """A ÚLTIMA fala desta pessoa na conversa aberta — e nada que esta conta tenha escrito.

    É o equivalente de `comentario_de` para o fio de mensagem direta. Sem ele, o caminho de DM nunca tinha lado
    de "recebido": o que a outra pessoa respondeu entrava só como texto de tela, e de tela não sai memória
    (achado #108). Com ele, mandar mensagem numa conversa em que ela acabou de falar vira RESPONDER.

    Duas decisões que valem mais que o formato:

    * **Exige o autor.** Sem atribuição explícita devolve vazio, e vazio quer dizer "escreva sem isto" — nunca
      "invente". Quem chama volta a puxar conversa, que é o comportamento de sempre.
    * **Pega a última, não a primeira.** A hierarquia vem de cima para baixo e a conversa também: a fala que
      pede resposta é a de baixo. `comentario_de` pega a primeira porque ali há UM comentário sendo respondido.

    Tela sensível devolve vazio, como todo o resto deste módulo: dali não sai nada, nem para o modelo.
    """
    if tree.sensitive or not (username or "").strip().lstrip("@"):
        return ""
    padroes = _padroes_de_mensagem(username)
    achado = ""
    for e in tree.elements:
        # `desc` entra junto com `text`: em bolha de conversa o autor costuma estar na descrição de
        # acessibilidade, e o texto visível é só a frase.
        for bruto in ((e.text or "").strip(), (e.desc or "").strip()):
            if not bruto:
                continue
            for padrao in padroes:
                if (m := padrao.match(bruto)) and (texto := m.group(1).strip()):
                    achado = texto
                    break
    return achado[:limite].rstrip() if len(achado) > limite else achado


def classify(tree: UiTree, *, package: str | None, locale: str | None = None) -> Classification:
    """Decide o que está na tela. Estrutura primeiro; texto localizado só desempata."""
    if package and package != PACKAGE:
        return Classification(Screen.UNKNOWN, f"outro app em primeiro plano ({package})")
    texto = _screen_text(tree)
    sig = signals(locale)

    # Challenge e 2FA vêm ANTES do login: eles também têm campo de entrada, e confundir os dois faria o sistema
    # tentar digitar a senha numa tela de código.
    if sig["challenge"].search(texto):
        return Classification(Screen.CHALLENGE, "a tela pede confirmação adicional", evidence=_hits(sig["challenge"], texto))
    if sig["two_factor"].search(texto):
        return Classification(Screen.TWO_FACTOR, "a tela pede código de dois fatores", evidence=_hits(sig["two_factor"], texto))
    if sig["save_login"].search(texto):
        return Classification(Screen.SAVE_LOGIN_PROMPT, "o app pergunta se quer salvar os dados de login")
    if sig["switcher"].search(texto):
        return Classification(Screen.ACCOUNT_SWITCHER, "seletor de contas aberto")

    form = login_form(tree, locale)
    if form is not None:
        return Classification(Screen.LOGIN, "há um campo de senha na tela", form=form)

    if not tree.elements:
        return Classification(Screen.LOADING, "tela ainda sem elementos")
    if sig["inbox"].search(texto):
        return Classification(Screen.INBOX, "caixa de mensagens")
    if observed_username(tree) and _has_id(tree, ("profile_header", "row_profile_header", "profile_tab")):
        return Classification(Screen.PROFILE, "tela de perfil")
    if _has_id(tree, ("feed_tab", "tab_bar", "main_tab_bar", "feed_timeline", "action_bar_inbox_button")):
        return Classification(Screen.FEED, "barra de navegação principal visível")
    return Classification(Screen.UNKNOWN, "nenhum sinal conhecido na tela")


def _hits(pattern: re.Pattern[str], texto: str) -> list[str]:
    return [m.group(0)[:80] for m in list(pattern.finditer(texto))[:3]]


def _has_id(tree: UiTree, sufixos: tuple[str, ...]) -> bool:
    for e in tree.elements:
        alvo = e.resource_id.rsplit("/", 1)[-1].lower()
        if any(alvo.startswith(s) for s in sufixos):
            return True
    return False
