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
                                r"|enter the code we sent|captcha|i'?m not a robot)", re.IGNORECASE),
        "save_login": re.compile(r"save (your )?login info", re.IGNORECASE),
        "save_dismiss": re.compile(r"^\s*not now\s*$", re.IGNORECASE),
        "wrong_password": re.compile(r"(incorrect password|password (you )?entered .* incorrect|wrong password)",
                                     re.IGNORECASE),
        "user_not_found": re.compile(r"(couldn'?t find|user not found|no account found)", re.IGNORECASE),
        "switcher": re.compile(r"(switch accounts?|log into another account)", re.IGNORECASE),
        "inbox": re.compile(r"^\s*(messages|direct)\s*$", re.IGNORECASE),
    },
    "pt": {
        "login_button": re.compile(r"^\s*(entrar|fazer login)\s*$", re.IGNORECASE),
        "facebook": re.compile(r"facebook", re.IGNORECASE),
        "two_factor": re.compile(r"(autentica[çc][ãa]o de dois fatores|c[óo]digo de seguran[çc]a|c[óo]digo de confirma)",
                                 re.IGNORECASE),
        "challenge": re.compile(r"(detectamos|suspeit|confirme que [ée] voc[êe]|ajude a confirmar|verifique sua conta"
                                r"|insira o c[óo]digo|captcha|n[ãa]o sou um rob[ôo])", re.IGNORECASE),
        "save_login": re.compile(r"salvar (suas )?informa[çc][õo]es de login", re.IGNORECASE),
        "save_dismiss": re.compile(r"^\s*agora n[ãa]o\s*$", re.IGNORECASE),
        "wrong_password": re.compile(r"(senha incorreta|senha .* incorreta)", re.IGNORECASE),
        "user_not_found": re.compile(r"(n[ãa]o foi poss[íi]vel encontrar|usu[áa]rio n[ãa]o encontrado)", re.IGNORECASE),
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


# Telas benignas que o Instagram intercala depois de entrar: dicas ("Got it") e passos de onboarding ("Skip").
# Só rótulos que SEGUEM SEM CONCEDER NADA entram aqui — nunca "Allow"/"Permitir"/"Next", que liberariam contatos,
# notificações ou sincronização. Casamento exato, nos dois idiomas.
_DISPENSAR = ("skip", "got it", "not now", "maybe later", "pular", "entendi", "agora não", "agora nao",
              "talvez mais tarde")


def dismiss_button(tree: UiTree) -> UiElement | None:
    """Botão que fecha uma tela intermediária benigna sem conceder permissão nenhuma."""
    for e in tree.elements:
        if e.clickable and f"{e.text} {e.desc}".strip().lower() in _DISPENSAR:
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
