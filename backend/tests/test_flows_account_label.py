"""LT-3 (caminho rápido 1): `FlowStore.match` não exige valor para parâmetro RESERVED.

`account_label` (e `instance_id`/`run_id`) nunca é capturado do comando por `_extract`: o valor é do APARELHO e entra
na materialização por aparelho. O molde `{account_label}` ficava no plano congelado e `match` o tratava como "faltou
valor", recusando todo fluxo que o carregava — a execução repetida pagava o planejador de novo. Parâmetro NÃO reservado
sem valor continua recusando.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.modules.skills.domain.matching import bind_template_parameters
from app.taskqueue.flows import FlowStore
from app.taskqueue.repository import resolve_templates

from .fake_skills import banco, fluxo
from .test_habilidades_na_execucao import Mundo

MODELO = "curtir o post de {perfil}"


def _plano(parametros: dict[str, str]) -> dict[str, object]:
    return {"summary": "Curtir o post de {perfil}", "app_id": "instagram", "parameters": parametros,
            "steps": [{"key": "abrir", "title": "Abrir o perfil de {perfil} como {account_label}",
                       "goal": "abrir {perfil}",
                       "postcondition": {"kind": "app_foreground", "value": "instagram", "description": "app aberto"}}],
            "planner": {"provider": "fluxo", "model": "m", "simulated": True}}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco(tmp_path)
    yield d
    d.close()


def test_fluxo_com_account_label_casa(db: Database) -> None:
    fluxo(db, "f-conta", MODELO, plano=_plano({"perfil": "{perfil}", "account_label": "{account_label}"}))
    achado = FlowStore(db).match("curtir o post de @spacex")
    assert achado is not None
    _, plano = achado
    assert plano.parameters["perfil"] == "@spacex"
    # O molde do reservado segue intacto — quem o resolve é a materialização, por aparelho; nada foi inventado aqui.
    assert plano.parameters["account_label"] == "{account_label}"
    assert resolve_templates(plano.parameters["account_label"], {"account_label": "@tadeu.real"}) == "@tadeu.real"


def test_fluxo_com_account_label_casa_o_comando_repetido(db: Database) -> None:
    fluxo(db, "f-conta2", MODELO, plano=_plano({"perfil": "{perfil}", "account_label": "{account_label}",
                                                "instance_id": "{instance_id}", "run_id": "{run_id}"}))
    flows = FlowStore(db)
    assert flows.match("curtir o post de @esa") is not None
    assert flows.match("curtir o post de @esa") is not None


def test_parametro_nao_reservado_sem_valor_continua_recusando(db: Database) -> None:
    # `{extra}` não está no comando (nem no molde): faltou valor de verdade, não é este fluxo.
    fluxo(db, "f-falta", MODELO, plano=_plano({"perfil": "{perfil}", "extra": "{extra}",
                                               "account_label": "{account_label}"}))
    assert FlowStore(db).match("curtir o post de @spacex") is None
    assert bind_template_parameters({"perfil": "{perfil}", "extra": "{extra}",
                                     "account_label": "{account_label}"}, {"perfil": "@spacex"}) is None


# ------------------------------------------------------------------ 30.29: o mesmo pelo caminho da habilidade
def test_bind_nao_exige_os_reservados_do_comando() -> None:
    reservados = {"account_label": "{account_label}", "instance_id": "{instance_id}", "run_id": "{run_id}"}
    assert bind_template_parameters({"perfil": "{perfil}", **reservados}, {"perfil": "@esa"}) == {
        "perfil": "@esa", **reservados}


def test_fluxo_com_account_label_compila_pela_habilidade_como_pelo_flowstore(db: Database) -> None:
    # 03/10: `flow:abrir-o-qa-messenger-e-navegar-ate-a-tel@1` casava pela RESOLVE e não compilava ("o comando não
    # dá valor a todos os parâmetros do plano"): as 4 execuções do abrir-tela foram a needs_input sem planejador.
    fluxo(db, "f-conta", MODELO, plano=_plano({"perfil": "{perfil}", "account_label": "{account_label}"}))
    comando = "curtir o post de @spacex"
    rp = Mundo(db, skills=True, flows=True).planejador.for_command(comando, None)
    casado = FlowStore(db).match(comando)
    assert rp is not None and rp.plan is not None and casado is not None
    assert rp.plan == casado[1]
    assert rp.plan.parameters == {"perfil": "@spacex", "account_label": "{account_label}"}


def test_o_aprendizado_do_fluxo_nunca_templatiza_os_reservados(db: Database) -> None:
    # O valor de `account_label` está no comando, mas o reservado não vira `{nome}` no modelo nem na chave; o
    # parâmetro de verdade (`perfil`) vira. Espelho do `_NAO_TEMPLATIZA` das receitas.
    plano = _plano({"perfil": "@nasa", "account_label": "tadeu.qa"})
    run = {"id": "r-x", "plan": json.dumps(plano), "flow_id": None, "skill_id": None,
           "command": "curtir o post de @nasa como tadeu.qa"}
    flow_id = FlowStore(db).learn_from_run(run)
    assert flow_id is not None
    linha = db.one("SELECT command_template, plan FROM flows WHERE id=?", (flow_id,))
    assert linha is not None and linha["command_template"] == "curtir o post de {perfil} como tadeu.qa"
    assert json.loads(linha["plan"])["parameters"] == {"perfil": "{perfil}", "account_label": "tadeu.qa"}
