"""Sessão como DADO (ADR-052, fatia 3): o login deixou de ser código do Instagram e virou um motor genérico
(`app/integrations/app_declarado/`) dirigido pelo `sessao.yaml` de cada app.

- a catraca: o motor não conhece app nenhum, e o login do Instagram não volta a ser Python;
- a compatibilidade: os padrões do `sessao.yaml` são os do `InstagramCfg`, e o `config.yaml` da instalação continua
  sobrescrevendo pelo bloco `instagram:` (só o que foi escrito);
- a carga recusa arquivo errado com o caminho do campo;
- um cliente de e-mail declarado SÓ em arquivos de dado ganha login, conferência da conta, desafio e senha recusada
  pelo mesmo motor — sem uma linha de Python do app.

Nível de prova: `simulated` (dublês de aparelho; nenhum emulador, nenhuma conta real).
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import quoteattr

import pytest
import yaml

from app.automation import conhecimento_de_telas as telas
from app.config import AJUSTES_DE_SESSAO, InstagramCfg
from app.db import Database
from app.events import EventBus
from app.integrations.app_declarado import conhecimento
from app.integrations.app_declarado.conhecimento import SessaoInvalida, do_app
from app.integrations.app_declarado.sessao import Outcome, SessaoDeclarada
from app.integrations.instagram import manifesto
from app.models import SessionStatus
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService

from .conftest import make_config
from .fake_instagram import PKG
from .test_instagram_auth import FakeDevices, FakeRt, cadastrar

RAIZ = Path(__file__).resolve().parents[1]                      # backend/
MOTOR = RAIZ / "app" / "integrations" / "app_declarado"
PASTA_DO_INSTAGRAM = RAIZ / "app" / "conhecimento" / "apps" / PKG


# ------------------------------------------------------------------ catracas
def test_o_motor_de_sessao_nao_conhece_app_nenhum() -> None:
    """Nenhum arquivo do motor cita o Instagram — nem em comentário. O conhecimento dele é dado; se um achado do app
    precisa ser lembrado, mora no comentário do `sessao.yaml`/`telas.yaml` dele."""
    arquivos = sorted(p for p in MOTOR.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    assert {p.name for p in arquivos} >= {"__init__.py", "conhecimento.py", "formulario.py", "sessao.py"}
    citam = [p.relative_to(RAIZ).as_posix() for p in arquivos if "instagram" in p.read_text(encoding="utf-8").lower()]
    assert citam == [], f"o motor genérico voltou a conhecer o Instagram: {citam}"


def test_o_login_do_instagram_nao_volta_a_ser_python() -> None:
    """A catraca da fatia 3, como a da fatia 1 em `test_conhecimento_de_telas`: autenticador, verificação e
    reconciliação do Instagram deixaram de existir, e as peças de login saíram de `navigation.py`."""
    pasta = RAIZ / "app" / "integrations" / "instagram"
    for apagado in ("authentication.py", "verification.py", "reconciliation.py"):
        assert not (pasta / apagado).exists(), apagado
    fonte = (pasta / "navigation.py").read_text(encoding="utf-8")
    for literal in ("def login_form", "def dismiss_button", "def save_login_dismiss", "def password_field",
                    "alert_dialog_cancel", "negative_button", "maybe later", "profile_tab"):
        assert literal not in fonte, literal
    # A fábrica do manifesto monta o motor genérico com o conhecimento do pacote do Instagram.
    provedor = manifesto.sessao(manifesto.SessionDeps(None, None, None, None, None, None))  # type: ignore[arg-type]
    assert isinstance(provedor, SessaoDeclarada) and provedor.package == PKG
    assert provedor.conhecimento is do_app(PKG)


# ------------------------------------------------------------------ compatibilidade com o `config.yaml`
def test_ajustes_padrao_do_sessao_yaml_sao_os_do_instagram_cfg() -> None:
    """É isto que garante o comportamento idêntico numa instalação que não escreve o bloco `instagram:`: antes, os
    padrões vinham do `InstagramCfg`; agora vêm do dado — e são os mesmos, do mesmo tipo (o log imprime o cooldown
    como veio: "300s", não "300.0s")."""
    ajustes = do_app(PKG).ajustes
    for campo in AJUSTES_DE_SESSAO:
        padrao = InstagramCfg.model_fields[campo].default
        valor = getattr(ajustes, campo)
        assert valor == padrao and type(valor) is type(padrao), campo


def test_config_sobrescreve_so_o_que_a_instalacao_escreveu(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert cfg.ajustes_de_sessao(PKG) == {}                              # nada escrito: vale o dado do app
    sessao = SessaoDeclarada(do_app(PKG), cfg, None, None, None, None, None)  # type: ignore[arg-type]
    assert sessao.ajustes == do_app(PKG).ajustes

    cfg.file.instagram.settle_s = 0.5
    cfg.file.instagram.max_auth_attempts = 5
    assert cfg.ajustes_de_sessao(PKG) == {"settle_s": 0.5, "max_auth_attempts": 5}
    # Lido a cada uso: a mudança vale sem remontar o provedor, e o resto continua do dado.
    assert sessao.ajustes.settle_s == 0.5 and sessao.ajustes.max_auth_attempts == 5
    assert sessao.ajustes.submit_wait_s == do_app(PKG).ajustes.submit_wait_s
    # O bloco `instagram:` é do Instagram: outro pacote não herda nada dele.
    assert cfg.ajustes_de_sessao("com.exemplo.email") == {}


# ------------------------------------------------------------------ carga: arquivo errado é recusado
def _dados_do_instagram() -> dict[str, Any]:
    return yaml.safe_load((PASTA_DO_INSTAGRAM / "sessao.yaml").read_text(encoding="utf-8"))


def _com_sinal_so_em_ingles() -> telas.ConhecimentoDeTelas:
    dados = yaml.safe_load((PASTA_DO_INSTAGRAM / "telas.yaml").read_text(encoding="utf-8"))
    dados["sinais"]["en"]["so_em_ingles"] = "only english"
    return telas.de_dados(dados)


def test_o_sessao_yaml_do_instagram_carrega() -> None:
    """Controle dos casos abaixo: sem a mexida, o mesmo dado passa."""
    k = conhecimento.de_dados(_dados_do_instagram(), do_app(PKG).telas)
    assert k.app == PKG and k.rotulo == "Instagram" and len(k.depois_do_envio) == 6


@pytest.mark.parametrize(("mexe", "trecho"), [
    (lambda d: d.update(tentativas=3), "campo desconhecido"),
    (lambda d: d["conta"]["aba"].update(prefixo="x"), r"conta\.aba: campo desconhecido"),
    (lambda d: d["depois_do_envio"][0].update(resultado="x"), "campo desconhecido"),
    (lambda d: d.pop("textos"), "falta textos"),
    (lambda d: d["formulario"].update(sinal_do_botao="nao_existe"), "sinal 'nao_existe'"),
    (lambda d: d["dispensa"].update(sinal_de_salvar_login="nao_existe"), "sinal 'nao_existe'"),
    (lambda d: d["depois_do_envio"][0].update(sinal="nao_existe"), "sinal 'nao_existe'"),
    (lambda d: d["ajustes"].update(max_auth_attempts="3"), "inteiro"),
    (lambda d: d["ajustes"].update(settle_s=True), "número"),
    (lambda d: d["conta"].update(passos_max=0), "inteiro maior ou igual a 1"),
    (lambda d: d["conta"]["aba"].update(faixa_inferior=1.5), "fração"),
    (lambda d: d["conta"].update(tela_de_perfil="perfil_x"), "tela 'perfil_x'"),
    (lambda d: d["conta"].update(extracao="nao_existe"), "extração"),
    (lambda d: d["depois_do_envio"][0].update(desfecho="retryable"), "desfecho 'retryable'"),
    (lambda d: d["depois_do_envio"][0].update(desfecho="session_ready"), "desfecho 'session_ready'"),
    (lambda d: d["depois_do_envio"][0].update(tipos=["login"]), "exatamente um critério"),
    (lambda d: d["depois_do_envio"][2].update(tipos=["qualquer"]), "tipo qualquer"),
    (lambda d: d["depois_do_envio"][4].update(detalhe="x"), "conferir_conta"),
    (lambda d: d["depois_do_envio"].clear(), "sem regra"),
    (lambda d: d["dispensa"]["rotulos"].append("(sem fechar"), "expressão regular"),
    (lambda d: d["textos"].update(desafio="resolva {conta}"), "lacuna 'conta'"),
    (lambda d: d["textos"].update(credencial_recusada="@{usuario"), "desbalanceadas"),
    (lambda d: d.update(app="com.outro.app"), "difere do `telas.yaml`"),
])
def test_sessao_yaml_invalido_e_recusado_na_carga(mexe: Callable[[dict[str, Any]], object], trecho: str) -> None:
    dados = copy.deepcopy(_dados_do_instagram())
    mexe(dados)
    with pytest.raises(SessaoInvalida, match=trecho):
        conhecimento.de_dados(dados, do_app(PKG).telas)


def test_sinal_que_falta_num_idioma_e_recusado() -> None:
    """O motor lê a tabela do idioma DO APARELHO: um sinal só em inglês quebraria no meio de um login em pt."""
    dados = _dados_do_instagram()
    dados["formulario"]["sinal_do_botao"] = "so_em_ingles"
    with pytest.raises(SessaoInvalida, match="falta em: pt"):
        conhecimento.de_dados(dados, _com_sinal_so_em_ingles())


def test_a_recusa_e_da_mesma_familia_do_conhecimento_de_telas(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Quem já trata `ConhecimentoInvalido` (arquivo de telas) trata o de sessão também. E a pasta de um app que traz
    o conhecimento de OUTRO (cópia sem ajuste) é recusada."""
    assert issubclass(SessaoInvalida, telas.ConhecimentoInvalido)
    _gravar_correio(tmp_path / "com.copia.errada")
    monkeypatch.setattr(conhecimento, "PASTA_DOS_APPS", tmp_path)
    with pytest.raises(SessaoInvalida, match="traz o conhecimento de 'com.exemplo.email'"):
        conhecimento.do_app.__wrapped__("com.copia.errada")


