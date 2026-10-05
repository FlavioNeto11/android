"""31.92: encerrar ou descartar uma gravação VIVA do modo treinamento exige o controle do aparelho (como o `start`).

Quem só olha o painel não derruba a gravação de quem está ensinando. A órfã, o descarte do que já não grava e os
encerramentos do sistema seguem sem pedir controle."""
from __future__ import annotations

import pytest

from app.models import ControlOwner
from app.training.recorder import TrainingError

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente

MSG_ENCERRAR = "Só quem está com o controle do aparelho encerra esta gravação."
MSG_DESCARTAR = "Só quem está com o controle do aparelho descarta esta gravação."


async def _gravando(harness: Harness):
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    status, lease = st.devices.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    sessao = st.training.start("android-01", intent="Mandar mensagem para um contato do QA", lease_id=lease)
    st.training.record(rt, {"type": "key", "key": "back"}, None)
    return st, rt, lease, sessao["id"]


def _linha(st, sid: str):
    return st.db.one("SELECT status, finished_at FROM training_sessions WHERE id=?", (sid,))


def _segue_gravando(st, rt, sid: str) -> None:
    """A recusa não muda nada: a linha segue `recording`, o gravador segue ativo e nenhuma entrada se perdeu."""
    assert _linha(st, sid)["status"] == "recording" and not _linha(st, sid)["finished_at"]
    assert rt.training_session_id == sid and st.training.active_for("android-01") == sid
    assert len(st.training.get(sid)["inputs"]) == 1
    st.training.record(rt, {"type": "key", "key": "home"}, None)             # o gravador continua gravando
    assert len(st.training.get(sid)["inputs"]) == 2


