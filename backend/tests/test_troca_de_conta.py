"""Troca de conta declarada (31.155, ADR-080): com a seção `troca` no `sessao.yaml`, o motor de sessão tira a conta
aberta pelos toques DECLARADOS e entra na conta esperada pelo `_login` de sempre (senha do cofre, só pelo canal
sensível). Sem a seção, nada muda: conta errada continua caso de pessoa (achado #115).

O app é o correio de exemplo de `test_sessao_declarada.py`, declarado SÓ em arquivos de teste, com duas contas de
duas personas e uma saída ("Sair", e a confirmação quando o dublê a pede). Nenhum app do parque declara a troca.

Nível de prova: `simulated` (dublês de aparelho; nenhum emulador, nenhuma conta real).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from app.db import Database
from app.events import EventBus
from app.integrations.app_declarado import conhecimento
from app.integrations.app_declarado.conhecimento import SessaoInvalida
from app.integrations.app_declarado.sessao import Outcome, SessaoDeclarada
from app.models import ProfileAccountCreate, SessionStatus
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService

from .conftest import make_config
from .fake_instagram import PKG
from .test_instagram_auth import FakeDevices, FakeRt, cadastrar
from .test_sessao_declarada import CORREIO, SESSAO_DO_CORREIO, TELAS_DO_CORREIO, FakeCorreio, _No

IID = "android-02"
#: Duas contas do correio, de duas personas, cada uma com a sua senha. Valores de teste, gerados aqui.
CONTA_A, SENHA_A = "bia.correio", "troca-A#1"
CONTA_B, SENHA_B = "caio.correio", "troca-B#2"

TELAS_COM_SAIDA: dict[str, Any] = copy.deepcopy(TELAS_DO_CORREIO)
TELAS_COM_SAIDA["sinais"]["pt"].update({"sair": r"^\s*sair\s*$", "deseja_sair": "deseja sair"})
TELAS_COM_SAIDA["telas"].insert(1, {"tela": "confirmar_saida", "tipo": "intersticial", "sinal": "deseja_sair",
                                    "razao": "confirmação de saída"})

TROCA: dict[str, Any] = {"sair": [{"tela": "conta", "sinal_do_botao": "sair"},
                                  {"tela": "confirmar_saida", "sinal_do_botao": "sair"}]}


def _gravar(pasta: Path, *, troca: dict[str, Any] | None) -> Path:
    pasta.mkdir(parents=True)
    sessao = copy.deepcopy(SESSAO_DO_CORREIO)
    if troca is not None:
        sessao["troca"] = troca
    (pasta / "telas.yaml").write_text(yaml.safe_dump(TELAS_COM_SAIDA, allow_unicode=True), encoding="utf-8")
    (pasta / "sessao.yaml").write_text(yaml.safe_dump(sessao, allow_unicode=True), encoding="utf-8")
    return pasta


@dataclass
class CorreioComSaida(FakeCorreio):
    """O correio com várias contas (`senhas`: @ → senha aceita) e a saída: "Sair" na tela da conta e, com
    `confirmar`, um diálogo "Deseja sair da conta?" com "Sair" e "Cancelar". `sair_abre` muda o destino do "Sair"
    para uma tela que não é a de login (a saída mal declarada)."""

    senhas: dict[str, str] = field(default_factory=dict)
    confirmar: bool = True
    sair_abre: str = "entrada"
    botoes_sair: int = 1

    def _montar(self) -> list[_No]:
        if self.tela == "confirmar_saida":
            return [_No("android.widget.TextView", (40, 400, 680, 460), text="Deseja sair da conta?"),
                    _No("android.widget.Button", (40, 600, 340, 670), text="Cancelar", clickable=True, acao="cancelar"),
                    _No("android.widget.Button", (380, 600, 680, 670), text="Sair", clickable=True,
                        acao="confirmar_sair")]
        nos = super()._montar()
        if self.tela == "conta":
            nos += [_No("android.widget.Button", (40, 400 + 90 * n, 680, 470 + 90 * n), text="Sair", clickable=True,
                        acao="sair") for n in range(self.botoes_sair)]
        return nos

    def tap(self, x: int, y: int) -> None:
        alvo = next((n for n in self._nos if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                     and n.bounds[1] <= y <= n.bounds[3]), None)
        if alvo is not None and alvo.acao in ("sair", "confirmar_sair", "cancelar"):
            self.calls.append(alvo.acao)
            if alvo.acao == "cancelar":
                self.tela = "conta"
            elif alvo.acao == "sair" and self.confirmar:
                self.tela = "confirmar_saida"
            else:
                self.conta, self.usuario, self.senha, self.tela = None, "", "", self.sair_abre
            return
        if alvo is not None and alvo.acao == "enviar":
            self.senha_aceita = self.senhas.get(self.usuario.lstrip("@"), "\x00")
        super().tap(x, y)


@dataclass
class Parque:
    db: Database
    repo: SocialRepository
    social: SocialService
    a: tuple[str, str]           # (persona, conta) de quem está logado no aparelho
    b: tuple[str, str]           # (persona, conta) de quem a tarefa espera
    motor: Any


@pytest.fixture
def parque(tmp_path: Path) -> Any:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('correio', 'Correio de Exemplo', ?, 0)",
               (CORREIO,))
    bus = EventBus(db)
    secrets = SecretStore(db, MemoryKeyProvider())
    repo = SocialRepository(db)
    social = SocialService(repo, secrets, bus, known_instances=lambda: ["android-01", IID, "android-03"])
    pastas = iter(range(100))

    def montar(app: CorreioComSaida, *, troca: dict[str, Any] | None = TROCA) -> Parque:
        k = conhecimento.carregar(_gravar(tmp_path / f"p{next(pastas)}" / CORREIO, troca=troca))
        motor = SessaoDeclarada(k, cfg, FakeDevices(app), repo, secrets,  # type: ignore[arg-type]
                                SensitiveInputChannel(lambda: True), bus)
        motor.focus_poll_s = 0.01
        contas = []
        for n, (handle, senha) in enumerate(((CONTA_A, SENHA_A), (CONTA_B, SENHA_B))):
            pid = cadastrar(social, username=f"ancora.{n}", senha="ancora-Senha#9", instance_id=f"android-0{n * 2 + 1}")
            conta = social.add_account(pid, ProfileAccountCreate(app_id="correio", handle=handle,
                                                                 password=SecretStr(senha), consent=True)).id
            contas.append((pid, conta))
        # A conta A está aberta e verificada neste aparelho.
        repo.set_account_session(contas[0][0], contas[0][1], IID, status=SessionStatus.session_ready,
                                 observed_handle=CONTA_A, verified_at="2026-10-06T00:00:00+00:00")
        return Parque(db, repo, social, contas[0], contas[1], motor)

    yield montar
    db.close()


def _status(p: Parque, quem: tuple[str, str]) -> str:
    return str(p.repo.account_session_row(quem[0], quem[1], IID)["status"])


def _novo_app(**kw: Any) -> CorreioComSaida:
    return CorreioComSaida(conta=CONTA_A, tela="caixa", senhas={CONTA_A: SENHA_A, CONTA_B: SENHA_B}, **kw)


async def test_a_conta_aberta_sai_pelos_toques_declarados_e_a_esperada_entra_pelo_cofre(parque: Any) -> None:
    app = _novo_app()
    p = parque(app)
    r = await p.motor.ensure_session(FakeRt(app, IID), p.b[0], account_id=p.b[1], automatic=True)
    assert r.outcome is Outcome.SESSION_READY, r.detail
    assert app.conta == CONTA_B and r.observed_username == CONTA_B
    # A ordem: a aba da conta, o "Sair", a confirmação, e só depois o login (a senha sai do cofre, a de B).
    assert app.calls.index("sair") < app.calls.index("confirmar_sair") < app.calls.index("enviar")
    assert SENHA_B in app.typed and SENHA_A not in app.typed
    assert _status(p, p.b) == SessionStatus.session_ready.value
    sessao_a = p.repo.account_session_row(p.a[0], p.a[1], IID)
    assert sessao_a["status"] == SessionStatus.unknown.value and "troca de conta" in sessao_a["detail"]
    logs = [r["message"] for r in p.db.query("SELECT message FROM events WHERE kind='log'")]
    assert any("troca de conta" in m for m in logs)
    assert not any(SENHA_B in m or SENHA_A in m for m in logs)          # a senha nunca vai a evento


async def test_sem_a_secao_troca_conta_errada_continua_caso_de_pessoa(parque: Any) -> None:
    app = _novo_app()
    p = parque(app, troca=None)
    r = await p.motor.ensure_session(FakeRt(app, IID), p.b[0], account_id=p.b[1], automatic=True)
    assert r.outcome is Outcome.WRONG_ACCOUNT and "sempre manual" in r.detail
    assert app.conta == CONTA_A and app.typed == [] and "sair" not in app.calls
    assert _status(p, p.a) == SessionStatus.session_ready.value      # a conta aberta nem foi mexida


async def test_verificar_conta_so_le_e_nunca_troca(parque: Any) -> None:
    app = _novo_app()
    p = parque(app)
    r = await p.motor.ensure_session(FakeRt(app, IID), p.b[0], account_id=p.b[1], observe_only=True)
    assert r.outcome is Outcome.WRONG_ACCOUNT
    assert app.conta == CONTA_A and app.typed == [] and "sair" not in app.calls


async def test_saida_que_nao_chega_ao_login_para_sem_digitar_e_nao_vira_laco(parque: Any) -> None:
    """O "Sair" leva a uma tela que não é a de login: nada é digitado, a sessão vai a `wrong_account` com o motivo, e
    o tick seguinte do agendador nem toca no aparelho."""
    app = _novo_app(confirmar=False, sair_abre="desconhecida")
    p = parque(app)
    r = await p.motor.ensure_session(FakeRt(app, IID), p.b[0], account_id=p.b[1], automatic=True)
    assert r.outcome is Outcome.WRONG_ACCOUNT and "troca de conta declarada pelo app não terminou" in r.detail
    assert app.typed == [] and "enviar" not in app.calls
    assert _status(p, p.b) == SessionStatus.wrong_account.value
    assert _status(p, p.a) == SessionStatus.unknown.value                  # houve toque de saída: não vale mais
    app.calls.clear()
    r2 = await p.motor.ensure_session(FakeRt(app, IID), p.b[0], account_id=p.b[1], automatic=True)
    assert r2.outcome is Outcome.WRONG_ACCOUNT and app.calls == []


async def test_dois_botoes_de_sair_e_incerteza_e_nada_e_tocado(parque: Any) -> None:
    app = _novo_app(botoes_sair=2)
    p = parque(app)
    r = await p.motor.ensure_session(FakeRt(app, IID), p.b[0], account_id=p.b[1], automatic=True)
    assert r.outcome is Outcome.WRONG_ACCOUNT and "um candidato só" in r.detail
    assert app.conta == CONTA_A and "sair" not in app.calls and app.typed == []
    assert _status(p, p.a) == SessionStatus.session_ready.value      # nenhum toque de saída: A segue valendo


async def test_conta_esperada_que_nao_pode_entrar_mantem_a_aberta(parque: Any) -> None:
    """Sem consentimento na conta esperada (ADR-040), a troca nem começa: a conta aberta continua logada."""
    app = _novo_app()
    p = parque(app)
    p.db.execute("UPDATE account_credentials SET consent_at=NULL WHERE account_id=?", (p.b[1],))
    r = await p.motor.ensure_session(FakeRt(app, IID), p.b[0], account_id=p.b[1], automatic=True)
    assert r.outcome is Outcome.INVALID_CREDENTIAL and "consentimento" in r.detail
    assert app.conta == CONTA_A and "sair" not in app.calls and app.typed == []
    assert _status(p, p.a) == SessionStatus.session_ready.value


async def test_teto_diario_da_esperada_mantem_a_aberta_e_para_o_login(parque: Any) -> None:
    app = _novo_app()
    p = parque(app)
    for _ in range(3):                                      # max_logins_per_day: 3 no correio
        tentativa = p.repo.start_auth_attempt(p.b[0], IID, stage="submitted", account_id=p.b[1])
        p.repo.finish_auth_attempt(p.b[0], tentativa, outcome="uncertain", stage="submitted")
    r = await p.motor.ensure_session(FakeRt(app, IID), p.b[0], account_id=p.b[1], automatic=True)
    assert r.outcome is Outcome.INVALID_CREDENTIAL and "teto diário" in r.detail
    assert app.conta == CONTA_A and "sair" not in app.calls
    assert p.repo.account_credential_row(p.b[0], p.b[1])["status"] == "review"


def test_troca_mal_declarada_e_recusada_na_carga() -> None:
    telas = conhecimento.telas_.de_dados(copy.deepcopy(TELAS_COM_SAIDA))

    def carregar(troca: Any) -> None:
        dados = copy.deepcopy(SESSAO_DO_CORREIO)
        dados["troca"] = troca
        conhecimento.de_dados(dados, telas)

    carregar(TROCA)                                                        # controle: a declaração certa carrega
    with pytest.raises(SessaoInvalida, match="sem passo nenhum"):
        carregar({"sair": []})
    with pytest.raises(SessaoInvalida, match="campo desconhecido"):
        carregar({"sair": TROCA["sair"], "entrar": []})
    with pytest.raises(SessaoInvalida, match="sinal 'nao_existe'"):
        carregar({"sair": [{"tela": "conta", "sinal_do_botao": "nao_existe"}]})
    with pytest.raises(SessaoInvalida, match="precisa ser do tipo autenticada"):
        carregar({"sair": [{"tela": "confirmar_saida", "sinal_do_botao": "sair"}]})   # 1º passo: tela da conta
    with pytest.raises(SessaoInvalida, match="tipo autenticada ou intersticial"):
        carregar({"sair": [TROCA["sair"][0], {"tela": "desafio", "sinal_do_botao": "sair"}]})


def test_nenhum_app_do_parque_declara_a_troca() -> None:
    """ADR-080: nesta entrega a troca é mecanismo, não adoção. O Instagram (e os demais) segue sem `troca`."""
    from app.integrations.app_declarado.pacote import PASTA_DOS_APPS
    declarados = sorted(p.parent.name for p in PASTA_DOS_APPS.glob("*/sessao.yaml")
                        if "troca" in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}))
    assert PKG not in declarados and declarados == []