# ------------------------------------------------------------------ um app novo, só com dado
CORREIO = "com.exemplo.email"

TELAS_DO_CORREIO: dict[str, Any] = {
    "app": CORREIO, "versao": 1, "idioma_padrao": "pt",
    "sinais": {"pt": {"entrar": r"^\s*entrar\s*$", "outra_conta": "google", "senha_errada": "senha incorreta",
                      "desafio": "confirme sua identidade", "agora_nao": r"^\s*agora n[ãa]o\s*$"}},
    "extracoes": {"conta": {"ids": ["account_name"], "padrao": r"^@?([a-z0-9._]{1,30})$"}},
    "telas": [
        {"tela": "desafio", "tipo": "desafio", "sinal": "desafio", "razao": "o correio pede confirmação"},
        {"tela": "entrada", "tipo": "login", "formulario_de_senha": True, "razao": "formulário de entrada"},
        {"tela": "conta", "tipo": "autenticada", "autenticada": True, "extracao": "conta", "ids": ["account_header"],
         "razao": "tela da conta"},
        {"tela": "caixa", "tipo": "autenticada", "autenticada": True, "ids": ["message_list"], "razao": "caixa"},
    ],
    "estado_conhecido": {"telas": ["caixa", "conta"], "voltar_max": 2, "reabrir": True},
}

