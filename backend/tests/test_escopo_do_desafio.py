"""Item 23.5 — escopo do bloqueio por desafio (decisão do dono P9, 29/09; ADR-055, ADR-057).

Na composição: a tela que desmente a sessão no meio de uma execução (`AppState._sessao_desmentida`) aplica a MESMA
regra do motor de sessão (`session_rules.aplicar_desafio`, provada em `test_sessao_declarada.py`):

- num app que não é o âncora, código e verificação param SÓ a conta daquele app (credencial em `review`), sem
  bloquear a persona nem tocar na conta âncora, e sem quarentena;
- a conta travada daquele app mantém a quarentena do aparelho, sem bloquear a persona — inclusive depois de um código
  já visto (sessão já em `auth_challenge`);
- no app âncora, nada mudou: a trava bloqueia a persona e vai à quarentena; o código só pede uma pessoa.

Nível de prova: `simulated` — Harness (porta base 5640), aparelhos falsos. Nenhum aparelho, conta ou IA real.
"""
from __future__ import annotations

import pytest

from app.models import SessionStatus
from app.state import AppState
from app.util import now_iso

from .conftest import Harness
from .fake_instagram import PKG as IG
from .test_sessao_declarada import CORREIO
from .test_sessao_por_conta import IID, correio_registrado, estado, persona_com_duas_contas, status

__all__ = ["correio_registrado"]          # a fixture, importada para os testes daqui


def _credencial(s: AppState, pid: str, conta: str) -> str:
    return str(s.social_repo.account_credential_row(pid, conta)["status"])


def _marcadores(s: AppState) -> list[tuple[str, str | None, str | None]]:
    return [(str(r["handle"]), r["app_id"], r["profile_id"]) for r in s.db.query(
        "SELECT handle, app_id, profile_id FROM device_locked_accounts WHERE instance_id=? AND resolved_at IS NULL",
        (IID,))]


async def test_codigo_e_verificacao_no_outro_app_param_so_a_conta_dele(harness: Harness,
                                                                       correio_registrado: None) -> None:
    s = estado(harness)
    pid, ancora, conta = persona_com_duas_contas(s)
    for c in (ancora, conta):
        s.social_repo.set_account_session(pid, c, IID, status=SessionStatus.session_ready, verified_at=now_iso())

    s._sessao_desmentida(IID, "auth_challenge", "o correio pediu um código de segurança", subtipo="codigo",
                         package=CORREIO)

    assert (status(s, pid, conta), status(s, pid, ancora)) == ("auth_challenge", "session_ready")
    assert (_credencial(s, pid, conta), _credencial(s, pid, ancora)) == ("review", "active")
    assert s.social_repo.profile_row(pid)["status"] == "active"
    assert _marcadores(s) == []
    # A porta: a tarefa do correio para (pede pessoa); a do app âncora segue.
    rt = s.devices.get(IID)
    porta = s._session_gate(rt, CORREIO, pid)
    assert porta is not None and porta[1] is None
    assert s._session_gate(rt, IG, pid) is None
    parada = [e for e in s.db.query("SELECT data FROM events WHERE kind='log' AND data LIKE ?",
                                    ('%"desafio_na_conta"%',))]
    assert len(parada) == 1

    # A verificação relatada pela IA, com a credencial já parada: nada a mais — nem bloqueio, nem quarentena.
    s.social_repo.set_account_session(pid, conta, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    s._sessao_desmentida(IID, "auth_challenge", "tela de verificação no correio", subtipo="verificacao",
                         package=CORREIO)
    assert status(s, pid, conta) == "auth_challenge" and _credencial(s, pid, conta) == "review"
    assert s.social_repo.profile_row(pid)["status"] == "active" and _marcadores(s) == []


async def test_conta_travada_no_outro_app_poe_o_aparelho_em_quarentena_sem_bloquear_a_persona(
        harness: Harness, correio_registrado: None) -> None:
    """Primeiro um código (sessão já em `auth_challenge`), depois a trava: a quarentena nasce mesmo assim, com a
    conta DO CORREIO; a persona segue `active` e a credencial âncora intacta."""
    s = estado(harness)
    pid, ancora, conta = persona_com_duas_contas(s)
    s._sessao_desmentida(IID, "auth_challenge", "código pedido", subtipo="codigo", package=CORREIO)
    assert _marcadores(s) == []

    s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=CORREIO)

    assert _marcadores(s) == [("ana.correio", "correio", pid)]
    assert s.social_repo.profile_row(pid)["status"] == "active"
    # 29.23 (ADR-068): a trava confirmada retira a conta do correio na hora; a âncora fica intacta.
    assert s.social_repo.account_row(pid, conta) is None
    assert _credencial(s, pid, ancora) == "active"
    # A quarentena é do APARELHO (ADR-055): nada é despachado nele, de app nenhum, até uma pessoa decidir.
    rt = s.devices.get(IID)
    for pacote in (CORREIO, IG):
        porta = s._session_gate(rt, pacote, pid)
        assert porta is not None and porta[1] is None


