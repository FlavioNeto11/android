"""31.152: os pacotes vizinhos que o ensino descobriu viram conhecimento do app para o planejador livre.

Medido em 06/10: `pacotes_aceitos` (31.123) só existia no plano de fluxo ensinado; a execução livre que abre a busca do
Configurações terminava no pacote da busca, a etapa era recusada e a IA assumia (r-20261006102728-1157c6, US$ 0,039).

O que estes testes protegem:
* o par (app, vizinho) sai dos fluxos ENSINADOS, com a contagem de sessões distintas e a origem; o fluxo desligado
  também conta, o fluxo aprendido de execução não, e o systemui e o lançador nunca entram;
* o plano livre do mesmo app ganha o vizinho nas etapas SEM efeito e sem lista própria, e a trilha diz de onde veio;
* a etapa com efeito, a etapa que já declara os seus e o app cadastrado como vizinho ficam de fora.

Nível de prova: `simulated` (funções puras e harness com provedor falso; nenhuma IA).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.planning.provider import PlanRequest
from app.taskqueue.vizinhos import Vizinho, aplicar, pares_dos_fluxos

from .conftest import Harness
from .test_etapa_pacotes_aceitos import BUSCA, _gravada_com_busca, _proposta

QA = "qa-messenger"
POST = Postcondition(kind="text_visible", value="Resultados", description="a busca mostrou o resultado")


def _plano_json(app: str | None, *passos: dict[str, Any]) -> str:
    return json.dumps({"app_id": app, "steps": list(passos)})


def test_o_par_vem_so_do_ensino_com_contagem_e_origem() -> None:
    linhas = [
        ("training:s1", QA, _plano_json(QA, {"key": "a", "pacotes_aceitos": [BUSCA, "com.android.systemui"]})),
        ("training:s2", QA, _plano_json(None, {"key": "b", "pacotes_aceitos": [BUSCA]},
                                        {"key": "c", "pacotes_aceitos": ["com.google.android.apps.nexuslauncher"]})),
        ("training:s2", QA, _plano_json(QA, {"key": "d", "pacotes_aceitos": [BUSCA]})),     # mesma sessão: conta 1
        ("run", QA, _plano_json(QA, {"key": "e", "pacotes_aceitos": ["com.de.execucao"]})),  # não é ensino
        ("training:s3", QA, _plano_json(QA, {"key": "f", "app_id": "outro", "pacotes_aceitos": ["com.do.outro"]})),
        ("training:s4", QA, "{quebrado"),
        ("training:s5", QA, _plano_json(QA, {"key": "g", "pacotes_aceitos": ["nao e pacote", "{x}"]})),
    ]
    pares = pares_dos_fluxos(linhas)
    assert set(pares) == {QA, "outro"}
    assert list(pares[QA]) == [BUSCA]
    assert pares[QA][BUSCA].contagem == 2 and pares[QA][BUSCA].origens == ["training:s1", "training:s2"]
    assert list(pares["outro"]) == ["com.do.outro"]


def test_so_a_etapa_sem_efeito_e_sem_lista_propria_ganha_o_vizinho() -> None:
    plano = Plan(summary="s", app_id=QA, planner=PlannerInfo(provider="t", model="t", simulated=True), steps=[
        PlanStep(key="buscar", title="Buscar", goal="g", postcondition=POST),
        PlanStep(key="enviar", title="Enviar", goal="g", postcondition=POST, side_effect=True),
        PlanStep(key="propria", title="P", goal="g", postcondition=POST, pacotes_aceitos=["com.dela"]),
        PlanStep(key="outro_app", title="O", goal="g", postcondition=POST, app_id="sem-ensino"),
        PlanStep(key="fazer_login", title="L", goal="g", postcondition=POST)])
    conhecidos = {QA: {BUSCA: Vizinho(BUSCA, ["training:s1"]), "com.cadastrado": Vizinho("com.cadastrado", ["training:s1"])}}
    novo, mudou = aplicar(plano, conhecidos, ["com.cadastrado"])
    por = {s.key: s.pacotes_aceitos for s in novo.steps}
    assert por == {"buscar": [BUSCA], "enviar": [], "propria": ["com.dela"], "outro_app": [], "fazer_login": []}
    assert [(k, [v.pacote for v in vs]) for k, vs in mudou] == [("buscar", [BUSCA])]
    assert plano.steps[0].pacotes_aceitos == []                                  # o plano de entrada não muda
    assert aplicar(plano, {}, [])[0] is plano


async def test_o_ensino_grava_o_par_e_o_plano_livre_aceita_o_vizinho(harness: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    st, sid = await _gravada_com_busca(harness)
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    st.db.execute("UPDATE flows SET status='disabled' WHERE id=?", (salvo["flow_id"],))   # o saber é do app
    conhecidos = st.runs.flows.vizinhos_conhecidos()
    assert conhecidos[QA][BUSCA].origens == [f"training:{sid}"] and conhecidos[QA][BUSCA].contagem == 1

    pedidos: list[PlanRequest] = []
    original = st.runs.provider.plan

    async def planejador(req: PlanRequest) -> Any:
        pedidos.append(req)
        _, uso = await original(req)
        return Plan(summary="buscar bluetooth", app_id=QA, planner=PlannerInfo(provider="t", model="t", simulated=True),
                    steps=[PlanStep(key="procurar", title="Procurar", goal="g", postcondition=POST),
                           PlanStep(key="mandar", title="Mandar", goal="g", postcondition=POST, side_effect=True,
                                    depends_on=["procurar"])]), uso
    monkeypatch.setattr(st.runs.provider, "plan", planejador)
    run = harness.run(["android-01"], mode="plan", command="procure bluetooth no QA Messenger")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=30)
    assert pedidos, "o planejador livre não foi chamado"
    plano = Plan.model_validate_json(st.repo.run_row(run.id)["plan"])
    assert {s.key: s.pacotes_aceitos for s in plano.steps} == {"procurar": [BUSCA], "mandar": []}
    trilha = st.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND message LIKE ? AND message LIKE ?",
                          (run.id, "%31.152%", f"%procurar: {BUSCA} (1 ensino(s): training:{sid})%"))
    assert trilha == 1
