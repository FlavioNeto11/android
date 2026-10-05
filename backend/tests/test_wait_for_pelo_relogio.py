"""Item 31.74: o `wait_for` conta a leitura da árvore no prazo, não só o sono.

Achado real (r-20261005071303-f24955, Chrome, android-09, 05/10): dois `wait_for("R$", 8)` duraram 15,6 s e 40,9 s com
`waited_s` 8,0. O laço somava 1 s por volta e não via as 8 leituras da árvore do Chrome (~1 s e ~4 s cada). Aqui o
relógio é falso e a leitura é lenta de propósito: a parede (o relógio falso do começo ao fim) não passa do pedido mais
uma leitura. Nível de prova: `simulated`.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.automation import tools
from app.automation.hierarchy import parse_hierarchy
from app.automation.tools import ToolContext, WaitFor, execute_tool

ARVORE = parse_hierarchy('<hierarchy rotation="0"><node index="0" text="carregando" resource-id="" '
                         'class="android.widget.TextView" package="com.android.chrome" content-desc="" '
                         'clickable="false" enabled="true" bounds="[0,0][720,100]" /></hierarchy>')
COM_PRECO = parse_hierarchy('<hierarchy rotation="0"><node index="0" text="R$ 479" resource-id="" '
                            'class="android.widget.TextView" package="com.android.chrome" content-desc="" '
                            'clickable="false" enabled="true" bounds="[0,0][720,100]" /></hierarchy>')


class Relogio:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _ctx(relogio: Relogio, leitura_s: float, arvores: list[Any]) -> tuple[ToolContext, list[float]]:
    leituras: list[float] = []

    async def observe() -> Any:
        relogio.t += leitura_s                                 # a árvore do Chrome é lenta
        leituras.append(relogio.t)
        return arvores.pop(0) if len(arvores) > 1 else arvores[0]

    async def dormir(s: float) -> None:
        relogio.t += s

    ctx = ToolContext(io=None, call=None, tree=ARVORE, width=720, height=1280, image_scale=1.0,  # type: ignore[arg-type]
                      app_package="com.android.chrome", app_activity=None, observe=observe, dormir=dormir)
    return ctx, leituras


@pytest.mark.parametrize("leitura_s", [1.0, 4.0], ids=["leitura_1s_como_o_15_6", "leitura_4s_como_o_40_9"])
async def test_a_parede_nao_passa_do_pedido_mais_uma_leitura(monkeypatch: pytest.MonkeyPatch, leitura_s: float) -> None:
    relogio = Relogio()
    monkeypatch.setattr(tools, "_relogio", relogio)
    ctx, leituras = _ctx(relogio, leitura_s, [ARVORE])
    saida = await execute_tool(ctx, "wait_for", WaitFor(rationale="r", text="R$", seconds=8))
    assert saida.result["found"] is False
    assert relogio.t <= 8 + leitura_s                          # antes: 8 + 8 × leitura_s (16 s e 40 s)
    assert saida.result["waited_s"] == pytest.approx(relogio.t)  # o que se relata é a parede, não só o sono
    assert leituras[-1] == relogio.t                           # termina numa leitura, depois do último sono


async def test_achou_relata_o_tempo_real(monkeypatch: pytest.MonkeyPatch) -> None:
    relogio = Relogio()
    monkeypatch.setattr(tools, "_relogio", relogio)
    ctx, _ = _ctx(relogio, 2.0, [ARVORE, COM_PRECO])
    saida = await execute_tool(ctx, "wait_for", WaitFor(rationale="r", text="R$", seconds=8))
    assert saida.result == {"found": True, "waited_s": 5.0}    # leitura 2 s, sono 1 s, leitura 2 s


async def test_leitura_rapida_ainda_espera_o_pedido(monkeypatch: pytest.MonkeyPatch) -> None:
    """Com a árvore instantânea, o comportamento é o de antes: o tempo pedido, em sonos de 1 s."""
    relogio = Relogio()
    monkeypatch.setattr(tools, "_relogio", relogio)
    ctx, leituras = _ctx(relogio, 0.0, [ARVORE])
    saida = await execute_tool(ctx, "wait_for", WaitFor(rationale="r", text="R$", seconds=3))
    assert saida.result == {"found": False, "waited_s": 3.0} and len(leituras) == 4
