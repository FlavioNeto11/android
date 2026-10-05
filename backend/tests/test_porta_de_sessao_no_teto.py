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
* launcher / outro app na frente não soma no contador e não pede que a pessoa "identifique a tela" — vai para o
  cartão do aparelho, e sai dele quando o app volta à frente; mas a tela do PRÓPRIO app não reconhecida continua
  contando mesmo quando o "voltar" dela sai do app (o caso do achado #104);
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

    async def ensure_session(self, rt: Any, profile_id: str, *, account_id: str | None = None,
                             force_login: bool = False, automatic: bool = False, observe_only: bool = False) -> AuthResult:
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


class RaizEstranha(TelaEstranha):
    """A tela estranha é a RAIZ do app: o "voltar" do Android sai dele para o launcher. É o caso do próprio achado
    #104 — um feed cujos sinais mudaram numa atualização do app ("sinal ausente da tabela")."""

    def _voltar(self) -> None:
        self.screen = "launcher"


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
    devices = motor.devices
    assert isinstance(devices, FakeDevices)
    rt = FakeRt(app, IID)
    for _ in range(3):
        r = await motor.ensure_session(rt, pid, automatic=True)
        assert r.outcome is Outcome.UNCERTAIN and "outro app" in r.detail, r.detail
        assert int(s.social_repo.session_row(pid, IID)["unknown_streak"] or 0) == 0
    assert rt.attention is not None and "primeiro plano" in rt.attention        # é o aparelho: vai para o cartão
    # O app chegou à frente e caiu na leitura — em TODA tentativa. O aviso entra uma vez e fica: tirá-lo ao ver o app
    # e pô-lo de novo quando ele cai publicaria "voltou / caiu" a cada tentativa, com a porta tentando sem parar.
    assert len(devices.avisos) == 1 and devices.publicados == [], (devices.avisos, devices.publicados)


@pytest.mark.asyncio
async def test_tela_do_app_nao_reconhecida_que_sai_do_app_no_voltar_chega_ao_teto(harness: Harness) -> None:
    """Revisão do pacote: a tela do PRÓPRIO app não reconhecida conta mesmo quando o "voltar" dela cai no launcher.

    `voltar_ao_estado_conhecido` faz desconhecida → voltar → launcher → reabrir → desconhecida → voltar → launcher e
    devolve o launcher. Tomar esse fim por "o app não chegou ao primeiro plano" zerava o contador a cada volta: a
    porta devolvia trabalho automático para sempre (abrir o app e ler a tela a cada tick), com uma mensagem falsa —
    o laço do achado #104 de volta. O app esteve na frente, então também não é aviso de aparelho.
    """
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    app = RaizEstranha(account=USUARIO, screen="feed", stored_password=SENHA)
    motor = _motor(s, app)
    rt = FakeRt(app, IID)
    teto = s.settings.get().session_unknown_retry_cap

    contadores = []
    for _ in range(teto + 2):
        r = await motor.ensure_session(rt, pid, automatic=True)
        assert r.outcome is Outcome.UNCERTAIN and "não chegou ao primeiro plano" not in r.detail, r.detail
        sessao = s.social_repo.session_row(pid, IID)
        assert sessao is not None
        contadores.append(int(sessao["unknown_streak"] or 0))
    assert "key:back" in app.calls                                   # o voltar foi dado sobre a tela do app
    assert contadores == [*range(1, teto + 1), teto, teto], contadores

    motivo, trabalho = s._session_gate(s.devices.get(IID))
    assert trabalho is None and "assuma o controle" in motivo, motivo
    assert "voltar saiu do app" in motivo, motivo
    assert rt.attention is None


@pytest.mark.asyncio
async def test_launcher_vai_para_o_cartao_do_aparelho_e_sai_quando_o_app_volta(harness: Harness) -> None:
    """O pedido (b) inteiro: o launcher na frente vai para a saúde do APARELHO (o cartão, `rt.attention`), não só
    para o histórico — e o aviso sai do cartão quando o app volta a abrir a tempo (uma abertura lenta não deixa
    alarme falso para sempre). r-20260928195344-02ee9e: o convidado do android-06 saturado não trazia o Instagram."""
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    lento = FakeInstagram(account=USUARIO, stored_password=SENHA, cold_start_reads=10_000)
    motor = _motor(s, lento)
    devices = motor.devices
    assert isinstance(devices, FakeDevices)
    rt = FakeRt(lento, IID)

    for _ in range(2):
        r = await motor.ensure_session(rt, pid, automatic=True)
        assert r.outcome is Outcome.UNCERTAIN, r.detail
    assert rt.attention is not None and "primeiro plano" in rt.attention, rt.attention
    assert "identificar a tela" not in rt.attention and "reinicie o aparelho" in rt.attention
    assert len(devices.avisos) == 1                           # repetido, o aviso não muda (nem publica) de novo

    rapido = FakeInstagram(account=USUARIO, screen="feed", stored_password=SENHA)
    rt.io = rt.adb = devices.app = rapido                     # o mesmo aparelho, agora abrindo o app a tempo
    r = await motor.ensure_session(rt, pid, automatic=True)
    assert r.ready, r.detail
    assert rt.attention is None and devices.publicados


@pytest.mark.asyncio
async def test_aviso_do_launcher_nao_atropela_nem_apaga_o_aviso_de_outro_assunto(harness: Harness) -> None:
    """A regra dos outros avisos do cartão (pressão, relógio, internet): só ocupa o cartão vazio ou o que já é dele.
    No android-06 o cartão costuma estar com a pressão do convidado — e ela é a causa, não pode sumir."""
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    lento = FakeInstagram(account=USUARIO, stored_password=SENHA, cold_start_reads=10_000)
    motor = _motor(s, lento)
    devices = motor.devices
    assert isinstance(devices, FakeDevices)
    rt = FakeRt(lento, IID)
    alheio = "Convidado sob pressão: load 9.0 em 2 vCPU, 80 MB livres de 2048 MB."
    rt.attention = alheio

    await motor.ensure_session(rt, pid, automatic=True)
    assert rt.attention == alheio and devices.avisos == []

    rt.io = rt.adb = devices.app = FakeInstagram(account=USUARIO, screen="feed", stored_password=SENHA)
    assert (await motor.ensure_session(rt, pid, automatic=True)).ready
    assert rt.attention == alheio


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



# ---------------------------------------------------------------- 29.92: aparelho com conta real (vínculo ativo)
class VerificacaoDeOutroApp(FakeInstagram):
    """Outro pacote na frente mostrando uma verificação humana, e o app NÃO volta à frente ao ser aberto. Conta as
    aberturas do app: a ressalva (b) do 29.92 é não reabrir por cima."""

    aberturas: int = 0

    def open_app(self, package: str, activity: str | None) -> None:
        self.aberturas += 1

    def _build(self) -> list[Node]:
        return [Node("android.widget.TextView", (40, 100, 680, 200), text="Confirm you're human")]


def _eventos_de_pessoa(s: AppState) -> list[dict[str, Any]]:
    import json
    return [json.loads(r["data"] or "{}") for r in
            s.db.query("SELECT data FROM events WHERE kind='session.needs_person' ORDER BY id")]


@pytest.mark.asyncio
async def test_unknown_em_aparelho_com_conta_real_para_na_primeira_e_avisa_uma_vez(harness: Harness) -> None:
    """29.92 (B): com vínculo ativo o teto é 1. O primeiro `unknown` de um `ensure_session` automático já para: a porta
    não devolve outro trabalho (a rodada seguinte podia cair no login e digitar a senha guardada em cima de uma tela que
    ninguém reconheceu). (A): o `session.needs_person` sai UMA vez, na entrada; reler a mesma tela não o repete."""
    from app.shared.vinculos import tem_vinculo_ativo
    s = _estado(harness)
    s.appium.log_masking_active = True
    assert s.settings.get().session_unknown_retry_cap == 3            # o teto global segue 3: o achado é ele não agir
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    assert tem_vinculo_ativo(s.db, IID)
    app = RaizEstranha(account=USUARIO, screen="feed", stored_password=SENHA)
    motor = _motor(s, app)
    rt = FakeRt(app, IID)
    r = await motor.ensure_session(rt, pid, automatic=True)
    assert r.outcome is Outcome.UNCERTAIN
    motivo, trabalho = s._session_gate(s.devices.get(IID))
    assert trabalho is None and "assuma o controle" in motivo, motivo
    entradas = [e for e in _eventos_de_pessoa(s) if e.get("status") == "unknown"]
    assert len(entradas) == 1 and entradas[0]["active"] is True and entradas[0]["instance_id"] == IID
    await motor.ensure_session(rt, pid, observe_only=True)            # "Verificar conta": a mesma tela de novo
    assert len([e for e in _eventos_de_pessoa(s) if e.get("status") == "unknown"]) == 1
    assert SENHA not in "".join(app.typed)                             # nada digitado


@pytest.mark.asyncio
async def test_login_direto_em_aparelho_com_conta_real_segue(harness: Harness) -> None:
    """29.92 não estreita o login legítimo (ADR-040): tela classificada direto como login, com consentimento, entra."""
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    app = FakeInstagram(account=None, screen="login", stored_password=SENHA)
    motor = _motor(s, app)
    r = await motor.ensure_session(FakeRt(app, IID), pid, automatic=True)
    assert r.outcome is Outcome.SESSION_READY, r.detail


@pytest.mark.asyncio
async def test_verificacao_de_outro_app_com_conta_real_nao_reabre_por_cima(harness: Harness) -> None:
    """29.92 (ressalva b): outro pacote na frente casando com o detector, num aparelho com conta real: o app não é
    reaberto por cima; a sessão fica `unknown` com motivo próprio (no teto 1: parada e aviso), sem marcar conta
    travada nem tocar nada."""
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    app = VerificacaoDeOutroApp(account=USUARIO, screen="feed", stored_password=SENHA,
                                pacote_forcado="com.exemplo.outro")
    motor = _motor(s, app)
    r = await motor.ensure_session(FakeRt(app, IID), pid, automatic=True)
    assert r.outcome is Outcome.UNCERTAIN and "verificação humana" in r.detail, r.detail
    assert app.aberturas == 1                                          # só a abertura inicial, nenhuma por cima
    sessao = s.social_repo.session_row(pid, IID)
    assert sessao is not None and sessao["status"] == SessionStatus.unknown.value
    assert s.quarentena(IID) is None                                    # nada de conta travada
    assert not any(c.startswith("tap") or c == "key:back" for c in app.calls), app.calls



class TelaComumDeOutroApp(VerificacaoDeOutroApp):
    """Outro pacote na frente, SEM verificação humana: o caminho de sempre (o app é reaberto por cima)."""

    def _build(self) -> list[Node]:
        return [Node("android.widget.TextView", (40, 100, 680, 200), text="Uma página qualquer")]


@pytest.mark.asyncio
async def test_outro_app_sem_verificacao_reabre_como_antes(harness: Harness) -> None:
    """O outro lado da ressalva (b): tela de outro pacote que NÃO casa com o detector segue o caminho de antes."""
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    app = TelaComumDeOutroApp(account=USUARIO, screen="feed", stored_password=SENHA,
                              pacote_forcado="com.exemplo.outro")
    motor = _motor(s, app)
    r = await motor.ensure_session(FakeRt(app, IID), pid, automatic=True)
    assert "verificação humana" not in (r.detail or ""), r.detail
    assert app.aberturas >= 2                                          # a abertura inicial e a reabertura por cima


@pytest.mark.asyncio
async def test_parada_resolvida_na_releitura_conta_pela_via(harness: Harness) -> None:
    """29.92: a parada no teto que uma releitura tira para `session_ready` conta em `sessao.parada_resolvida{via}`, pela
    origem da releitura (o motor não a sabe: as duas são `observe_only`). `releitura_sem_toque` responde se vale uma
    rodada automática só de observar; `pessoa_devolveu` não conta como "sozinha"."""
    from app.metricas import metricas
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    estranha = RaizEstranha(account=USUARIO, screen="feed", stored_password=SENHA)
    await _motor(s, estranha).ensure_session(FakeRt(estranha, IID), pid, automatic=True)
    metricas.limpar()
    logado = FakeInstagram(account=USUARIO, screen="feed", stored_password=SENHA)
    conta_id = str(s.social_repo.session_row(pid, IID)["account_id"])  # type: ignore[index]
    await s._medindo_a_parada("releitura_sem_toque", IID, pid, conta_id,
                              lambda: _motor(s, logado).ensure_session(FakeRt(logado, IID), pid, observe_only=True))
    assert metricas.valor("sessao.parada_resolvida", instancia=IID, via="releitura_sem_toque") == 1
    await s._medindo_a_parada("pessoa_devolveu", IID, pid, conta_id,                # já resolvida: não conta de novo
                              lambda: _motor(s, logado).ensure_session(FakeRt(logado, IID), pid, observe_only=True))
    assert metricas.valor("sessao.parada_resolvida", instancia=IID, via="pessoa_devolveu") == 0
    assert metricas.valor("sessao.unknown_resolvida", instancia=IID, rodada_antes=1) == 1
    saidas = [e for e in _eventos_de_pessoa(s) if e.get("active") is False]
    assert len(saidas) == 1                                            # a saída da fila também sai uma vez
