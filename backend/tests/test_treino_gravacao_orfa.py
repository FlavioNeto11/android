"""31.80: gravação do modo treinamento órfã (reinício do backend no meio dela) não tranca o aparelho nem mente no painel."""
from __future__ import annotations

import pytest

from app.models import ControlOwner
from app.training.recorder import TrainingError

from .conftest import Harness


async def _no_controle(harness: Harness):
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    status, lease = st.devices.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    return st, rt, lease


def _linha(st, sid: str):
    return st.db.one("SELECT status, finished_at, updated_at FROM training_sessions WHERE id=?", (sid,))


def _eventos_da_sessao(st, sid: str) -> list[str]:
    return [r["message"] for r in st.db.query("SELECT message, data FROM events WHERE kind='log' AND data LIKE ?",
                                              (f"%{sid}%",))]


async def test_reinicio_encerra_a_gravacao_que_ficou_recording(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="Mandar mensagem", lease_id=lease)
    st.training.record(rt, {"type": "key", "key": "back"}, None)
    assert st.training.active_for("android-01") == s["id"]
    rt.training_session_id = None                         # o processo novo: o aparelho nasce sem gravador ativo

    assert st.training.reconcile_after_restart() == 1

    linha = _linha(st, s["id"])
    assert linha["status"] == "recorded" and linha["finished_at"] and linha["updated_at"]
    assert st.training.active_for("android-01") is None
    assert len(st.training.get(s["id"])["inputs"]) == 1       # o que já estava gravado vale
    assert any("reinício" in m for m in _eventos_da_sessao(st, s["id"])), "falta o evento de log da reconciliação"
    assert st.training.reconcile_after_restart() == 0         # idempotente
    st.training.record(rt, {"type": "key", "key": "back"}, None)   # não religa sozinho
    assert len(st.training.get(s["id"])["inputs"]) == 1


async def test_start_com_orfa_encerra_a_antiga_e_aceita(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    velha = st.training.start("android-01", intent="Gravação que ficou para trás", lease_id=lease)
    rt.training_session_id = None                         # órfã por qualquer caminho (nada grava nela)

    nova = st.training.start("android-01", intent="Gravação nova", lease_id=lease)

    assert nova["id"] != velha["id"] and nova["status"] == "recording" and rt.training_session_id == nova["id"]
    assert _linha(st, velha["id"])["status"] == "recorded" and _linha(st, velha["id"])["finished_at"]
    assert st.training.active_for("android-01") == nova["id"]
    assert any("sem gravador" in m for m in _eventos_da_sessao(st, velha["id"]))


async def test_start_com_gravacao_viva_continua_recusando(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    viva = st.training.start("android-01", intent="Gravação viva", lease_id=lease)
    with pytest.raises(TrainingError) as erro:
        st.training.start("android-01", intent="outra", lease_id=lease)
    assert erro.value.code == "already_recording"
    assert _linha(st, viva["id"])["status"] == "recording" and rt.training_session_id == viva["id"]


def _gravacao_de(st, instance_id: str, sid: str) -> None:
    agora = "2026-10-05T12:00:00+00:00"
    st.db.execute("INSERT INTO training_sessions(id, instance_id, intent, status, created_at, updated_at)"
                  " VALUES (?,?,?,?,?,?)", (sid, instance_id, "x", "recording", agora, agora))


async def test_reconciliacao_so_fecha_o_que_este_backend_hospeda(harness: Harness) -> None:
    """Duas réplicas: a partida de uma não encerra a gravação VIVA de um aparelho que a outra hospeda."""
    st = harness.state
    assert st.training.owner_id == st.cfg.owner_id and st.cfg.owner_id
    st.db.execute("UPDATE instances SET hosted_by=? WHERE id='android-01'", (st.cfg.owner_id,))
    st.db.execute("UPDATE instances SET hosted_by=? WHERE id='android-02'", ("outra-replica",))
    st.db.execute("UPDATE instances SET hosted_by=NULL WHERE id='android-03'")
    for iid in ("android-01", "android-02", "android-03"):
        _gravacao_de(st, iid, f"trn-{iid}")

    assert st.training.reconcile_after_restart() == 2

    assert _linha(st, "trn-android-01")["status"] == "recorded"        # próprio: fecha
    assert _linha(st, "trn-android-03")["status"] == "recorded"        # sem dono carimbado: como antes
    assert _linha(st, "trn-android-02")["status"] == "recording"       # alheio: segue viva
    assert not _eventos_da_sessao(st, "trn-android-02")


async def test_encerrar_duas_vezes_nao_repete_o_log(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="x", lease_id=lease)
    rt.training_session_id = None
    st.training.reconcile_after_restart()
    st.training._encerrar_orfa(s["id"], "de novo")                     # noqa: SLF001 - a segunda partida
    assert len(_eventos_da_sessao(st, s["id"])) == 2                   # o "iniciado" e UM "encerrada"


def test_a_subida_do_estado_chama_a_reconciliacao_depois_de_os_aparelhos_existirem() -> None:
    """N3: a fiação em `AppState.start` (o harness já subiu o estado; inspeciona a ordem das chamadas)."""
    import inspect

    from app.state import AppState
    fonte = inspect.getsource(AppState.start)
    assert "self.training.reconcile_after_restart()" in fonte
    assert fonte.index("await self.devices.start()") < fonte.index("self.training.reconcile_after_restart()")
