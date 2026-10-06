"""31.142 (adendo v1.91): a sugestão de pós-condição sai pronta (`kind` e `value`) e a prévia devolve o comando
repetido no corpo, junto das pós-condições e dos avisos.

Achados da prova F2 (06/10, trn-YjYU8iobj_V42xXx): a única sugestão era "Back", a descrição do botão voltar; trocar só o
valor de um `element_present` deixaria o seletor puro, que só olha o texto. E a prévia parava no 409
`duplicate_command`, escondendo as pós-condições que já valem até a pessoa trocar o comando.

Nível de prova: `simulated` (funções puras e harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.automation.hierarchy import UiTree
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.training import partida
from app.training.recorder import TrainingError

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_partida_f2_e_sequencia import BUSCANDO, INICIO, _gravada, _passo, _proposta


def _tela(*elementos: dict[str, str]) -> UiTree:
    tela = partida.tela_de_partida({"screen_elements": list(elementos)})
    assert tela is not None
    return tela


# ------------------------------------------------------------------ a sugestão pronta, função pura
def test_element_present_vira_o_seletor_do_campo_em_que_o_texto_esta() -> None:
    seguinte = _tela({"d": "Back"}, {"t": "No results"})
    assert partida.pronta("element_present", "Back", seguinte) == {
        "kind": "element_present", "value": "desc==Back", "texto": "Back"}
    assert partida.pronta("element_present", "No results", seguinte) == {
        "kind": "element_present", "value": "text==No results", "texto": "No results"}
    # o seletor pronto casa na tela seguinte; o puro "Back" (só o valor trocado) não casaria
    assert seguinte.find_selector("desc==Back") and not seguinte.find_selector("Back")


def test_text_visible_fica_text_visible_e_o_que_nao_da_para_saber_tambem() -> None:
    seguinte = _tela({"d": "Back"})
    assert partida.pronta("text_visible", "Back", seguinte) == {"kind": "text_visible", "value": "Back", "texto": "Back"}
    assert seguinte.contains_text("Back")                                    # o verificador lê a descrição também
    # só nas screen_lines (nenhum elemento com esse texto), sem tela, ou com o separador do seletor
    assert partida.pronta("element_present", "Wi-Fi", seguinte)["kind"] == "text_visible"
    assert partida.pronta("element_present", "Back", None)["kind"] == "text_visible"
    assert partida.pronta("element_present", "a | b", _tela({"t": "a | b"}))["kind"] == "text_visible"


def test_o_caso_da_prova_sai_com_desc_e_nao_casa_na_partida() -> None:
    (achado,) = partida.ja_valem([_passo("abrir_busca", [1], "element_present", "text==Search settings")],
                                 [INICIO, BUSCANDO])
    assert achado["sugestoes"] == ["Back", "No results"]                    # as de sempre, na mesma ordem
    assert achado["sugestoes_prontas"] == [
        {"kind": "element_present", "value": "desc==Back", "texto": "Back"},
        {"kind": "element_present", "value": "text==No results", "texto": "No results"}]
    partida_, seguinte = partida.tela_de_partida(INICIO), partida.tela_de_partida(BUSCANDO)
    assert partida_ is not None and seguinte is not None
    for s in achado["sugestoes_prontas"]:                                   # type: ignore[attr-defined]
        assert seguinte.find_selector(s["value"]) and not partida_.find_selector(s["value"])
    (texto,) = partida.ja_valem([_passo("a", [1], "text_visible", "Search settings")], [INICIO, BUSCANDO])
    assert [s["kind"] for s in texto["sugestoes_prontas"]] == ["text_visible", "text_visible"]  # type: ignore[union-attr]


def test_a_sugestao_pronta_leva_a_marca_da_persona() -> None:
    achado = {"key": "abrir", "titulo": "abrir", "kind": "element_present", "valor": "text==Ana",
              "sugestoes": ["Ana respondeu"],
              "sugestoes_prontas": [{"kind": "element_present", "value": "text==Ana respondeu", "texto": "Ana respondeu"}]}
    (item,) = partida.estruturados([achado], {"perfil_nome": "Ana"})
    assert item["sugestoes_prontas"] == [{"kind": "element_present", "value": "text=={perfil_nome} respondeu",
                                          "texto": "{perfil_nome} respondeu"}]
    assert "Ana" not in json.dumps(item, ensure_ascii=False)


# ------------------------------------------------------------------ a prévia com o comando repetido (harness)
def _ja_existe(st: Any, comando: str) -> None:
    passo = PlanStep(key="outra", title="x", goal="x",
                     postcondition=Postcondition(kind="model_judged", value="", description="x"))
    st.scheduler.flows.learn_from_plan(Plan(summary="outro", steps=[passo],
                                           planner=PlannerInfo(provider="fake", model="fake", simulated=True)), comando, source="run:r-outro")


async def test_a_previa_devolve_o_comando_repetido_num_200_junto_das_pos_condicoes(harness: Harness) -> None:
    st, sid = await _gravada(harness)
    ja_estava = _proposta({"kind": "element_present", "value": "text==Buscar no app", "description": "x"})
    _ja_existe(st, ja_estava["command_template"])
    fluxos = st.db.scalar("SELECT COUNT(*) FROM flows")
    async with _cliente(harness) as c:
        r = await c.post(f"/api/training/{sid}/preview", json={"proposal": ja_estava})
    assert r.status_code == 200, r.text
    previa = r.json()
    assert previa["code"] == "duplicate_command" and previa["message"] and previa["warnings"][0] == previa["message"]
    (item,) = previa["pos_condicoes_ja_valem"]                             # o que o 409 escondia
    assert item["etapa"] == "abrir_busca" and item["sugestoes_prontas"] == [
        {"kind": "element_present", "value": "text==Nenhum resultado", "texto": "Nenhum resultado"}]
    assert {s["key"] for s in previa["steps"]} == {"abrir", "abrir_busca", "buscar"}
    assert st.db.scalar("SELECT COUNT(*) FROM flows") == fluxos            # nada gravado

    # o `save` segue recusando: primeiro a pós-condição que já vale; trocada, o comando repetido (409)
    with pytest.raises(TrainingError) as pos:
        await st.skills.save(sid, proposal=ja_estava, profile_ids=[], group_ids=[])
    assert (pos.value.code, pos.value.status) == ("pos_condicao_ja_vale", 400)
    trocada = _proposta({"kind": item["sugestoes_prontas"][0]["kind"], "value": item["sugestoes_prontas"][0]["value"],
                         "description": "x"})
    with pytest.raises(TrainingError) as repetido:
        await st.skills.save(sid, proposal=trocada, profile_ids=[], group_ids=[])
    assert (repetido.value.code, repetido.value.status) == ("duplicate_command", 409)
    assert st.db.scalar("SELECT COUNT(*) FROM flows") == fluxos


async def test_sem_recusa_o_codigo_e_nulo(harness: Harness) -> None:
    st, sid = await _gravada(harness)
    previa = await st.skills.preview(sid, proposal=_proposta({"kind": "text_visible", "value": "Nenhum resultado",
                                                              "description": "x"}), profile_ids=[], group_ids=[])
    assert previa["code"] is None and previa["message"] is None and previa["pos_condicoes_ja_valem"] == []
