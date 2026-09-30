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

    @property
    def resource_id(self) -> str:
        if not self.rid or self.web:
            return self.rid
        return f"{OUTLOOK}:id/{self.rid}"


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
    de código traz também o texto de conta segurada."""

    senha_aceita: str = SENHA
    conta: str | None = None
    tela: str = ""
    oferece_senha: bool = True
    direto_na_senha: bool = False
    troca_lenta: int = 0
    espera_da_pagina: int = 2
    apos_continuar: str = ""
    apos_senha: str = "manter"
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
        return OUTLOOK

    def current_focus(self) -> tuple[str | None, str | None]:
        return OUTLOOK, ".MainActivity"

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
                    _No("android.widget.Button", continuar, text="Continue", rid="btn_primary_button",
                        clickable=True, acao="continuar")]
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
        avatar = _No("android.widget.ImageButton", (20, 40, 100, 120), desc="Open navigation drawer",
                     rid="account_button", clickable=True, acao="gaveta")
        if t == "gaveta":
            return [_No("android.widget.FrameLayout", (0, 0, 600, 1280), rid="drawer"),
                    _No("android.widget.TextView", (40, 200, 580, 240), text=self.conta or "", rid="account_email"),
                    _No("android.widget.TextView", (40, 300, 580, 340), text="Inbox"),
                    _No("android.widget.TextView", (40, 360, 580, 400), text="Drafts"),
                    _No("android.widget.ImageButton", (40, 1100, 120, 1180), desc="Add account", clickable=True,
                        acao="outra_conta")]
        # Caixa de entrada (SUPOSIÇÃO): um remetente sem nome mostra o endereço, e um assunto é "Verify your email".
        return [avatar,
                _No("android.widget.TextView", (120, 50, 400, 110), text="Inbox"),
                _No("android.widget.ImageButton", (620, 40, 700, 120), desc="Search", clickable=True, acao="busca"),
                _No("androidx.recyclerview.widget.RecyclerView", (0, 130, 720, 1250), rid="message_list"),
                _No("android.widget.TextView", (40, 150, 680, 190), text="noreply@servico.exemplo"),
                _No("android.widget.TextView", (40, 190, 680, 230), text="Verify your email"),
                _No("android.widget.TextView", (40, 260, 680, 300), text="Equipe Exemplo"),
                _No("android.widget.TextView", (40, 300, 680, 340), text="Seu pedido chegou")]

    def _depois_da_espera(self) -> str:
        if not self.conta_existe:
            return "conta_inexistente"
        if self.apos_continuar:
            return self.apos_continuar
        return "senha" if self.direto_na_senha else "verificar"

    def page_source(self) -> str:
        self._nos = self._montar()
        linhas = "".join(
            f'<node class={quoteattr(n.cls)} package="{OUTLOOK}" text={quoteattr(n.text)} '
            f'resource-id={quoteattr(n.resource_id)} content-desc={quoteattr(n.desc)} '
            f'clickable="{str(n.clickable).lower()}" enabled="true" focused="false" '
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
        if key == "back" and self.tela == "gaveta":
            self.tela = "caixa"


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
    """Os toques que o login NUNCA dá: criar conta, conta Google, pedir código, trocar de conta, manter conectado."""
    return {f"toque:{a}" for a in ("criar", "google", "enviar_codigo", "ja_recebi", "trocar", "sim", "outra_conta",
                                   "qr", "termos")} & set(app.calls)


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
    """Boas-vindas → "Add account" → e-mail → "Continue" → espera → "Verify your email" → "Use your password" → senha
    pelo canal sensível na página que mostra ESTE e-mail → "Next" → "Stay signed in?" (recusa) → caixa → gaveta."""
    app = FakeOutlook()
    m = outlook(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert r.observed_username == EMAIL and r.attempted_login
    # O e-mail no campo dele, a senha UMA vez no `passwordEntry`; nada mais digitado em lugar nenhum.
    assert app.typed == [("email", EMAIL), ("senha", SENHA)]
    toques = [c for c in app.calls if c.startswith("toque:")]
    for passo in ("toque:entrada", "toque:continuar", "toque:usar_senha", "toque:enviar", "toque:nao",
                  "toque:gaveta"):
        assert passo in toques, passo
    assert toques.index("toque:entrada") < toques.index("toque:continuar") < toques.index("toque:usar_senha") \
        < toques.index("toque:enviar") < toques.index("toque:nao")
    assert app.calls.count("toque:usar_senha") == 1 and app.calls.count("enviar") == 1
    assert _nunca_tocados(app) == set()
    assert _sessao(m)["status"] == SessionStatus.session_ready.value
    assert _credencial(m) == "active" and [t["stage"] for t in _tentativas(m)] == ["classified"]
    assert all(SENHA not in (t["detail"] or "") for t in _tentativas(m))


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
])
def test_dado_errado_dos_blocos_novos_e_recusado_na_carga(mexe: Callable[[dict[str, Any]], object],
                                                         trecho: str) -> None:
    dados = _sessao_do_outlook()
    mexe(dados)
    with pytest.raises(SessaoInvalida, match=trecho):
        conhecimento.de_dados(dados, _telas_do_outlook())