SESSAO_DO_CORREIO: dict[str, Any] = {
    "app": CORREIO, "versao": 1, "rotulo": "Correio de Exemplo",
    "ajustes": {"max_auth_attempts": 3, "auth_cooldown_s": 0, "open_timeout_s": 0.5, "settle_s": 0.01,
                "submit_wait_s": 6.0, "verify_timeout_s": 5.0},
    "formulario": {"sinal_do_botao": "entrar", "sinal_de_exclusao": "outra_conta"},
    "dispensa": {"intersticiais_max": 2, "rotulos": [r"^\s*pular\s*$"], "ids": ["botao_negativo"],
                 "sinal_de_salvar_login": "agora_nao"},
    "conta": {"extracao": "conta", "tela_de_perfil": "conta",
              "aba": {"prefixo_de_id": "conta_tab", "rotulos": ["conta"], "faixa_inferior": 0.88},
              "passos_max": 3, "espera_s": 0.01},
    "depois_do_envio": [
        {"sinal": "senha_errada", "desfecho": "invalid_credential", "detalhe": "o correio recusou a senha"},
        {"tipos": ["desafio"], "desfecho": "auth_challenge", "detalhe": "o correio pediu confirmação de identidade"},
        {"telas": ["caixa", "conta"], "desfecho": "conferir_conta"},
        {"tipos": ["login"], "desfecho": "uncertain", "detalhe": "ficou na entrada do correio"},
    ],
    "textos": {
        "desafio": "O Correio de Exemplo pediu confirmação. Assuma o controle do aparelho e resolva na tela.",
        "desafio_pendente": "o Correio de Exemplo pediu confirmação",
        "senha_recusada": "a senha foi recusada pelo Correio de Exemplo; troque-a no portal",
        "credencial_recusada": "a senha de @{usuario} foi recusada pelo Correio de Exemplo",
    },
}


