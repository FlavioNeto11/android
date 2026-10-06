"""Conduta do login automático (ADR-055, 29/09/2026): um envio sem sucesso para tudo até uma pessoa olhar, teto
diário por conta, e conta bloqueada nunca recebe a senha.

O caso real: a fabiana recebeu seis envios de senha em 4h25 em 18/09. O freio de antes (3 falhas, 300 s de espera)
se repetia sem fim: a cada intervalo vencido, mais três envios reais. E o motor de sessão não olhava o status do
PERFIL — só a credencial —, então nada impedia digitar a senha de uma conta que o próprio sistema já tinha dado por
bloqueada.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.automation.driver import DriverError
from app.db import Database
from app.integrations.app_declarado.sessao import ETAPAS_ANTES_DO_ENVIO, Outcome
from app.models import ProfileCreate, SessionStatus
from app.modules.identity.application.session_rules import CREDENCIAL_EM_REVISAO
from app.modules.identity.domain.resources import CredentialState, SessionCode, diff_app_session, plan_app_session
from app.modules.identity.domain.resources import SessionStatus as SessaoDoDominio
from app.shared.resources import DriftStatus, Target

from .fake_instagram import FakeInstagram
from .test_instagram_auth import SENHA, USUARIO, FakeRt, build, cadastrar
from .test_leitura_de_recursos import SESSAO as SESSAO_LIDA
from .test_leitura_de_recursos import _identidades, _sessoes, banco  # noqa: F401 - `banco` é fixture
from .test_recursos_declarativos import PRONTA, SESSAO_APPLY, _acoes, _sess


def _envios(app: FakeInstagram) -> int:
    return app.calls.count("submit")


async def test_um_envio_sem_sucesso_para_o_login_automatico_ate_uma_pessoa_olhar(tmp_path: Path) -> None:
    # Senha que o app não aceita, sem mensagem de erro na tela: o desfecho é incerto — o caso que se repetia.
    app = FakeInstagram(stored_password="outra-senha", wrong_password_message=False)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert r.outcome is Outcome.UNCERTAIN and _envios(app) == 1
        cred = repo.credential_row(pid)
        assert cred["status"] == CREDENCIAL_EM_REVISAO and cred["failed_attempts"] == 1
        assert "pessoa" in (repo.session_row(pid)["detail"] or "")          # o estado diz o que fazer
        parados = [json.loads(e["data"]) for e in db.query("SELECT data FROM events WHERE kind='log'")
                   if e["data"] and json.loads(e["data"]).get("reason") == "login_parado"]
        assert len(parados) == 1 and parados[0]["profile_id"] == pid

        # A segunda chamada AUTOMÁTICA não digita nem envia nada — nem depois de qualquer intervalo.
        digitado = list(app.typed)
        app.calls.clear()
        r2 = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert not r2.ready and "pessoa" in r2.detail
        assert app.typed == digitado and _envios(app) == 0 and app.calls == []   # sem tocar no aparelho

        # Uma pessoa olha e pede "Conectar" (chamada explícita do painel): essa pode tentar.
        app.stored_password = SENHA
        r3 = await auth.ensure_session(FakeRt(app), pid)
        assert r3.ready and _envios(app) == 1
        assert repo.credential_row(pid)["status"] == "active"
    finally:
        db.close()


def _falha_ao_digitar_a_senha(app: FakeInstagram) -> None:
    """O driver falha ao preencher o campo de senha (uma vez): `fill_failed`, e nada é enviado."""
    original = app.type_text

    def type_text(text: str, *, clear_first: bool) -> None:
        if getattr(app, "_focus", "username") == "password":
            app.type_text = original  # type: ignore[method-assign]
            raise DriverError("falha simulada ao digitar", effect_possible=False)
        original(text, clear_first=clear_first)

    app.type_text = type_text  # type: ignore[method-assign]


@pytest.mark.parametrize("falha", ["submit_not_delivered", "fill_failed"])
async def test_conectar_que_falha_antes_do_envio_nao_solta_o_login_parado(tmp_path: Path, falha: str) -> None:
    """Revisão do pacote (ADR-055): a credencial está em `review`; uma pessoa aperta Conectar (chamada não
    automática, que pode tentar) e a tentativa falha ANTES do envio. Contar essa falha trocava `review` por `active`,
    e a volta seguinte do agendador enviava a senha de novo sem ninguém ter visto um login dar certo. Só saem de
    `review` a senha guardada de novo ou um login que confirma a conta."""
    app = FakeInstagram(stored_password=SENHA, submit_fault="lost" if falha == "submit_not_delivered" else None)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        repo.mark_credential(pid, status=CREDENCIAL_EM_REVISAO, failed_attempts=1)
        if falha == "fill_failed":
            _falha_ao_digitar_a_senha(app)
        r = await auth.ensure_session(FakeRt(app), pid)                      # "Conectar" de uma pessoa
        assert r.outcome is Outcome.RETRYABLE, r.detail
        assert repo.auth_attempts(pid)[0]["stage"] == falha
        cred = repo.credential_row(pid)
        assert cred["status"] == CREDENCIAL_EM_REVISAO and cred["failed_attempts"] == 2

        # O automático seguinte continua parado: não toca no aparelho, não digita, não envia.
        app.submit_fault = None
        digitado = list(app.typed)
        app.calls.clear()
        r2 = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert not r2.ready and "pessoa" in r2.detail
        assert app.typed == digitado and _envios(app) == 0 and app.calls == []
    finally:
        db.close()


async def test_login_parado_no_outro_aparelho_interrompe_o_automatico_em_curso(tmp_path: Path) -> None:
    """Corrida entre dois aparelhos (revisão do pacote): o login automático do B já passou da porta quando o envio sem
    sucesso do A pôs a credencial em `review`. O B relê antes da senha e não digita nem envia nada — seria um segundo
    envio sem ninguém ter olhado (regra (e) do ADR-055) — e não solta o login parado."""
    app = FakeInstagram(stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        original = app.type_text

        def type_text(text: str, *, clear_first: bool) -> None:
            original(text, clear_first=clear_first)
            if getattr(app, "_focus", "username") == "username":
                repo.mark_credential(pid, status=CREDENCIAL_EM_REVISAO, failed_attempts=1)   # o A, agora

        app.type_text = type_text  # type: ignore[method-assign]
        r = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert not r.ready and "pessoa" in r.detail
        assert SENHA not in app.typed and app.password_field == "" and _envios(app) == 0
        assert repo.credential_row(pid)["status"] == CREDENCIAL_EM_REVISAO
        # A interrupção não é envio: não gasta o teto diário da conta.
        assert repo.auth_attempts(pid)[0]["stage"] in ETAPAS_ANTES_DO_ENVIO
    finally:
        db.close()


async def test_login_parado_no_meio_para_antes_do_toque_em_entrar(tmp_path: Path) -> None:
    """A mesma corrida, mais tarde: a senha já está no campo quando o A põe a credencial em `review`. O campo
    preenchido não é envio; o toque em Entrar é — e ele não acontece."""
    app = FakeInstagram(stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        original = app.type_text

        def type_text(text: str, *, clear_first: bool) -> None:
            original(text, clear_first=clear_first)
            if getattr(app, "_focus", "username") == "password":
                repo.mark_credential(pid, status=CREDENCIAL_EM_REVISAO, failed_attempts=1)

        app.type_text = type_text  # type: ignore[method-assign]
        r = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert not r.ready and "pessoa" in r.detail
        assert _envios(app) == 0
        assert repo.credential_row(pid)["status"] == CREDENCIAL_EM_REVISAO
    finally:
        db.close()


async def test_teto_diario_de_logins_por_conta(tmp_path: Path) -> None:
    """Login que dá certo também conta: uma conta que precisa entrar várias vezes no dia está perdendo a sessão, e
    insistir é o padrão que a plataforma pune. Passado o teto, nem o automático nem o "Conectar" digitam."""
    app = FakeInstagram(stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app, max_logins_per_day=2)
    try:
        pid = cadastrar(social)

        def deslogar() -> None:
            app.account, app.screen, app.username_field, app.password_field = None, "login", "", ""

        for _ in range(2):
            assert (await auth.ensure_session(FakeRt(app), pid, automatic=True)).ready
            deslogar()
        assert _envios(app) == 2

        digitado = list(app.typed)
        r = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert not r.ready and "24 h" in r.detail
        assert app.typed == digitado and _envios(app) == 2
        # No automático, o teto também para o login até uma pessoa olhar — senão a porta de sessão pediria o mesmo
        # login a cada volta do agendador até a janela andar.
        assert repo.credential_row(pid)["status"] == CREDENCIAL_EM_REVISAO

        r2 = await auth.ensure_session(FakeRt(app), pid)                     # "Conectar" de uma pessoa
        assert not r2.ready and "24 h" in r2.detail
        assert app.typed == digitado and _envios(app) == 2
    finally:
        db.close()


async def test_falha_antes_do_envio_nao_para_o_login_automatico(tmp_path: Path) -> None:
    """Toque em Entrar que comprovadamente não chegou: a senha não saiu da máquina. Não é "envio sem sucesso" e
    não gasta o teto diário — a regra de antes (pode tentar de novo) continua valendo."""
    app = FakeInstagram(stored_password=SENHA, submit_fault="lost")
    auth, repo, social, db = build(tmp_path, app, max_logins_per_day=1)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert r.outcome is Outcome.RETRYABLE
        assert repo.credential_row(pid)["status"] == "active"
        assert (await auth.ensure_session(FakeRt(app), pid, automatic=True)).ready
    finally:
        db.close()


async def test_conta_bloqueada_nunca_recebe_a_senha(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)                    # deslogado, na tela de login
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        repo.set_session(pid, status=SessionStatus.auth_challenge, instance_id="android-02",
                         detail="o Instagram exige confirmação adicional")
        repo.update_profile(pid, {"status": "blocked"})
        for chamada in ({"automatic": True}, {}, {"force_login": True}):
            r = await auth.ensure_session(FakeRt(app), pid, **chamada)
            assert not r.ready and "bloqueada" in r.detail, (chamada, r.detail)
        assert app.typed == [] and _envios(app) == 0
        assert repo.auth_attempts(pid) == []
        # Recusar não reescreve a sessão: o desafio continua na fila de quem precisa de uma pessoa.
        assert repo.session_row(pid)["status"] == SessionStatus.auth_challenge.value
    finally:
        db.close()


async def test_bloqueio_no_meio_do_login_nao_digita_a_senha(tmp_path: Path) -> None:
    """A mesma pessoa em dois aparelhos: o desafio visto no OUTRO bloqueia o perfil enquanto este já digitou o
    usuário. A senha não vai para o campo, e nada é enviado."""
    app = FakeInstagram(stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        original = app.type_text

        def type_text(text: str, *, clear_first: bool) -> None:
            original(text, clear_first=clear_first)
            if getattr(app, "_focus", "username") == "username":
                repo.update_profile(pid, {"status": "blocked"})

        app.type_text = type_text  # type: ignore[method-assign]
        r = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert not r.ready and "bloqueada" in r.detail
        assert SENHA not in app.typed and app.password_field == ""
        assert _envios(app) == 0
    finally:
        db.close()


async def test_porta_de_sessao_bloqueia_com_o_login_parado_sem_reagendar(harness: Any) -> None:
    """Com o login parado, a porta de sessão não devolve trabalho de autenticação a cada volta do agendador (que o
    motor recusaria sem tocar no aparelho, para sempre): bloqueia o item para uma pessoa, com o motivo."""
    state = harness.state
    state.appium.log_masking_active = True
    pid = state.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id="android-01")).id
    state.social_repo.set_session(pid, status=SessionStatus.auth_required, instance_id="android-01",
                                  detail="deslogado")
    state.social_repo.mark_credential(pid, status=CREDENCIAL_EM_REVISAO, failed_attempts=1)
    motivo, trabalho = state._session_gate(state.devices.get("android-01"))
    assert trabalho is None
    assert "pessoa" in motivo


def test_recurso_de_sessao_com_login_parado_e_de_pessoa() -> None:
    """O modelo declarativo (aplicação de recursos) enxerga o mesmo estado: com o login parado, "deslogada" não
    converge para `session.connect` automático — que o motor recusaria —, e sim para uma pessoa."""
    parado = replace(PRONTA, status=SessaoDoDominio.auth_required, credential=CredentialState.review)
    drift = diff_app_session(SESSAO_APPLY, _sess(SESSAO_APPLY, provider=parado))
    assert (drift.status, drift.code) == (DriftStatus.blocked, SessionCode.needs_person), drift.detail
    assert _acoes(plan_app_session(drift)) == [("ask", None)]


def test_leitura_da_sessao_reconhece_o_login_parado(banco: Database) -> None:
    """A leitura do banco (`AppSessionProvider`) tem de entregar o estado: antes, `review` caía em "utilizável" (não
    é `invalid` e tem consentimento) e o recurso convergia para um login automático que o motor recusaria."""
    _identidades(banco)
    banco.execute("UPDATE account_credentials SET status=? WHERE account_id='acc-p-tadeu'", (CREDENCIAL_EM_REVISAO,))
    banco.execute("UPDATE account_sessions SET status='auth_required' WHERE account_id='acc-p-tadeu'"
                  " AND instance_id='android-01'")
    p = _sessoes(banco)
    lido = p.read_current_state(SESSAO_LIDA.ref, Target("android-01"))
    assert lido.provider is not None and lido.provider.credential is CredentialState.review
    assert p.diff(SESSAO_LIDA, lido).code == SessionCode.needs_person
