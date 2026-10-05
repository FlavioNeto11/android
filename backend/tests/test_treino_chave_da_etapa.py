"""Item 31.93: a chave de etapa repetida de 40 caracteres não trava mais o `propose`.

O laço antigo de `normalizar_proposta` (`while k in vistos: k = f"{k}_2"[:40]`) nunca saía com uma chave de 40
caracteres e congelava o laço de eventos. Aqui a função roda numa thread com prazo: com o código antigo o teste FALHA."""
from __future__ import annotations

import threading
from typing import Any

import pytest

from app.models import PlanStep
from app.planning.training import TrainingRequest, normalizar_proposta

LONGO = "Abrir a conversa com o contato escolhido na lista"


def _normalizar_com_prazo(titulos: list[str]) -> list[str]:
    req = TrainingRequest(intent="x", app_id=None, apps=[], inputs=[{"seq": 1}])
    saida: dict[str, Any] = {}

    def _roda() -> None:
        saida["p"] = normalizar_proposta({"steps": [{"title": t, "inputs": [1]} for t in titulos]}, req)

    t = threading.Thread(target=_roda, daemon=True)
    t.start()
    t.join(2)
    assert not t.is_alive(), "normalizar_proposta não terminou em 2 s: laço infinito na chave repetida"
    return [e["key"] for e in saida["p"]["steps"]]


@pytest.mark.parametrize("titulos", [
    [LONGO] * 2,
    [LONGO] * 3,
    [LONGO] * 12,                                   # passa de _9 para _10: o sufixo cresce e a base encolhe
    ["1" * 60] * 3,                                 # começa por dígito: `etapa_` + 40 passava de 41 e dava 500
    ["!!!", "???", "áéí"],                          # só símbolos/acentos: "etapa", "etapa_2", "etapa_3"
    ["x", "x"],
], ids=["dois", "tres", "doze", "digitos", "simbolos", "uma_letra"])
def test_chaves_distintas_e_validas_no_padrao_do_plan_step(titulos: list[str]) -> None:
    chaves = _normalizar_com_prazo(titulos)
    assert len(chaves) == len(titulos) and len(set(chaves)) == len(chaves), chaves
    for k in chaves:
        PlanStep.model_validate({"key": k, "title": "t", "goal": "g",
                                 "postcondition": {"kind": "model_judged", "value": "", "description": "d"}})
        assert len(k) <= 40


def test_titulo_so_com_simbolos_vira_etapa() -> None:
    assert _normalizar_com_prazo(["???"]) == ["etapa"]


def test_descarte_repetido_pelo_modelo_fica_uma_vez() -> None:
    """31.95 (leitura do #442): o descarte repetido sai deduplicado da proposta, na ordem, ficando o primeiro."""
    req = TrainingRequest(intent="x", app_id=None, apps=[], inputs=[{"seq": 1}, {"seq": 2}])
    saida = normalizar_proposta({"steps": [], "discarded": [{"seq": 2, "why": "a"}, {"seq": 1, "why": "b"},
                                                           {"seq": 2, "why": "c"}, {"seq": 9, "why": "fora"}]}, req)
    assert saida["discarded"] == [{"seq": 2, "why": "a"}, {"seq": 1, "why": "b"}]


def test_parametro_com_sublinhado_inicial_fica_na_proposta() -> None:
    """31.95: o padrão é o `PLACEHOLDER` do fluxo (aceita `{_x}`); antes `{_x}` perdia o parâmetro e o save recusava."""
    req = TrainingRequest(intent="x", app_id=None, apps=[], inputs=[{"seq": 1}])
    saida = normalizar_proposta({"command_template": "abra o perfil {_x} agora", "steps": [],
                                 "parameters": [{"name": "_x", "example": "a"}, {"name": "fora", "example": "b"}]}, req)
    assert [x["name"] for x in saida["parameters"]] == ["_x"]
