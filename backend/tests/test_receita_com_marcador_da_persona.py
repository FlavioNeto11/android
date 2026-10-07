"""31.244: o `type_text` gravado com o marcador da persona vira receita, e a reprodução digita o valor da persona.

Desde o 31.113 F1 o registro grava o dado da persona já como marcador (`{perfil_nome}`), e desde o 31.243 o usuário da
conta também (`@{conta_<app>_usuario}`). A destilação lê o `type_text` do banco: o texto não tinha valor para trocar por
parâmetro, ficava "não 100 % coberto" e a etapa não virava receita. Agora o marcador cuja variável a persona do objetivo
tem conta como coberto (a arroba logo antes dele não sobra como literal), e a reprodução o resolve com as variáveis da
persona (31.87 F2: `{**persona, **rr.variables}`). Sem a variável na persona, segue recusado; sem `persona`, como antes.

Nível de prova: `simulated` (linhas de ação montadas, harness com provedor e aparelho falsos; valores inventados).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.taskqueue import executor as executor_mod
from app.taskqueue.executor import Outcome
from app.taskqueue.recipes import QUARANTINE_AFTER, Replayer, detemplate, distill

from .conftest import Harness
from .test_candidata_da_ativa_que_divergiu import _depois, _preparar

CONTA = "conta_instagram_usuario"
PERSONA = {CONTA: "usuario.inventado", "perfil_nome": "Zelda", "perfil_sobrenome": "Sintetica"}
VARIAVEIS = {"instance_id": "android-01", "run_id": "r-1", "message": "mensagem inventada"}


def _digitou(texto: str) -> list[dict[str, Any]]:
    return [{"id": 1, "tool": "type_text", "status": "done", "source": "ai", "side_effect": 0, "rationale": "buscar",
             "target": None, "args": json.dumps({"text": texto, "element_id": None, "clear_first": True})}]


def test_o_detemplate_aceita_o_marcador_que_a_persona_resolve() -> None:
    nomes = frozenset(PERSONA)
    assert detemplate("@{" + CONTA + "}", VARIAVEIS, marcadores=nomes)[2] is True
    assert detemplate("{perfil_nome} {perfil_sobrenome}", VARIAVEIS, marcadores=nomes)[2] is True
    assert detemplate("{perfil_nome}: mensagem inventada", VARIAVEIS, marcadores=nomes) == \
        ("{perfil_nome}: {message}", True, True)
    assert detemplate("@{" + CONTA + "} e sobra", VARIAVEIS, marcadores=nomes)[2] is False       # literal sobrando
    assert detemplate("{perfil_cidade}", VARIAVEIS, marcadores=nomes)[2] is False               # a persona não tem
    assert detemplate("@{" + CONTA + "}", VARIAVEIS)[2] is False                                # sem persona: antes
    assert detemplate("mensagem inventada", VARIAVEIS, marcadores=nomes) == ("{message}", True, True)


def test_o_passo_com_a_conta_vira_receita_e_reproduz_com_a_conta_da_persona() -> None:
    acoes, motivo = distill(_digitou("@{" + CONTA + "}"), VARIAVEIS, persona=frozenset(PERSONA))  # type: ignore[arg-type]
    assert motivo == "ok" and acoes is not None and acoes[0]["args"]["text"] == "@{" + CONTA + "}"
    tela = parse_hierarchy('<hierarchy><node class="android.widget.EditText" text="" resource-id="app:id/q"'
                           ' focused="true" bounds="[0,0][700,80]"/></hierarchy>')
    rep = Replayer(recipe_id=1, version=1, actions=acoes, variables={**PERSONA, **VARIAVEIS})
    decisao = rep.next(tela)
    assert decisao is not None and decisao.args["text"] == "@" + PERSONA[CONTA]


def test_sem_a_variavel_na_persona_segue_recusado() -> None:
    assert distill(_digitou("@{" + CONTA + "}"), VARIAVEIS)[0] is None                           # type: ignore[arg-type]
    assert distill(_digitou("@{" + CONTA + "}"), VARIAVEIS,                                      # type: ignore[arg-type]
                   persona=frozenset({"perfil_nome"}))[0] is None


@pytest.mark.parametrize("persona", [frozenset(PERSONA), frozenset()])
async def test_o_executor_leva_a_persona_da_tentativa_a_destilacao(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                                 persona: frozenset[str]) -> None:
    vistos: list[object] = []

    def _destilar(*_a: object, **kw: object) -> tuple[None, str]:
        vistos.append(kw.get("persona"))
        return None, "capturado"

    db, receita, etapa, tentativa = await _preparar(harness, seguidas=QUARANTINE_AFTER - 1)
    monkeypatch.setattr(executor_mod, "distill", _destilar)        # depois: o preparo aprende com a destilação real
    original = executor_mod._RecipeRun

    def _com_persona(*a: Any, **kw: Any) -> Any:
        rr = original(*a, **kw)
        rr.persona = persona
        return rr

    monkeypatch.setattr("tests.test_candidata_da_ativa_que_divergiu._RecipeRun", _com_persona)
    _depois(harness, db, receita, etapa, tentativa, Outcome.succeeded)
    assert vistos == [persona]