def _gravar_correio(pasta: Path) -> Path:
    """O pacote de conhecimento do correio em ARQUIVOS, como o de um app de verdade: nada dele é Python."""
    pasta.mkdir(parents=True)
    (pasta / "telas.yaml").write_text(yaml.safe_dump(TELAS_DO_CORREIO, allow_unicode=True), encoding="utf-8")
    (pasta / "sessao.yaml").write_text(yaml.safe_dump(SESSAO_DO_CORREIO, allow_unicode=True), encoding="utf-8")
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


@dataclass
class FakeCorreio:
    """Um cliente de e-mail de mentira: entrada (usuário, senha, "Entrar" e "Entrar com Google"), caixa, tela da conta
    e desafio. `conta` é quem está logado; `senha_aceita`, a senha que ele aceita."""

    conta: str | None = None
    senha_aceita: str = ""
    tela: str = "entrada"
    desafio_ao_entrar: bool = False
    conta_na_caixa: bool = True
    usuario: str = ""
    senha: str = ""
    erro: bool = False
    typed: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    _foco: str = ""
    _nos: list[_No] = field(default_factory=list)

    def current_package(self) -> str | None:
        return CORREIO

    def current_focus(self) -> tuple[str | None, str | None]:
        return CORREIO, ".Principal"

    def getprop(self, name: str) -> str:
        return "pt-BR" if name == "ro.product.locale" else ""

    def start_app(self, package: str, activity: str | None = None) -> None:
        if self.tela != "desafio":
            self.tela = "caixa" if self.conta else "entrada"

    def _montar(self) -> list[_No]:
        barra = [_No("android.widget.ImageView", (20, 1180, 340, 1260), desc="Caixa", rid="inbox_tab", clickable=True),
                 _No("android.widget.ImageView", (380, 1180, 700, 1260), desc="Conta", rid="conta_tab",
                     clickable=True, acao="conta")]
        if self.tela == "desafio":
            return [_No("android.widget.TextView", (40, 200, 680, 260), text="Confirme sua identidade"),
                    _No("android.widget.Button", (40, 900, 680, 960), text="Continuar", clickable=True)]
        if self.tela == "caixa":
            topo = [_No("android.widget.TextView", (40, 60, 400, 110), text=self.conta or "", rid="account_name")] \
                if self.conta_na_caixa else []
            return [*topo, _No("androidx.recyclerview.widget.RecyclerView", (0, 130, 720, 1150), rid="message_list"),
                    *barra]
        if self.tela == "conta":
            return [_No("android.view.View", (0, 40, 720, 300), rid="account_header"),
                    _No("android.widget.TextView", (40, 120, 400, 180), text=self.conta or "", rid="account_name"),
                    *barra]
        erro = [_No("android.widget.TextView", (40, 600, 680, 650), text="Senha incorreta.")] if self.erro else []
        return [
            _No("android.widget.EditText", (40, 400, 680, 470), text=self.usuario, rid="campo_usuario", clickable=True,
                acao="foco:usuario"),
            _No("android.widget.EditText", (40, 500, 680, 570), text="••••" if self.senha else "", rid="campo_senha",
                clickable=True, password=True, acao="foco:senha"),
            *erro,
            _No("android.widget.Button", (40, 680, 680, 750), text="Entrar", rid="botao_entrar", clickable=True,
                acao="enviar"),
            _No("android.widget.TextView", (40, 800, 680, 850), text="Entrar com Google", clickable=True),
        ]

    def page_source(self) -> str:
        self._nos = self._montar()
        linhas = "".join(
            f'<node class={quoteattr(n.cls)} package="{CORREIO}" text={quoteattr(n.text)} '
            f'resource-id={quoteattr((CORREIO + ":id/" + n.rid) if n.rid else "")} content-desc={quoteattr(n.desc)} '
            f'clickable="{str(n.clickable).lower()}" enabled="true" focused="false" '
            f'password="{str(n.password).lower()}" scrollable="false" '
            f'bounds="[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]" />' for n in self._nos)
        return f'<hierarchy rotation="0">{linhas}</hierarchy>'

    def tap(self, x: int, y: int) -> None:
        self.calls.append(f"tap:{x},{y}")
        alvo = next((n for n in self._nos if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                     and n.bounds[1] <= y <= n.bounds[3]), None)
        if alvo is None:
            return
        if alvo.acao.startswith("foco:"):
            self._foco = alvo.acao.split(":", 1)[1]
        elif alvo.acao == "conta":
            self.tela = "conta"
        elif alvo.acao == "enviar":
            self.calls.append("enviar")
            if self.senha != self.senha_aceita:
                self.erro, self.senha = True, ""
            elif self.desafio_ao_entrar:
                self.tela = "desafio"
            else:
                self.conta, self.senha, self.tela = self.usuario.lstrip("@"), "", "caixa"

    def type_text(self, text: str, *, clear_first: bool) -> None:
        if text:
            self.typed.append(text)
        if self._foco == "senha":
            self.senha = text if clear_first else self.senha + text
        else:
            self.usuario = text if clear_first else self.usuario + text

    def press_key(self, key: str) -> None:
        self.calls.append(f"key:{key}")


