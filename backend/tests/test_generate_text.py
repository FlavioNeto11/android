"""`generate_text` (texto curto livre; ponte android <-> igfarm): o provedor simulado e o roteador (papel `persona`).
Nivel de prova: `simulated`."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.planning.provider import Usage
from app.planning.routing import RoutingProvider
from app.planning.simulated_provider import SimulatedProvider

from .test_hub_de_ia import FakeProvider, com_hub
from .test_papel_persona import PROVEDORES, _instala

pytestmark = pytest.mark.asyncio


async def test_simulado_e_deterministico_sem_custo_e_no_formato_de_um_arroba() -> None:
    p = SimulatedProvider()
    a, usage = await p.generate_text("sys", "Pessoa: Ana\nProfissao: ceramista\nGostos: trilhas, cafe")
    b, _ = await p.generate_text("sys", "Pessoa: Ana\nProfissao: ceramista\nGostos: trilhas, cafe")
    c, _ = await p.generate_text("sys", "Pessoa: Ana\nProfissao: ceramista\nGostos: trilhas, cafe\nNao sirvam: " + a)
    assert a == b and a != c and usage.calls == 0
    assert re.fullmatch(r"[a-z0-9._]{3,30}", a) and not a.endswith(".") and ".." not in a


async def test_vazio_ainda_da_um_texto_valido() -> None:
    texto, _ = await SimulatedProvider().generate_text("", "")
    assert len(texto) >= 3


class TextoFake(FakeProvider):
    async def generate_text(self, system: str, prompt: str, *, max_tokens: int = 64) -> Any:
        self.calls.append("texto")
        return "ana.ceramica", Usage(calls=1, role="persona", model=self.model, provider=self.name)


async def test_roteador_usa_o_papel_persona_e_a_social_nao_e_tocada(tmp_path: Path) -> None:
    r = RoutingProvider(com_hub(tmp_path, providers=PROVEDORES, roles={}))
    unico = TextoFake("anthropic", "padrao")
    _instala(r, "persona", unico)
    texto, usage = await r.generate_text("sys", "pedido", max_tokens=200)
    assert texto == "ana.ceramica" and usage.role == "persona" and unico.calls == ["texto"]


async def test_openai_compat_devolve_o_texto_cortado_e_o_papel_persona(tmp_path: Path) -> None:
    from .test_openai_provider import _resposta, provider as provedor_openai

    p, _ = provedor_openai(tmp_path, [_resposta("  ana.ceramica\n")])
    texto, usage = await p.generate_text("sys", "pedido", max_tokens=200)
    assert texto == "ana.ceramica" and usage.role == "persona"


async def test_anthropic_devolve_o_texto_e_recusa_truncado(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from app.planning.provider import AIError

    from .test_anthropic_provider import _resp, provider as provedor_anthropic

    p, _ = provedor_anthropic(tmp_path, [_resp([SimpleNamespace(type="text", text=" ana.ceramica ")])])
    texto, usage = await p.generate_text("sys", "pedido", max_tokens=200)
    assert texto == "ana.ceramica" and usage.role == "persona"
    p2, _ = provedor_anthropic(tmp_path, [_resp([SimpleNamespace(type="text", text="ana")], stop="max_tokens")])
    with pytest.raises(AIError):
        await p2.generate_text("sys", "pedido", max_tokens=5)