# ------------------------------------------------------------------ encerrar (stop)
async def test_stop_sem_lease_numa_gravacao_viva_recusa_e_nada_muda(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/stop")
        assert r.status_code == 409, r.text
        assert "control_required" in r.text and MSG_ENCERRAR in r.text
        vazio = await c.post(f"/api/training/{sid}/stop", json={})
        assert vazio.status_code == 409 and MSG_ENCERRAR in vazio.text
    _segue_gravando(st, rt, sid)


async def test_stop_com_lease_errado_recusa(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/stop", json={"lease_id": "lease-de-outra-pessoa"})
        assert r.status_code == 409 and "control_required" in r.text
    _segue_gravando(st, rt, sid)


async def test_stop_com_o_lease_certo_encerra_como_antes(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/stop", json={"lease_id": lease})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "recorded"
    assert _linha(st, sid)["status"] == "recorded" and _linha(st, sid)["finished_at"]
    assert rt.training_session_id is None and len(st.training.get(sid)["inputs"]) == 1


# ------------------------------------------------------------------ descartar (discard)
async def test_discard_sem_lease_numa_gravacao_viva_recusa_e_nada_muda(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/discard")
        assert r.status_code == 409 and "control_required" in r.text and MSG_DESCARTAR in r.text
    _segue_gravando(st, rt, sid)


async def test_discard_com_lease_errado_recusa(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/discard", json={"lease_id": "lease-de-outra-pessoa"})
        assert r.status_code == 409 and "control_required" in r.text
    _segue_gravando(st, rt, sid)


async def test_discard_com_o_lease_certo_descarta_como_antes(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/discard", json={"lease_id": lease})
        assert r.status_code == 200 and r.json()["status"] == "discarded"
    assert _linha(st, sid)["status"] == "discarded" and rt.training_session_id is None


# ------------------------------------------------------------------ o controle mudou de mãos
async def test_controle_mudou_de_maos_quem_tem_o_lease_atual_para_e_o_antigo_nao(harness: Harness) -> None:
    st, rt, lease_antigo, sid = await _gravando(harness)
    rt.lease_id = "lease-novo"                       # outra pessoa assumiu e o gravador ainda é o da sessão antiga
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/stop", json={"lease_id": lease_antigo})
        assert r.status_code == 409 and "control_required" in r.text
        r = await c.post(f"/api/training/{sid}/discard")
        assert r.status_code == 409
        _segue_gravando(st, rt, sid)
        r = await c.post(f"/api/training/{sid}/stop", json={"lease_id": "lease-novo"})
        assert r.status_code == 200 and r.json()["status"] == "recorded"


# ------------------------------------------------------------------ sem controle de usuário: o painel conclui sem lease
@pytest.mark.parametrize("dono", [ControlOwner.ai, ControlOwner.none])
@pytest.mark.parametrize("acao", ["stop", "discard"])
async def test_sem_controle_de_usuario_stop_e_discard_funcionam_sem_lease(harness: Harness, dono, acao) -> None:
    st, rt, lease, sid = await _gravando(harness)
    rt.control, rt.lease_id = dono, None             # a IA voltou ao aparelho, ou o controle foi devolvido/expirou
    assert st.training.active_for("android-01") == sid
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/{acao}")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == ("recorded" if acao == "stop" else "discarded")


async def test_com_controle_de_usuario_e_sem_lease_recusa_stop_e_discard(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    assert rt.control == ControlOwner.user
    async with _cliente(harness) as c:
        assert (await c.post(f"/api/training/{sid}/stop")).status_code == 409
        assert (await c.post(f"/api/training/{sid}/discard")).status_code == 409
    _segue_gravando(st, rt, sid)


# ------------------------------------------------------------------ órfã e já gravada: sem controle
async def test_orfa_sem_gravador_encerra_e_descarta_sem_lease(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    rt.training_session_id = None                    # nada grava nela (31.80)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/stop")
        assert r.status_code == 200 and r.json()["status"] == "recorded"
    nova = st.training.start("android-01", intent="Outra gravação", lease_id=lease)["id"]
    rt.training_session_id = None
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{nova}/discard")
        assert r.status_code == 200 and r.json()["status"] == "discarded"


async def test_discard_de_sessao_ja_gravada_funciona_sem_lease(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    st.training.stop(sid, lease_id=lease)            # `recorded`
    assert _linha(st, sid)["status"] == "recorded"
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/discard")
        assert r.status_code == 200 and r.json()["status"] == "discarded"


async def test_sessao_inexistente_continua_404(harness: Harness) -> None:
    async with _cliente(harness) as c:
        assert (await c.post("/api/training/nao-existe/stop")).status_code == 404
        assert (await c.post("/api/training/nao-existe/discard")).status_code == 404


# ------------------------------------------------------------------ encerramentos do sistema
async def test_encerramentos_internos_nao_pedem_controle(harness: Harness) -> None:
    # devolução do controle (`stop_for_instance`)
    st, rt, lease, sid = await _gravando(harness)
    st.training.stop_for_instance("android-01")
    assert _linha(st, sid)["status"] == "recorded"
    # chamada do sistema que diz o que é; sem dizer, o `stop` confere
    status, lease = st.devices.request_control(rt)
    sid2 = st.training.start("android-01", intent="Segunda", lease_id=lease)["id"]
    with pytest.raises(TrainingError) as sem_dizer:
        st.training.stop(sid2, discard=True)
    assert sem_dizer.value.code == "control_required" and _linha(st, sid2)["status"] == "recording"
    assert st.training.stop(sid2, discard=True, por_sistema=True)["status"] == "discarded"
    # troca de gravação órfã no `start` e reconciliação do reinício
    sid3 = st.training.start("android-01", intent="Terceira", lease_id=lease)["id"]
    rt.training_session_id = None
    sid4 = st.training.start("android-01", intent="Quarta", lease_id=lease)["id"]
    assert _linha(st, sid3)["status"] == "recorded"
    rt.training_session_id = None
    assert st.training.reconcile_after_restart() == 1 and _linha(st, sid4)["status"] == "recorded"


async def test_no_dominio_a_conferencia_e_o_padrao_e_recusa_com_o_mesmo_codigo_do_start(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    with pytest.raises(TrainingError) as erro:
        st.training.stop(sid)
    assert (erro.value.code, erro.value.status) == ("control_required", 409)
    assert st.training.stop(sid, lease_id=lease)["status"] == "recorded"


# ------------------------------------------------------------------ duas réplicas
async def test_gravacao_de_aparelho_hospedado_por_outra_replica_nao_e_orfa(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    assert st.training.owner_id == st.cfg.owner_id and st.cfg.owner_id
    st.db.execute("UPDATE instances SET hosted_by=? WHERE id='android-01'", ("outra-replica",))
    rt.training_session_id = None                    # daqui parece órfã, mas o gravador pode estar na outra réplica
    async with _cliente(harness) as c:
        for acao in ("stop", "discard"):
            r = await c.post(f"/api/training/{sid}/{acao}", json={"lease_id": lease})
            assert r.status_code == 409, r.text
            assert "gravacao_em_outro_servidor" in r.text and "Esta gravação está em outro servidor; encerre por lá." in r.text
    assert _linha(st, sid)["status"] == "recording" and not _linha(st, sid)["finished_at"]
    assert len(st.training.get(sid)["inputs"]) == 1


@pytest.mark.parametrize("dono", [None, "proprio"])
@pytest.mark.parametrize("acao", ["stop", "discard"])
async def test_sem_dono_ou_do_proprio_processo_sem_gravador_segue_orfa(harness: Harness, dono, acao) -> None:
    st, rt, lease, sid = await _gravando(harness)
    st.db.execute("UPDATE instances SET hosted_by=? WHERE id='android-01'",
                  (st.cfg.owner_id if dono == "proprio" else None,))
    rt.training_session_id = None
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/{acao}")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == ("recorded" if acao == "stop" else "discarded")


async def test_outra_replica_nao_atrapalha_o_descarte_de_sessao_que_nao_grava(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    st.training.stop(sid, lease_id=lease)
    st.db.execute("UPDATE instances SET hosted_by=? WHERE id='android-01'", ("outra-replica",))
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/discard")
        assert r.status_code == 200 and r.json()["status"] == "discarded"