SENHA_DO_CORREIO = "correio-Senha#7"
USUARIO_DO_CORREIO = "ana.correio"


@pytest.fixture
def correio(tmp_path: Path) -> Any:
    """O motor montado com o conhecimento LIDO DOS ARQUIVOS do correio, sobre um banco e um cofre de verdade."""
    k = conhecimento.carregar(_gravar_correio(tmp_path / CORREIO))
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    bus = EventBus(db)
    secrets = SecretStore(db, MemoryKeyProvider())
    repo = SocialRepository(db)
    social = SocialService(repo, secrets, bus, known_instances=lambda: ["android-01", "android-02"])

    def montar(app: FakeCorreio) -> tuple[SessaoDeclarada, SocialRepository, str]:
        sessao = SessaoDeclarada(k, cfg, FakeDevices(app), repo, secrets,  # type: ignore[arg-type]
                                 SensitiveInputChannel(lambda: True), bus)
        sessao.focus_poll_s = 0.01
        # A conta ainda é cadastrada pelo caminho de sempre (a persona tem um app âncora — item 12.3); o que se
        # prova aqui é o motor de sessão, que só lê o usuário e a credencial do perfil.
        pid = cadastrar(social, username=USUARIO_DO_CORREIO, senha=SENHA_DO_CORREIO)
        return sessao, repo, pid

    yield montar, db
    db.close()


