"""Item 23.8 — o Outlook como DADO: `telas.yaml`, `sessao.yaml` e `catalogo.yaml` da pasta real, lidos pelo motor
genérico de sessão, sem uma linha de Python do Outlook (ADR-052, ADR-057).

As árvores falsas usam os ids e textos OBSERVADOS no android-10 (Outlook 5.2635.3, UiAutomator2, 30/09/2026) até a
página da senha: boas-vindas (`title`, `btn_primary_button` "Add account", `btn_secondary_button` "Create new
account"), "Add account" (`auto_complete_input_email`, `btn_add_google_account`, `btn_privacy_terms`,
`btn_primary_button` "Continue", `menu_qr_code`, `menu_help`), a espera (`common_auth_webview_progressbar`), o WebView
`common_auth_webview` com "Verify your email" (`identityBadge`/`bannerText`, `proof-confirmation-email-input`, "Send
code", "Already received a code?", "Use your password") e "Enter your password" (`back-button`, `passwordEntry`, "Show
password", "Next", "Send a code to <e-mail>").

SUPOSIÇÃO (não observado; o dublê segue o `telas.yaml`, e a observação real é do 29.12): o "Stay signed in?" com
"No"/"Yes", a caixa com "Inbox" e o botão `account_button` ("Open navigation drawer"), a gaveta com o e-mail em
`account_email`, e as páginas de desafio da Microsoft ("Help us protect your account", "Enter code"). O que o dublê
supõe e o Outlook real não fizer termina incerto no aparelho — nunca sucesso —, e é isso que os testes de tela
desconhecida conferem.

Nível de prova: `simulated` (dublês de aparelho; nenhum emulador, conta Microsoft ou IA real).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import quoteattr

import pytest
import yaml
from pydantic import SecretStr

from app.automation import conhecimento_de_telas as telas
from app.automation.hierarchy import parse_hierarchy
from app.db import Database
from app.events import EventBus
from app.integrations.app_declarado import conhecimento
from app.integrations.app_declarado import formulario as geometria
from app.integrations.app_declarado import sessao as sessao_mod
from app.integrations.app_declarado.conhecimento import SessaoInvalida
from app.integrations.app_declarado.pacote import PASTA_DOS_APPS, descobrir
from app.integrations.app_declarado.sessao import (ETAPA_DESAFIO_ANTES_DA_SENHA, ETAPA_IDENTIFICADOR_RECUSADO,
                                                   Outcome, SessaoDeclarada)
from app.models import ProfileAccountCreate, SessionStatus
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService

from .conftest import make_config
from .fake_instagram import PKG as INSTAGRAM
from .test_instagram_auth import FakeDevices, FakeRt, cadastrar
from .test_sessao_em_etapas import SESSAO as SESSAO_EM_ETAPAS
from .test_sessao_em_etapas import TELAS as TELAS_EM_ETAPAS

OUTLOOK = "com.microsoft.office.outlook"
CREDENCIAIS = "com.android.credentialmanager"   # o diálogo de chave de acesso do sistema (observado 30/09 android-06)
PASTA = PASTA_DOS_APPS / OUTLOOK
EMAIL = "pessoa@exemplo.com"          # endereço de exemplo; nunca uma conta real
MASCARADO = "pe*****@exemplo.com"
SENHA = "outlook-Teste#4"             # valor do dublê, não de conta nenhuma


def _dados(nome: str) -> dict[str, Any]:
    return yaml.safe_load((PASTA / nome).read_text(encoding="utf-8"))


def _conhecimento_rapido() -> conhecimento.ConhecimentoDeSessao:
    """O conhecimento LIDO DA PASTA REAL, só com os prazos encurtados (o dublê responde na hora). Nada do que o teste
    prova — sinais, telas, passos, desfechos — é mexido."""
    k = conhecimento.carregar(PASTA)
    return replace(k, ajustes=replace(k.ajustes, settle_s=0.01, submit_wait_s=2.0, open_timeout_s=0.5),
                   conta=replace(k.conta, espera_s=0.01))


@dataclass
class _No:
    cls: str
    bounds: tuple[int, int, int, int]
    text: str = ""
    rid: str = ""
    desc: str = ""
    clickable: bool = False
    password: bool = False
    acao: str = ""
    web: bool = False            # id do WebView: o id do HTML, sem o pacote (é como o UiAutomator2 o entrega)
    enabled: bool = True
    pkg: str = OUTLOOK

    @property
    def resource_id(self) -> str:
        if not self.rid or self.web:
            return self.rid
        return f"{self.pkg}:id/{self.rid}"


def _web(cls: str, bounds: tuple[int, int, int, int], **kw: Any) -> _No:
    return _No(cls, bounds, web=True, **kw)


@dataclass
class FakeOutlook:
    """O Outlook deslogado até a caixa. `tela`: boas_vindas, adicionar_conta, carregando, verificar, senha, manter,
    caixa, gaveta, travada, codigo, desconhecida, conta_inexistente.

    `oferece_senha`: a página "Verify your email" traz "Use your password". `direto_na_senha`: depois do "Continue" a
    Microsoft pede a senha sem propor código. `troca_lenta`: leituras em que a página de código continua na tela depois
    do toque em "Use your password" (o WebView demora). `apos_continuar` / `apos_senha`: a página que a Microsoft
    mostra depois do "Continue" (no lugar da de código) e depois do "Next" certo. `protecao_na_verificacao`: a página
    de código traz também o texto de conta segurada.

    Depois do "Next" certo (observado 30/09 android-06, `apos_senha="aviso"`, o padrão): aviso da conta Microsoft (o
    "OK" pede `toques_no_ok` toques) → diálogo de chave de acesso do SISTEMA (`chave`, outro pacote; sai pelo Voltar) →
    "Authentication in progress" → "Add another account" → privacidade → diagnóstico → experiências → caixa. Com
    `apos_senha="manter"`, o "Stay signed in?" suposto, direto para a caixa. `outra_conta_na_coluna` põe OUTRO e-mail
    na coluna de contas da gaveta, fora do `drawer_folder_composable`."""

    senha_aceita: str = SENHA
    conta: str | None = None
    tela: str = ""
    oferece_senha: bool = True
    direto_na_senha: bool = False
    troca_lenta: int = 0
    espera_da_pagina: int = 2
    apos_continuar: str = ""
    apos_senha: str = "aviso"
    toques_no_ok: int = 2
    espera_da_autenticacao: int = 3
    outra_conta_na_coluna: str = ""
    protecao_na_verificacao: bool = False
    duas_entradas: bool = False
    conta_existe: bool = True
    email: str = ""
    senha: str = ""
    erro: str = ""
    teclado: bool = False
    typed: list[tuple[str, str]] = field(default_factory=list)     # (campo em foco, texto): o valor fica no dublê
    calls: list[str] = field(default_factory=list)
    _foco: str = ""
    _espera: int = 0
    _lenta: int = 0
    _nos: list[_No] = field(default_factory=list)

    # ------------------------------------------------------------------ o que o motor lê do aparelho
    def current_package(self) -> str | None:
        return CREDENCIAIS if self.tela == "chave" else OUTLOOK

    def current_focus(self) -> tuple[str | None, str | None]:
        return (CREDENCIAIS, ".CredentialSelectorActivity") if self.tela == "chave" else (OUTLOOK, ".MainActivity")

    def getprop(self, name: str) -> str:
        return "en-US" if name == "ro.product.locale" else ""

    def start_app(self, package: str, activity: str | None = None) -> None:
        if self.tela:
            return                                      # o app volta na tela em que estava
        self.tela = "caixa" if self.conta else "boas_vindas"

    def _pagina(self, titulo: str, *resto: _No) -> list[_No]:
        """Uma página do WebView da Microsoft: o nó do WebView traz o título da página como texto (observado)."""
        return [_No("android.webkit.WebView", (0, 0, 720, 1280), text=titulo, rid="common_auth_webview"), *resto]

    def _identidade(self) -> list[_No]:
        return [_web("android.view.View", (100, 100, 680, 160), rid="identityBadge", clickable=True, acao="trocar"),
                _web("android.widget.TextView", (140, 110, 600, 150), text=self.email, rid="bannerText")]

    def _montar(self) -> list[_No]:
        t = self.tela
        if t == "boas_vindas":
            extra = [_No("android.widget.Button", (40, 900, 680, 970), text="Add account", clickable=True,
                         acao="entrada")] if self.duas_entradas else []
            return [_No("android.widget.TextView", (40, 300, 680, 360), text="Welcome to Outlook", rid="title"),
                    *extra,
                    _No("android.widget.Button", (40, 1000, 680, 1070), text="Add account", rid="btn_primary_button",
                        clickable=True, acao="entrada"),
                    _No("android.widget.Button", (40, 1090, 680, 1160), text="Create new account",
                        rid="btn_secondary_button", clickable=True, acao="criar")]
        if t == "adicionar_conta":
            # O "Continue" sobe acima do teclado quando ele abre (observado).
            continuar = (40, 700, 680, 770) if self.teclado else (40, 1180, 680, 1250)
            return [_No("android.widget.TextView", (100, 40, 500, 110), text="Add account"),
                    _No("android.widget.ImageButton", (560, 40, 620, 110), rid="menu_qr_code", clickable=True,
                        acao="qr"),
                    _No("android.widget.ImageButton", (630, 40, 700, 110), rid="menu_help", clickable=True,
                        acao="ajuda"),
                    _No("android.widget.AutoCompleteTextView", (40, 200, 680, 270),
                        text=self.email or "Enter your email", rid="auto_complete_input_email", clickable=True,
                        acao="foco:email"),
                    _No("android.widget.Button", (40, 320, 680, 390), text="Add Google account",
                        rid="btn_add_google_account", clickable=True, acao="google"),
                    _No("android.widget.Button", (40, 1100, 680, 1150), rid="btn_privacy_terms", clickable=True,
                        acao="termos"),
                    # Observado em 30/09 (android-06): o "Continue" fica DESABILITADO até haver e-mail no campo, e
                    # traz o texto repetido na descrição ("Continue Continue").
                    _No("android.widget.Button", continuar, text="Continue", desc="Continue", rid="btn_primary_button",
                        clickable=True, acao="continuar", enabled=bool(self.email))]
        if t == "carregando":
            self._espera -= 1
            if self._espera <= 0:
                self.tela = self._depois_da_espera()
            return [_No("android.widget.ProgressBar", (310, 600, 410, 700), rid="common_auth_webview_progressbar")]
        if t == "verificar":
            if self._lenta > 0:
                self._lenta -= 1
                if self._lenta == 0:
                    self.tela = "senha"
            protecao = [_web("android.widget.TextView", (40, 180, 680, 200),
                             text="Help us protect your account")] if self.protecao_na_verificacao else []
            senha = [_web("android.widget.Button", (40, 740, 680, 800), text="Use your password", clickable=True,
                          acao="usar_senha")] if self.oferece_senha else []
            return self._pagina(
                "Verify your email", *self._identidade(), *protecao,
                _web("android.widget.TextView", (40, 200, 680, 260), text="Verify your email"),
                _web("android.widget.TextView", (40, 280, 680, 380),
                     text=f"We'll send a code to {MASCARADO} To verify this is your email, enter it here."),
                _web("android.widget.EditText", (40, 420, 680, 490), rid="proof-confirmation-email-input",
                     clickable=True, acao="foco:prova"),
                _web("android.widget.Button", (40, 560, 680, 630), text="Send code", clickable=True,
                     acao="enviar_codigo"),
                _web("android.widget.Button", (40, 660, 680, 720), text="Already received a code?", clickable=True,
                     acao="ja_recebi"),
                *senha,
                _web("android.widget.TextView", (40, 1150, 300, 1190), text="Help and feedback", clickable=True),
                _web("android.widget.TextView", (320, 1150, 500, 1190), text="Terms of use", clickable=True),
                _web("android.widget.TextView", (520, 1150, 700, 1190), text="Privacy and cookies", clickable=True))
        if t == "senha":
            erro = [_web("android.widget.TextView", (40, 440, 680, 490), text=self.erro)] if self.erro else []
            return self._pagina(
                "Enter your password",
                _web("android.widget.Button", (20, 100, 90, 160), text="Back", rid="back-button", clickable=True,
                     acao="voltar"),
                *self._identidade(),
                _web("android.widget.TextView", (40, 200, 680, 260), text="Enter your password"),
                _web("android.widget.EditText", (40, 300, 680, 370), text="••••" if self.senha else "",
                     rid="passwordEntry", clickable=True, password=True, acao="foco:senha"),
                *erro,
                _web("android.widget.Button", (560, 380, 680, 430), text="Show password", clickable=True,
                     acao="mostrar"),
                _web("android.widget.Button", (420, 500, 680, 570), text="Next", clickable=True, acao="enviar"),
                _web("android.widget.Button", (40, 600, 680, 660), text=f"Send a code to {self.email}",
                     clickable=True, acao="enviar_codigo"))
        if t == "conta_inexistente":
            return self._pagina(
                "Sign in",
                _web("android.widget.TextView", (40, 200, 680, 260), text="Sign in"),
                _web("android.widget.TextView", (40, 280, 680, 380),
                     text="That Microsoft account doesn't exist. Enter a different account or get a new one."),
                _web("android.widget.EditText", (40, 420, 680, 490), text=self.email, rid="i0116", clickable=True),
                _web("android.widget.Button", (420, 560, 680, 630), text="Next", clickable=True, acao="avancar_web"))
        if t == "travada":
            return self._pagina(
                "Help us protect your account",
                *self._identidade(),
                _web("android.widget.TextView", (40, 200, 680, 260), text="Help us protect your account"),
                _web("android.widget.Button", (420, 560, 680, 630), text="Next", clickable=True, acao="proteger"),
                # Um botão com o rótulo da alternativa numa página de conta segurada: nunca é tocado.
                _web("android.widget.Button", (40, 740, 680, 800), text="Use your password", clickable=True,
                     acao="usar_senha"))
        if t == "codigo":
            return self._pagina(
                "Enter code",
                *self._identidade(),
                _web("android.widget.TextView", (40, 200, 680, 260), text="Enter code"),
                _web("android.widget.TextView", (40, 280, 680, 380),
                     text=f"We emailed a code to {MASCARADO}. Please enter the code to sign in."),
                _web("android.widget.EditText", (40, 420, 680, 490), rid="idTxtBx_OTC_Password", clickable=True),
                _web("android.widget.Button", (420, 560, 680, 630), text="Verify", clickable=True, acao="verificar"))
        if t == "manter":
            return self._pagina(
                "Stay signed in?",
                *self._identidade(),
                _web("android.widget.TextView", (40, 200, 680, 260), text="Stay signed in?"),
                _web("android.widget.CheckBox", (40, 400, 680, 450), text="Don't show this again", clickable=True,
                     acao="nao_mostrar"),
                _web("android.widget.Button", (40, 900, 340, 960), text="No", clickable=True, acao="nao"),
                _web("android.widget.Button", (380, 900, 680, 960), text="Yes", clickable=True, acao="sim"))
        if t == "desconhecida":
            return [_No("android.widget.TextView", (40, 300, 680, 360), text="Getting things ready"),
                    _No("android.widget.Button", (40, 1000, 680, 1070), text="Continue", clickable=True,
                        acao="seguir")]
        # ---- depois do "Next": a sequência observada em 30/09 no android-06 ----
        if t == "aviso":
            return self._pagina(
                "Microsoft account notice",
                _web("android.view.View", (0, 0, 720, 1280), rid="app-host"),
                _web("android.view.View", (0, 60, 720, 1100), rid="scrollDiv"),
                _web("android.view.View", (0, 60, 720, 1100), rid="interruptContainer"),
                _web("android.widget.TextView", (40, 120, 680, 180), text="A quick note about your Microsoft account"),
                _web("android.widget.TextView", (40, 220, 680, 260), text="Your important things are right here"),
                _web("android.widget.TextView", (40, 400, 680, 440), text="Your privacy is our priority"),
                _web("android.widget.TextView", (40, 580, 680, 620), text="You're in control"),
                _web("android.widget.TextView", (40, 900, 300, 940), text="Learn more", clickable=True,
                     acao="saiba_mais"),
                _web("android.view.View", (0, 1100, 720, 1280), rid="StickyFooter"),
                _web("android.widget.Button", (420, 1160, 680, 1230), text="OK", clickable=True, acao="ok"))
        if t == "chave":
            # O diálogo do SISTEMA: outro pacote. O "Continue" criaria a chave de acesso — nunca é tocado.
            return [_No("android.widget.TextView", (40, 700, 680, 760), text="Safer with passkeys", pkg=CREDENCIAIS),
                    _No("android.widget.TextView", (40, 770, 680, 860), pkg=CREDENCIAIS,
                        text="With passkeys, you don't need to create or remember complex passwords."),
                    _No("android.widget.Button", (40, 1100, 300, 1170), text="Learn more", clickable=True,
                        acao="saiba_mais", pkg=CREDENCIAIS),
                    _No("android.widget.Button", (420, 1100, 680, 1170), text="Continue", desc="Continue",
                        clickable=True, acao="criar_chave", pkg=CREDENCIAIS)]
        if t == "autenticando":
            self._espera -= 1
            if self._espera <= 0:
                self.tela = "outra_conta"
            return [_No("android.widget.TextView", (40, 600, 680, 660), text="Authentication in progress",
                        rid="message")]
        if t == "outra_conta":
            return [_No("android.widget.TextView", (100, 40, 600, 110), text="Add another account"),
                    _No("android.widget.TextView", (40, 400, 680, 460), text="Would you like to add another account?",
                        rid="empty_state_title"),
                    _No("android.widget.Button", (40, 1160, 340, 1230), text="MAYBE LATER",
                        rid="bottom_flow_navigation_start_button", clickable=True, acao="talvez_depois"),
                    _No("android.widget.Button", (380, 1160, 680, 1230), text="ADD",
                        rid="bottom_flow_navigation_end_button", clickable=True, acao="adicionar_outra")]
        if t == "privacidade":
            return [_No("android.widget.TextView", (40, 500, 680, 560), text="Your Data, Your Way",
                        rid="illustration_detail_title"),
                    _No("android.widget.TextView", (40, 580, 680, 700), rid="illustration_detail_description",
                        text="We've updated Outlook's privacy settings to give you more control."),
                    _No("android.widget.Button", (380, 1160, 680, 1230), text="NEXT",
                        rid="bottom_flow_navigation_end_button", clickable=True, acao="privacidade_next")]
        if t == "diagnostico":
            return [_No("android.widget.TextView", (40, 500, 680, 560), text="Getting Better Together",
                        rid="illustration_detail_title"),
                    _No("android.widget.TextView", (40, 580, 680, 700), rid="illustration_detail_description",
                        text="We'd like additional diagnostic and usage data sent to us to improve Outlook."),
                    _No("android.widget.Button", (40, 1080, 680, 1150), text="Accept", rid="btn_primary_button",
                        clickable=True, acao="aceitar"),
                    _No("android.widget.Button", (40, 1160, 680, 1230), text="Decline", rid="btn_secondary_button",
                        clickable=True, acao="recusar")]
        if t == "experiencias":
            return [_No("android.widget.TextView", (40, 500, 680, 560), text="Powering Your Experiences",
                        rid="illustration_detail_title"),
                    _No("android.widget.TextView", (40, 580, 680, 700), rid="illustration_detail_description",
                        text="Outlook includes experiences that connect to online services."),
                    _No("android.widget.Button", (40, 1160, 680, 1230), text="CONTINUE TO OUTLOOK",
                        rid="bottom_flow_navigation_end_button", clickable=True, acao="continuar_no_outlook")]
        # O botão do canto não tem descrição (observado): só o id.
        avatar = _No("android.widget.ImageButton", (20, 40, 100, 120), rid="account_button", clickable=True,
                     acao="gaveta")
        caixa = [avatar,
                 _No("android.widget.TextView", (120, 50, 400, 110), text="Inbox"),
                 _No("androidx.compose.ui.platform.ComposeView", (0, 130, 720, 1150), rid="conversation_list"),
                 # Um remetente sem nome mostra o endereço, e um assunto é "Verify your email" (SUPOSIÇÃO do conteúdo).
                 _No("android.widget.TextView", (40, 150, 680, 190), text="noreply@servico.exemplo"),
                 _No("android.widget.TextView", (40, 190, 680, 230), text="Verify your email"),
                 _No("android.widget.TextView", (40, 260, 680, 300), text="Equipe Exemplo"),
                 _No("android.widget.TextView", (40, 300, 680, 340), text="Seu pedido chegou"),
                 _No("android.widget.TextView", (40, 1180, 200, 1230), text="Mail", rid="label"),
                 _No("android.widget.TextView", (260, 1180, 460, 1230), text="Calendar", rid="label"),
                 _No("android.widget.TextView", (520, 1180, 700, 1230), text="Apps", rid="menu_more")]
        if t == "gaveta":
            # Por cima da caixa, que continua na árvore. A conta é o TextView SEM id abaixo de "Outlook.com", dentro do
            # `drawer_folder_composable` (bounds observados); a coluna da esquerda pode mostrar outras contas.
            coluna = [_No("android.widget.ImageView", (10, 200, 150, 280), desc=self.outra_conta_na_coluna,
                          clickable=True, acao="trocar_conta")] if self.outra_conta_na_coluna else []
            return [*caixa,
                    _No("android.widget.FrameLayout", (0, 0, 620, 1280), rid="drawer_content"),
                    _No("android.widget.LinearLayout", (0, 60, 160, 1280), rid="account_navigation_view"),
                    *coluna,
                    _No("android.widget.ImageButton", (10, 1000, 150, 1080), rid="btn_add_account", clickable=True,
                        acao="outra_conta"),
                    _No("android.widget.ImageButton", (10, 1100, 150, 1180), rid="action_help", clickable=True),
                    _No("android.widget.ImageButton", (10, 1190, 150, 1270), rid="action_settings", clickable=True),
                    _No("androidx.compose.ui.platform.ComposeView", (160, 60, 620, 1280),
                        rid="drawer_folder_composable"),
                    _No("android.widget.TextView", (177, 85, 358, 123), text="Outlook.com"),
                    _No("android.widget.TextView", (177, 123, 605, 156), text=self.conta or ""),
                    *[_No("android.widget.TextView", (177, 200 + 60 * i, 605, 250 + 60 * i), text=pasta,
                          clickable=True)
                      for i, pasta in enumerate(("Favorites", "Inbox", "Sent", "Drafts", "Archive", "Deleted",
                                                 "Conversation History", "Junk"))]]
        return caixa

    def _depois_da_espera(self) -> str:
        if not self.conta_existe:
            return "conta_inexistente"
        if self.apos_continuar:
            return self.apos_continuar
        return "senha" if self.direto_na_senha else "verificar"

    def page_source(self) -> str:
        self._nos = self._montar()
        linhas = "".join(
            f'<node class={quoteattr(n.cls)} package="{n.pkg}" text={quoteattr(n.text)} '
            f'resource-id={quoteattr(n.resource_id)} content-desc={quoteattr(n.desc)} '
            f'clickable="{str(n.clickable).lower()}" enabled="{str(n.enabled).lower()}" focused="false" '
            f'password="{str(n.password).lower()}" scrollable="false" '
            f'bounds="[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]" />' for n in self._nos)
        return f'<hierarchy rotation="0">{linhas}</hierarchy>'

    # ------------------------------------------------------------------ o que o motor faz no aparelho
    def tap(self, x: int, y: int) -> None:
        self.calls.append(f"tap:{x},{y}")
        # O de cima vence: o nó do WebView cobre a página inteira, e o toque é no botão dentro dele.
        alvo = next((n for n in reversed(self._nos) if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                     and n.bounds[1] <= y <= n.bounds[3]), None)
        if alvo is None:
            return
        self.calls.append(f"toque:{alvo.acao or alvo.text}")
        seguinte = {"talvez_depois": "privacidade", "privacidade_next": "diagnostico", "recusar": "experiencias",
                    "continuar_no_outlook": "caixa"}
        if alvo.acao.startswith("foco:"):
            self._foco = alvo.acao.split(":", 1)[1]
            self.teclado = True
        elif alvo.acao == "entrada":
            self.tela = "adicionar_conta"
        elif alvo.acao == "continuar":
            self.tela, self._espera, self._foco, self.teclado = "carregando", self.espera_da_pagina, "", False
        elif alvo.acao == "usar_senha":
            if self.troca_lenta:
                self._lenta = self.troca_lenta
            else:
                self.tela = "senha"
        elif alvo.acao == "enviar":
            self.calls.append("enviar")
            if self.senha != self.senha_aceita:
                self.erro, self.senha = "Your account or password is incorrect.", ""
                return
            self.conta, self.senha, self.tela = self.email, "", self.apos_senha
        elif alvo.acao == "ok":
            # Observado: o primeiro toque só rolou a página e deu foco ao botão; o segundo avançou.
            self.toques_no_ok -= 1
            if self.toques_no_ok <= 0:
                self.tela = "chave"
        elif alvo.acao in seguinte:
            self.tela = seguinte[alvo.acao]
        elif alvo.acao in ("nao", "sim"):
            self.tela = "caixa"
        elif alvo.acao == "gaveta":
            self.tela = "gaveta"

    def type_text(self, text: str, *, clear_first: bool) -> None:
        if text:
            self.typed.append((self._foco, text))
        if self._foco == "senha":
            self.senha = text if clear_first else self.senha + text
        elif self._foco == "email":
            self.email = text if clear_first else self.email + text

    def press_key(self, key: str) -> None:
        self.calls.append(f"key:{key}")
        if key != "back":
            return
        if self.tela == "gaveta":
            self.tela = "caixa"
        elif self.tela == "chave":
            # O Voltar fecha o diálogo sem criar a chave; o Outlook conclui a autenticação.
            self.tela = "autenticando" if self.espera_da_autenticacao else "outra_conta"
            self._espera = self.espera_da_autenticacao


@dataclass
class Montado:
    sessao: SessaoDeclarada
    repo: SocialRepository
    db: Database
    pid: str
    conta: str


@pytest.fixture
def outlook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """O motor genérico com o conhecimento da pasta REAL do Outlook, sobre banco e cofre de verdade."""
    monkeypatch.setattr(sessao_mod, "OBSERVAR_DEPOIS_DO_ENVIO_S", 0.02)
    k = _conhecimento_rapido()
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('outlook', 'Microsoft Outlook', ?, 0)",
               (OUTLOOK,))
    bus = EventBus(db)
    secrets = SecretStore(db, MemoryKeyProvider())
    repo = SocialRepository(db)
    social = SocialService(repo, secrets, bus, known_instances=lambda: ["android-01", "android-02"])

    def montar(app: FakeOutlook, *, mascaramento: bool = True) -> Montado:
        sessao = SessaoDeclarada(k, cfg, FakeDevices(app), repo, secrets,  # type: ignore[arg-type]
                                 SensitiveInputChannel(lambda: mascaramento), bus)
        sessao.focus_poll_s = 0.01
        pid = cadastrar(social, username="ana.ancora", senha="ancora-Senha#3", instance_id="android-02")
        conta = social.add_account(pid, ProfileAccountCreate(app_id="outlook", login_identifier=EMAIL,
                                                             password=SecretStr(SENHA), consent=True)).id
        return Montado(sessao, repo, db, pid, conta)

    yield montar
    db.close()


def _tentativas(m: Montado) -> list[dict[str, Any]]:
    return [dict(r) for r in m.db.query("SELECT stage, outcome, detail FROM authentication_attempts"
                                        " WHERE account_id=? ORDER BY id", (m.conta,))]


def _credencial(m: Montado) -> str:
    return str(m.repo.account_credential_row(m.pid, m.conta)["status"])


def _sessao(m: Montado) -> dict[str, Any]:
    return dict(m.repo.account_session_row(m.pid, m.conta, "android-02"))


def _perfil(m: Montado) -> str:
    return str(m.db.one("SELECT status FROM instagram_profiles WHERE id=?", (m.pid,))["status"])


def _nunca_tocados(app: FakeOutlook) -> set[str]:
    """Os toques que o login NUNCA dá: criar conta, conta Google, pedir código, trocar de conta, manter conectado,
    criar chave de acesso, adicionar outra conta, aceitar o diagnóstico opcional, "saiba mais"."""
    return {f"toque:{a}" for a in ("criar", "google", "enviar_codigo", "ja_recebi", "trocar", "sim", "outra_conta",
                                   "qr", "termos", "criar_chave", "adicionar_outra", "aceitar", "saiba_mais",
                                   "trocar_conta")} & set(app.calls)


# ==================================================================== o pacote
def test_a_pasta_do_outlook_traz_o_login_gerenciado_sem_ser_ancora() -> None:
    manifestos = {m.definition.package: m for m in descobrir(PASTA_DOS_APPS)}
    m = manifestos[OUTLOOK]
    assert m.definition.session_provider == "microsoft" and m.session is not None
    assert not m.definition.profile_anchor and [p for p, x in manifestos.items() if x.definition.profile_anchor] == [
        INSTAGRAM]
    # Um tipo de provedor por app: o registro acha o pacote pelo tipo.
    tipos = [x.definition.session_provider for x in manifestos.values() if x.definition.session_provider]
    assert len(tipos) == len(set(tipos))


def test_o_conhecimento_declara_o_login_em_etapas_com_entrada_e_alternativa() -> None:
    k = conhecimento.do_app(OUTLOOK)
    etapa = k.formulario.etapa_do_usuario
    assert etapa is not None and etapa.tela == "adicionar_conta"
    assert etapa.entrada is not None and etapa.entrada.tela == "boas_vindas"
    assert [(a.tela, a.sinal_do_botao) for a in etapa.alternativas] == [("verificar_email", "usar_senha")]
    assert k.conta.acesso is not None and k.conta.ler_ao_entrar and k.navegador is None


# ==================================================================== o caminho observado
async def test_login_percorre_as_telas_observadas_e_confirma_pela_gaveta(outlook: Any) -> None:
    """A sequência inteira observada em 30/09 (android-10 até a senha, android-06 depois): boas-vindas → "Add account"
    → e-mail → "Continue" → espera → "Verify your email" → "Use your password" → senha pelo canal sensível na página
    que mostra ESTE e-mail → "Next" → aviso da conta ("OK", dois toques) → chave de acesso do sistema (Voltar) →
    "Authentication in progress" → "Add another account" ("MAYBE LATER") → privacidade ("NEXT") → diagnóstico
    ("Decline") → experiências ("CONTINUE TO OUTLOOK") → caixa → gaveta pelo `account_button` → o e-mail."""
    app = FakeOutlook()
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert r.observed_username == EMAIL and r.attempted_login
    # O e-mail no campo dele, a senha UMA vez no `passwordEntry`; nada mais digitado em lugar nenhum.
    assert app.typed == [("email", EMAIL), ("senha", SENHA)]
    ordem = ["toque:entrada", "toque:continuar", "toque:usar_senha", "toque:enviar", "toque:ok", "key:back",
             "toque:talvez_depois", "toque:privacidade_next", "toque:recusar", "toque:continuar_no_outlook",
             "toque:gaveta"]
    passos = [c for c in app.calls if c.startswith(("toque:", "key:")) and not c.startswith("toque:foco:")]
    assert [p for p in passos if p != "toque:ok"] == [p for p in ordem if p != "toque:ok"]
    assert app.calls.count("toque:ok") == 2                           # o primeiro só deu foco ao botão (observado)
    assert passos.index("toque:enviar") < passos.index("toque:ok") < passos.index("key:back")
    assert app.calls.count("toque:usar_senha") == 1 and app.calls.count("enviar") == 1
    assert _nunca_tocados(app) == set()
    assert app.tela == "gaveta"
    assert _sessao(m)["status"] == SessionStatus.session_ready.value
    assert _credencial(m) == "active" and [t["stage"] for t in _tentativas(m)] == ["classified"]
    assert all(SENHA not in (t["detail"] or "") for t in _tentativas(m))


async def test_o_stay_signed_in_suposto_e_recusado_com_no(outlook: Any) -> None:
    """SUPOSIÇÃO (não apareceu no login observado): se a Microsoft perguntar "Stay signed in?", a resposta é "No"."""
    app = FakeOutlook(apos_senha="manter")
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert "toque:nao" in app.calls and _nunca_tocados(app) == set()


async def test_a_sequencia_retomada_no_meio_segue_pela_leitura_da_conta(outlook: Any) -> None:
    """O app reaberto no meio do primeiro uso (logado, num informativo): a verificação da conta atravessa pelas mesmas
    dispensas declaradas, sem digitar nada, até a gaveta."""
    app = FakeOutlook(conta=EMAIL, tela="privacidade")
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.ready and not r.attempted_login and app.typed == []
    assert "toque:privacidade_next" in app.calls and "toque:recusar" in app.calls
    assert _nunca_tocados(app) == set() and app.tela == "gaveta"


async def test_dialogo_de_chave_de_acesso_na_reabertura_sai_pelo_voltar(outlook: Any) -> None:
    app = FakeOutlook(conta=EMAIL, tela="chave", espera_da_autenticacao=0)
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.ready, r.detail
    assert "key:back" in app.calls and "toque:criar_chave" not in app.calls


async def test_gaveta_com_outra_conta_na_coluna_le_so_a_do_painel(outlook: Any) -> None:
    """A coluna de contas à esquerda (fora do `drawer_folder_composable`) pode mostrar outra conta: ela não conta."""
    app = FakeOutlook(conta=EMAIL, tela="caixa", outra_conta_na_coluna="outra@exemplo.com")
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.ready and r.observed_username == EMAIL
    assert "toque:trocar_conta" not in app.calls


async def test_a_microsoft_pedindo_a_senha_direto_dispensa_a_alternativa(outlook: Any) -> None:
    app = FakeOutlook(direto_na_senha=True)
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert "toque:usar_senha" not in app.calls and app.typed == [("email", EMAIL), ("senha", SENHA)]


async def test_a_pagina_de_codigo_que_demora_a_trocar_nao_vira_desafio_nem_segundo_toque(outlook: Any) -> None:
    """Depois do "Use your password" o WebView leva um instante: a mesma página, com o botão, NÃO é julgada desafio
    (seria a pessoa chamada à toa) e o botão não é tocado de novo."""
    app = FakeOutlook(troca_lenta=3)
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert app.calls.count("toque:usar_senha") == 1


async def test_logado_le_a_conta_pela_gaveta_sem_digitar_nada(outlook: Any) -> None:
    app = FakeOutlook(conta=EMAIL)
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.ready and not r.attempted_login and app.typed == []
    assert app.tela == "gaveta" and "toque:gaveta" in app.calls


async def test_verificar_conta_nas_boas_vindas_so_observa(outlook: Any) -> None:
    """"Verificar conta" (só leitura) nas boas-vindas: deslogado, e nem o "Add account" é tocado."""
    app = FakeOutlook()
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid, observe_only=True)  # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "deslogado" in r.detail
    assert not any(c.startswith("toque:") for c in app.calls) and app.typed == []


async def test_duas_entradas_iguais_nao_tocam_em_nenhuma(outlook: Any) -> None:
    app = FakeOutlook(duas_entradas=True)
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "botão único" in r.detail
    assert not any(c.startswith("toque:") for c in app.calls) and _tentativas(m) == []


# ==================================================================== desafio: da pessoa
async def test_verificar_email_sem_a_senha_como_opcao_e_desafio(outlook: Any) -> None:
    """Sem "Use your password" a página é de código: vai para a pessoa. Nada é pedido ("Send code" nunca é tocado), a
    senha não sai, o teto diário não anda, e a trava para só a conta Outlook — a persona segue (item 23.5)."""
    app = FakeOutlook(oferece_senha=False)
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
    assert app.typed == [("email", EMAIL)] and "enviar" not in app.calls
    assert _nunca_tocados(app) == set()
    assert [t["stage"] for t in _tentativas(m)] == [ETAPA_DESAFIO_ANTES_DA_SENHA]
    assert _sessao(m)["status"] == SessionStatus.auth_challenge.value
    assert m.sessao._teto_diario(m.sessao._resolver_conta(m.pid, None)) is None  # noqa: SLF001
    assert _perfil(m) != "blocked"


async def test_conta_segurada_depois_do_continue_nao_toca_em_nada(outlook: Any) -> None:
    """"Help us protect your account" é conta travada (ADR-055): nem um botão com o rótulo da alternativa é tocado."""
    app = FakeOutlook(apos_continuar="travada")
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
    assert "toque:usar_senha" not in app.calls and "toque:proteger" not in app.calls
    assert app.typed == [("email", EMAIL)]
    assert _perfil(m) != "blocked"                                    # o Outlook não é o app âncora


async def test_pagina_de_codigo_com_texto_de_conta_segurada_nao_escolhe_a_senha(outlook: Any) -> None:
    app = FakeOutlook(protecao_na_verificacao=True)
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
    assert "toque:usar_senha" not in app.calls and app.typed == [("email", EMAIL)]


async def test_codigo_depois_da_senha_e_desafio(outlook: Any) -> None:
    app = FakeOutlook(apos_senha="codigo")
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
    assert app.calls.count("enviar") == 1 and "toque:verificar" not in app.calls
    assert _sessao(m)["status"] == SessionStatus.auth_challenge.value and _perfil(m) != "blocked"


# ==================================================================== a senha: só pelo canal, só onde é desta conta
async def test_sem_mascaramento_comprovado_a_senha_nao_sai(outlook: Any) -> None:
    """O campo de senha só recebe a senha pelo canal sensível; sem ele, nada vai ao `passwordEntry` e o "Next" não é
    tocado."""
    app = FakeOutlook()
    m = outlook(app, mascaramento=False)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.RETRYABLE
    assert app.typed == [("email", EMAIL)] and "enviar" not in app.calls
    assert [t["stage"] for t in _tentativas(m)] == ["sensitive_channel_blocked"]


async def test_senha_errada_pela_tabela_declarada(outlook: Any) -> None:
    app = FakeOutlook(senha_aceita="outra")
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.INVALID_CREDENTIAL and "senha incorreta" in r.detail
    assert _credencial(m) == "invalid" and app.calls.count("enviar") == 1


async def test_email_sem_conta_microsoft_para_sem_digitar_a_senha(outlook: Any) -> None:
    app = FakeOutlook(conta_existe=False)
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.INVALID_CREDENTIAL and "não há conta" in r.detail
    assert app.typed == [("email", EMAIL)] and "enviar" not in app.calls
    assert [t["stage"] for t in _tentativas(m)] == [ETAPA_IDENTIFICADOR_RECUSADO]


# ==================================================================== o desconhecido nunca é sucesso
async def test_tela_desconhecida_depois_da_senha_nao_e_sucesso(outlook: Any) -> None:
    """Depois do "Next" nada foi observado: uma tela que o `telas.yaml` não conhece termina incerta e para o login
    automático até uma pessoa olhar (ADR-055) — o "Continue" dela nunca é tocado."""
    app = FakeOutlook(apos_senha="desconhecida")
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and r.outcome is not Outcome.SESSION_READY
    assert "toque:seguir" not in app.calls and app.calls.count("enviar") == 1
    assert _sessao(m)["status"] != SessionStatus.session_ready.value
    assert _credencial(m) == "review"


def test_a_caixa_nao_e_desafio_nem_mostra_a_conta() -> None:
    """Na caixa, o assunto "Verify your email" não é a página de código (falta o "Send code" da mesma página) e o
    endereço de um remetente não é lido como a conta: os ids da extração são os da gaveta."""
    k = conhecimento.do_app(OUTLOOK)
    arvore = parse_hierarchy(FakeOutlook(conta=EMAIL, tela="caixa").page_source())
    r = k.reconhecer(arvore, package=OUTLOOK, locale="en-US")
    assert r.tela == "caixa_de_entrada" and r.trava is None
    assert telas.detectar_conta_travada(arvore, k.telas) is None
    assert k.conta_observada(arvore) is None
    gaveta = parse_hierarchy(FakeOutlook(conta=EMAIL, tela="gaveta").page_source())
    assert k.reconhecer(gaveta, package=OUTLOOK).tela == "conta_aberta" and k.conta_observada(gaveta) == EMAIL


def test_paginas_de_autenticacao_nao_confirmam_a_conta() -> None:
    """O e-mail do `bannerText` diz para QUEM é a senha, mas não que o Outlook abriu a conta: nenhuma página do WebView
    serve de leitura da conta."""
    k = conhecimento.do_app(OUTLOOK)
    for tela in ("verificar", "senha", "manter"):
        arvore = parse_hierarchy(FakeOutlook(tela=tela, email=EMAIL).page_source())
        assert k.conta_observada(arvore) is None, tela
        assert geometria.mostra_o_identificador(arvore, EMAIL), tela


@pytest.mark.parametrize(("tela", "esperada", "tipo"), [
    ("boas_vindas", "boas_vindas", "login"), ("adicionar_conta", "adicionar_conta", "login"),
    ("carregando", "carregando_autenticacao", "carregando"), ("verificar", "verificar_email", "dois_fatores"),
    ("senha", "senha", "login"), ("travada", "conta_travada", "desafio"), ("codigo", "codigo", "dois_fatores"),
    ("manter", "manter_conectado", "intersticial"), ("desconhecida", telas.DESCONHECIDA, telas.DESCONHECIDA),
    # Observadas 30/09 no android-06, depois do "Next".
    ("aviso", "aviso_da_conta", "intersticial"), ("autenticando", "autenticando", "carregando"),
    ("outra_conta", "outra_conta", "intersticial"), ("privacidade", "privacidade", "intersticial"),
    ("diagnostico", "diagnostico", "intersticial"), ("experiencias", "experiencias", "intersticial"),
    ("caixa", "caixa_de_entrada", "autenticada"), ("gaveta", "conta_aberta", "autenticada"),
])
def test_cada_tela_do_dublê_e_reconhecida_pelo_dado(tela: str, esperada: str, tipo: str) -> None:
    k = conhecimento.do_app(OUTLOOK)
    app = FakeOutlook(tela=tela, email=EMAIL, espera_da_pagina=99)
    r = k.reconhecer(parse_hierarchy(app.page_source()), package=OUTLOOK, locale="en-US")
    assert (r.tela, r.tipo) == (esperada, tipo)


def test_a_senha_so_tem_um_campo_e_um_next() -> None:
    k = conhecimento.do_app(OUTLOOK)
    arvore = parse_hierarchy(FakeOutlook(tela="senha", email=EMAIL).page_source())
    form = k.formulario_de_login(arvore, "en-US")
    assert form is not None and form.password.resource_id == "passwordEntry"
    assert form.submit is not None and form.submit.text == "Next"
    # O cabeçalho com a conta (clicável) fica acima da senha, mas não é campo de texto: nunca recebe digitação.
    assert form.username is not None and not form.usuario_editavel


# ==================================================================== o motor genérico: os blocos novos
def test_quem_nao_declara_entrada_nem_alternativa_segue_como_antes() -> None:
    k = conhecimento.de_dados(copy.deepcopy(SESSAO_EM_ETAPAS), telas.de_dados(copy.deepcopy(TELAS_EM_ETAPAS)))
    etapa = k.formulario.etapa_do_usuario
    assert etapa is not None and etapa.entrada is None and etapa.alternativas == ()
    assert conhecimento.do_app(INSTAGRAM).formulario.etapa_do_usuario is None


def test_botao_unico_vale_por_texto_ou_descricao_e_exige_um_so() -> None:
    import re
    usar = re.compile(r"^\s*use your password\s*$", re.I)
    nada = re.compile("google", re.I)

    def arvore(*nos: str) -> Any:
        return parse_hierarchy("<hierarchy>" + "".join(nos) + "</hierarchy>")

    def botao(texto: str, desc: str = "", y: int = 100) -> str:
        return (f'<node class="android.widget.Button" package="{OUTLOOK}" text="{texto}" content-desc="{desc}" '
                f'resource-id="" clickable="true" enabled="true" bounds="[0,{y}][300,{y + 50}]" />')

    repetido = arvore(botao("Use your password", "Use your password"))
    assert geometria.botao_unico(repetido, pacote=OUTLOOK, rotulo=usar, exclusao=nada) is not None
    dois = arvore(botao("Use your password"), botao("Use your password", y=300))
    assert geometria.botao_unico(dois, pacote=OUTLOOK, rotulo=usar, exclusao=nada) is None
    outro_app = parse_hierarchy('<hierarchy><node class="android.widget.Button" package="com.outro" '
                                'text="Use your password" clickable="true" enabled="true" '
                                'bounds="[0,0][300,50]" /></hierarchy>')
    assert geometria.botao_unico(outro_app, pacote=OUTLOOK, rotulo=usar, exclusao=nada) is None


def _sessao_do_outlook() -> dict[str, Any]:
    return copy.deepcopy(_dados("sessao.yaml"))


def _telas_do_outlook() -> telas.ConhecimentoDeTelas:
    return telas.de_dados(copy.deepcopy(_dados("telas.yaml")))


def _etapa(d: dict[str, Any]) -> dict[str, Any]:
    return d["formulario"]["etapa_do_usuario"]


@pytest.mark.parametrize(("mexe", "trecho"), [
    (lambda d: _etapa(d)["entrada"].update(tela="caixa_de_entrada"), "tipo login"),
    (lambda d: _etapa(d)["entrada"].update(tela="adicionar_conta"), "ANTES do identificador"),
    (lambda d: _etapa(d)["entrada"].update(tela="senha"), "campo de senha"),
    (lambda d: _etapa(d)["entrada"].update(sinal_do_botao="nao_existe"), "sinal 'nao_existe'"),
    (lambda d: _etapa(d)["entrada"].update(botao="x"), "campo desconhecido"),
    (lambda d: _etapa(d)["entrada"].pop("sinal_do_botao"), "falta sinal_do_botao"),
    # Conta travada nunca é tela de alternativa (ADR-055): nada se toca nela.
    (lambda d: _etapa(d)["alternativas"][0].update(tela="conta_travada"), "dois_fatores ou login"),
    (lambda d: _etapa(d)["alternativas"][0].update(tela="caixa_de_entrada"), "dois_fatores ou login"),
    (lambda d: _etapa(d)["alternativas"][0].update(tela="senha"), "campo de senha"),
    (lambda d: _etapa(d)["alternativas"][0].update(tela="adicionar_conta"), "repetida ou é a do identificador"),
    (lambda d: _etapa(d)["alternativas"].append(dict(_etapa(d)["alternativas"][0])), "repetida"),
    (lambda d: _etapa(d)["alternativas"][0].update(sinal_do_botao="nao_existe"), "sinal 'nao_existe'"),
    (lambda d: _etapa(d).update(alternativas={"tela": "verificar_email"}), "esperava uma lista"),
    # Dispensa por tela: só `intersticial` (desafio, código, login e casa nunca se dispensam), uma vez cada.
    (lambda d: d["dispensa"]["por_tela"][0].update(tela="conta_travada"), "tipo intersticial"),
    (lambda d: d["dispensa"]["por_tela"][0].update(tela="verificar_email"), "tipo intersticial"),
    (lambda d: d["dispensa"]["por_tela"][0].update(tela="caixa_de_entrada"), "tipo intersticial"),
    (lambda d: d["dispensa"]["por_tela"][0].update(tela="nao_existe"), "tela 'nao_existe'"),
    (lambda d: d["dispensa"]["por_tela"].append(dict(d["dispensa"]["por_tela"][0])), "repetida"),
    (lambda d: d["dispensa"]["por_tela"][0].update(sinal_do_botao="nao_existe"), "sinal 'nao_existe'"),
    # Dispensa pelo Voltar: só outro pacote Android, com um sinal que exista em todo idioma.
    (lambda d: d["dispensa"]["voltar"][0].update(pacote="credentialmanager"), "não é um pacote"),
    (lambda d: d["dispensa"]["voltar"][0].update(pacote=OUTLOOK), "pelo botão"),
    (lambda d: d["dispensa"]["voltar"][0].update(sinal="nao_existe"), "sinal 'nao_existe'"),
    (lambda d: d["dispensa"]["voltar"][0].update(botao="x"), "campo desconhecido"),
    (lambda d: d["dispensa"].update(aceitar=[]), "campo desconhecido"),
])
def test_dado_errado_dos_blocos_novos_e_recusado_na_carga(mexe: Callable[[dict[str, Any]], object],
                                                         trecho: str) -> None:
    dados = _sessao_do_outlook()
    mexe(dados)
    with pytest.raises(SessaoInvalida, match=trecho):
        conhecimento.de_dados(dados, _telas_do_outlook())


# ==================================================================== a sequência depois do "Next" (observada 30/09)
def _reconhecer(app: FakeOutlook) -> tuple[Any, Any]:
    k = conhecimento.do_app(OUTLOOK)
    arvore = parse_hierarchy(app.page_source())
    return arvore, k.reconhecer(arvore, package=app.current_package(), locale="en-US")


def test_o_continue_do_dialogo_de_chave_de_acesso_nunca_e_o_do_formulario() -> None:
    """O diálogo do sistema também tem "Continue": ele não é o formulário do identificador, nem entrada, nem dispensa
    por botão — a única saída declarada é o Voltar."""
    k = conhecimento.do_app(OUTLOOK)
    app = FakeOutlook(tela="chave")
    arvore, r = _reconhecer(app)
    assert r.outro_app
    assert k.formulario_de_usuario(arvore, "en-US") is None and k.botao_da_entrada(arvore, "en-US") is None
    d = k.dispensa_declarada(arvore, r, CREDENCIAIS, "en-US")
    assert d is not None and d.voltar and d.botao is None
    # Outro pacote qualquer com o mesmo texto não é dispensado: o Voltar é só do pacote declarado.
    assert k.dispensa_declarada(arvore, r, "com.exemplo.outro", "en-US") is None


@pytest.mark.parametrize(("tela", "acao"), [
    ("aviso", "ok"), ("outra_conta", "talvez_depois"), ("privacidade", "privacidade_next"),
    ("diagnostico", "recusar"), ("experiencias", "continuar_no_outlook"),
])
def test_cada_intermediaria_tem_um_botao_declarado_e_so_o_dela(tela: str, acao: str) -> None:
    k = conhecimento.do_app(OUTLOOK)
    app = FakeOutlook(tela=tela)
    arvore, r = _reconhecer(app)
    d = k.dispensa_declarada(arvore, r, OUTLOOK, "en-US")
    assert d is not None and d.botao is not None
    assert next(n.acao for n in app._nos if n.text == d.botao.text) == acao     # noqa: SLF001
    # "ADD" e "Accept" nunca: nem declarados, nem como recusa global (a global só acha o "MAYBE LATER").
    global_ = k.botao_de_dispensa(arvore)
    assert global_ is None or global_.text == "MAYBE LATER"


def test_o_ok_do_aviso_nao_vale_fora_do_aviso() -> None:
    """"OK" e "NEXT" não são recusas em lugar nenhum além da tela deles: na caixa ou numa página da Microsoft que não
    é o aviso, nada é dispensado."""
    k = conhecimento.do_app(OUTLOOK)
    for tela in ("caixa", "senha", "verificar", "desconhecida"):
        arvore, r = _reconhecer(FakeOutlook(tela=tela, email=EMAIL, conta=EMAIL))
        assert k.dispensa_declarada(arvore, r, OUTLOOK, "en-US") is None, tela


def test_intermediaria_com_verificacao_na_tela_nao_e_dispensada() -> None:
    k = conhecimento.do_app(OUTLOOK)
    xml = FakeOutlook(tela="aviso").page_source().replace(
        "</hierarchy>", f'<node class="android.widget.TextView" package="{OUTLOOK}" text="Help us protect your account" '
        'resource-id="" content-desc="" clickable="false" enabled="true" bounds="[40,60][680,100]" /></hierarchy>')
    arvore = parse_hierarchy(xml)
    r = k.reconhecer(arvore, package=OUTLOOK, locale="en-US")
    assert r.trava is not None and k.dispensa_declarada(arvore, r, OUTLOOK, "en-US") is None


def test_o_email_fora_do_painel_da_gaveta_nao_e_a_conta() -> None:
    """A extração vale só dentro do `drawer_folder_composable`: sem o contêiner na árvore, nada é lido."""
    k = conhecimento.do_app(OUTLOOK)
    xml = FakeOutlook(conta=EMAIL, tela="gaveta").page_source().replace("drawer_folder_composable", "outro_painel")
    assert k.conta_observada(parse_hierarchy(xml)) is None
