"""31.111 F1: a sessão de ensino que nasce de uma etapa que falhou numa execução.

A pessoa assume o aparelho e abre a sessão pela etapa: ela fica ligada à execução, à etapa e à tentativa que falhou
(três ids opacos, migração 119), e `origin` sai em `GET /api/training/{id}` com o motivo literal do executor. Nada é
automático: sem o controle da pessoa recusa; etapa que não falhou recusa. A gravação comum não ganha `origin`.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json

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
        contexto = sessao["origin"].pop("context")                            # o F2 (detalhe) acrescenta o contexto
        assert contexto["disponivel"] is True
        assert sessao["origin"] == esperado
        assert not any(k.startswith("origin_") for k in sessao)             # as colunas não vazam, só `origin`
        lido = (await c.get(f"/api/training/{sessao['id']}")).json()
        assert {k: v for k, v in lido["origin"].items() if k != "context"} == esperado
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


async def test_o_detalhe_traz_a_trilha_o_esperado_a_tentativa_e_as_evidencias_e_a_lista_nao(harness: Harness) -> None:
    st, _rt, lease = await _com_controle(harness)
    run, step = _execucao(st, tentativas=2)
    st.db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                  " postcondition, timeout_s, max_attempts, status) VALUES (?,?,?,?,1,0,'abrir','Abrir o app','Abrir o app',"
                  "'{}',60,3,'succeeded')", (f"{run}:android-01:v1:abrir", run, f"{run}:o1", "android-01"))
    st.db.execute("UPDATE steps SET postcondition=? WHERE id=?",
                  ('{"kind":"text_visible","value":"Mensagem enviada","description":"A conversa mostra o envio.",'
                   '"required_delivery_level":null}', step))
    st.db.execute("UPDATE attempts SET failure_kind='postcondition', failure_screen='Conversa', strategy='recipe'"
                  " WHERE id=?", (f"{step}:a2",))
    for i, (kind, caminho, redigida) in enumerate([("screenshot", "ev/a.png", 0), ("screenshot", "ev/b.png", 1),
                                                    ("hierarchy", None, 0)]):
        st.db.execute("INSERT INTO evidence(run_id, instance_id, step_id, attempt_id, ts, kind, note, path, redacted)"
                      " VALUES (?,?,?,?,?,?,?,?,?)", (run, "android-01", step, f"{step}:a2", TS, kind,
                                                      f"nota {i}", caminho, redigida))
    async with _cliente(harness) as c:
        sessao = (await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease})).json()
        ctx = (await c.get(f"/api/training/{sessao['id']}")).json()["origin"]["context"]
        lista = (await c.get("/api/training")).json()
    assert [(t["step_key"], t["status"], t["falhou"]) for t in ctx["trilha"]] == [("abrir", "succeeded", False),
                                                                                 ("enviar", "failed", True)]
    assert ctx["trilha"][0]["motivo"] is None and ctx["trilha"][1]["motivo"] == MOTIVO     # motivo só de quem não deu certo
    assert ctx["esperado"] == {"kind": "text_visible", "value": "Mensagem enviada", "description": "A conversa mostra o envio."}
    assert ctx["tentativa"] == {"number": 2, "status": "failed", "erro": "erro 2", "failure_kind": "postcondition",
                                "failure_screen": "Conversa", "strategy": "recipe"}
    assert [(e["kind"], e["disponivel"]) for e in ctx["evidencias"]] == [("screenshot", True), ("screenshot", False),
                                                                        ("hierarchy", False)]   # redigida e sem arquivo
    assert all("context" not in (x["origin"] or {}) for x in lista)


async def test_o_contexto_nao_leva_segredo_e_some_quando_a_execucao_foi_limpa(harness: Harness) -> None:
    st, _rt, lease = await _com_controle(harness)
    run, step = _execucao(st)
    st.db.execute("UPDATE steps SET status_detail=? WHERE id=?",
                  ("o executor leu password=hunter2-segredo na tela", step))
    async with _cliente(harness) as c:
        sessao = (await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease})).json()
        ctx = (await c.get(f"/api/training/{sessao['id']}")).json()["origin"]
        assert "hunter2-segredo" not in str(ctx) and "hunter2-segredo" not in str(sessao)
        st.db.execute("DELETE FROM steps WHERE id=?", (step,))
        assert (await c.get(f"/api/training/{sessao['id']}")).json()["origin"]["context"] == {"disponivel": False}


async def test_f3_a_correcao_salva_nasce_candidata_com_escopo_e_prova_e_ligada_a_execucao(harness: Harness) -> None:
    """F3: a sessão aberta pela falha segue o caminho comum (gravar, propor, salvar). O fluxo nasce candidato (escopo do
    31.88, prova do 30.81, só casa para quem ensinou até a prova), e fica ligado à execução de origem: no evento do salvar
    e em `GET /api/flows`. Um fluxo que não veio de falha não tem origem."""
    from .test_treino_escopo_ao_provar import _ensinou, _persona
    from .test_modo_treinamento import SEGREDO, _entrada
    from .test_treino_previa_e_refazer_receitas import PACOTE_DO_APP, _proposta
    from .test_treino_validacao_do_salvar import COMANDO

    st, rt, lease = await _com_controle(harness)
    run, step = _execucao(st)
    origem = st.training.origem_da_falha(run, step)
    sessao = st.training.start("android-01", intent="Corrigir a etapa", lease_id=lease, app_id=PACOTE_DO_APP, origem=origem)
    sid = sessao["id"]
    st.training.record(rt, {"type": "open_app", "app_id": PACOTE_DO_APP}, None)
    harness.fakes["android-01"].screen = "home"
    await _entrada(st, rt, lease, type="tap", x=100, y=200 + 3 * 120 + 30)      # o contato
    await _entrada(st, rt, lease, type="tap", x=100, y=1200)                    # o campo de mensagem
    await _entrada(st, rt, lease, type="text", text=SEGREDO)
    await _entrada(st, rt, lease, type="tap", x=640, y=1200)                    # Enviar
    await _entrada(st, rt, lease, type="key", key="back")
    st.training.stop(sid, lease_id=lease)
    persona = _persona(st, "ensinou.falha")
    _ensinou(st, sid, persona)

    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[], scope_on_proof="quem_ensinou")
    flow_id = salvo["flow_id"]
    assert salvo["scope"] == {"on_proof": "quem_ensinou", "profile_ids": [persona], "group_ids": []}
    assert salvo["session"]["origin"]["run_id"] == run and salvo["session"]["origin"]["attempt_id"] == f"{step}:a2"
    assert salvo["ensinado_em_prova"]["persona"] == persona                    # a prova do 30.81 segue valendo
    outra = _persona(st, "outra.sem.aparelho", aparelho=False)
    assert st.scheduler.flows.match(COMANDO.replace("{contato}", "QA-001").replace("{mensagem}", "oi"), [outra]) is None
    ev = st.db.one("SELECT data FROM events WHERE kind='log' AND data LIKE ? ORDER BY id DESC LIMIT 1", (f"%{flow_id}%",))
    assert json.loads(ev["data"])["origin"] == {"run_id": run, "step_id": step, "attempt_id": f"{step}:a2"}
    async with _cliente(harness) as c:
        fluxos = {f["id"]: f for f in (await c.get("/api/flows")).json()}
    assert fluxos[flow_id]["origin"] == {"session_id": sid, "run_id": run, "step_id": step, "attempt_id": f"{step}:a2"}
    assert [fid for fid, f in fluxos.items() if f["origin"]] == [flow_id]           # os outros fluxos não têm origem
