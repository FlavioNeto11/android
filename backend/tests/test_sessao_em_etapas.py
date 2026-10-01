"""Item 23.6 — formulário em etapas, conta fora da barra inferior e Custom Tab com host declarado, no motor genérico
de sessão, só com DADO (ADR-052, ADR-057 decisão 3).

O app de mistura é um correio cujo login é o de muitos provedores de conta: o identificador numa tela com "Avançar",
a senha na seguinte (que mostra o identificador num cabeçalho clicável), um "continuar conectado?" depois de entrar, a
caixa SEM a conta à vista e a conta lida num menu aberto pelo avatar. Opcionalmente, a tela da senha abre numa Custom
Tab de um navegador de mentira, com a barra de endereço.

O que se prova:
- o login em etapas entra e confirma a conta pelo acesso declarado, sem Python do app;
- a senha só é digitada numa tela que mostra ESTE identificador, e só no pacote do app ou no navegador declarado num
  site da conta (os do `sessao.yaml` ou o `host` da própria conta) — conferido no instante de digitar;
- a recusa do identificador, a Custom Tab fora do site e a tela da senha sem a conta param o login SEM digitar a senha
  e sem gastar o teto diário; o desafio dentro da Custom Tab é visto;
- a leitura da conta fora da barra exige um valor só;
- a carga recusa o dado errado com o caminho do campo.

Nível de prova: `simulated` (dublês de aparelho; nenhum emulador, navegador, conta ou IA real).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
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
from app.integrations.app_declarado.sessao import (ETAPA_CONTA_NAO_MOSTRADA, ETAPA_IDENTIFICADOR_RECUSADO,
                                                   ETAPA_NAVEGADOR_FORA_DA_CONTA, ETAPA_SEM_TELA_DA_SENHA,
                                                   ETAPAS_ANTES_DO_ENVIO, Outcome, SessaoDeclarada)
from app.models import ProfileAccountCreate, SessionStatus
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService

from .conftest import make_config
from .test_instagram_auth import FakeDevices, FakeRt, cadastrar

ETAPAS = "com.exemplo.etapas"
NAVEGADOR = "com.exemplo.navegador"
SITE_DE_LOGIN = "login.exemplo.com"

TELAS: dict[str, Any] = {
    "app": ETAPAS, "versao": 1, "idioma_padrao": "pt",
    "sinais": {"pt": {
        "entrar": r"^\s*entrar\s*$",
        "avancar": r"^\s*avan[çc]ar\s*$",
        "outra_forma": r"(op[çc][õo]es de entrada|criar conta|outra conta)",
        "pede_identificador": r"e-mail ou telefone",
        "senha_errada": "senha incorreta",
        "conta_inexistente": r"essa conta n[ãa]o existe",
        "desafio": "verifique sua identidade",
        "agora_nao": r"^\s*agora n[ãa]o\s*$",
        "manter": "continuar conectado",
    }},
    "extracoes": {"email": {"ids": ["email_da_conta"],
                            "padrao": r"^([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})$"}},
    "telas": [
        {"tela": "desafio", "tipo": "desafio", "sinal": "desafio", "razao": "o correio pede verificação"},
        {"tela": "senha", "tipo": "login", "formulario_de_senha": True, "razao": "tela da senha"},
        {"tela": "identificador", "tipo": "login", "sinal": "pede_identificador", "razao": "tela do identificador"},
        {"tela": "manter", "tipo": "intersticial", "autenticada": True, "sinal": "manter",
         "razao": "o correio pergunta se continua conectado"},
        {"tela": "gaveta", "tipo": "autenticada", "autenticada": True, "ids": ["gaveta_de_contas"],
         "razao": "menu de contas"},
        {"tela": "caixa", "tipo": "autenticada", "autenticada": True, "ids": ["lista_de_mensagens"],
         "razao": "caixa de entrada"},
    ],
    "estado_conhecido": {"telas": ["caixa"], "voltar_max": 2, "reabrir": True},
}

SESSAO: dict[str, Any] = {
    "app": ETAPAS, "versao": 1, "rotulo": "Correio em Etapas",
    "ajustes": {"max_auth_attempts": 3, "auth_cooldown_s": 0, "open_timeout_s": 0.5, "settle_s": 0.01,
                "submit_wait_s": 2.0, "verify_timeout_s": 5.0, "max_logins_per_day": 3},
    "formulario": {
        "sinal_do_botao": "entrar", "sinal_de_exclusao": "outra_forma",
        "etapa_do_usuario": {"tela": "identificador", "sinal_do_botao": "avancar",
                             "recusas": [{"sinal": "conta_inexistente",
                                          "detalhe": "o correio informou que a conta não existe"}]},
    },
    "dispensa": {"intersticiais_max": 2, "rotulos": [r"^\s*n[ãa]o\s*$"], "ids": [],
                 "sinal_de_salvar_login": "agora_nao"},
    "conta": {"extracao": "email", "tela_de_perfil": "gaveta",
              "acesso": {"ids": ["avatar_da_conta"]},
              "passos_max": 3, "espera_s": 0.01, "ler_ao_entrar": True},
    "navegador": {"pacotes": {NAVEGADOR: "url_bar"}, "hosts": [SITE_DE_LOGIN]},
    "depois_do_envio": [
        {"sinal": "senha_errada", "desfecho": "invalid_credential", "detalhe": "o correio recusou a senha"},
        {"tipos": ["desafio", "dois_fatores"], "desfecho": "auth_challenge", "detalhe": "o correio pediu verificação"},
        {"telas": ["caixa", "manter", "gaveta"], "desfecho": "conferir_conta"},
        {"tipos": ["login"], "desfecho": "uncertain", "detalhe": "ficou na entrada do correio"},
    ],
    "textos": {
        "desafio": "O Correio em Etapas pediu verificação. Assuma o controle do aparelho e resolva na tela.",
        "desafio_pendente": "o Correio em Etapas pediu verificação",
        "senha_recusada": "a senha foi recusada pelo Correio em Etapas; troque-a no portal",
        "credencial_recusada": "a senha de @{usuario} foi recusada pelo Correio em Etapas",
    },
}

EMAIL = "ana@exemplo.com"
HANDLE = "ana.etapas"
SENHA = "etapas-Senha#9"


def _gravar(pasta: Path, sessao: dict[str, Any] | None = None) -> Path:
    pasta.mkdir(parents=True)
    (pasta / "telas.yaml").write_text(yaml.safe_dump(TELAS, allow_unicode=True), encoding="utf-8")
    (pasta / "sessao.yaml").write_text(yaml.safe_dump(sessao or SESSAO, allow_unicode=True), encoding="utf-8")
    return pasta


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
    pkg: str = ETAPAS


@dataclass
class FakeCorreioEmEtapas:
    """O correio em etapas. `tela`: identificador, senha, manter, caixa, gaveta, desafio. `conta` é quem está logado;
    `contas` são os identificadores que o correio conhece; `lembrado` é o que a tela da senha mostra quando o app
    abre direto nela. `custom_tab` = o site em que a tela da senha abre no navegador (None = no próprio app);
    `desafio_na_tab` = a Custom Tab mostra a verificação no lugar da senha; `trocar_site_ao_focar_senha` = o toque
    no campo de senha leva a Custom Tab a outro site (a corrida que a conferência no instante de digitar pega)."""

    senha_aceita: str = SENHA
    contas: tuple[str, ...] = (EMAIL,)
    conta: str | None = None
    tela: str = "identificador"
    lembrado: str = ""
    custom_tab: str | None = None
    desafio_na_tab: bool = False
    trocar_site_ao_focar_senha: str | None = None
    outra_conta_na_gaveta: str | None = None
    mostrar_na_senha: str | None = None
    recusa_na_pagina: bool = False
    sem_cabecalho: bool = False
    manter_na_tab: bool = False
    transicao: int = 0
    _transicao_restante: int = 0
    email: str = ""
    senha: str = ""
    erro: str = ""
    no_navegador: bool = False
    typed: list[tuple[str, str]] = field(default_factory=list)      # (pacote da frente, texto): nunca vai a log
    calls: list[str] = field(default_factory=list)
    _foco: str = ""
    _nos: list[_No] = field(default_factory=list)

    # ------------------------------------------------------------------ o que o motor lê do aparelho
    def current_package(self) -> str | None:
        return NAVEGADOR if self.no_navegador else ETAPAS

    def current_focus(self) -> tuple[str | None, str | None]:
        return (NAVEGADOR, ".CustomTab") if self.no_navegador else (ETAPAS, ".Principal")

    def getprop(self, name: str) -> str:
        return "pt-BR" if name == "ro.product.locale" else ""

    def start_app(self, package: str, activity: str | None = None) -> None:
        if self.no_navegador or self.tela == "desafio":
            return
        if self.conta:
            self.tela = "caixa"
        elif self.lembrado:
            self.tela, self.email = "senha", self.lembrado
        else:
            self.tela = "identificador"

    def _barra(self) -> list[_No]:
        if not self.no_navegador:
            return []
        return [_No("android.widget.EditText", (0, 0, 720, 60), text=f"{self.custom_tab}/entrar", rid="url_bar",
                    pkg=NAVEGADOR)]

    def _montar(self) -> list[_No]:
        pkg = NAVEGADOR if self.no_navegador else ETAPAS
        if self.tela == "desafio":
            return [*self._barra(),
                    _No("android.widget.TextView", (40, 200, 680, 260), text="Verifique sua identidade", pkg=pkg),
                    _No("android.widget.Button", (40, 900, 680, 960), text="Continuar", clickable=True, pkg=pkg)]
        if self.tela == "identificador":
            erro = [_No("android.widget.TextView", (40, 480, 680, 520), text=self.erro, pkg=pkg)] if self.erro else []
            return [
                *self._barra(),
                _No("android.widget.TextView", (40, 200, 680, 260), text="Entrar", pkg=pkg),
                _No("android.widget.EditText", (40, 400, 680, 470), text=self.email, desc="E-mail ou telefone",
                    rid="campo_email", clickable=True, acao="foco:email", pkg=pkg),
                *erro,
                _No("android.widget.TextView", (40, 560, 400, 600), text="Opções de entrada", clickable=True, pkg=pkg),
                _No("android.widget.Button", (420, 700, 680, 760), text="Avançar", rid="botao_avancar",
                    clickable=True, acao="avancar", pkg=pkg),
            ]
        if self.tela == "senha":
            erro = [_No("android.widget.TextView", (40, 600, 680, 640), text=self.erro, pkg=pkg)] if self.erro else []
            recusa = [_No("android.widget.Button", (40, 1000, 680, 1060), text="Não", clickable=True, acao="nao",
                          pkg=pkg)] if self.recusa_na_pagina else []
            return [
                *self._barra(),
                *recusa,
                # O cabeçalho com a conta é CLICÁVEL (volta para trocar de conta) e fica logo acima da senha: a
                # geometria o toma por "usuário"; no login em etapas ele não é campo de texto e nunca recebe digitação.
                *([] if self.sem_cabecalho else [
                    _No("android.view.View", (40, 380, 680, 440), text=self.mostrar_na_senha or self.email,
                        rid="identidade", clickable=True, acao="trocar", pkg=pkg)]),
                _No("android.widget.EditText", (40, 500, 680, 570), text="••••" if self.senha else "",
                    rid="campo_senha", clickable=True, password=True, acao="foco:senha", pkg=pkg),
                *erro,
                _No("android.widget.TextView", (40, 660, 400, 700), text="Esqueci a senha", clickable=True, pkg=pkg),
                _No("android.widget.Button", (420, 760, 680, 820), text="Entrar", rid="botao_entrar",
                    clickable=True, acao="enviar", pkg=pkg),
            ]
        if self.tela == "manter":
            return [*self._barra(),
                    _No("android.widget.TextView", (40, 200, 680, 260), text="Continuar conectado?", pkg=pkg),
                    _No("android.widget.Button", (40, 900, 340, 960), text="Não", clickable=True, acao="nao", pkg=pkg),
                    _No("android.widget.Button", (380, 900, 680, 960), text="Sim", clickable=True, acao="sim",
                        pkg=pkg)]
        if self.tela == "transicao":
            # A Custom Tab fechando: só a barra e uma página em branco por algumas leituras, e o app volta à caixa.
            self._transicao_restante -= 1
            if self._transicao_restante <= 0:
                self.tela, self.no_navegador = "caixa", False
            return [*self._barra(), _No("android.widget.ProgressBar", (300, 600, 420, 720), desc="Carregando",
                                        pkg=pkg)]
        avatar = _No("android.widget.ImageView", (20, 40, 100, 120), desc="Abrir contas", rid="avatar_da_conta",
                     clickable=True, acao="gaveta")
        if self.tela == "gaveta":
            outra = [_No("android.widget.TextView", (40, 260, 600, 300), text=self.outra_conta_na_gaveta,
                         rid="email_da_conta")] if self.outra_conta_na_gaveta else []
            return [_No("android.widget.FrameLayout", (0, 0, 600, 1280), rid="gaveta_de_contas"),
                    _No("android.widget.TextView", (40, 200, 600, 240), text=self.conta or "", rid="email_da_conta"),
                    *outra,
                    _No("android.widget.Button", (40, 1100, 600, 1160), text="Adicionar conta", clickable=True)]
        return [avatar, _No("androidx.recyclerview.widget.RecyclerView", (0, 130, 720, 1250), rid="lista_de_mensagens")]

    def page_source(self) -> str:
        self._nos = self._montar()
        linhas = "".join(
            f'<node class={quoteattr(n.cls)} package="{n.pkg}" text={quoteattr(n.text)} '
            f'resource-id={quoteattr((n.pkg + ":id/" + n.rid) if n.rid else "")} content-desc={quoteattr(n.desc)} '
            f'clickable="{str(n.clickable).lower()}" enabled="true" focused="false" '
            f'password="{str(n.password).lower()}" scrollable="false" '
            f'bounds="[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]" />' for n in self._nos)
        return f'<hierarchy rotation="0">{linhas}</hierarchy>'

    # ------------------------------------------------------------------ o que o motor faz no aparelho
    def tap(self, x: int, y: int) -> None:
        self.calls.append(f"tap:{x},{y}")
        alvo = next((n for n in self._nos if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                     and n.bounds[1] <= y <= n.bounds[3]), None)
        if alvo is None:
            return
        self.calls.append(f"toque:{alvo.acao or alvo.text}")
        if alvo.acao.startswith("foco:"):
            self._foco = alvo.acao.split(":", 1)[1]
            if self._foco == "senha" and self.trocar_site_ao_focar_senha:
                self.custom_tab = self.trocar_site_ao_focar_senha
        elif alvo.acao == "avancar":
            if self.email not in self.contas:
                self.erro = "Essa conta não existe. Tente outra."
                return
            self.erro = ""
            if self.custom_tab is not None:
                self.no_navegador = True
            self.tela = "desafio" if self.desafio_na_tab else "senha"
        elif alvo.acao == "enviar":
            self.calls.append("enviar")
            if self.senha != self.senha_aceita:
                self.erro, self.senha = "Senha incorreta.", ""
                return
            self.conta, self.senha, self.tela = self.email, "", "manter"
            self.no_navegador = self.no_navegador and self.manter_na_tab
        elif alvo.acao in ("nao", "sim"):
            if self.no_navegador and self.transicao:
                self.tela, self._transicao_restante = "transicao", self.transicao
            else:
                self.tela, self.no_navegador = "caixa", False
        elif alvo.acao == "gaveta":
            self.tela = "gaveta"
        elif alvo.acao == "trocar":
            self.calls.append("trocou de conta")

    def type_text(self, text: str, *, clear_first: bool) -> None:
        if text:
            self.typed.append((self.current_package() or "", text))
        if self._foco == "senha":
            self.senha = text if clear_first else self.senha + text
        else:
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
def correio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """O motor com o conhecimento LIDO DOS ARQUIVOS do correio em etapas, sobre banco e cofre de verdade. A observação
    depois do "avançar" e do "entrar" roda em passos curtos: o dublê responde na hora."""
    monkeypatch.setattr(sessao_mod, "OBSERVAR_DEPOIS_DO_ENVIO_S", 0.02)
    k = conhecimento.carregar(_gravar(tmp_path / ETAPAS))
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('etapas', 'Correio em Etapas', ?, 0)", (ETAPAS,))
    bus = EventBus(db)
    secrets = SecretStore(db, MemoryKeyProvider())
    repo = SocialRepository(db)
    social = SocialService(repo, secrets, bus, known_instances=lambda: ["android-01", "android-02"])

    def montar(app: FakeCorreioEmEtapas, *, host: str | None = None, login: str = EMAIL) -> Montado:
        sessao = SessaoDeclarada(k, cfg, FakeDevices(app), repo, secrets,  # type: ignore[arg-type]
                                 SensitiveInputChannel(lambda: True), bus)
        sessao.focus_poll_s = 0.01
        pid = cadastrar(social, username="ana.ancora", senha="ancora-Senha#3", instance_id="android-02")
        conta = social.add_account(pid, ProfileAccountCreate(app_id="etapas", handle=HANDLE, login_identifier=login,
                                                             host=host, password=SecretStr(SENHA),
                                                             consent=True)).id
        return Montado(sessao, repo, db, pid, conta)

    yield montar
    db.close()


def _senhas_digitadas(app: FakeCorreioEmEtapas) -> list[str]:
    """Em que pacote cada digitação da SENHA caiu (o valor fica no dublê; a asserção compara só o pacote)."""
    return [pacote for pacote, texto in app.typed if texto == SENHA]


def _tentativas(m: Montado) -> list[dict[str, Any]]:
    return [dict(r) for r in m.db.query("SELECT stage, outcome, detail FROM authentication_attempts"
                                        " WHERE account_id=? ORDER BY id", (m.conta,))]


def _credencial(m: Montado) -> str:
    return str(m.repo.account_credential_row(m.pid, m.conta)["status"])


def _sessao(m: Montado) -> dict[str, Any]:
    return dict(m.repo.account_session_row(m.pid, m.conta, "android-02"))


# ==================================================================== o caminho feliz
async def test_login_em_etapas_entra_e_confirma_a_conta_pelo_acesso_declarado(correio: Any) -> None:
    """Identificador → "Avançar" → senha (na tela que mostra ESTE identificador) → "Entrar" → "continuar conectado?"
    dispensado com a recusa → caixa sem a conta → avatar → menu de contas → a conta confere. Tudo pelo dado."""
    app = FakeCorreioEmEtapas()
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert r.observed_username == EMAIL and r.attempted_login
    # O identificador foi digitado no campo dele; a senha, UMA vez, no próprio app. O cabeçalho clicável da conta
    # (acima da senha) nunca foi tocado nem recebeu digitação.
    assert [t for _, t in app.typed if t != SENHA] == [EMAIL]
    assert _senhas_digitadas(app) == [ETAPAS]
    assert "trocou de conta" not in app.calls and app.calls.count("enviar") == 1
    assert "toque:nao" in app.calls and "toque:sim" not in app.calls          # só a RECUSA dispensa
    assert app.tela == "gaveta"
    assert _sessao(m)["status"] == SessionStatus.session_ready.value
    assert _credencial(m) == "active"
    assert [t["stage"] for t in _tentativas(m)] == ["classified"]


async def test_logado_le_a_conta_pelo_acesso_sem_digitar_nada(correio: Any) -> None:
    app = FakeCorreioEmEtapas(conta=EMAIL, tela="caixa")
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.ready and not r.attempted_login and app.typed == []
    assert app.tela == "gaveta" and "toque:gaveta" in app.calls


async def test_menu_com_duas_contas_nao_le_nenhuma(correio: Any) -> None:
    """Fora da barra inferior a conta só é lida com UM valor: o menu que lista duas não diz qual está aberta, e a
    primeira seria um falso "conta certa"."""
    app = FakeCorreioEmEtapas(conta=EMAIL, tela="caixa", outra_conta_na_gaveta="bia@exemplo.com")
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and r.observed_username is None
    assert "não pôde ser lida" in r.detail and app.typed == []
    assert _sessao(m)["status"] == SessionStatus.unknown.value


async def test_tela_da_senha_direto_com_esta_conta_recebe_so_a_senha(correio: Any) -> None:
    """O app lembrou o identificador e abriu direto na senha: mostrando ESTE identificador, a senha segue."""
    app = FakeCorreioEmEtapas(lembrado=EMAIL)
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert [t for _, t in app.typed] == [SENHA]


# ==================================================================== a senha só onde é desta conta
async def test_tela_da_senha_de_outra_conta_nao_recebe_a_senha(correio: Any) -> None:
    """O app lembrou OUTRA conta e abriu direto na senha dela: a senha desta conta nunca é digitada, e o login para
    até uma pessoa olhar (a tela seria a mesma a cada tentativa) sem gastar o teto diário."""
    app = FakeCorreioEmEtapas(lembrado="joana@exemplo.com", contas=(EMAIL, "joana@exemplo.com"))
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "não mostra a conta" in r.detail
    assert app.typed == [] and "enviar" not in app.calls
    assert _credencial(m) == "review"
    assert _sessao(m)["status"] == SessionStatus.auth_required.value
    assert [t["stage"] for t in _tentativas(m)] == [ETAPA_CONTA_NAO_MOSTRADA]
    assert m.sessao._teto_diario(m.sessao._resolver_conta(m.pid, None)) is None  # noqa: SLF001
    # A volta do agendador não toca no aparelho: o login está parado.
    app.calls.clear()
    r2 = await m.sessao.ensure_session(FakeRt(app), m.pid, automatic=True)  # type: ignore[arg-type]
    assert r2.outcome is not Outcome.SESSION_READY and app.calls == []


async def test_tela_da_senha_que_mostra_outra_conta_depois_do_avancar(correio: Any) -> None:
    """Depois do "Avançar", a tela da senha chega dizendo OUTRA conta (e segue assim até o prazo): a senha não sai."""
    app = FakeCorreioEmEtapas(mostrar_na_senha="bia@exemplo.com")
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "não mostra a conta" in r.detail
    assert [t for _, t in app.typed] == [EMAIL] and "enviar" not in app.calls
    assert [t["stage"] for t in _tentativas(m)] == [ETAPA_CONTA_NAO_MOSTRADA]


async def test_identificador_recusado_para_sem_digitar_a_senha(correio: Any) -> None:
    """"Essa conta não existe" depois do "Avançar": a senha não sai; a credencial não vira `invalid` (ninguém julgou
    a senha), o login para em `review` até uma pessoa conferir o identificador."""
    app = FakeCorreioEmEtapas()
    m = correio(app, login="ana@errado.com")
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.INVALID_CREDENTIAL and "conta não existe" in r.detail
    assert [t for _, t in app.typed] == ["ana@errado.com"] and "enviar" not in app.calls
    assert _credencial(m) == "review"
    assert [t["stage"] for t in _tentativas(m)] == [ETAPA_IDENTIFICADOR_RECUSADO]


@dataclass
class FakeCorreioQueAprovaNoTelefone(FakeCorreioEmEtapas):
    """O "Avançar" leva a uma tela que ninguém declarou ("aprove no telefone": o provedor de contas mandou um push ou
    um e-mail com código) e a tela da senha nunca chega; reabrir o app volta ao identificador vazio."""

    def start_app(self, package: str, activity: str | None = None) -> None:
        self.tela, self.email, self.no_navegador = "identificador", "", False

    def _montar(self) -> list[_No]:
        if self.tela == "aprovar":
            return [_No("android.widget.TextView", (40, 200, 680, 260), text="Aprove o pedido no seu telefone")]
        return super()._montar()

    def tap(self, x: int, y: int) -> None:
        super().tap(x, y)
        if self.tela == "senha":
            self.tela = "aprovar"


async def test_tela_da_senha_que_nao_chega_para_o_login_automatico(correio: Any) -> None:
    """O "Avançar" já é efeito no servidor (o identificador saiu: pode disparar um código ou um push a cada vez). A
    tela da senha que não chega para o login até uma pessoa olhar — antes contava falha e o agendador repetia o
    "Avançar" sem fim, com a credencial `active`. A etapa segue fora do teto diário: a senha não saiu."""
    app = FakeCorreioQueAprovaNoTelefone()
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid, automatic=True)  # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "tela da senha não apareceu" in r.detail
    assert _credencial(m) == "review" and _sessao(m)["status"] == SessionStatus.auth_required.value
    for _ in range(5):                                                 # as voltas do agendador
        await m.sessao.ensure_session(FakeRt(app), m.pid, automatic=True)  # type: ignore[arg-type]
    assert app.calls.count("toque:avancar") == 1
    assert _senhas_digitadas(app) == [] and "enviar" not in app.calls
    assert [t["stage"] for t in _tentativas(m)] == [ETAPA_SEM_TELA_DA_SENHA]
    assert m.sessao._teto_diario(m.sessao._resolver_conta(m.pid, None)) is None  # noqa: SLF001


async def test_senha_errada_no_login_em_etapas_usa_a_tabela_declarada(correio: Any) -> None:
    app = FakeCorreioEmEtapas(senha_aceita="outra")
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.INVALID_CREDENTIAL and r.detail == "o correio recusou a senha"
    assert _credencial(m) == "invalid" and app.calls.count("enviar") == 1


# ==================================================================== Custom Tab
async def test_custom_tab_no_site_de_login_declarado_recebe_a_senha_no_navegador(correio: Any) -> None:
    app = FakeCorreioEmEtapas(custom_tab=SITE_DE_LOGIN)
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert _senhas_digitadas(app) == [NAVEGADOR]                       # a senha caiu na Custom Tab, no site declarado


async def test_custom_tab_que_segue_no_navegador_depois_do_envio(correio: Any) -> None:
    """O jeito que o login de um provedor de contas costuma ter: o "continuar conectado?" ainda NA Custom Tab, e a
    tab demorando a fechar depois da recusa. A leitura com a Custom Tab ainda na frente não gasta a leitura única; a
    conta é confirmada quando a caixa volta."""
    app = FakeCorreioEmEtapas(custom_tab=SITE_DE_LOGIN, manter_na_tab=True, transicao=6)
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert "toque:nao" in app.calls and "toque:sim" not in app.calls
    assert _senhas_digitadas(app) == [NAVEGADOR] and app.tela == "gaveta"


async def test_custom_tab_em_subdominio_do_site_declarado_tambem_vale(correio: Any) -> None:
    app = FakeCorreioEmEtapas(custom_tab="contas." + SITE_DE_LOGIN)
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail


async def test_conta_de_site_nao_abre_o_login_gerenciado_nem_soma_o_site(correio: Any) -> None:
    """A conta com `host` é de PORTAL (um site, pelo navegador): a porta de sessão e o despacho nunca a acham
    (`conta_do_pacote` é a do app inteiro), então o "Conectar" dela também não abre o login — e o site dela nunca
    vira site de login do app. Antes, dita pelo id, ela abria o login e a senha caía na Custom Tab do site dela."""
    app = FakeCorreioEmEtapas(custom_tab="entrar.minhaconta.com.br")
    m = correio(app, host="entrar.minhaconta.com.br")
    r = await m.sessao.ensure_session(FakeRt(app), m.pid, account_id=m.conta)  # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "é de site (entrar.minhaconta.com.br)" in r.detail
    assert app.calls == [] and app.typed == []                        # recusa sem tocar no aparelho
    assert _tentativas(m) == [] and _credencial(m) == "active"
    # Sem o id, é o caminho da porta: esta persona não tem a conta do app inteiro.
    r2 = await m.sessao.ensure_session(FakeRt(app), m.pid)             # type: ignore[arg-type]
    assert r2.outcome is Outcome.INVALID_CREDENTIAL and "não tem conta" in r2.detail
    assert app.calls == [] and app.typed == []


async def test_custom_tab_num_site_nao_declarado_pelo_app_nao_recebe_a_senha(correio: Any) -> None:
    """O site de login que vale é só o que o app declara (`navegador.hosts`): nenhum outro, nem um que se pareça."""
    app = FakeCorreioEmEtapas(custom_tab="entrar.minhaconta.com.br")
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "entrar.minhaconta.com.br" in r.detail
    assert _senhas_digitadas(app) == [] and "enviar" not in app.calls


async def test_custom_tab_fora_do_site_devolve_a_pessoa_sem_digitar_a_senha(correio: Any) -> None:
    app = FakeCorreioEmEtapas(custom_tab="login.exemplo.com.golpe.net")
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN
    assert "fora dos sites declarados" in r.detail and "login.exemplo.com.golpe.net" in r.detail
    assert _senhas_digitadas(app) == [] and "enviar" not in app.calls
    assert _sessao(m)["status"] == SessionStatus.auth_required.value
    assert _credencial(m) == "review"                                  # o login automático para até alguém olhar
    assert [t["stage"] for t in _tentativas(m)] == [ETAPA_NAVEGADOR_FORA_DA_CONTA]
    assert m.sessao._teto_diario(m.sessao._resolver_conta(m.pid, None)) is None  # noqa: SLF001


async def test_custom_tab_ja_aberta_fora_do_site_devolve_a_pessoa_na_abertura(correio: Any) -> None:
    """O app abre com a Custom Tab por cima, num site que ninguém declarou: nem "voltar", nem digitar."""
    app = FakeCorreioEmEtapas(custom_tab="outro.exemplo.org", tela="senha", email=EMAIL, no_navegador=True,
                              recusa_na_pagina=True)
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "fora dos sites declarados" in r.detail
    # Nem a recusa ("Não") se toca numa página alheia, nem "voltar", nem digitação.
    assert app.typed == [] and not any(c.startswith(("key:", "toque:")) for c in app.calls)
    # "Verificar conta" (só leitura) registra sem parar o login automático.
    m.repo.mark_account_credential(m.pid, m.conta, status="active")
    r2 = await m.sessao.ensure_session(FakeRt(app), m.pid, observe_only=True)  # type: ignore[arg-type]
    assert r2.outcome is Outcome.UNCERTAIN and _credencial(m) == "active"


async def test_desafio_dentro_da_custom_tab_fora_do_site_e_visto(correio: Any) -> None:
    """`classificar` devolve "outro app" antes do detector; o motor pergunta ao detector mesmo assim, e a pessoa é
    chamada pelo caminho do desafio — nada é tocado nem digitado depois do identificador."""
    app = FakeCorreioEmEtapas(custom_tab="verificacao.exemplo.net", desafio_na_tab=True)
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
    assert _senhas_digitadas(app) == [] and "toque:Continuar" not in app.calls
    assert _sessao(m)["status"] == SessionStatus.auth_challenge.value


async def test_a_barra_de_endereco_nunca_e_o_campo_de_usuario(correio: Any) -> None:
    """Uma página de senha na Custom Tab sem o cabeçalho da conta: a barra de endereço é o único campo de texto acima
    da senha, e a geometria a tomaria por usuário. Ela é ignorada — nada é digitado na barra, e sem a conta na página
    a senha não sai."""
    app = FakeCorreioEmEtapas(custom_tab=SITE_DE_LOGIN, sem_cabecalho=True)
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "não mostra a conta" in r.detail
    assert app.typed == [(ETAPAS, EMAIL)]


async def test_o_site_e_conferido_no_instante_de_digitar(correio: Any) -> None:
    """A tela da senha estava no site declarado quando foi reconhecida, mas o toque no campo levou a Custom Tab a
    outro site: o canal sensível não acha campo "permitido" e recusa com a mensagem fixa — a senha não sai."""
    app = FakeCorreioEmEtapas(custom_tab=SITE_DE_LOGIN, trocar_site_ao_focar_senha="golpe.exemplo.net")
    m = correio(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.RETRYABLE
    assert _senhas_digitadas(app) == [] and "enviar" not in app.calls
    assert SENHA not in r.detail


# ==================================================================== o motor segue sem mudança para quem não declara
def test_as_novas_etapas_nao_gastam_o_teto_diario() -> None:
    assert {ETAPA_CONTA_NAO_MOSTRADA, ETAPA_IDENTIFICADOR_RECUSADO, ETAPA_NAVEGADOR_FORA_DA_CONTA,
            "challenge_before_password", "password_step_missing"} <= ETAPAS_ANTES_DO_ENVIO


def test_sem_os_blocos_novos_o_conhecimento_e_o_de_antes(tmp_path: Path) -> None:
    dados = copy.deepcopy(SESSAO)
    dados["formulario"].pop("etapa_do_usuario")
    dados.pop("navegador")
    dados["conta"] = {"extracao": "email", "tela_de_perfil": "gaveta", "passos_max": 3, "espera_s": 0.01,
                      "aba": {"prefixo_de_id": "conta_tab", "rotulos": ["conta"], "faixa_inferior": 0.88}}
    k = conhecimento.carregar(_gravar(tmp_path / ETAPAS, dados))
    assert k.formulario.etapa_do_usuario is None and k.navegador is None
    assert k.conta.acesso is None and k.conta.aba is not None and not k.conta.ler_ao_entrar
    assert k.barra_de_endereco(NAVEGADOR) is None


# ==================================================================== carga: dado errado é recusado
def _telas() -> telas.ConhecimentoDeTelas:
    return telas.de_dados(copy.deepcopy(TELAS))


def test_o_dado_do_correio_em_etapas_carrega() -> None:
    k = conhecimento.de_dados(copy.deepcopy(SESSAO), _telas())
    assert k.formulario.etapa_do_usuario is not None and k.formulario.etapa_do_usuario.tela == "identificador"
    assert k.conta.acesso is not None and k.conta.aba is None and k.conta.ler_ao_entrar
    assert k.navegador is not None and k.barra_de_endereco(NAVEGADOR) == "url_bar"


@pytest.mark.parametrize(("mexe", "trecho"), [
    (lambda d: d["conta"].update(aba={"prefixo_de_id": "x", "rotulos": ["x"], "faixa_inferior": 0.9}),
     "exatamente um entre `aba`"),
    (lambda d: d["conta"].pop("acesso"), "exatamente um entre `aba`"),
    (lambda d: d["conta"].update(acesso={}), "declare `ids` ou `rotulos`"),
    (lambda d: d["conta"]["acesso"].update(atalho="x"), r"conta\.acesso: campo desconhecido"),
    (lambda d: d["conta"]["acesso"].update(rotulos=["(sem fechar"]), "expressão regular"),
    (lambda d: d["conta"].update(ler_ao_entrar="sim"), "true ou false"),
    (lambda d: d["formulario"]["etapa_do_usuario"].update(tela="caixa"), "tipo `login`"),
    (lambda d: d["formulario"]["etapa_do_usuario"].update(tela="senha"), "campo de senha"),
    (lambda d: d["formulario"]["etapa_do_usuario"].update(tela="nao_existe"), "tela 'nao_existe'"),
    (lambda d: d["formulario"]["etapa_do_usuario"].update(sinal_do_botao="nao_existe"), "sinal 'nao_existe'"),
    (lambda d: d["formulario"]["etapa_do_usuario"].update(botao="x"), "campo desconhecido"),
    (lambda d: d["formulario"]["etapa_do_usuario"]["recusas"][0].update(sinal="nao_existe"), "sinal 'nao_existe'"),
    (lambda d: d["formulario"]["etapa_do_usuario"]["recusas"][0].pop("detalhe"), "falta detalhe"),
    (lambda d: d["navegador"].update(pacotes={}), "mapa não vazio"),
    (lambda d: d["navegador"].update(pacotes={"navegador": "url_bar"}), "não é um pacote"),
    (lambda d: d["navegador"].update(pacotes={ETAPAS: "url_bar"}), "próprio app"),
    (lambda d: d["navegador"].update(hosts=["https://login.exemplo.com"]), "não é um host"),
    (lambda d: d["navegador"].update(hosts=["login.exemplo.com/entrar"]), "não é um host"),
    (lambda d: d["navegador"].update(sites=["x"]), r"navegador: campo desconhecido"),
])
def test_dado_errado_e_recusado_na_carga(mexe: Callable[[dict[str, Any]], object], trecho: str) -> None:
    dados = copy.deepcopy(SESSAO)
    mexe(dados)
    with pytest.raises(SessaoInvalida, match=trecho):
        conhecimento.de_dados(dados, _telas())


# ==================================================================== a geometria nova
def _arvore(app: FakeCorreioEmEtapas) -> Any:
    return parse_hierarchy(app.page_source())


def test_etapa_do_identificador_exige_um_campo_e_um_avancar() -> None:
    avancar, exclusao = re.compile(r"^\s*avan[çc]ar\s*$", re.I), re.compile("op[çc][õo]es", re.I)
    arvore = _arvore(FakeCorreioEmEtapas())
    achado = geometria.identifier_form(arvore, avancar=avancar, exclusao=exclusao)
    assert achado is not None and achado.campo.resource_id.endswith("campo_email")
    assert achado.botao.text == "Avançar"
    # Com o campo de senha na tela, não é a etapa do identificador.
    assert geometria.identifier_form(_arvore(FakeCorreioEmEtapas(tela="senha", email=EMAIL)), avancar=avancar,
                                     exclusao=exclusao) is None
    # Dois campos de texto: incerteza, nada escolhido.
    dois = parse_hierarchy(FakeCorreioEmEtapas().page_source().replace(
        "</hierarchy>", '<node class="android.widget.EditText" package="com.exemplo.etapas" text="" resource-id="" '
        'content-desc="Busca" clickable="true" enabled="true" focused="false" password="false" scrollable="false" '
        'bounds="[40,300][680,360]" /></hierarchy>'))
    assert geometria.identifier_form(dois, avancar=avancar, exclusao=exclusao) is None


def test_a_barra_de_endereco_fica_fora_da_geometria_do_formulario() -> None:
    avancar, exclusao = re.compile(r"^\s*avan[çc]ar\s*$", re.I), re.compile("op[çc][õo]es", re.I)
    barra = ((NAVEGADOR, "url_bar"),)
    na_tab = _arvore(FakeCorreioEmEtapas(custom_tab=SITE_DE_LOGIN, no_navegador=True))
    assert geometria.identifier_form(na_tab, avancar=avancar, exclusao=exclusao) is None     # dois campos de texto
    achado = geometria.identifier_form(na_tab, avancar=avancar, exclusao=exclusao, ignorar=barra)
    assert achado is not None and achado.campo.resource_id.endswith("campo_email")
    senha = _arvore(FakeCorreioEmEtapas(custom_tab=SITE_DE_LOGIN, no_navegador=True, tela="senha", sem_cabecalho=True))
    entrar = re.compile(r"^\s*entrar\s*$", re.I)
    sem = geometria.login_form(senha, entrar=entrar, exclusao=exclusao)
    assert sem is not None and sem.username is not None and sem.username.resource_id.endswith("url_bar")
    com = geometria.login_form(senha, entrar=entrar, exclusao=exclusao, ignorar=barra)
    assert com is not None and com.username is None and com.submit is not None


def test_o_identificador_na_tela_e_palavra_inteira() -> None:
    arvore = _arvore(FakeCorreioEmEtapas(tela="senha", email="joana@exemplo.com"))
    assert geometria.mostra_o_identificador(arvore, "joana@exemplo.com")
    assert not geometria.mostra_o_identificador(arvore, "ana@exemplo.com")          # dentro de "joana@..." não vale
    assert not geometria.mostra_o_identificador(arvore, "")
    # O identificador só na barra de endereço (a URL com a dica de login) não é a página dizendo de quem é a senha.
    app = FakeCorreioEmEtapas(tela="senha", email="joana@exemplo.com", no_navegador=True,
                              custom_tab=f"{SITE_DE_LOGIN}/?dica=ana@exemplo.com&x=")
    assert not geometria.mostra_o_identificador(_arvore(app), "ana@exemplo.com")


def test_site_permitido_e_o_declarado_ou_subdominio() -> None:
    assert geometria.host_permitido("login.exemplo.com", ["login.exemplo.com"])
    assert geometria.host_permitido("a.login.exemplo.com", ["login.exemplo.com"])
    assert not geometria.host_permitido("login.exemplo.com.golpe.net", ["login.exemplo.com"])
    assert not geometria.host_permitido("xlogin.exemplo.com", ["login.exemplo.com"])
    assert not geometria.host_permitido("", ["login.exemplo.com"])
    assert geometria.host_da_url("https://Login.Exemplo.com:443/entrar?x=1") == "login.exemplo.com"


def test_acesso_a_conta_com_dois_candidatos_nao_toca_em_nenhum() -> None:
    arvore = _arvore(FakeCorreioEmEtapas(conta=EMAIL, tela="caixa"))
    assert geometria.account_opener(arvore, pacote=ETAPAS, prefixos_de_id=("avatar",), rotulos=()) is not None
    assert geometria.account_opener(arvore, pacote=ETAPAS, prefixos_de_id=("avatar",),
                                    rotulos=(re.compile("mensagens|contas", re.I),)) is not None
    # Um rótulo largo demais pega mais de um elemento: nenhum.
    largo = parse_hierarchy(FakeCorreioEmEtapas(conta=EMAIL, tela="caixa").page_source().replace(
        "</hierarchy>", '<node class="android.widget.ImageView" package="com.exemplo.etapas" text="" '
        'resource-id="com.exemplo.etapas:id/avatar_de_outro" content-desc="" clickable="true" enabled="true" '
        'focused="false" password="false" scrollable="false" bounds="[600,40][700,120]" /></hierarchy>'))
    assert geometria.account_opener(largo, pacote=ETAPAS, prefixos_de_id=("avatar",), rotulos=()) is None


def test_nao_salvar_login_aceita_o_texto_sem_clicavel_quando_e_um_so() -> None:
    """Instagram 447 (android-01, 30/09/2026): no "Save your login info?" o "Not now" é um View sem `clickable` (o
    contêiner acima trata o toque). Sem clicável que case, vale o texto sozinho, se for UM e o rótulo inteiro for a
    recusa; dois iguais, ou o texto no meio de outra frase, não são escolhidos."""
    agora_nao = re.compile(r"^\s*not now\s*$", re.I)

    def arvore(*nos: tuple[str, bool]) -> Any:
        linhas = "".join(
            f'<node class="android.view.View" package="com.instagram.android" text="{t}" resource-id="" '
            f'content-desc="" clickable="{str(c).lower()}" enabled="true" bounds="[100,{900 + 100 * i}][600,{950 + 100 * i}]" />'
            for i, (t, c) in enumerate(nos))
        return parse_hierarchy(f"<hierarchy>{linhas}</hierarchy>")

    achado = geometria.save_login_dismiss(arvore(("Save your login info?", False), ("Save", False), ("Not now", False)),
                                          agora_nao=agora_nao)
    assert achado is not None and achado.text == "Not now"
    assert geometria.save_login_dismiss(arvore(("Not now", False), ("Not now", False)), agora_nao=agora_nao) is None
    assert geometria.save_login_dismiss(arvore(("Tap Not now to skip", False),), agora_nao=agora_nao) is None
