"""31.66: ao fechar o objetivo, o navegador sai do primeiro plano (HOME).

O fim de uma execução de navegador deixava o Chrome na frente redesenhando a página, e o convidado ficava com carga de
3 a 10 em 2 vCPU (android-09, 05/10). A medição real de antes e depois é do central; aqui, o contrato com o aparelho
falso do harness.

Nível de prova: `simulated` (harness, aparelho falso). Nada real.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.automation.driver import DriverError
from app.automation.hierarchy import parse_hierarchy
from app.models import ControlOwner, ObjectiveStatus
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



# --------------------------------------------------------------- leitura do #368: H1, H2 e H3 (testes que discriminam)
@pytest.mark.parametrize("parada", [ObjectiveStatus.waiting_user, ObjectiveStatus.uncertain])
async def test_objetivo_que_nao_terminou_deixa_a_tela_para_a_pessoa(harness: Harness, monkeypatch: Any,
                                                                   parada: ObjectiveStatus) -> None:
    """O app de teste faz as vezes do navegador (o HOME sairia); o objetivo para em `parada`, e a tela fica."""
    monkeypatch.setattr(modulo_scheduler, "NAVEGADORES", frozenset({PKG}))

    def parar(self: Any, objective_id: str) -> None:
        self.repo.set_objective(objective_id, parada, detail="parado para a pessoa")

    monkeypatch.setattr(modulo_scheduler.Scheduler, "_maybe_complete", parar)
    run = harness.run(["android-01"])
    st = harness.state
    assert st is not None
    for _ in range(400):                                     # o objetivo para; a cauda do `_work` roda em seguida
        if _status_do_objetivo(st, run.id) == parada.value:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)
    assert _status_do_objetivo(st, run.id) == parada.value
    assert harness.fakes[st.devices.get("android-01").id].current_focus()[0] == PKG


@pytest.mark.parametrize("de_quem", ["pedido", "usuario"])
async def test_controle_manual_pedido_ou_tomado_nao_recebe_home(harness: Harness, de_quem: str) -> None:
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    falso = _no_navegador(harness, rt)
    if de_quem == "pedido":
        rt.takeover_requested = True
    else:
        rt.control = ControlOwner.user
    assert await st.devices.tirar_da_frente(rt, {PKG}) is None
    assert falso.current_focus()[0] == PKG


async def test_tela_de_verificacao_na_frente_nao_recebe_home(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    falso = _no_navegador(harness, rt)
    rt.last_tree = parse_hierarchy(
        '<hierarchy rotation="0"><node index="0" text="Confirme que você é uma pessoa" resource-id="" '
        'class="android.widget.TextView" package="p" content-desc="" bounds="[0,0][720,100]" /></hierarchy>')
    assert await st.devices.tirar_da_frente(rt, {PKG}) is None
    assert falso.current_focus()[0] == PKG


async def test_falha_do_adb_no_foco_nao_muda_o_desfecho(harness: Harness, monkeypatch: Any) -> None:
    monkeypatch.setattr(modulo_scheduler, "NAVEGADORES", frozenset({PKG}))
    run = harness.run(["android-01"])
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    falso = harness.fakes.get(rt.id)
    assert falso is not None
    original = falso.current_focus
    quebrou: list[bool] = []

    def foco_que_cai_no_fim() -> tuple[str | None, str | None]:
        if _status_do_objetivo(st, run.id) == "succeeded":       # só a leitura do fim, a do HOME
            quebrou.append(True)
            raise DriverError("adb caiu")
        return original()

    monkeypatch.setattr(falso, "current_focus", foco_que_cai_no_fim)
    await harness.wait_run(run.id)
    assert quebrou, "a leitura do foco no fim não aconteceu"
    assert st.repo.run_row(run.id)["status"] == "completed"
    assert st.repo.objective_row(f"{run.id}:android-01")["status"] == "succeeded"


def _status_do_objetivo(st: Any, run_id: str) -> str | None:
    return st.db.scalar("SELECT status FROM objectives WHERE id=?", (f"{run_id}:android-01",))


def _no_navegador(harness: Harness, rt: Any) -> Any:
    falso = harness.fakes.get(rt.id)
    assert falso is not None
    falso.screen, falso.foreground = "home", PKG
    return falso
