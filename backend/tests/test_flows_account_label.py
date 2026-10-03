"""LT-3 (caminho rápido 1): `FlowStore.match` não exige valor para parâmetro RESERVED.

`account_label` (e `instance_id`/`run_id`) nunca é capturado do comando por `_extract`: o valor é do APARELHO e entra
na materialização por aparelho. O molde `{account_label}` ficava no plano congelado e `match` o tratava como "faltou
valor", recusando todo fluxo que o carregava — a execução repetida pagava o planejador de novo. Parâmetro NÃO reservado
sem valor continua recusando.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.taskqueue.flows import FlowStore
from app.taskqueue.repository import resolve_templates

from .fake_skills import banco, fluxo

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
    assert resolve_templates(plano.parameters["account_label"], {"account_label": "@lucas.real"}) == "@lucas.real"


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
