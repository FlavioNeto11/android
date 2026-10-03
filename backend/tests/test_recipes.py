"""Receitas e fluxos: aprender uma vez com a IA, repetir sem IA — contado NO PROVEDOR.
- 2º aparelho/execução: 0 decisões de IA nas etapas com receita; a mensagem sai uma única vez, com OS PARÂMETROS DELE;
- divergência (aviso inesperado) devolve só AQUELA etapa à IA;
- etapa com efeito externo nunca repete o commit, nem quando a execução é retomada depois da queda;
- seletor ambíguo diverge; versão nova do app reaprende; 3 falhas seguidas → quarentena;
- nada sensível vira receita; fluxo reaproveita o plano sem chamar o planejador."""
from __future__ import annotations

from app.automation.hierarchy import parse_hierarchy
from app.db import loads
from app.taskqueue.flows import FlowStore
from app.taskqueue.recipes import RecipeDiverged, Replayer, detemplate, distill, resolve_selectors, unique_selectors

from .conftest import Harness


def _replay(h: Harness) -> None:
    h.cfg.file.ai.recipes = "replay"


async def test_aprende_num_aparelho_e_repete_no_outro_sem_decisoes_de_ia(harness: Harness) -> None:
    _replay(harness)
    first = await harness.wait_run(harness.run(["android-01"]).id)
    assert first.status == "completed"
    db = harness.state.db                                # type: ignore[union-attr]
    learned = {r["step_key"]: loads(r["actions"]) for r in db.query("SELECT * FROM recipes WHERE status='active'")}
    assert {"open_conversation", "compose_message", "send_message"} <= set(learned)
    assert learned["compose_message"][0]["args"]["text"] == "{message}" or "{" in learned["compose_message"][0]["args"]["text"]
    assert learned["send_message"][-1]["commit"] is True
    assert "android-01" not in str(learned) and first.id not in str(learned)       # nada específico de quem aprendeu

    harness.ai.calls.clear()
    second = await harness.wait_run(harness.run(["android-02"]).id)
    assert second.status == "completed"
    ai = harness.ai
    for key in ("open_conversation", "compose_message", "send_message"):
        assert ai.count("decide", step=key) == 0, key                              # repetido por seletor, sem modelo
    msgs = harness.fakes["android-02"].messages
    assert len(msgs) == 1 and "android-02" in msgs[0].body and second.id in msgs[0].body   # parâmetros DESTE aparelho
    sources = {r["source"] for r in db.query(
        "SELECT a.source FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id"
        " WHERE s.run_id=? AND s.key='send_message'", (second.id,))}
    assert sources == {"recipe"}
    driven = {r["key"]: r["driven_by"] for r in db.query("SELECT key, driven_by FROM steps WHERE run_id=?", (second.id,))}
    assert driven["send_message"] == "recipe" and driven["open_conversation"] == "recipe"
    assert ai.count("decide") < 4                                                   # antes: ≥ 8 decisões por aparelho


async def test_herda_da_versao_anterior(harness: Harness) -> None:
    """RA-20: o app atualizou e a etapa não tem receita na versão nova. A receita PROVADA da versão anterior é herdada
    como candidata (a IA segue decidindo e ela só é comparada); depois de concordar `recipes_promote_after` vezes,
    volta a agir: 0 decisões do ator nas etapas sem efeito. A de envio (`commit`) para em `validated` (D1)."""
    _replay(harness)
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    db = harness.state.db                                # type: ignore[union-attr]
    doadoras = {r["step_key"]: r for r in db.query("SELECT * FROM recipes WHERE app_version='1.0(1)' AND status='active'")}
    harness.cfg.file.ai.recipes_promote_after = 2
    fake = harness._factory(type("RT", (), {"id": "android-02", "index": 2})())
    fake.version = "2.0(7)"
    for _ in range(2):                                    # duas execuções em sombra, a IA decidindo
        harness.ai.calls.clear()
        assert (await harness.wait_run(harness.run(["android-02"]).id)).status == "completed"
        assert harness.ai.count("decide", step="open_conversation") >= 1
    herdeiras = {r["step_key"]: r for r in db.query("SELECT * FROM recipes WHERE app_version='2.0(7)'")}
    assert herdeiras["open_conversation"]["status"] == "active"
    assert herdeiras["compose_message"]["status"] == "active"
    assert herdeiras["send_message"]["status"] == "validated"                       # efeito externo: espera o dono
    for key in ("open_conversation", "compose_message", "send_message"):          # a origem é a da doadora
        assert herdeiras[key]["learned_from_step"] == doadoras[key]["learned_from_step"], key
        assert loads(herdeiras[key]["actions"]) == loads(doadoras[key]["actions"]), key
    harness.ai.calls.clear()
    assert (await harness.wait_run(harness.run(["android-02"]).id)).status == "completed"
    assert harness.ai.count("decide", step="open_conversation") == 0
    assert harness.ai.count("decide", step="compose_message") == 0


