"""A porta de sessão com o contador de "tela não reconhecida" no teto (achado #104) — e o que NÃO conta para ele.

Diagnóstico das execuções reais r-20260928165254-e31953 e r-20260928195344-02ee9e (causa C8): a porta bloqueava sem
olhar o aparelho quando o `unknown_streak` GRAVADO estava no teto. O android-01 ficou preso por um `unknown_streak=4`
gravado em 26/09, que sobreviveu ~47 h e dois reinícios do emulador — as execuções ee9f21, 3e5ad8, 7b32d6 e c17cec
foram bloqueadas em 2–13 ms, sem uma leitura sequer. E o contador era alimentado pelo launcher na frente (o app lento
num convidado de 2 vCPU saturado, android-06), que não é tela do app nenhum, e pelo "Verificar conta" do painel, que
somava acima do teto.

O que estes testes guardam:

* teto gravado ANTES de o aparelho entrar no ar (ou mais velho que a validade) → UMA releitura `observe_only`, nunca o
  bloqueio direto; e uma releitura que lança exceção não é reagendada no tick seguinte (o laço do achado #104);
* launcher / outro app na frente não soma no contador e não pede que a pessoa "identifique a tela";
* "Verificar conta" (e qualquer releitura) não leva o contador acima do teto.

Prova `simulated`: harness com aparelho falso e o motor de sessão sobre o `FakeInstagram`.
"""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pytest

from app.integrations.app_declarado.conhecimento import do_app
from app.integrations.app_declarado.sessao import AuthResult, Outcome, SessaoDeclarada
from app.models import ProfileCreate, SessionStatus
from app.security.sensitive_input import SensitiveInputChannel
from app.state import AppState
from app.util import now, to_iso

from .conftest import Harness
from .fake_instagram import PKG, FakeInstagram, Node
from .test_instagram_auth import SENHA, USUARIO, FakeDevices, FakeRt

IID = "android-01"


class Espiao:
    """Provedor de sessão que só registra quem pediu o quê — e, se mandado, falha como um driver que caiu."""

    package = PKG

    def __init__(self, falha: Exception | None = None) -> None:
        self.falha = falha
        self.chamadas: list[tuple[str, str, bool, bool]] = []

    async def ensure_session(self, rt: Any, profile_id: str, *, force_login: bool = False,
                             automatic: bool = False, observe_only: bool = False) -> AuthResult:
        self.chamadas.append((rt.id, profile_id, automatic, observe_only))
        if self.falha is not None:
            raise self.falha
        return AuthResult(Outcome.UNCERTAIN, "espião: nada lido")


class TelaEstranha(FakeInstagram):
    """O PRÓPRIO app em primeiro plano, numa tela que o conhecimento não mapeia (onboarding novo, por exemplo)."""

    def _build(self) -> list[Node]:
        if self.screen == "launcher":
            return super()._build()
        return [Node("android.widget.TextView", (40, 100, 680, 200), text="Uma novidade que ninguém mapeou")]


class CaiAoAbrirOPerfil(FakeInstagram):
    """Logado no feed, mas o app cai para o launcher ao tocar na aba de perfil — convidado saturado."""

    def tap(self, x: int, y: int) -> None:
        super().tap(x, y)
        if self.screen == "profile":
            self.screen = "launcher"


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _perfil_no_teto(s: AppState) -> str:
    s.appium.log_masking_active = True            # canal sensível comprovado: não é o que estes testes cobrem
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    for i in range(s.settings.get().session_unknown_retry_cap):
        s.social_repo.set_session(pid, status=SessionStatus.unknown, instance_id=IID,
                                  detail=f"tela não reconhecida ({i})", reobserved=True)
    return pid


def _gravada_em(s: AppState, pid: str, quando: str) -> None:
    """Data da última gravação da sessão: é por ela que a porta sabe se o teto é de antes do aparelho entrar no ar."""
    sessao = s.social_repo.session_row(pid, IID)
    assert sessao is not None
    s.db.execute("UPDATE account_sessions SET updated_at=? WHERE account_id=? AND instance_id=?",
                 (quando, sessao["account_id"], IID))


