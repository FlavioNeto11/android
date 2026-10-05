"""31.66: ao fechar o objetivo, o navegador sai do primeiro plano (HOME).

O fim de uma execução de navegador deixava o Chrome na frente redesenhando a página, e o convidado ficava com carga de
3 a 10 em 2 vCPU (android-09, 05/10). A medição real de antes e depois é do central; aqui, o contrato com o aparelho
falso do harness.

Nível de prova: `simulated` (harness, aparelho falso). Nada real.
"""
from __future__ import annotations

from typing import Any

from app.taskqueue import scheduler as modulo_scheduler

from .conftest import Harness
from .fake_device import LAUNCHER, PKG


async def test_o_navegador_na_frente_volta_a_tela_inicial(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    falso = harness.fakes.get(rt.id)
    assert falso is not None
    falso.screen, falso.foreground = "home", PKG
    assert await st.devices.tirar_da_frente(rt, {PKG}) == PKG
    assert falso.current_focus()[0] == LAUNCHER


async def test_outro_app_na_frente_fica_como_esta(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    falso = harness.fakes.get(rt.id)
    assert falso is not None
    falso.screen, falso.foreground = "home", PKG
    assert await st.devices.tirar_da_frente(rt, {"com.android.chrome"}) is None
    assert falso.current_focus()[0] == PKG


async def _rodar_ate_o_fim(harness: Harness) -> Any:
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    st = harness.state
    assert st is not None
    return harness.fakes[st.devices.get("android-01").id]


async def test_o_fim_do_objetivo_tira_o_navegador_da_frente(harness: Harness, monkeypatch: Any) -> None:
    """O app de teste faz as vezes do navegador: no fim do objetivo terminal, a tela inicial."""
    monkeypatch.setattr(modulo_scheduler, "NAVEGADORES", frozenset({PKG}))
    falso = await _rodar_ate_o_fim(harness)
    assert falso.current_focus()[0] == LAUNCHER


async def test_fora_do_navegador_o_fim_do_objetivo_nao_mexe_na_tela(harness: Harness) -> None:
    falso = await _rodar_ate_o_fim(harness)
    assert falso.current_focus()[0] == PKG
