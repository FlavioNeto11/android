"""Item 28.23, lado do laço — a execução nascida de um pedido leva o teto de autonomia dele.

Prova `simulated` (`arquivo::teste`), pura: `LacoDePedidos._requisicao` com uma linha de pedido escrita à mão. O campo
`RunCreate.teto_de_autonomia` é do lado da execução (migração 100, outra frente). Por isso os testes trocam o `RunCreate`
do módulo por um modelo COM o campo e por outro SEM ele: o laço passa o teto quando o campo existe e não quebra quando
falta, qualquer que seja a ordem em que os dois lados entram.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import ConfigDict, create_model

from app.models import RunCreate
from app.modules.pedidos.infrastructure import laco as modulo_laco
from app.modules.pedidos.infrastructure.laco import LacoDePedidos

ALVOS = json.dumps({"alvos": [{"instance_id": "android-01", "profile_id": None, "app_id": None}], "device_policy": "one"})


class ComTeto(RunCreate):
    teto_de_autonomia: Literal["observar", "preparar", "agir"] | None = None


# O `RunCreate` de antes do lado da execução: `extra="forbid"` recusaria um campo que não existe. Subclasse não serve
# desde que a migração 100 entrou (herdaria o campo): o modelo é montado com os campos de hoje, menos o teto.
SemTeto = create_model(
    "SemTeto", __config__=ConfigDict(extra="forbid"),
    **{n: (f.annotation, f) for n, f in RunCreate.model_fields.items() if n != "teto_de_autonomia"})  # type: ignore[call-overload]


def _requisicao(autonomia: str, papel: str | None):
    p = {"id": "ped-teste", "pai_id": None, "objetivo": "Abrir a lista e anotar o primeiro preço", "alvos": ALVOS,
         "autonomia": autonomia, "papel": papel}
    # 28.10 F5: `_autonomia` lê a família (porta-voz) pela instância; sem porta-voz, a autonomia efetiva é a de antes.
    laco = SimpleNamespace(_familia_com_porta_voz=lambda *_a: False)
    laco._autonomia = lambda linha: LacoDePedidos._autonomia(laco, linha)  # type: ignore[arg-type]
    return LacoDePedidos._requisicao(laco, p, "ped:x:chave-de-teste")  # type: ignore[arg-type]


@pytest.mark.parametrize(("autonomia", "papel", "teto"), [
    ("agir", None, "agir"), ("preparar", None, "preparar"), ("observar", None, "observar"),
    ("agir", "pesquisador", "observar"), ("agir", "redator", "preparar"), ("agir", "porta_voz", "agir"),
])
def test_com_o_campo_a_execucao_leva_o_teto_efetivo(monkeypatch, autonomia, papel, teto) -> None:
    monkeypatch.setattr(modulo_laco, "RunCreate", ComTeto)
    req = _requisicao(autonomia, papel)
    assert isinstance(req, ComTeto) and req.teto_de_autonomia == teto
    assert req.instance_ids == ["android-01"] and req.mode == "execute"


def test_sem_o_campo_nada_vai_e_nada_quebra(monkeypatch) -> None:
    monkeypatch.setattr(modulo_laco, "RunCreate", SemTeto)
    req = _requisicao("agir", "pesquisador")
    assert isinstance(req, SemTeto) and "teto_de_autonomia" not in req.model_dump()