def _motor(s: AppState, app: FakeInstagram) -> SessaoDeclarada:
    """O motor de sessão de verdade, sobre o repositório, o cofre e o barramento do harness — só o aparelho é falso."""
    ajustes = s.cfg.file.contas.ajustes(PKG)
    ajustes.settle_s, ajustes.open_timeout_s = 0.01, 0.3
    motor = SessaoDeclarada(do_app(PKG), s.cfg, FakeDevices(app), s.social_repo,  # type: ignore[arg-type]
                            s.secrets, SensitiveInputChannel(lambda: True), s.bus)
    motor.focus_poll_s = 0.01
    return motor


# ---------------------------------------------------------------- (a) teto velho: relê uma vez antes de bloquear
@pytest.mark.asyncio
async def test_teto_gravado_antes_de_o_aparelho_entrar_no_ar_gera_uma_releitura(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    pid = _perfil_no_teto(s)
    rt = s.devices.get(IID)
    # O harness acabou de ligar o aparelho: uma gravação de 5 min atrás é de ANTES dele entrar no ar.
    _gravada_em(s, pid, to_iso(now() - timedelta(minutes=5)))
    espiao = Espiao()
    monkeypatch.setattr(s.sessoes, "for_package", lambda _pacote: espiao)

    porta = s._session_gate(rt)
    assert porta is not None
    motivo, trabalho = porta
    assert trabalho is not None, f"teto de antes do boot bloqueou sem olhar o aparelho: {motivo}"
    assert "relid" in motivo and "identificar a tela" not in motivo
    await trabalho()
    assert espiao.chamadas == [(IID, pid, False, True)]             # só leitura: nunca autentica

    # A releitura não mudou nada (o espião não grava): na mesma janela, a porta volta a bloquear — sem reler de novo.
    motivo, trabalho = s._session_gate(rt)
    assert trabalho is None and "tentativas seguidas" in motivo
    assert len(espiao.chamadas) == 1

    # O aparelho entrou no ar de novo (reinício do emulador): uma nova janela, uma nova releitura.
    s.db.execute("UPDATE instances SET emulator_started_at=? WHERE id=?", (to_iso(now() + timedelta(seconds=2)), IID))
    motivo, trabalho = s._session_gate(rt)
    assert trabalho is not None, motivo


@pytest.mark.asyncio
async def test_releitura_que_lanca_excecao_nao_reagenda(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado #104 de novo, pelo outro lado: uma releitura que QUEBRA não grava nada, e a gravação continua sendo de
    antes do boot. Sem a trava, o tick seguinte reagendaria a mesma releitura — para sempre, a cada tick."""
    s = _estado(harness)
    pid = _perfil_no_teto(s)
    rt = s.devices.get(IID)
    _gravada_em(s, pid, to_iso(now() - timedelta(minutes=5)))
    espiao = Espiao(falha=RuntimeError("driver caiu no meio da leitura"))
    monkeypatch.setattr(s.sessoes, "for_package", lambda _pacote: espiao)

    motivo, trabalho = s._session_gate(rt)
    assert trabalho is not None, motivo
    with pytest.raises(RuntimeError):
        await trabalho()

    for _ in range(3):
        motivo, trabalho = s._session_gate(rt)
        assert trabalho is None and "tentativas seguidas" in motivo
    assert len(espiao.chamadas) == 1


@pytest.mark.asyncio
async def test_teto_mais_velho_que_a_validade_gera_releitura(harness: Harness,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """O caso do android-01: `unknown_streak=4` (acima do teto) gravado ~47 h antes, aparelho no ar há dias."""
    s = _estado(harness)
    pid = _perfil_no_teto(s)
    rt = s.devices.get(IID)
    validade = s.social_repo.session_max_age_s
    assert validade > 0
    tres_dias = 3 * 86_400.0
    rt.online_since_mono = time.monotonic() - tres_dias
    s.db.execute("UPDATE instances SET emulator_started_at=? WHERE id=?", (to_iso(now() - timedelta(days=3)), IID))
    _gravada_em(s, pid, to_iso(now() - timedelta(hours=47)))
    s.db.execute("UPDATE account_sessions SET unknown_streak=? WHERE instance_id=?",
                 (s.settings.get().session_unknown_retry_cap + 1, IID))
    espiao = Espiao()
    monkeypatch.setattr(s.sessoes, "for_package", lambda _pacote: espiao)

    motivo, trabalho = s._session_gate(rt)
    assert trabalho is not None, motivo
    await trabalho()
    assert espiao.chamadas == [(IID, pid, False, True)]


@pytest.mark.asyncio
async def test_teto_gravado_depois_de_entrar_no_ar_continua_bloqueando(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """A releitura é para o dado VELHO. Três leituras de agora, neste boot, continuam sendo caso de pessoa."""
    s = _estado(harness)
    _perfil_no_teto(s)
    espiao = Espiao()
    monkeypatch.setattr(s.sessoes, "for_package", lambda _pacote: espiao)
    motivo, trabalho = s._session_gate(s.devices.get(IID))
    assert trabalho is None and "tentativas seguidas" in motivo
    assert espiao.chamadas == []


# ---------------------------------------------------------------- (b) launcher na frente não é tela não reconhecida
@pytest.mark.asyncio
async def test_launcher_repetido_nao_soma_nem_pede_para_identificar_a_tela(harness: Harness) -> None:
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    app = FakeInstagram(stored_password=SENHA, cold_start_reads=10_000)       # o app nunca chega à frente
    motor = _motor(s, app)
    teto = s.settings.get().session_unknown_retry_cap

    for i in range(teto + 1):
        r = await motor.ensure_session(FakeRt(app, IID), pid, automatic=True)
        assert r.outcome is Outcome.UNCERTAIN and "outro app" in r.detail, r.detail
        sessao = s.social_repo.session_row(pid, IID)
        assert sessao is not None and int(sessao["unknown_streak"] or 0) == 0, f"somou na volta {i}"
        assert "identificar a tela" not in (sessao["detail"] or "")
    r = await motor.ensure_session(FakeRt(app, IID), pid, observe_only=True)         # "Verificar conta"
    assert int(s.social_repo.session_row(pid, IID)["unknown_streak"] or 0) == 0

    # A porta tenta de novo sozinha (o app pode só estar lento), em vez de pôr o item em "precisa de pessoa".
    motivo, trabalho = s._session_gate(s.devices.get(IID))
    assert trabalho is not None, motivo
    assert "identificar a tela" not in motivo and "assuma o controle" not in motivo
    # O caso vai para o histórico do APARELHO — uma vez, não a cada tentativa.
    avisos = s.db.query("SELECT message FROM events WHERE kind='log' AND instance_id=? AND message LIKE ?",
                        (IID, "%primeiro plano%"))
    assert len(avisos) == 1, avisos


@pytest.mark.asyncio
async def test_app_que_cai_para_o_launcher_ao_ler_a_conta_nao_soma(harness: Harness) -> None:
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    app = CaiAoAbrirOPerfil(account=USUARIO, screen="feed", stored_password=SENHA)
    motor = _motor(s, app)
    for _ in range(2):
        r = await motor.ensure_session(FakeRt(app, IID), pid, automatic=True)
        assert r.outcome is Outcome.UNCERTAIN and "outro app" in r.detail, r.detail
        assert int(s.social_repo.session_row(pid, IID)["unknown_streak"] or 0) == 0


# ---------------------------------------------------------------- (c) "Verificar conta" não passa do teto
@pytest.mark.asyncio
async def test_verificar_conta_nao_soma_acima_do_teto(harness: Harness) -> None:
    s = _estado(harness)
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    app = TelaEstranha(account=USUARIO, screen="feed", stored_password=SENHA)
    motor = _motor(s, app)
    teto = s.settings.get().session_unknown_retry_cap

    for _ in range(teto + 2):
        r = await motor.ensure_session(FakeRt(app, IID), pid, observe_only=True)
        assert r.outcome is Outcome.UNCERTAIN
    sessao = s.social_repo.session_row(pid, IID)
    assert sessao is not None and sessao["status"] == SessionStatus.unknown.value
    assert int(sessao["unknown_streak"]) == teto            # a tela do app foi lida e não reconhecida: conta, até o teto

    # A mesma regra para quem grava direto no repositório.
    s.social_repo.set_session(pid, status=SessionStatus.unknown, instance_id=IID, detail="de novo", reobserved=True)
    assert int(s.social_repo.session_row(pid, IID)["unknown_streak"]) == teto