async def test_divergencia_devolve_so_aquela_etapa_a_ia(harness: Harness) -> None:
    _replay(harness)
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    fake = harness._factory(type("RT", (), {"id": "android-03", "index": 3})())
    fake.interstitial = True                              # aviso inesperado cobre a tela inicial deste aparelho
    harness.ai.calls.clear()
    run = await harness.wait_run(harness.run(["android-03"]).id)
    assert run.status == "completed" and len(fake.messages) == 1
    ai = harness.ai
    assert ai.count("decide") >= 1                        # a IA dispensou o aviso…
    assert ai.count("decide", step="send_message") == 0   # …e as etapas seguintes voltaram para a receita
    assert ai.count("decide", step="compose_message") == 0


async def test_commit_nunca_se_repete_mesmo_com_erro_depois_do_efeito(harness: Harness) -> None:
    _replay(harness)
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    fake = harness._factory(type("RT", (), {"id": "android-02", "index": 2})())
    fake.send_fault = "error_after_effect"                # o envio acontece, mas o driver devolve erro
    run = await harness.wait_run(harness.run(["android-02"]).id)
    assert len(fake.messages) == 1                        # exatamente uma mensagem, com receita ou sem
    assert run.status in ("completed", "completed_with_issues")
    commits = harness.state.db.query(                     # type: ignore[union-attr]
        "SELECT a.status FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id"
        " WHERE s.run_id=? AND a.side_effect=1", (run.id,))
    assert len(commits) == 1


async def test_versao_nova_do_app_reaprende_e_quarentena_apos_falhas(harness: Harness) -> None:
    _replay(harness)
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    db = harness.state.db                                # type: ignore[union-attr]
    n_v1 = db.scalar("SELECT COUNT(*) FROM recipes WHERE app_version='1.0(1)'")
    fake = harness._factory(type("RT", (), {"id": "android-02", "index": 2})())
    fake.version = "2.0(7)"                               # app atualizado neste aparelho: receitas antigas não valem
    harness.ai.calls.clear()
    assert (await harness.wait_run(harness.run(["android-02"]).id)).status == "completed"
    assert harness.ai.count("decide", step="send_message") == 1                     # reaprendeu com a IA
    assert db.scalar("SELECT COUNT(*) FROM recipes WHERE app_version='2.0(7)'") >= 3
    assert db.scalar("SELECT COUNT(*) FROM recipes WHERE app_version='1.0(1)'") == n_v1   # as antigas ficam

    store = harness.state.scheduler.executor.recipes      # type: ignore[union-attr]
    receita = db.one("SELECT * FROM recipes WHERE step_key='send_message' AND app_version='1.0(1)'")
    rid = receita["id"]
    # a busca usa a MESMA identidade com que a receita foi gravada (pacote, versão, assinatura e variante)
    def procurar() -> object:
        return store.find("com.pocqa.messenger", "1.0(1)", receita["step_hash"],
                          signature=receita["app_signature"], variant=receita["variant"])
    assert procurar() is not None
    assert [store.result(rid, False) for _ in range(3)] == [False, False, True]     # 3ª falha seguida → quarentena
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (rid,)) == "quarantined"
    assert procurar() is None
    # variante diferente (outro idioma/densidade) NÃO reaproveita a receita: a tela é outra
    assert store.find("com.pocqa.messenger", "1.0(1)", receita["step_hash"],
                      signature=receita["app_signature"], variant="pt-BR/xxhdpi") is None


async def test_fluxo_reaproveita_o_plano_sem_chamar_o_planejador(harness: Harness) -> None:
    harness.cfg.file.ai.flows = True
    _replay(harness)
    cmd = ('Abra o QA Messenger, entre na conversa com o contato de teste identificado como {c} e envie '
           '"{m} {{instance_id}} {{run_id}}". Confirme que a mensagem apareceu como enviada.')
    first = await harness.wait_run(harness.run(["android-01"], command=cmd.format(c="QA-001", m="Teste POC")).id)
    assert first.status == "completed" and harness.ai.count("plan") == 1
    flows = harness.state.db.query("SELECT * FROM flows")            # type: ignore[union-attr]
    assert len(flows) == 1 and "{recipient}" in flows[0]["command_template"]
    # mesmo comando, OUTRO contato e OUTRO texto: plano reaproveitado, zero chamadas ao planejador
    second = await harness.wait_run(harness.run(["android-02"], command=cmd.format(c="QA-002", m="Aviso geral")).id)
    assert second.status == "completed" and harness.ai.count("plan") == 1
    msg = harness.fakes["android-02"].messages[0]
    assert msg.contact == "QA-002" and msg.body.startswith("Aviso geral android-02")
    assert harness.state.db.scalar("SELECT flow_id FROM runs WHERE id=?", (second.id,)) == flows[0]["id"]  # type: ignore[union-attr]
    # comando diferente não casa com o fluxo
    assert FlowStore(harness.state.db).match("Abra o QA Messenger e apague a conversa com QA-001") is None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------- unidades
