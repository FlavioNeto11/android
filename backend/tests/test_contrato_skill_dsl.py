"""O contrato da DSL `automation/v1alpha1` congelado (design §12.2, fase E).

O esquema JSON é o que o painel valida e o que o prompt de ensino entrega ao LLM. Uma mudança nele — um campo novo,
um padrão afrouxado, um limite trocado — passaria em toda a suíte, que usa o MESMO commit dos dois lados, e só
apareceria como documento recusado (ou aceito sem querer) do outro lado. Mudou de propósito? Regere com
`ATUALIZAR_CONTRATOS=1` e diga no commit o que mudou e por quê.
"""
from __future__ import annotations

import json
import os
import typing
from pathlib import Path

from app.contracts.skills import v1alpha1 as dsl
from app.models import DeliveryLevel, PlanStep, Postcondition
from app.modules.capabilities.domain.strategy import StrategyKind

SNAPSHOT = Path(__file__).parent / "contratos" / "skill-dsl.v1alpha1.json"


def _esquema() -> str:
    return json.dumps(dsl.SkillDocument.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_esquema_da_dsl_nao_muda_sem_querer() -> None:
    atual = _esquema()
    if os.environ.get("ATUALIZAR_CONTRATOS") == "1":
        SNAPSHOT.write_text(atual, encoding="utf-8", newline="\n")
    assert SNAPSHOT.read_text(encoding="utf-8") == atual, (
        "o esquema de automation/v1alpha1 mudou. Se foi de propósito: ATUALIZAR_CONTRATOS=1 pytest "
        "tests/test_contrato_skill_dsl.py, e o motivo vai no commit")


def test_todo_objeto_do_esquema_recusa_campo_desconhecido_e_nao_ha_prova_local() -> None:
    esquema = dsl.SkillDocument.model_json_schema()
    objetos = [esquema, *esquema["$defs"].values()]
    abertos = [o.get("title") for o in objetos if o.get("type") == "object" and o.get("additionalProperties") is not False]
    assert not abertos, f"objeto do contrato aceitando campo extra: {abertos}"
    assert "local_proof" not in json.dumps(esquema), "prova local é só do catálogo (E_VERIFICATION_WEAKENED)"


def test_vocabularios_repetidos_no_contrato_batem_com_os_donos() -> None:
    """O contrato não importa ninguém (D1); os vocabulários são cópias, e a cópia não pode divergir."""
    assert set(typing.get_args(dsl.StrategyName)) == {k.value for k in StrategyKind}
    assert set(typing.get_args(dsl.DeliveryLevelName)) == {d.value for d in DeliveryLevel}
    tipos_de_pos = set(typing.get_args(Postcondition.model_fields["kind"].annotation))
    assert set(typing.get_args(dsl.GoalPostconditionKind)) == tipos_de_pos - {"items_collected"}
    assert set(typing.get_args(dsl.ResourceKind)) == {"device.state", "app.installation", "account.binding",
                                                       "app.session"}
    # o id do nó VIRA a chave da etapa: o padrão tem de ser o mesmo
    padrao_da_chave = next(m.pattern for m in PlanStep.model_fields["key"].metadata if hasattr(m, "pattern"))
    assert dsl.NODE_ID == padrao_da_chave
