"""31.278: o app pediu a senha e a persona NÃO tem senha guardada para ele — o item e o aviso dizem isso.

O defeito do campo: a persona tinha senha só no Instagram e no Outlook; o QA Messenger pediu a senha e a etapa foi a
`waiting_user` com o aviso genérico ("a conta pediu um novo login"), sem dizer que faltava guardar a senha. O
comportamento continua o do ADR-040 (só se digita o que a pessoa guardou e consentiu); muda o que se diz.

Nível de prova: `simulated` (aparelho falso de QA, banco de teste, nenhuma IA real).
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from app.modules.avisos.domain.mensagem import (
    GESTO_DO_OBJETIVO,
    MOTIVO_DA_PARADA,
    MOTIVO_SEM_SENHA_GUARDADA,
    aviso_de_evento,
)
from app.modules.identity.domain.available_data import AccountRecord, typable_secret_for
from app.models import ProfileCreate

from .conftest import Harness

IID = "android-01"
QA = "com.pocqa.messenger"


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def _conta(**kw: object) -> AccountRecord:
    base: dict[str, object] = dict(account_id="a1", app_id="app", package=QA, host=None, has_credential=False,
                                   consent_at=None, secret_ref=None, managed=False, credential_status="ok", app_label="App", handle=None, login_identifier=None)
    base.update(kw)
    return AccountRecord(**base)  # type: ignore[arg-type]


def test_so_sem_credencial_nenhuma_o_app_fica_sem_senha_guardada() -> None:
    # nenhuma conta, ou conta sem senha, no app: falta guardar
    assert typable_secret_for([], QA).sem_senha_guardada
    assert typable_secret_for([_conta(package="com.outro")], QA).sem_senha_guardada
    assert typable_secret_for([_conta()], QA).sem_senha_guardada
    # há senha sem consentimento, login gerenciado ou senha recusada: a causa é outra, o aviso genérico não muda
    assert not typable_secret_for([_conta(has_credential=True, secret_ref="s")], QA).sem_senha_guardada
    assert not typable_secret_for([_conta(managed=True)], QA).sem_senha_guardada
    assert not typable_secret_for([_conta(has_credential=True, consent_at="2026-10-01T00:00:00Z", secret_ref="s",
                                          credential_status="invalid")], QA).sem_senha_guardada
    assert not typable_secret_for([], None).sem_senha_guardada


def test_o_aviso_ao_dono_diz_que_falta_a_senha_sem_persona_nem_app() -> None:
    dados = {"objective": {"id": "r1:o1", "run_id": "r1", "instance_id": IID, "status": "waiting_user",
                           "blocked_kind": None, "finished_at": "2026-10-07T21:13:14.000Z",
                           "status_detail": "o app pede autenticação no QA Messenger da @fulana"},
             "failure_kind": "autenticacao", "sem_senha_guardada": True}
    a = aviso_de_evento("objective.updated", dados, 7)
    assert a is not None and a.corpo == f"{MOTIVO_SEM_SENHA_GUARDADA}\n{GESTO_DO_OBJETIVO}"
    assert "fulana" not in a.corpo and "QA" not in a.corpo
    sem_sinal = aviso_de_evento("objective.updated", {**dados, "sem_senha_guardada": None}, 7)
    assert sem_sinal is not None and sem_sinal.corpo.startswith(MOTIVO_DA_PARADA["autenticacao"])


async def test_a_etapa_pede_a_senha_do_qa_e_o_item_diz_que_nao_ha_senha_guardada(parque: Harness) -> None:
    """Com o executor de verdade: a persona tem conta só no Instagram; o QA Messenger abre na tela de senha."""
    h = parque
    s = h.state
    assert s is not None
    # Persona sem aparelho vinculado: o provedor de sessão do Instagram não tenta abrir nada no aparelho de QA.
    pid = s.social.create_profile(ProfileCreate(username="persona.teste4821", first_name="Teste",
                                                last_name="Persona")).id
    h.fakes[IID].screen = "login"
    _, lease = s.devices.request_control(s.devices.get(IID))     # segura o despacho até o perfil estar no objetivo
    run = h.run([IID])
    await h.wait(lambda: s.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run.id,)) == 1,
                 what="objetivo planejado")
    s.db.execute("UPDATE objectives SET profile_id=? WHERE run_id=?", (pid, run.id))
    s.devices.release_control(s.devices.get(IID), lease)
    s.scheduler.wake()

    def parado() -> bool:
        o = s.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))
        return bool(o) and o["status"] in ("waiting_user", "failed", "succeeded")

    await h.wait(parado, timeout=60, what="objetivo parar")
    o = s.db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert o is not None and o["status"] == "waiting_user", dict(o)
    assert "não há senha guardada" in (o["blocked_reason"] or "")
    assert "ficha da persona" in (o["needs"] or "")
    ev = s.db.one("SELECT data FROM events WHERE kind='objective.updated' AND data LIKE ? ORDER BY id DESC LIMIT 1",
                  ("%sem_senha_guardada%",))
    assert ev is not None, "o sinal não chegou ao evento do objetivo"
