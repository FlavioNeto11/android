"""31.151: o pedido parecido chega ao fluxo ensinado pelo planejador.

Medido em 06/10: 8 de 275 execuções foram planejadas por fluxo ensinado, todas lote de prova; `match` só casa o texto
inteiro do molde e o planejador não conhecia fluxo nenhum.

O que estes testes protegem:
* o fluxo que o comando parece vai ao planejador como habilidade conhecida: referência, molde, nomes dos parâmetros e
  apps; o nome do fluxo (que pode trazer o valor demonstrado) não vai ao prompt;
* escolhido e conferido (referência oferecida, parâmetros exatos, valor presente no comando), o plano é o do fluxo, a
  trilha diz "por semelhança, nota N", `runs.flow_id` e `flows.uses` andam, e o `execute` para em `planned` (a prévia
  aprovada, até a decisão do dono);
* parece e é recusado (valor fora do comando): fica o plano livre, com o motivo na trilha;
* parece e não é escolhido: o plano livre, e a trilha diz o que foi oferecido.

Nível de prova: `simulated` (harness com provedor falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.planning import habilidades as hab
from app.planning.prompts import planner_user
from app.planning.provider import PlanRequest

from .conftest import Harness

MOLDE = "abra as configurações e pesquise por {termo}"
COMANDO = "procure wifi nas configurações"
NOME_DEMONSTRADO = "Buscar rede-demonstrada-777 no app"


def _fluxo(st: Any) -> str:
    plano = Plan(summary=NOME_DEMONSTRADO, app_id="qa-messenger", parameters={"termo": "{termo}"},
                 planner=PlannerInfo(provider="treino", model="treino", simulated=True),
                 steps=[PlanStep(key="pesquisar", title="Pesquisar {termo}", goal="pesquisar {termo}",
                                 postcondition=Postcondition(kind="text_visible", value="{termo}", description="d"))])
    fid = st.scheduler.flows.learn_from_plan(plano, MOLDE, source="manual")
    return str(st.db.scalar("SELECT ref_publico FROM flows WHERE id=?", (fid,)))


def test_a_escolha_e_conferida_pelo_codigo() -> None:
    h = hab.HabilidadeConhecida(ref="f-1", molde=MOLDE, parametros=("termo",), apps=("qa-messenger",), nota=0.33)
    of = {"f-1": h}
    assert hab.escolha_do_json('{"summary": "x", "habilidade": {"ref": "f-1", "valores": {"termo": "wifi"}}}') == \
        hab.Escolha("f-1", {"termo": "wifi"})
    assert hab.escolha_do_json('{"summary": "x"}') is None and hab.escolha_do_json("sem json") is None
    assert hab.escolha_valida(hab.Escolha("f-1", {"termo": "wifi"}), of, COMANDO) is None
    assert hab.escolha_valida(hab.Escolha("f-1", {"termo": "Wifi"}), of, "Procure WIFI") is None   # sem caixa
    assert "oferecidas" in str(hab.escolha_valida(hab.Escolha("f-9", {"termo": "wifi"}), of, COMANDO))
    assert "não está no comando" in str(hab.escolha_valida(hab.Escolha("f-1", {"termo": "bluetooth"}), of, COMANDO))
    assert "parâmetros" in str(hab.escolha_valida(hab.Escolha("f-1", {}), of, COMANDO))
    assert hab.parametros_do_molde("mande {texto} para {contato} e {texto}") == ("texto", "contato")
    assert hab.parametros_do_molde("abra {account_label} e {termo}") == ("termo",)              # o aparelho resolve
    assert hab.molde_oferecivel(MOLDE) and hab.molde_oferecivel("curtir o post de {perfil}")
    assert not hab.molde_oferecivel("curtir o post de @fulana") and not hab.molde_oferecivel("ligue para 11999998888")
    assert not hab.molde_oferecivel("abra https://site.exemplo/x e {termo}")


async def _planejar(harness: Harness, monkeypatch: pytest.MonkeyPatch, escolha: dict[str, Any] | None,
                    *, mode: str = "execute") -> tuple[Any, str, list[PlanRequest]]:
    st = harness.state
    assert st is not None
    pedidos: list[PlanRequest] = []
    original = st.runs.provider.plan

    async def planejador(req: PlanRequest) -> Any:
        pedidos.append(req)
        plano, uso = await original(req)
        plano.escolha_por_semelhanca = escolha
        return plano, uso
    monkeypatch.setattr(st.runs.provider, "plan", planejador)
    run = harness.run(["android-01"], mode=mode, command=COMANDO)
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed", "running", "completed",
                                             "completed_with_issues"), timeout=30)
    return st, run.id, pedidos


def _trilha(st: Any, run_id: str, trecho: str) -> int:
    return int(st.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND message LIKE ?", (run_id, f"%{trecho}%")))


async def test_parece_e_escolhido_vira_o_plano_do_fluxo_e_espera_a_previa(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    ref = _fluxo(st)
    st, run_id, pedidos = await _planejar(harness, monkeypatch, {"ref": ref, "valores": {"termo": "wifi"}})
    (req,) = pedidos
    [h] = req.habilidades
    assert (h.ref, h.molde, h.parametros) == (ref, MOLDE, ("termo",)) and h.nota >= hab.NOTA_MINIMA
    texto = planner_user(req, 10)
    assert MOLDE in texto and "rede-demonstrada-777" not in texto and NOME_DEMONSTRADO not in texto
    row = st.repo.run_row(run_id)
    assert row["status"] == "planned"                                     # execute, mas a prévia espera uma pessoa
    plano = Plan.model_validate_json(row["plan"])
    fid = st.db.scalar("SELECT id FROM flows WHERE ref_publico=?", (ref,))
    assert plano.planner.model == f"fluxo:{fid}" and [s.key for s in plano.steps] == ["pesquisar"]
    assert plano.parameters == {"termo": "wifi"} and "escolha_por_semelhanca" not in row["plan"]
    assert row["flow_id"] == fid and st.db.scalar("SELECT uses FROM flows WHERE id=?", (fid,)) == 0   # só ao aprovar
    assert _trilha(st, run_id, f"Plano do fluxo {ref}") == 1 and _trilha(st, run_id, "por semelhança, nota") == 1


async def test_parece_e_recusado_fica_o_plano_livre(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    ref = _fluxo(st)
    st, run_id, _ = await _planejar(harness, monkeypatch, {"ref": ref, "valores": {"termo": "bluetooth"}}, mode="plan")
    plano = Plan.model_validate_json(st.repo.run_row(run_id)["plan"])
    assert not plano.planner.model.startswith("fluxo:") and st.repo.run_row(run_id)["flow_id"] is None
    assert _trilha(st, run_id, f"Habilidade {ref} escolhida por semelhança e recusada") == 1
    assert _trilha(st, run_id, "não está no comando") == 1


async def test_parece_e_nao_e_escolhido_a_trilha_diz_o_oferecido(harness: Harness,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    ref = _fluxo(st)
    st, run_id, _ = await _planejar(harness, monkeypatch, None, mode="plan")
    assert not Plan.model_validate_json(st.repo.run_row(run_id)["plan"]).planner.model.startswith("fluxo:")
    assert _trilha(st, run_id, f"Habilidades parecidas oferecidas ao planejador (31.151): {ref}") == 1


def test_o_json_do_provedor_real_traz_a_escolha_so_com_habilidades_oferecidas() -> None:
    from app.planning.parsing import plan_from_json
    from app.planning.provider import AppContext
    raw = ('{"summary": "s", "app_id": "qa-messenger", "parameters": [], "success_criteria": [], "steps": [], '
           '"missing": [], "habilidade": {"ref": "f-1", "valores": [{"nome": "termo", "valor": "wifi"}]}}')
    h = hab.HabilidadeConhecida(ref="f-1", molde=MOLDE, parametros=("termo",), apps=(), nota=0.33)
    com = PlanRequest(command=COMANDO, run_id="r", instances=[], apps=[AppContext(id="qa-messenger", name="QA",
                      package="com.pocqa.messenger", activity=None, nav_hints="", known_selectors={})], habilidades=[h])
    sem = PlanRequest(command=COMANDO, run_id="r", instances=[], apps=com.apps)
    assert plan_from_json(raw, com, provider="p", model="m", max_steps=5).escolha_por_semelhanca == {
        "ref": "f-1", "valores": {"termo": "wifi"}}
    assert plan_from_json(raw, sem, provider="p", model="m", max_steps=5).escolha_por_semelhanca is None