async def test_um_app_novo_entra_e_confere_a_conta_so_com_dados(correio: Any) -> None:
    montar, db = correio
    app = FakeCorreio(senha_aceita=SENHA_DO_CORREIO)
    sessao, repo, pid = montar(app)
    assert sessao.package == CORREIO
    r = await sessao.ensure_session(FakeRt(app), pid)                  # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert r.observed_username == USUARIO_DO_CORREIO and app.conta == USUARIO_DO_CORREIO
    assert "enviar" in app.calls and SENHA_DO_CORREIO in app.typed        # "Entrar com Google" nunca foi candidato
    assert repo.session_row(pid)["status"] == SessionStatus.session_ready.value


async def test_um_app_novo_le_a_conta_pela_aba_declarada(correio: Any) -> None:
    """Logado, com a conta fora da caixa: o motor toca na aba de conta DECLARADA e lê de lá, sem digitar nada."""
    montar, _ = correio
    app = FakeCorreio(conta=USUARIO_DO_CORREIO, tela="caixa", conta_na_caixa=False)
    sessao, repo, pid = montar(app)
    r = await sessao.ensure_session(FakeRt(app), pid)                  # type: ignore[arg-type]
    assert r.ready and not r.attempted_login and app.typed == []
    assert app.tela == "conta"


async def test_um_app_novo_recusa_senha_com_os_proprios_textos(correio: Any) -> None:
    montar, db = correio
    app = FakeCorreio(senha_aceita="outra")
    sessao, repo, pid = montar(app)
    r = await sessao.ensure_session(FakeRt(app), pid)                  # type: ignore[arg-type]
    assert r.outcome is Outcome.INVALID_CREDENTIAL and r.detail == "o correio recusou a senha"
    assert repo.credential_row(pid)["status"] == "invalid"
    app.calls.clear()
    r2 = await sessao.ensure_session(FakeRt(app), pid)                 # type: ignore[arg-type]
    assert r2.detail == "a senha foi recusada pelo Correio de Exemplo; troque-a no portal"
    assert app.calls == []                                             # nem tocou no aparelho
    avisos = [r["message"] for r in db.query("SELECT message FROM events WHERE kind='log'")]
    assert any(f"a senha de @{USUARIO_DO_CORREIO} foi recusada pelo Correio de Exemplo" in m for m in avisos)


async def test_um_app_novo_para_no_desafio_e_chama_a_pessoa(correio: Any) -> None:
    """Desafio segue com a pessoa (ADR-009/ADR-029), em qualquer app: o perfil é bloqueado com o rótulo do app."""
    montar, db = correio
    app = FakeCorreio(senha_aceita=SENHA_DO_CORREIO, desafio_ao_entrar=True)
    sessao, repo, pid = montar(app)
    r = await sessao.ensure_session(FakeRt(app), pid)                  # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE
    assert "O Correio de Exemplo pediu confirmação" in (repo.session_row(pid)["detail"] or "")
    assert repo.profile_row(pid)["status"] == "blocked"
    bloqueio = [json.loads(r["data"]) for r in db.query(
        "SELECT data FROM events WHERE kind='log' AND message LIKE ?", ("%bloqueado automaticamente%",))]
    assert len(bloqueio) == 1
    mensagens = [r["message"] for r in db.query("SELECT message FROM events WHERE kind='log'")]
    assert any("o Correio de Exemplo pediu verificação" in m for m in mensagens)
