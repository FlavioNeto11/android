"""31.90-D: desfazer a última entrada de uma gravação VIVA do modo treinamento, sem descartar a sessão inteira.

Só quem está com o controle do aparelho desfaz (o mesmo lease do `start` e do `stop`). A gravação parada se corrige na
revisão, e a órfã não tem quem esteja ensinando. O `seq` opcional impede apagar uma entrada que chegou depois do que a
pessoa viu. A entrada sai só da gravação: o aparelho não volta.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_parar_exige_controle import _gravando, _linha

MSG_CONTROLE = "Só quem está com o controle do aparelho desfaz a última entrada."


def _seqs(st, sid: str) -> list[tuple[int, str]]:
    return [(e["seq"], e["type"]) for e in st.training.get(sid)["inputs"]]


async def test_com_o_lease_tira_so_a_ultima_e_a_gravacao_segue(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    st.training.record(rt, {"type": "key", "key": "home"}, None)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["undone"] == {"seq": 2, "type": "key"} and corpo["status"] == "recording"
    assert [e["seq"] for e in corpo["inputs"]] == [1] and _seqs(st, sid) == [(1, "key")]
    assert rt.training_session_id == sid and st.training.active_for("android-01") == sid
    st.training.record(rt, {"type": "key", "key": "back"}, None)              # o gravador continua do que ficou
    assert _seqs(st, sid) == [(1, "key"), (2, "key")]
    ev = st.db.one("SELECT data FROM events WHERE kind='training.input.undone' ORDER BY id DESC LIMIT 1")
    assert ev is not None and json.loads(ev["data"]) == {"training_session_id": sid, "seq": 2, "type": "key"}


async def test_desfazer_ate_esvaziar_e_depois_nao_ha_o_que_desfazer(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    async with _cliente(harness) as c:
        assert (await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease})).status_code == 200
        r = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease})
    assert r.status_code == 409 and "sem_entrada" in r.text, r.text
    assert _seqs(st, sid) == [] and _linha(st, sid)["status"] == "recording"


async def test_sem_lease_ou_com_lease_errado_recusa_e_nada_muda(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    async with _cliente(harness) as c:
        sem = await c.post(f"/api/training/{sid}/undo", json={})
        assert sem.status_code == 422, sem.text                   # o lease é obrigatório no corpo
        errado = await c.post(f"/api/training/{sid}/undo", json={"lease_id": "lease-de-outra-pessoa"})
        assert errado.status_code == 409 and "control_required" in errado.text and MSG_CONTROLE in errado.text
        extra = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease, "tudo": True})
        assert extra.status_code == 422
    assert _seqs(st, sid) == [(1, "key")]


async def test_o_seq_visto_confere_que_a_ultima_nao_mudou(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    st.training.record(rt, {"type": "key", "key": "home"}, None)         # chegou depois do que a pessoa viu (seq 1)
    async with _cliente(harness) as c:
        mudou = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease, "seq": 1})
        assert mudou.status_code == 409 and "entrada_mudou" in mudou.text, mudou.text
        assert _seqs(st, sid) == [(1, "key"), (2, "key")]
        certo = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease, "seq": 2})
        assert certo.status_code == 200 and certo.json()["undone"]["seq"] == 2
        zero = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease, "seq": 0})
        assert zero.status_code == 422
    assert _seqs(st, sid) == [(1, "key")]


async def test_gravacao_parada_se_corrige_na_revisao(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    st.training.stop(sid, lease_id=lease)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease})
    assert r.status_code == 409 and "nao_esta_gravando" in r.text, r.text
    assert _seqs(st, sid) == [(1, "key")]


async def test_parou_entre_a_conferencia_e_a_escrita_nao_apaga(harness: Harness) -> None:
    """N1 da leitura: o `stop` que chega depois da conferência do lease e antes da transação. O status se confere de
    novo dentro dela, e a gravação parada fica com a entrada."""
    st, rt, lease, sid = await _gravando(harness)
    conferir = st.training._gravando_com_controle                           # noqa: SLF001

    def para_no_meio(instance_id: str, session_id: str) -> bool:
        ok = conferir(instance_id, session_id)
        st.db.execute("UPDATE training_sessions SET status='recorded' WHERE id=?", (session_id,))
        return ok

    st.training._gravando_com_controle = para_no_meio                       # noqa: SLF001
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease})
    assert r.status_code == 409 and "nao_esta_gravando" in r.text, r.text
    assert _seqs(st, sid) == [(1, "key")]


async def test_orfa_sem_gravador_nao_tem_quem_desfaca(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    rt.training_session_id = None
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease})
    assert r.status_code == 409 and "control_required" in r.text and MSG_CONTROLE in r.text
    assert _seqs(st, sid) == [(1, "key")]


async def test_gravacao_de_outra_replica_desfaz_por_la(harness: Harness) -> None:
    st, rt, lease, sid = await _gravando(harness)
    st.db.execute("UPDATE instances SET hosted_by=? WHERE id='android-01'", ("outra-replica",))
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/undo", json={"lease_id": lease})
    assert r.status_code == 409 and "gravacao_em_outro_servidor" in r.text, r.text
    assert _seqs(st, sid) == [(1, "key")]


async def test_sessao_inexistente_e_404(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.post("/api/training/nao-existe/undo", json={"lease_id": "x"})
    assert r.status_code == 404 and "not_found" in r.text
