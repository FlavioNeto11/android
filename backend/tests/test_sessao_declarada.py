"""Sessão como DADO (ADR-052, fatia 3): o login deixou de ser código do Instagram e virou um motor genérico
(`app/integrations/app_declarado/`) dirigido pelo `sessao.yaml` de cada app.

- a catraca: o motor não conhece app nenhum, e o login do Instagram não volta a ser Python;
- a compatibilidade: os padrões do `sessao.yaml` são os do antigo `InstagramCfg`, e o `config.yaml` da instalação
  sobrescreve por pacote em `contas.sessao.<pacote>` (só o que foi escrito; fatia 4);
- a carga recusa arquivo errado com o caminho do campo;
- um cliente de e-mail declarado SÓ em arquivos de dado ganha login, conferência da conta, desafio e senha recusada
  pelo mesmo motor — sem uma linha de Python do app;
- sessão por CONTA (item 23.4): com a conta âncora e a do correio na mesma persona, nada do login de um escreve na
  conta do outro, a conta dita tem de ser do perfil e do app, e a conta lida confere pelo @ e pelo login DA CONTA.
- escopo do desafio (item 23.5, P9): no correio (que não é o âncora), código e conta travada param só a conta dele
  (credencial em `review`), a trava mantém a quarentena do aparelho e a persona segue; no âncora, nada mudou.

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
from pydantic import SecretStr

from app.automation import conhecimento_de_telas as telas
from app.config import AJUSTES_DE_SESSAO
from app.db import Database
from app.events import EventBus
from app.integrations.app_declarado import conhecimento
from app.integrations.app_declarado.conhecimento import SessaoInvalida, do_app
from app.integrations.app_declarado.sessao import Outcome, SessaoDeclarada
from app.integrations.app_declarado.pacote import PASTA_DOS_APPS, manifesto_da_pasta
from app.models import ProfileAccountCreate, SessionStatus
from app.modules.identity.infrastructure.sessions import SessionDeps
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService

from .conftest import make_config
from .fake_instagram import PKG, FakeInstagram
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
    """A catraca da fatia 3: autenticador, verificação, reconciliação e navegação do Instagram deixaram de existir (a
    pasta inteira saiu na integração das fatias 2–4), e a sessão dele é o motor genérico com o dado do pacote."""
    assert not (RAIZ / "app" / "integrations" / "instagram").exists()
    # A fábrica do manifesto DESCOBERTO monta o motor genérico com o conhecimento do pacote do Instagram.
    fabrica = manifesto_da_pasta(PASTA_DOS_APPS / PKG).session
    assert fabrica is not None
    provedor = fabrica(SessionDeps(None, None, None, None, None, None))  # type: ignore[arg-type]
    assert isinstance(provedor, SessaoDeclarada) and provedor.package == PKG
    assert provedor.conhecimento is do_app(PKG)


# ------------------------------------------------------------------ compatibilidade com o `config.yaml`
#: Os padrões que o antigo `InstagramCfg` tinha, até a fatia 3. O `sessao.yaml` do Instagram precisa dizer os mesmos,
#: do mesmo tipo (o log imprime o cooldown como veio: "300s", não "300.0s").
#: `max_logins_per_day` não existia antes: é o teto diário do ADR-055 (29/09/2026), declarado no mesmo dado.
PADROES_DE_ANTES: dict[str, int | float] = {"max_auth_attempts": 3, "auth_cooldown_s": 300, "open_timeout_s": 60.0,
                                            "settle_s": 3.0, "submit_wait_s": 45.0, "verify_timeout_s": 45.0,
                                            "max_logins_per_day": 3}


def test_ajustes_padrao_do_sessao_yaml_sao_os_de_antes() -> None:
    """É isto que garante o comportamento idêntico numa instalação que não escreve ajuste nenhum: antes, os padrões
    vinham do `InstagramCfg`; agora vêm do dado, e são os mesmos."""
    assert set(AJUSTES_DE_SESSAO) == set(PADROES_DE_ANTES)
    ajustes = do_app(PKG).ajustes
    for campo, padrao in PADROES_DE_ANTES.items():
        valor = getattr(ajustes, campo)
        assert valor == padrao and type(valor) is type(padrao), campo


def test_config_sobrescreve_so_o_que_a_instalacao_escreveu(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert cfg.ajustes_de_sessao(PKG) == {}                              # nada escrito: vale o dado do app
    sessao = SessaoDeclarada(do_app(PKG), cfg, None, None, None, None, None)  # type: ignore[arg-type]
    assert sessao.ajustes == do_app(PKG).ajustes

    cfg.file.contas.ajustes(PKG).settle_s = 0.5
    cfg.file.contas.ajustes(PKG).max_auth_attempts = 5
    assert cfg.ajustes_de_sessao(PKG) == {"settle_s": 0.5, "max_auth_attempts": 5}
    # Lido a cada uso: a mudança vale sem remontar o provedor, e o resto continua do dado.
    assert sessao.ajustes.settle_s == 0.5 and sessao.ajustes.max_auth_attempts == 5
    assert sessao.ajustes.submit_wait_s == do_app(PKG).ajustes.submit_wait_s
    # Os ajustes são por pacote: outro app não herda nada do Instagram.
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
                "submit_wait_s": 6.0, "verify_timeout_s": 5.0, "max_logins_per_day": 3},
    "formulario": {"sinal_do_botao": "entrar", "sinal_de_exclusao": "outra_conta"},
    "dispensa": {"intersticiais_max": 2, "rotulos": [r"^\s*pular\s*$"], "ids": ["botao_negativo"],
                 "sinal_de_salvar_login": "agora_nao"},
    "conta": {"extracao": "conta", "tela_de_perfil": "conta",
              "aba": {"prefixo_de_id": "conta_tab", "rotulos": ["conta"], "faixa_inferior": 0.88},
              "passos_max": 3, "espera_s": 0.01},
    "depois_do_envio": [
        {"sinal": "senha_errada", "desfecho": "invalid_credential", "detalhe": "o correio recusou a senha"},
        {"tipos": ["desafio", "dois_fatores"], "desfecho": "auth_challenge",
         "detalhe": "o correio pediu confirmação de identidade"},
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
    """Um cliente de e-mail de mentira: entrada (usuário, senha, "Entrar" e "Entrar com Google"), caixa, tela da conta,
    desafio e pedido de código (`codigo`, com o campo onde digitá-lo: o sinal genérico de código exige onde digitar).
    `conta` é quem está logado; `senha_aceita`, a senha que ele aceita."""

    conta: str | None = None
    senha_aceita: str = ""
    tela: str = "entrada"
    desafio_ao_entrar: bool = False
    codigo_ao_entrar: bool = False
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
        if self.tela not in ("desafio", "codigo"):
            self.tela = "caixa" if self.conta else "entrada"

    def _montar(self) -> list[_No]:
        barra = [_No("android.widget.ImageView", (20, 1180, 340, 1260), desc="Caixa", rid="inbox_tab", clickable=True),
                 _No("android.widget.ImageView", (380, 1180, 700, 1260), desc="Conta", rid="conta_tab",
                     clickable=True, acao="conta")]
        if self.tela == "desafio":
            return [_No("android.widget.TextView", (40, 200, 680, 260), text="Confirme sua identidade"),
                    _No("android.widget.Button", (40, 900, 680, 960), text="Continuar", clickable=True)]
        if self.tela == "codigo":
            return [_No("android.widget.TextView", (40, 200, 680, 260), text="Digite o código de segurança"),
                    _No("android.widget.EditText", (40, 400, 680, 470), rid="campo_codigo", clickable=True),
                    _No("android.widget.Button", (40, 900, 680, 960), text="Confirmar", clickable=True)]
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
            elif self.codigo_ao_entrar:
                self.tela = "codigo"
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
#: A conta âncora da mesma persona: OUTRO @ e OUTRA senha, de propósito. Se o motor do correio lesse ou gravasse na
#: conta âncora (o login preso à âncora, item 23.4), ele digitaria esta senha, conferiria este @ e escreveria nesta
#: conta — e os testes abaixo veriam.
USUARIO_DA_ANCORA = "ana.ancora"
SENHA_DA_ANCORA = "ancora-Senha#3"


@dataclass
class Montado:
    sessao: SessaoDeclarada
    repo: SocialRepository
    pid: str
    conta: str                 # a conta do correio
    ancora: str                # a conta âncora da mesma persona


@pytest.fixture
def correio(tmp_path: Path) -> Any:
    """O motor montado com o conhecimento LIDO DOS ARQUIVOS do correio, sobre um banco e um cofre de verdade.

    A persona tem DUAS contas com login gerenciado: a âncora (o app que provê a conta do perfil) e a do correio, cada
    uma com a sua senha e o seu @. `instagram(app)` monta o motor do app âncora sobre o MESMO banco e cofre, para a
    prova no sentido contrário."""
    k = conhecimento.carregar(_gravar_correio(tmp_path / CORREIO))
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    ajustes = cfg.file.contas.ajustes(PKG)
    ajustes.settle_s, ajustes.submit_wait_s, ajustes.open_timeout_s = 0.01, 6, 0.5
    db = Database(cfg.db_dsn)
    db.migrate()
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('correio', 'Correio de Exemplo', ?, 0)",
               (CORREIO,))
    bus = EventBus(db)
    secrets = SecretStore(db, MemoryKeyProvider())
    repo = SocialRepository(db)
    social = SocialService(repo, secrets, bus, known_instances=lambda: ["android-01", "android-02"])

    def montar(app: FakeCorreio, *, usuario_da_ancora: str = USUARIO_DA_ANCORA, com_conta: bool = True,
               login: str | None = None, handle: str = USUARIO_DO_CORREIO, instance_id: str = "android-02") -> Montado:
        sessao = SessaoDeclarada(k, cfg, FakeDevices(app), repo, secrets,  # type: ignore[arg-type]
                                 SensitiveInputChannel(lambda: True), bus)
        sessao.focus_poll_s = 0.01
        pid = cadastrar(social, username=usuario_da_ancora, senha=SENHA_DA_ANCORA, instance_id=instance_id)
        conta = ""
        if com_conta:
            conta = social.add_account(pid, ProfileAccountCreate(
                app_id="correio", handle=handle, login_identifier=login, password=SecretStr(SENHA_DO_CORREIO),
                consent=True)).id
        ancora = repo.conta_ancora(pid)
        assert ancora is not None
        return Montado(sessao, repo, pid, conta, str(ancora["id"]))

    def instagram(app: FakeInstagram) -> SessaoDeclarada:
        motor = SessaoDeclarada(do_app(PKG), cfg, FakeDevices(app), repo, secrets,  # type: ignore[arg-type]
                                SensitiveInputChannel(lambda: True), bus)
        motor.focus_poll_s = 0.01
        return motor

    yield montar, db, instagram
    db.close()


def _linhas_da_conta(db: Database, conta: str) -> dict[str, list[dict[str, Any]]]:
    """Tudo o que o motor de sessão grava por conta: sessão por aparelho, credencial (e a marcação) e tentativas."""
    return {
        "sessoes": [dict(r) for r in db.query("SELECT * FROM account_sessions WHERE account_id=? ORDER BY instance_id",
                                              (conta,))],
        "credencial": [dict(r) for r in db.query("SELECT * FROM account_credentials WHERE account_id=?", (conta,))],
        "tentativas": [dict(r) for r in db.query("SELECT * FROM authentication_attempts WHERE account_id=?"
                                                 " ORDER BY id", (conta,))],
    }


async def test_um_app_novo_entra_e_confere_a_conta_so_com_dados(correio: Any) -> None:
    montar, db, _ = correio
    app = FakeCorreio(senha_aceita=SENHA_DO_CORREIO)
    m = montar(app)
    assert m.sessao.package == CORREIO
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert r.observed_username == USUARIO_DO_CORREIO and app.conta == USUARIO_DO_CORREIO
    assert "enviar" in app.calls and SENHA_DO_CORREIO in app.typed        # "Entrar com Google" nunca foi candidato
    # A senha e o usuário digitados são os da conta DO CORREIO — nunca os da conta âncora da mesma persona.
    assert SENHA_DA_ANCORA not in app.typed and USUARIO_DA_ANCORA not in app.typed
    assert m.repo.account_session_row(m.pid, m.conta, "android-02")["status"] == SessionStatus.session_ready.value


async def test_um_app_novo_le_a_conta_pela_aba_declarada(correio: Any) -> None:
    """Logado, com a conta fora da caixa: o motor toca na aba de conta DECLARADA e lê de lá, sem digitar nada."""
    montar, _, _ = correio
    app = FakeCorreio(conta=USUARIO_DO_CORREIO, tela="caixa", conta_na_caixa=False)
    m = montar(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.ready and not r.attempted_login and app.typed == []
    assert app.tela == "conta"


async def test_um_app_novo_recusa_senha_com_os_proprios_textos(correio: Any) -> None:
    montar, db, _ = correio
    app = FakeCorreio(senha_aceita="outra")
    m = montar(app)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.INVALID_CREDENTIAL and r.detail == "o correio recusou a senha"
    assert m.repo.account_credential_row(m.pid, m.conta)["status"] == "invalid"
    app.calls.clear()
    r2 = await m.sessao.ensure_session(FakeRt(app), m.pid)             # type: ignore[arg-type]
    assert r2.detail == "a senha foi recusada pelo Correio de Exemplo; troque-a no portal"
    assert app.calls == []                                             # nem tocou no aparelho
    avisos = [r["message"] for r in db.query("SELECT message FROM events WHERE kind='log'")]
    assert any(f"a senha de @{USUARIO_DO_CORREIO} foi recusada pelo Correio de Exemplo" in m for m in avisos)


async def test_um_app_novo_para_no_desafio_e_chama_a_pessoa(correio: Any) -> None:
    """Desafio segue com a pessoa (ADR-009), em qualquer app, com o rótulo do app. A conta TRAVADA de um app que não
    é o âncora (item 23.5, decisão do dono P9): o aparelho entra em quarentena com a conta DAQUELE app (ADR-055), o
    login automático dela para (credencial em `review`) — e a persona NÃO é bloqueada: a conta âncora segue."""
    montar, db, _ = correio
    app = FakeCorreio(senha_aceita=SENHA_DO_CORREIO, desafio_ao_entrar=True)
    m = montar(app)
    ancora_antes = _linhas_da_conta(db, m.ancora)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE
    assert "O Correio de Exemplo pediu confirmação" in (
        m.repo.account_session_row(m.pid, m.conta, "android-02")["detail"] or "")
    assert m.repo.profile_row(m.pid)["status"] == "active"
    assert m.repo.account_credential_row(m.pid, m.conta)["status"] == "review"
    assert _linhas_da_conta(db, m.ancora) == ancora_antes              # a conta âncora nem foi tocada
    assert db.query("SELECT 1 FROM events WHERE kind='log' AND message LIKE ?", ("%bloqueado automaticamente%",)) == []
    parada = [json.loads(r["data"]) for r in db.query("SELECT data FROM events WHERE kind='log'")
              if json.loads(r["data"] or "{}").get("reason") == "desafio_na_conta"]
    assert [(d["profile_id"], d["account_id"]) for d in parada] == [(m.pid, m.conta)]
    # A quarentena registra a conta DO CORREIO (o @ e o app dela), não o @ de cadastro da persona.
    marcador = db.one("SELECT handle, app_id, profile_id FROM device_locked_accounts WHERE instance_id='android-02'")
    assert marcador is not None and (marcador["handle"], marcador["app_id"], marcador["profile_id"]) == (
        USUARIO_DO_CORREIO, "correio", m.pid)


@pytest.mark.parametrize("antes_do_envio", [False, True], ids=["depois_do_envio", "na_abertura"])
async def test_codigo_num_app_que_nao_e_o_ancora_para_so_a_conta_dele(correio: Any, antes_do_envio: bool) -> None:
    """Item 23.5 (P9): o código pedido pelo correio — depois do envio da senha, ou já na abertura, sem senha enviada —
    para SÓ a conta do correio: a sessão dela vai a `auth_challenge`, a credencial dela a `review` (o login automático
    para em todo aparelho), a persona segue `active`, a conta âncora fica byte a byte igual e o aparelho não entra em
    quarentena (código não é conta travada)."""
    montar, db, _ = correio
    app = (FakeCorreio(tela="codigo") if antes_do_envio
           else FakeCorreio(senha_aceita=SENHA_DO_CORREIO, codigo_ao_entrar=True))
    m = montar(app)
    ancora_antes = _linhas_da_conta(db, m.ancora)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
    if antes_do_envio:
        assert app.typed == [] and r.attempted_login is False          # nada digitado na tela de código
    sessao = m.repo.account_session_row(m.pid, m.conta, "android-02")
    assert sessao["status"] == SessionStatus.auth_challenge.value and "codigo" in (sessao["detail"] or "")
    assert m.repo.account_credential_row(m.pid, m.conta)["status"] == "review"
    assert m.repo.profile_row(m.pid)["status"] == "active"
    assert _linhas_da_conta(db, m.ancora) == ancora_antes
    assert db.one("SELECT 1 FROM device_locked_accounts") is None
    # Chamada automática seguinte: a sessão pede pessoa e nada toca no aparelho.
    app.calls.clear()
    r2 = await m.sessao.ensure_session(FakeRt(app), m.pid, automatic=True)  # type: ignore[arg-type]
    assert r2.outcome is Outcome.AUTH_CHALLENGE and app.calls == []


async def test_o_desafio_no_app_ancora_segue_bloqueando_a_persona(correio: Any) -> None:
    """A âncora não mudou com o 23.5: o desafio de conta travada no Instagram bloqueia a persona (ADR-029) e põe o
    aparelho em quarentena; a conta do correio da mesma persona não é tocada."""
    montar, db, instagram = correio
    m = montar(FakeCorreio())
    correio_antes = _linhas_da_conta(db, m.conta)
    ig_app = FakeInstagram(stored_password=SENHA_DA_ANCORA, challenge_on_login=True)
    r = await instagram(ig_app).ensure_session(FakeRt(ig_app), m.pid)  # type: ignore[arg-type]
    assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
    assert m.repo.profile_row(m.pid)["status"] == "blocked"
    marcador = db.one("SELECT handle, profile_id FROM device_locked_accounts WHERE instance_id='android-02'")
    assert marcador is not None and (marcador["handle"], marcador["profile_id"]) == (USUARIO_DA_ANCORA, m.pid)
    assert _linhas_da_conta(db, m.conta) == correio_antes


# ------------------------------------------------------------------ dois apps de login gerenciado (item 23.4)
@pytest.mark.parametrize("cenario", ["entra", "senha_recusada", "desafio", "conta_errada"])
async def test_o_login_do_segundo_app_nao_escreve_na_conta_do_primeiro(correio: Any, cenario: str) -> None:
    """Credencial, tentativa, marcação da credencial e sessão do correio vão para a conta DO CORREIO. A conta âncora
    da mesma persona sai byte a byte igual — em sucesso, senha recusada, desafio e conta errada."""
    montar, db, _ = correio
    app = {"entra": FakeCorreio(senha_aceita=SENHA_DO_CORREIO),
           "senha_recusada": FakeCorreio(senha_aceita="outra"),
           "desafio": FakeCorreio(senha_aceita=SENHA_DO_CORREIO, desafio_ao_entrar=True),
           # logado na tela com o @ da conta ÂNCORA: para o correio isso é conta errada, não "a conta do perfil"
           "conta_errada": FakeCorreio(conta=USUARIO_DA_ANCORA, tela="caixa")}[cenario]
    m = montar(app)
    antes = _linhas_da_conta(db, m.ancora)
    verificado_antes = m.repo.profile_row(m.pid)["last_verified_at"]

    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]

    esperado = {"entra": Outcome.SESSION_READY, "senha_recusada": Outcome.INVALID_CREDENTIAL,
                "desafio": Outcome.AUTH_CHALLENGE, "conta_errada": Outcome.WRONG_ACCOUNT}[cenario]
    assert r.outcome is esperado, r.detail
    assert _linhas_da_conta(db, m.ancora) == antes                      # a conta âncora nem foi tocada
    assert m.repo.profile_row(m.pid)["last_verified_at"] == verificado_antes   # o cartão do perfil é o da âncora
    do_correio = _linhas_da_conta(db, m.conta)
    assert [s["status"] for s in do_correio["sessoes"]] == [SessionStatus(
        {"entra": "session_ready", "senha_recusada": "auth_required", "desafio": "auth_challenge",
         "conta_errada": "wrong_account"}[cenario]).value]
    if cenario == "conta_errada":
        assert do_correio["tentativas"] == [] and app.typed == []       # conta errada nunca digita nada
        assert f"a esperada é @{USUARIO_DO_CORREIO}" in r.detail and f"@{USUARIO_DA_ANCORA}" in r.detail
    else:
        assert len(do_correio["tentativas"]) == 1                       # a tentativa é da conta do correio
        status = {"entra": "active", "senha_recusada": "invalid", "desafio": "review"}[cenario]
        assert do_correio["credencial"][0]["status"] == status


async def test_o_login_do_primeiro_app_nao_escreve_na_conta_do_segundo(correio: Any) -> None:
    """O sentido contrário: o motor do app âncora, na mesma persona, abre e grava só a conta âncora — a do correio
    fica igual, e a senha dela nunca é digitada."""
    montar, db, instagram = correio
    m = montar(FakeCorreio())
    antes = _linhas_da_conta(db, m.conta)
    ig_app = FakeInstagram(stored_password=SENHA_DA_ANCORA)
    r = await instagram(ig_app).ensure_session(FakeRt(ig_app), m.pid)  # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert r.observed_username == USUARIO_DA_ANCORA
    assert SENHA_DO_CORREIO not in ig_app.typed
    assert _linhas_da_conta(db, m.conta) == antes
    ancora = _linhas_da_conta(db, m.ancora)
    assert [s["status"] for s in ancora["sessoes"]] == [SessionStatus.session_ready.value]
    assert len(ancora["tentativas"]) == 1


async def test_a_conta_dita_tem_de_ser_do_perfil_e_deste_app(correio: Any) -> None:
    """`account_id` de outro app (a conta âncora) ou de outra persona: recusa sem tocar no aparelho e sem gravar."""
    montar, db, _ = correio
    app = FakeCorreio(senha_aceita=SENHA_DO_CORREIO)
    m = montar(app)
    outra = montar(FakeCorreio(), usuario_da_ancora="bia.ancora", instance_id="android-01")
    antes = {c: _linhas_da_conta(db, c) for c in (m.conta, m.ancora, outra.conta, outra.ancora)}
    for conta in (m.ancora, outra.conta, "acc-nao-existe"):
        r = await m.sessao.ensure_session(FakeRt(app), m.pid, account_id=conta)  # type: ignore[arg-type]
        assert r.outcome is Outcome.UNCERTAIN and not r.ready, conta
    assert app.calls == [] and app.typed == []
    assert {c: _linhas_da_conta(db, c) for c in antes} == antes
    # a conta certa, dita, funciona como a resolvida pelo pacote
    r = await m.sessao.ensure_session(FakeRt(app), m.pid, account_id=m.conta)  # type: ignore[arg-type]
    assert r.ready, r.detail


async def test_persona_sem_conta_no_app_recusa_sem_cair_na_ancora(correio: Any) -> None:
    """Sem conta do correio, o motor do correio não usa a conta âncora como se fosse dele: recusa sem tocar."""
    montar, db, _ = correio
    app = FakeCorreio(senha_aceita=SENHA_DA_ANCORA)
    m = montar(app, com_conta=False)
    antes = _linhas_da_conta(db, m.ancora)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.INVALID_CREDENTIAL and "não tem conta no Correio de Exemplo" in r.detail
    assert app.calls == [] and app.typed == []
    assert _linhas_da_conta(db, m.ancora) == antes


async def test_a_conta_lida_confere_pelo_login_da_conta_do_app(correio: Any) -> None:
    """A conta na tela é comparada ao @ E ao login DA CONTA DO APP: um app que mostra o login no cabeçalho confere
    por ele. O @ de cadastro da persona não conta."""
    montar, _, _ = correio
    app = FakeCorreio(conta="ana.login", tela="caixa")
    m = montar(app, handle="Ana do Correio", login="ana.login")
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.ready and r.observed_username == "ana.login", r.detail


async def test_o_teto_diario_e_da_conta_e_nao_soma_os_envios_do_outro_app(correio: Any) -> None:
    """`max_logins_per_day` do correio é 3: três envios da conta âncora em 24 h não gastam o teto do correio."""
    montar, db, _ = correio
    app = FakeCorreio(senha_aceita=SENHA_DO_CORREIO)
    m = montar(app)
    for _ in range(3):
        m.repo.start_auth_attempt(m.pid, "android-02", stage="submitting", account_id=m.ancora)
    r = await m.sessao.ensure_session(FakeRt(app), m.pid)              # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY, r.detail
    # e os do correio, sim: com três envios dele, o próximo login do correio é recusado sem tocar no aparelho
    for _ in range(3):
        m.repo.start_auth_attempt(m.pid, "android-02", stage="submitting", account_id=m.conta)
    app.conta, app.tela = None, "entrada"
    app.calls.clear()
    r2 = await m.sessao.ensure_session(FakeRt(app), m.pid, force_login=True)  # type: ignore[arg-type]
    assert r2.outcome is Outcome.INVALID_CREDENTIAL and "teto diário" in r2.detail
    assert "enviar" not in app.calls
