"""29.143: o controle manual tem dono. Pedir o controle com outra pessoa no aparelho devolvia o MESMO lease a quem
chamasse, e quem só olhava passava a poder tocar, parar ou descartar a gravação de quem ensina. Agora a mesma pessoa
(outra aba) recebe o mesmo lease, outra recebe 409 `controlled_by_other` com quem controla e desde quando, e a tomada
explícita (`tomar`) dá um lease novo, invalida o antigo e encerra a gravação viva sem salvar nem descartar.

Nível de prova: `simulated` (harness na porta 5640, aparelho falso; nenhuma IA paga)."""
from __future__ import annotations

import pytest

from app.devices.manager import ControlError
from app.models import ControlOwner
from app.training.recorder import TrainingError

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente


async def _online(harness: Harness):
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    return st, rt


async def test_a_mesma_pessoa_recebe_o_mesmo_lease(harness: Harness) -> None:
    st, rt = await _online(harness)
    _, lease = st.devices.request_control(rt, por="operador-a")
    assert st.devices.request_control(rt, por="operador-a") == ("granted", lease)
    assert rt.lease_dono == "operador-a"


async def test_outra_pessoa_recebe_a_recusa_com_dono_e_hora_e_nada_muda(harness: Harness) -> None:
    st, rt = await _online(harness)
    _, lease = st.devices.request_control(rt, por="operador-a")
    with pytest.raises(ControlError) as exc:
        st.devices.request_control(rt, por="operador-b")
    assert exc.value.code == "controlled_by_other"
    assert exc.value.detalhes == {"dono": "operador-a", "desde": rt.control_since}
    assert "operador-a está no controle de android-01" in exc.value.message
    assert rt.lease_id == lease and rt.lease_dono == "operador-a" and rt.control == ControlOwner.user


async def test_sem_sessao_e_operador_se_recusam_um_ao_outro(harness: Harness) -> None:
    """Limite escrito: sem sessão todos são `panel` (indistinguíveis entre si), mas `panel` não toma de um operador
    com sessão, nem o contrário, sem a tomada explícita."""
    st, rt = await _online(harness)
    _, lease = st.devices.request_control(rt)                          # sem `por`: `panel`
    assert st.devices.request_control(rt, por="panel") == ("granted", lease)
    with pytest.raises(ControlError):
        st.devices.request_control(rt, por="operador-a")
    st.devices.release_control(rt, lease)
    st.devices.request_control(rt, por="operador-a")
    with pytest.raises(ControlError):
        st.devices.request_control(rt)


async def test_o_pedido_pendente_com_a_ia_tambem_tem_dono(harness: Harness) -> None:
    st, rt = await _online(harness)
    assert st.devices.ai_begin(rt)
    status, pendente = st.devices.request_control(rt, por="operador-a")
    assert status == "pending" and st.devices.request_control(rt, por="operador-a") == ("pending", pendente)
    with pytest.raises(ControlError) as exc:
        st.devices.request_control(rt, por="operador-b")
    assert exc.value.code == "controlled_by_other" and "já pediu o controle" in exc.value.message
    st.devices.ai_end(rt)                                              # a IA cede: o lease é de quem pediu
    assert rt.control == ControlOwner.user and rt.lease_id == pendente and rt.lease_dono == "operador-a"


async def test_devolvido_ou_cancelado_o_dono_sai(harness: Harness) -> None:
    st, rt = await _online(harness)
    _, lease = st.devices.request_control(rt, por="operador-a")
    st.devices.release_control(rt, lease)
    assert rt.lease_dono is None
    assert st.devices.request_control(rt, por="operador-b")[0] == "granted"   # livre: sem recusa
    assert rt.lease_dono == "operador-b"


async def test_a_tomada_explicita_troca_o_lease_e_encerra_a_gravacao_sem_descartar(harness: Harness) -> None:
    st, rt = await _online(harness)
    _, antigo = st.devices.request_control(rt, por="operador-a")
    sid = st.training.start("android-01", intent="Mandar mensagem para um contato do QA", lease_id=antigo)["id"]
    st.training.record(rt, {"type": "key", "key": "back"}, None)
    desde = int(st.db.query("SELECT COALESCE(MAX(id), 0) AS m FROM events")[0]["m"])
    status, novo = st.devices.request_control(rt, por="operador-b", tomar=True)
    assert status == "granted" and novo != antigo and rt.lease_id == novo and rt.lease_dono == "operador-b"
    # a gravação de quem ensinava terminou (nem salva nem descartada) e manteve as entradas
    sessao = st.training.get(sid)
    assert sessao["status"] == "recorded" and len(sessao["inputs"]) == 1
    assert rt.training_session_id is None and st.training.active_for("android-01") is None
    eventos = st.db.query("SELECT kind, level, message FROM events WHERE id > ? AND instance_id = ? ORDER BY id",
                          (desde, "android-01"))
    assert any(e["kind"] == "control.changed" and "operador-b tomou o controle de operador-a" in e["message"]
               for e in eventos)
    assert any(e["level"] == "warn" and "interrompida pela tomada de operador-b" in e["message"] for e in eventos)
    # o lease antigo não vale mais para nada: toque, gravação, devolução
    with pytest.raises(ControlError) as exc:
        st.devices.release_control(rt, antigo)
    assert exc.value.code == "not_controller"
    with pytest.raises(TrainingError):
        st.training.start("android-01", intent="Outra demonstração", lease_id=antigo)
    assert rt.control == ControlOwner.user and rt.lease_id == novo


async def test_a_tomada_da_propria_pessoa_nao_troca_o_lease(harness: Harness) -> None:
    st, rt = await _online(harness)
    _, lease = st.devices.request_control(rt, por="operador-a")
    sid = st.training.start("android-01", intent="Mandar mensagem para um contato do QA", lease_id=lease)["id"]
    assert st.devices.request_control(rt, por="operador-a", tomar=True) == ("granted", lease)
    assert st.training.active_for("android-01") == sid                 # outra aba não derruba a própria gravação


async def test_a_rota_responde_409_com_dono_e_toma_com_o_corpo(harness: Harness) -> None:
    st, rt = await _online(harness)
    _, antigo = st.devices.request_control(rt, por="operador-a")
    async with _cliente(harness) as c:                                 # sem sessão: `panel`
        r = await c.post("/api/instances/android-01/control/take")
        assert r.status_code == 409, r.text
        corpo = r.json()["detail"]
        assert corpo["code"] == "controlled_by_other" and corpo["dono"] == "operador-a"
        assert corpo["desde"] == rt.control_since
        assert (await c.post("/api/instances/android-01/control/take", json={"tomar": False})).status_code == 409
        r = await c.post("/api/instances/android-01/control/take", json={"tomar": True})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "granted" and r.json()["lease_id"] not in (antigo, None)
        velho = await c.post("/api/instances/android-01/control/release", json={"lease_id": antigo})
        assert velho.status_code == 409 and "not_controller" in velho.text
    assert rt.lease_dono == "panel"