XML = ('<hierarchy>'
       '<node class="android.widget.TextView" text="QA-001" resource-id="app:id/conversation_name" clickable="true" bounds="[0,100][700,160]"/>'
       '<node class="android.widget.TextView" text="QA-002" resource-id="app:id/conversation_name" clickable="true" bounds="[0,200][700,260]"/>'
       '<node class="android.widget.Button" text="Enviar" resource-id="app:id/send_button" clickable="true" bounds="[600,1100][700,1180]"/>'
       '<node class="android.widget.EditText" text="" resource-id="app:id/message_input" bounds="[0,1100][590,1180]"/>'
       '</hierarchy>')


def test_seletores_unicos_destemplatizacao_e_divergencia() -> None:
    tree = parse_hierarchy(XML)
    row1, send = tree.elements[0], tree.elements[2]
    assert unique_selectors(tree, row1) == ["rid+text", "text"]          # o id sozinho é ambíguo (2 linhas)
    assert unique_selectors(tree, send)[:2] == ["rid+text", "rid"]
    variables = {"recipient": "QA-001", "message": "Teste POC android-01 r-1", "instance_id": "android-01", "run_id": "r-1"}
    assert detemplate("Teste POC android-01 r-1", variables) == ("{message}", True, True)
    assert detemplate("Oi, QA-001!", variables) == ("Oi, {recipient}!", True, False)   # sobra literal → não coberto

    target = {**row1.to_dict(), "unique": unique_selectors(tree, row1)}
    rows = [{"tool": "tap", "status": "done", "source": "ai", "args": '{"element_id":"e1"}', "target": __import__("json").dumps(target),
             "side_effect": 0, "rationale": "abrir"}]
    actions, why = distill(rows, variables)                               # type: ignore[arg-type]
    assert why == "ok" and actions and actions[0]["selectors"][0] == {"kind": "rid+text", "rid": "app:id/conversation_name",
                                                                      "text": "{recipient}"}
    # outro aparelho, outro destinatário: o MESMO seletor aponta para a linha certa
    el = resolve_selectors(tree, actions[0]["selectors"], {**variables, "recipient": "QA-002"})
    assert el is not None and el.text == "QA-002"
    # destinatário que não está na tela → divergência (nunca toca no "mais parecido")
    rep = Replayer(recipe_id=1, version=1, actions=actions, variables={**variables, "recipient": "QA-404"})
    try:
        rep.next(tree)
        raise AssertionError("deveria divergir")
    except RecipeDiverged:
        pass


def test_nada_sensivel_ou_fragil_vira_receita() -> None:
    variables = {"pin": "1234", "recipient": "QA-001"}
    base = {"status": "done", "source": "ai", "side_effect": 0, "rationale": "", "target": None}
    # texto digitado que não vem de parâmetro (ou vem de parâmetro sensível) não é aprendido
    assert distill([{**base, "tool": "type_text", "args": '{"text":"segredo livre","element_id":null}'}], variables)[0] is None  # type: ignore[list-item]
    assert distill([{**base, "tool": "type_text", "args": '{"text":"1234","element_id":null}'}], variables)[0] is None           # type: ignore[list-item]
    # toque por coordenadas soltas (sem alvo resolvido) e navegação dependente de estado também não
    assert distill([{**base, "tool": "tap", "args": '{"x":10,"y":20}'}], variables)[0] is None                                   # type: ignore[list-item]
    assert distill([{**base, "tool": "press_back", "args": "{}"}], variables)[0] is None                                         # type: ignore[list-item]
    # tentativa que teve ação falha não é "limpa"
    assert distill([{**base, "tool": "open_app", "args": "{}", "status": "failed"}], variables)[0] is None                       # type: ignore[list-item]
    # a IA declarou pronto sem agir (o aparelho já estava lá): não há caminho a repetir — receita 46 de eda77f
    acoes, motivo = distill([{**base, "tool": "step_done", "args": "{}"}], variables)                                            # type: ignore[list-item]
    assert acoes is None and "nenhuma ação" in motivo


