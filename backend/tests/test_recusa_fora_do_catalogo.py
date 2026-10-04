"""Item 31.33: o pedido que nenhuma ação do catálogo do app cobre vira RECUSA, não pergunta.

Antes, o planejador punha esse caso em `missing` ("Como devo fazer isso?") e a execução ia a `needs_input` com uma
pergunta que a pessoa não tinha como responder (achado real do 23.14, 04/10). Agora o modelo diz o caso num campo
FECHADO (`fora_do_catalogo`: app e pedido), o backend monta a frase com os dados do catálogo (ADR-052: nenhum app
nem ação fixos no código) e a execução termina `failed`, sem etapa, sem pergunta e com o evento `plan.refused`.

Nível de prova: `simulated` (texto bruto do modelo escrito à mão e provedor falso; nenhuma chamada de IA).
"""
from __future__ import annotations

import json
from typing import Any

from app.automation.tools import strict_schema
from app.models import ForaDoCatalogo, Plan, PlannerInfo
from app.planning import prompts
from app.planning.capabilities import CapabilityCatalog, load_catalog
from app.planning.parsing import (_CapPlanOut, _MultiPlanCurtoOut, _MultiPlanOut, catalog_plan_from_json,
                                  texto_fora_do_catalogo)
from app.planning.provider import AppContext, PlanRequest

from .conftest import CountingProvider, Harness
from .test_recusa_no_planejamento import Roteiro, _contagem, _eventos, _parque

OUTLOOK = "com.microsoft.office.outlook"
IG = "com.instagram.android"
OUTLOOK_APP = AppContext("outlook", "Microsoft Outlook", OUTLOOK, None, None, None)
INSTAGRAM = AppContext("instagram", "Instagram", IG, None, None, None)
NO_IG = [{"instance_id": "android-01", "account_label": "eu.teste", "app_id": "instagram"}]


def _catalogo(pacote: str) -> CapabilityCatalog:
    catalogo = load_catalog(pacote)
    assert catalogo is not None
    return catalogo


def _bruto(fora: list[dict[str, Any]], *, entre_apps: bool, missing: list[Any] | None = None) -> str:
    corpo: dict[str, Any] = {"summary": "s", "parameters": [], "success_criteria": [], "steps": [],
                             "missing": missing or [], "fora_do_catalogo": fora}
    if entre_apps:
        corpo["app_id"] = "outlook"
    return json.dumps(corpo)


def test_o_campo_fechado_esta_nos_tres_esquemas_e_e_obrigatorio_no_estrito() -> None:
    for modelo in (_CapPlanOut, _MultiPlanOut, _MultiPlanCurtoOut):
        esquema = strict_schema(modelo)
        assert "fora_do_catalogo" in esquema["required"], modelo.__name__
    assert "fora_do_catalogo" in prompts.PLANNER_CAPABILITY_SYSTEM


def test_texto_sai_dos_dados_e_trata_lista_vazia() -> None:
    assert texto_fora_do_catalogo("enviar um fax.", "App X", ["Abrir a caixa", "Ler o item …"]) == (
        "Enviar um fax não está disponível no App X: o catálogo dele só tem abrir a caixa e ler o item …. Faça essa "
        "parte você mesmo ou peça só o que está nessa lista.")
    assert texto_fora_do_catalogo("", "App X", []) == "Isso não está disponível no App X. Faça essa parte você mesmo."


def test_plano_de_um_app_vira_recusa_com_os_titulos_do_catalogo() -> None:
    catalogo = _catalogo(OUTLOOK)
    req = PlanRequest(command="c", run_id="r", instances=NO_IG, apps=[OUTLOOK_APP], catalog=catalogo)
    plano = catalog_plan_from_json(_bruto([{"app_id": None, "pedido": "enviar e-mail"}], entre_apps=False,
                                          missing=[{"field": "capability", "question": "Como?"}]),
                                   req, provider="p", model="m", max_steps=10)
    assert plano.steps == [] and plano.missing == []     # a pergunta do modelo some: a recusa responde por ela
    [fora] = plano.fora_do_catalogo
    assert fora.app == "Microsoft Outlook" and fora.app_id == "outlook" and fora.pedido == "enviar e-mail"
    assert fora.disponiveis and all("{" not in d for d in fora.disponiveis)


def test_plano_entre_apps_usa_o_catalogo_do_app_citado() -> None:
    catalogos = {"instagram": _catalogo(IG), "outlook": _catalogo(OUTLOOK)}
    req = PlanRequest(command="c", run_id="r", instances=NO_IG, apps=[INSTAGRAM, OUTLOOK_APP], catalogs=catalogos)
    plano = catalog_plan_from_json(_bruto([{"app_id": "outlook", "pedido": "enviar e-mail"}], entre_apps=True),
                                   req, provider="p", model="m", max_steps=10)
    [fora] = plano.fora_do_catalogo
    assert fora.app_id == "outlook" and fora.app == "Microsoft Outlook"
    assert len(fora.disponiveis) == len({c.title for c in catalogos["outlook"].offered})


def test_sem_o_sinal_nada_muda() -> None:
    req = PlanRequest(command="c", run_id="r", instances=NO_IG, apps=[OUTLOOK_APP], catalog=_catalogo(OUTLOOK))
    plano = catalog_plan_from_json(_bruto([], entre_apps=False, missing=[{"field": "x", "question": "Qual?"}]),
                                   req, provider="p", model="m", max_steps=10)
    assert plano.fora_do_catalogo == [] and [m.question for m in plano.missing] == ["Qual?"]


async def test_execucao_termina_recusada_sem_pergunta_e_com_plan_refused(tmp_path: Any) -> None:
    recusa = ForaDoCatalogo(app_id="outlook", app="Microsoft Outlook", pedido="apagar a pasta",
                            disponiveis=["Abrir a caixa de entrada"])
    plano = Plan(summary="s", app_id="outlook", fora_do_catalogo=[recusa],
                 planner=PlannerInfo(provider="roteiro", model="t", simulated=True))
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(Roteiro(plano))
    state = await _parque(h, app_do_aparelho="outlook")
    try:
        run = h.run(["android-01"], command="apague a pasta no Outlook")
        await h.wait_run(run.id, ("needs_input", "failed", "planned", "running", "completed", "completed_with_issues"))
        linha = state.repo.run_row(run.id)
        assert linha["status"] == "failed", linha["status_detail"]
        assert linha["status_detail"].startswith("Apagar a pasta não está disponível no Microsoft Outlook: o catálogo "
                                                 "dele só tem abrir a caixa de entrada.")
        eventos = _eventos(state, run.id, "plan.refused")
        assert len(eventos) == 1 and eventos[0]["data"]["motivo"] == "sem_acao_do_catalogo"
        assert eventos[0]["data"]["pedidos"][0]["pedido"] == "apagar a pasta"
        assert _contagem(state, "steps", run.id) == 0 and _contagem(state, "objectives", run.id) == 0
        assert h.ai.count("decide") == 0 and h.ai.count("plan") == 1
        assert json.loads(linha["plan"])["fora_do_catalogo"][0]["app"] == "Microsoft Outlook"
    finally:
        await state.stop()
