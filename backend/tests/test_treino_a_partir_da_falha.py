"""31.111 F1: a sessão de ensino que nasce de uma etapa que falhou numa execução.

A pessoa assume o aparelho e abre a sessão pela etapa: ela fica ligada à execução, à etapa e à tentativa que falhou
(três ids opacos, migração 119), e `origin` sai em `GET /api/training/{id}` com o motivo literal do executor. Nada é
automático: sem o controle da pessoa recusa; etapa que não falhou recusa. A gravação comum não ganha `origin`.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente

TS = "2026-10-06T01:00:00.000Z"
MOTIVO = "o campo de mensagem não apareceu depois de tocar em Enviar"


def _execucao(st, status: str = "failed", *, tentativas: int = 2, instancia: str = "android-01") -> tuple[str, str]:
    run, step = "r-f1", "r-f1:android-01:v1:enviar"
    st.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (run, f"k-{run}", "x", "execute", "failed", f'["{instancia}"]', TS))
    st.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,1)",
                  (f"{run}:o1", run, instancia, "failed"))
    st.db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                  " postcondition, timeout_s, max_attempts, status, status_detail) VALUES"
                  " (?,?,?,?,1,1,'enviar','Enviar a mensagem','Enviar a mensagem','{}',60,3,?,?)",
                  (step, run, f"{run}:o1", instancia, status, MOTIVO))
    for n in range(1, tentativas + 1):
        st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, error) VALUES (?,?,?,?,?,?)",
                      (f"{step}:a{n}", step, n, "failed", TS, f"erro {n}"))
    return run, step


async def _com_controle(harness: Harness):
    st = harness.state
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    status, lease = st.devices.request_control(rt)
    assert status == "granted"
    return st, rt, lease


async def test_abre_a_sessao_ligada_a_etapa_e_a_ultima_tentativa(harness: Harness) -> None:
    st, rt, lease = await _com_controle(harness)
    run, step = _execucao(st, tentativas=2)
    async with _cliente(harness) as c:
        r = await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease})
        assert r.status_code == 201, r.text
        sessao = r.json()
        assert sessao["status"] == "recording" and sessao["instance_id"] == "android-01"
        assert sessao["intent"] == "Corrigir a etapa «Enviar a mensagem»"
        esperado = {"run_id": run, "step_id": step, "step_key": "enviar", "attempt_id": f"{step}:a2", "motivo": MOTIVO}
        assert sessao["origin"] == esperado
        assert not any(k.startswith("origin_") for k in sessao)             # as colunas não vazam, só `origin`
        lido = (await c.get(f"/api/training/{sessao['id']}")).json()
        assert lido["origin"] == esperado
        lista = (await c.get("/api/training", params={"instance_id": "android-01"})).json()
        assert [x["origin"] for x in lista] == [esperado]
    assert rt.training_session_id == sessao["id"]
    linha = st.db.one("SELECT origin_run_id, origin_step_id, origin_attempt_id FROM training_sessions WHERE id=?",
                      (sessao["id"],))
    assert (linha["origin_run_id"], linha["origin_step_id"], linha["origin_attempt_id"]) == (run, step, f"{step}:a2")


async def test_etapa_incerta_tambem_ensina_e_o_texto_e_da_pessoa_quando_ela_escreve(harness: Harness) -> None:
    st, _rt, lease = await _com_controle(harness)
    run, step = _execucao(st, "uncertain", tentativas=0)
    async with _cliente(harness) as c:
        r = await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease,
                                                         "intent": "Tocar em Enviar e esperar a caixa"})
    assert r.status_code == 201, r.text
    assert r.json()["intent"] == "Tocar em Enviar e esperar a caixa"
    assert r.json()["origin"]["attempt_id"] is None                           # sem tentativa, o resto da origem vale


async def test_recusas_nada_e_gravado(harness: Harness) -> None:
    st, rt, lease = await _com_controle(harness)
    run, step = _execucao(st)
    boa = {"run_id": run, "step_id": step, "lease_id": lease}
    async with _cliente(harness) as c:
        sem = await c.post("/api/training/from-run", json={"run_id": run, "step_id": step})
        assert sem.status_code == 422, sem.text                              # o controle da pessoa é obrigatório
        errado = await c.post("/api/training/from-run", json={**boa, "lease_id": "lease-de-outra-pessoa"})
        assert errado.status_code == 409 and "control_required" in errado.text, errado.text
        extra = await c.post("/api/training/from-run", json={**boa, "automatico": True})
        assert extra.status_code == 422
        outra = await c.post("/api/training/from-run", json={**boa, "run_id": "r-outra"})
        assert outra.status_code == 404 and "step_not_found" in outra.text, outra.text
        st.db.execute("UPDATE steps SET status='succeeded' WHERE id=?", (step,))
        ok = await c.post("/api/training/from-run", json=boa)
        assert ok.status_code == 409 and "step_not_failed" in ok.text, ok.text
        st.db.execute("UPDATE steps SET status='cancelled' WHERE id=?", (step,))
        assert (await c.post("/api/training/from-run", json=boa)).status_code == 409
    assert st.db.scalar("SELECT COUNT(*) FROM training_sessions") == 0 and rt.training_session_id is None


async def test_a_gravacao_comum_nao_tem_origem_e_a_limpeza_da_etapa_nao_derruba_a_sessao(harness: Harness) -> None:
    st, _rt, lease = await _com_controle(harness)
    comum = st.training.start("android-01", intent="Mandar mensagem para um contato do QA", lease_id=lease)
    assert comum["origin"] is None and not any(k.startswith("origin_") for k in comum)
    run, step = _execucao(st)
    async with _cliente(harness) as c:
        descartou = await c.post(f"/api/training/{comum['id']}/discard", json={"lease_id": lease})
        assert descartou.status_code == 200, descartou.text
        r = await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
    st.db.execute("DELETE FROM steps WHERE id=?", (step,))                    # a limpeza de execuções velhas
    origem = st.training.get(sid)["origin"]
    assert origem["run_id"] == run and origem["step_id"] == step and origem["attempt_id"] == f"{step}:a2"
    assert origem["step_key"] is None and origem["motivo"] is None