async def test_api_de_custo_fluxos_e_receitas(harness: Harness) -> None:
    import httpx

    from app.main import create_app

    harness.cfg.file.ai.flows = True
    _replay(harness)
    run = await harness.wait_run(harness.run(["android-01"]).id)
    await harness.wait_run(harness.run(["android-02"]).id)
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        u = (await c.get("/api/usage", params={"run_id": run.id})).json()
        assert {g["role"] for g in u["groups"]} >= {"plan", "decide", "verify"} and u["objectives_with_ai"] == 1
        assert u["unpriced_models"] == ["simulado"] and u["calls_per_objective"] > 0
        week = (await c.get("/api/usage")).json()
        assert week["steps_driven_by"].get("recipe", 0) >= 3            # 2ª execução: etapas por receita
        flows = (await c.get("/api/flows")).json()
        assert len(flows) == 1 and flows[0]["uses"] == 1                 # a 2ª execução reaproveitou o plano
        recipes = (await c.get("/api/recipes")).json()
        send = next(r for r in recipes if r["step_key"] == "send_message")
        assert send["replay_ok"] == 1 and send["actions"][-1]["commit"] is True
        # `GET /api/runs` passou a ser uma PÁGINA (`{runs, total, limit, offset}`): sem paginação, execução
        # antiga não tinha como ser alcançada por caminho nenhum (item 4.1).
        detail = (await c.get(f"/api/runs/{(await c.get('/api/runs')).json()['runs'][0]['id']}")).json()
        assert any(s["driven_by"] == "recipe" for s in detail["steps"])
        assert any(a["source"] == "recipe" for t in detail["attempts"] for a in t["actions"])
        assert (await c.put(f"/api/recipes/{send['id']}", json={"status": "quarantined"})).status_code == 200
        assert (await c.delete(f"/api/flows/{flows[0]['id']}", headers={"origin": "http://127.0.0.1:8000"})).status_code == 204
        assert (await c.get("/api/flows")).json() == []


async def test_app_travado_recupera_com_o_app_encerrado_e_nao_poe_receita_em_quarentena(harness: Harness) -> None:
    """Visto em emulador real: aparelho recém-ligado, app com a tela preta aceitando toques sem reagir. A
    recuperação automática encerra o app antes de refazer o plano; a receita (boa nos outros aparelhos) sobrevive."""
    _replay(harness)
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    fake = harness._factory(type("RT", (), {"id": "android-03", "index": 3})())
    fake.screen, fake.frozen = "home", True
    run = await harness.wait_run(harness.run(["android-03"]).id, timeout=120)
    assert run.status == "completed" and len(fake.messages) == 1
    assert "force_stop" in fake.calls and not fake.frozen
    db = harness.state.db                                # type: ignore[union-attr]
    assert db.scalar("SELECT COUNT(*) FROM recipes WHERE status='quarantined'") == 0


def test_seletor_com_username_sem_arroba_vira_parametro_e_reproduz_para_outra_pessoa() -> None:
    """A linha da conversa mostra "ana" para o parâmetro "@ana". O seletor tem de virar `{username}` — gravado
    literal, a receita reproduzida para "@bia" tocava a conversa da ana (achado da fase G, 27/09)."""
    xml = ('<hierarchy>'
           '<node class="android.widget.TextView" text="ana" resource-id="com.instagram.android:id/row_inbox_username" clickable="true" bounds="[0,100][700,160]"/>'
           '<node class="android.widget.TextView" text="bia" resource-id="com.instagram.android:id/row_inbox_username" clickable="true" bounds="[0,200][700,260]"/>'
           '<node class="android.widget.TextView" text="Mariana" resource-id="com.instagram.android:id/row_inbox_username" clickable="true" bounds="[0,300][700,360]"/>'
           '</hierarchy>')
    tree = parse_hierarchy(xml)
    ana = tree.elements[0]
    variables = {"username": "@ana"}
    target = {**ana.to_dict(), "unique": unique_selectors(tree, ana)}
    rows = [{"tool": "tap", "status": "done", "source": "ai", "args": '{"element_id":"e1"}',
             "target": __import__("json").dumps(target), "side_effect": 0, "rationale": "abrir"}]
    actions, why = distill(rows, variables)                               # type: ignore[arg-type]
    assert why == "ok" and actions
    assert all(s.get("text") in (None, "{username}") for s in actions[0]["selectors"]), actions[0]["selectors"]
    el = resolve_selectors(tree, actions[0]["selectors"], {"username": "@bia"})
    assert el is not None and el.text == "bia"
    # um pedaço do nome nunca vira parâmetro: "Mariana" não é "@ana"
    from app.taskqueue.recipes import build_selectors
    mari = {**tree.elements[2].to_dict(), "unique": unique_selectors(tree, tree.elements[2])}
    assert all(s.get("text") != "{username}" for s in build_selectors(mari, variables))

