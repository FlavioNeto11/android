"""O que aconteceu depois de tocar em Entrar.

Nunca se presume sucesso pelo retorno do Appium: observa-se de novo e classifica-se pelo que está na tela. E nunca
se repete o envio por causa de timeout — só uma falha COMPROVADA antes de qualquer efeito autoriza nova tentativa.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ...automation.hierarchy import UiTree
from . import navigation
from .navigation import Screen


#: Prefixo fixo do desfecho "o app mostrou erro genérico de login". Estável de propósito: diagnóstico e testes
#: procuram por ele.
LOGIN_ERROR_DETAIL = "login_error_dialog: o Instagram mostrou erro genérico de login, sem dizer a causa"


class Outcome(StrEnum):
    SESSION_READY = "session_ready"            # conta certa aberta
    INVALID_CREDENTIAL = "invalid_credential"  # a tela disse que a senha está errada
    AUTH_CHALLENGE = "auth_challenge"          # 2FA, captcha, confirmação: só uma pessoa resolve
    WRONG_ACCOUNT = "wrong_account"            # abriu, mas é outra conta
    RETRYABLE = "retryable"                    # falhou antes de qualquer efeito; pode tentar de novo
    UNCERTAIN = "uncertain"                    # não deu para saber; NÃO repete sozinho

    @property
    def terminal(self) -> bool:
        """Desfecho que nunca gera nova tentativa automática — é o que evita bloquear a conta."""
        return self in (Outcome.INVALID_CREDENTIAL, Outcome.AUTH_CHALLENGE, Outcome.WRONG_ACCOUNT)


@dataclass(slots=True)
class Verdict:
    outcome: Outcome
    detail: str
    observed_username: str | None = None
    screen: Screen = Screen.UNKNOWN


def classify_after_submit(tree: UiTree, *, package: str | None, expected_username: str,
                          locale: str | None = None) -> Verdict:
    """Lê a tela depois do envio e decide. `expected_username` é comparado sem diferenciar maiúsculas."""
    sig = navigation.signals(locale)
    texto = "\n".join(f"{e.text} {e.desc}".strip() for e in tree.elements if e.text or e.desc)
    classificacao = navigation.classify(tree, package=package, locale=locale)

    if sig["wrong_password"].search(texto):
        return Verdict(Outcome.INVALID_CREDENTIAL, "a tela informou senha incorreta", screen=classificacao.screen)
    if sig["user_not_found"].search(texto):
        return Verdict(Outcome.INVALID_CREDENTIAL, "a tela informou que a conta não existe", screen=classificacao.screen)

    if classificacao.screen in (Screen.CHALLENGE, Screen.TWO_FACTOR):
        return Verdict(Outcome.AUTH_CHALLENGE,
                       "o Instagram exige confirmação adicional; só uma pessoa pode resolver",
                       screen=classificacao.screen)

    if sig["login_error"].search(texto):
        # O app recusou com erro GENÉRICO. A causa não está na tela (pode ser rede, relógio, integridade do app,
        # bloqueio do lado do Instagram...): não se conta como senha errada nem se repete sozinho. O nome fixo no
        # detalhe é o que a fase local procura no histórico de tentativas.
        return Verdict(Outcome.UNCERTAIN, f"{LOGIN_ERROR_DETAIL} (tela: {classificacao.screen.value})",
                       screen=classificacao.screen)

    if classificacao.screen in (Screen.FEED, Screen.PROFILE, Screen.INBOX, Screen.SAVE_LOGIN_PROMPT):
        observado = navigation.observed_username(tree)
        if observado and observado.lower() != expected_username.lower():
            return Verdict(Outcome.WRONG_ACCOUNT, f"a conta aberta é @{observado}, e a esperada é "
                                                  f"@{expected_username}", observed_username=observado,
                           screen=classificacao.screen)
        if observado:
            return Verdict(Outcome.SESSION_READY, f"@{observado} confirmado na tela", observed_username=observado,
                           screen=classificacao.screen)
        # Entrou no app mas o username ainda não apareceu: quem confirma é a verificação, não este palpite.
        return Verdict(Outcome.UNCERTAIN, "o app abriu, mas a conta ainda não foi confirmada na tela",
                       screen=classificacao.screen)

    if classificacao.screen is Screen.LOGIN:
        # Continuar no login sem mensagem de erro costuma ser envio que não pegou, ou tela ainda carregando.
        return Verdict(Outcome.UNCERTAIN, "continuou na tela de login, sem mensagem de erro",
                       screen=classificacao.screen)

    return Verdict(Outcome.UNCERTAIN, f"não foi possível classificar a tela ({classificacao.reason})",
                   screen=classificacao.screen)