async def test_trava_na_conta_sem_arroba_vai_a_quarentena_pelo_login_dela(harness: Harness,
                                                                          correio_registrado: None) -> None:
    """A conta do correio sem @, identificada só pelo e-mail de login: o marcador da quarentena leva o e-mail — a mesma
    regra do motor de sessão (`SessaoDeclarada._resolver_conta`). Antes levava o @ de cadastro da persona, e como o
    marcador é único por (aparelho, conta), a trava posterior da conta âncora no mesmo aparelho não era registrada."""
    s = estado(harness)
    pid, _, conta = persona_com_duas_contas(s)
    s.db.execute("UPDATE profile_accounts SET handle='' WHERE id=?", (conta,))
    cred = s.social_repo.account_credential_row(pid, conta)
    s.social_repo.set_account_credential(pid, conta, login_identifier="ana@correio.com", secret_ref=cred["secret_ref"],
                                         key_id=cred["key_id"])

    s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=CORREIO)

    assert _marcadores(s) == [("ana@correio.com", "correio", pid)]
    ancora_app = str(s.social_repo.conta_ancora(pid)["app_id"])
    assert s.social_repo.marcar_conta_travada(IID, "ana.ancora", "confirm you're human", "observado", profile_id=pid,
                                              app_id=ancora_app) is True


async def test_no_app_ancora_o_comportamento_de_hoje_nao_muda(harness: Harness, correio_registrado: None) -> None:
    s = estado(harness)
    pid, ancora, conta = persona_com_duas_contas(s)
    # Código na âncora: só pede uma pessoa — nem bloqueio, nem credencial parada, nem quarentena.
    s._sessao_desmentida(IID, "auth_challenge", "código pedido", subtipo="codigo", package=IG)
    assert status(s, pid, ancora) == "auth_challenge"
    assert s.social_repo.profile_row(pid)["status"] == "active"
    assert (_credencial(s, pid, ancora), _credencial(s, pid, conta)) == ("active", "active")
    assert _marcadores(s) == []

    # A trava na âncora: a persona é bloqueada (ADR-029) e o aparelho entra em quarentena com a conta âncora.
    ancora_app = str(s.social_repo.conta_ancora(pid)["app_id"])
    s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=IG)
    # 29.23 (ADR-068): bloqueada e, na hora, a conta âncora sai e a persona volta a `active`, sem @.
    assert (s.social_repo.profile_row(pid)["status"], s.social_repo.profile_row(pid)["username"]) == ("active", "")
    assert s.social_repo.account_row(pid, ancora) is None
    assert _marcadores(s) == [("ana.ancora", ancora_app, pid)]
    assert _credencial(s, pid, conta) == "active"                      # a conta do correio não é tocada


async def test_a_trava_vista_pelo_executor_vai_a_conta_do_app_da_tela(harness: Harness, correio_registrado: None,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """O executor sabe o pacote da tela em que viu a trava (`parar_na_trava`) e o passa ao AppState pela ligação da
    composição (`on_auth_needed`). Antes o pacote se perdia no caminho e o AppState deduzia o app pela etapa em curso
    (o âncora, sem etapa): a trava do correio bloqueava a persona e punha a sessão âncora em `auth_challenge`; e a
    trava do app âncora vista numa etapa do correio caía na conta do correio, sem bloquear a persona."""
    s = estado(harness)
    pid, ancora, conta = persona_com_duas_contas(s)
    for c in (ancora, conta):
        s.social_repo.set_account_session(pid, c, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    executor = s.scheduler.executor

    executor._sessao_desmentida(IID, CORREIO, "auth_challenge", "confirm you're human", subtipo="conta_travada")

    assert s.social_repo.profile_row(pid)["status"] == "active"
    assert s.social_repo.account_row(pid, conta) is None               # 29.23: a conta do correio saiu na hora
    assert (status(s, pid, conta), status(s, pid, ancora)) == (None, "session_ready")
    assert _credencial(s, pid, ancora) == "active"
    assert _marcadores(s) == [("ana.correio", "correio", pid)]

    # O inverso: a etapa em curso é do correio, e a trava aparece na tela do app âncora.
    monkeypatch.setattr(s, "_pacote_em_curso", lambda _iid: CORREIO)
    ancora_app = str(s.social_repo.conta_ancora(pid)["app_id"])
    executor._sessao_desmentida(IID, IG, "auth_challenge", "confirm you're human", subtipo="conta_travada")
    assert s.social_repo.profile_row(pid)["status"] == "active"        # bloqueou e a retirada devolveu a persona
    assert s.social_repo.account_row(pid, ancora) is None and status(s, pid, ancora) is None
    assert ("ana.ancora", ancora_app, pid) in _marcadores(s)
