"""T.2 (achado #164/#165, o que faltava): as sondas do `_wait_boot` — `boot_completed`, `ui_ready` e
`prepare_for_automation` — com o aparelho FALSO, sem backend fake novo.

Os testes de prazo (`test_ciclo_de_vida_do_emulador.py`) estouram antes de qualquer sonda, e os de prontidão
(`test_prontidao*.py`) fixam as duas primeiras sondas em `True`. Aqui elas variam: a ordem em que são chamadas, o que
o laço faz enquanto ainda não estão prontas, o erro delas, o "120 s depois do boot" sem interface e o preparo que falha.
Só o que é de fora do processo vira dublê (as três chamadas ao `Adb`, a identidade do processo do emulador); o laço, a
fase de prontidão e o estado do aparelho são os de produção. `simulated`.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from app.automation.driver import DriverError
from app.devices import manager as manager_mod
from app.devices.adb import AdbError
from app.devices.apps_de_fundo import AjusteDosApps
from app.models import InstanceState

from .conftest import Harness


class _RelogioInjetavel:
    """Substitui o módulo `time` SÓ dentro de `manager`: `monotonic` anda por um número que o teste controla."""

    def __init__(self) -> None:
        self.agora = 1000.0

    def monotonic(self) -> float:
        return self.agora

    def __getattr__(self, nome: str) -> Any:
        return getattr(time, nome)


def _preparar(harness: Harness, monkeypatch: pytest.MonkeyPatch, *, boot: Any, ui: Any, preparo: Any = None) -> tuple[Any, list[str]]:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    chamadas: list[str] = []

    def sonda(nome: str, resposta: Any) -> Any:
        def chamar(*_a: Any, **_k: Any) -> Any:
            chamadas.append(nome)
            return resposta() if callable(resposta) else resposta
        return chamar

    # Um laço quebrado por regressão falha em 5 s com `error`, em vez de ficar nos 300 s do prazo de produção.
    harness.cfg.file.instances.overrides.setdefault("android-01", {}).setdefault("boot_timeout_s", 5)
    s.devices._set_state(rt, InstanceState.booting, "emulador iniciado")
    rt.pid = 4242
    monkeypatch.setattr(manager_mod.emu, "is_our_emulator", lambda *_a, **_k: True)
    monkeypatch.setattr(manager_mod, "RESPOSTA_POS_BOOT_S", 0.05)
    monkeypatch.setattr(manager_mod, "RESPOSTA_MIN_S", 0.05)
    monkeypatch.setattr(rt.adb, "boot_completed", sonda("boot_completed", boot))
    monkeypatch.setattr(rt.adb, "ui_ready", sonda("ui_ready", ui))
    monkeypatch.setattr(rt.adb, "prepare_for_automation", sonda("prepare", preparo))
    monkeypatch.setattr(s.devices, "_start_online_tasks", lambda _rt: None)
    s.cfg.file.limits.boot_poll_s = 0.01
    return rt, chamadas


def _sequencia(*valores: Any) -> Any:
    """Cada chamada devolve o próximo valor; o último se repete. Uma exceção na lista é levantada."""
    fila = list(valores)

    def proximo() -> Any:
        v = fila.pop(0) if len(fila) > 1 else fila[0]
        if isinstance(v, Exception):
            raise v
        return v
    return proximo


async def test_a_interface_so_e_sondada_depois_de_o_android_iniciar(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """`boot_completed` falso três vezes: `ui_ready` não é chamado nenhuma vez antes dele. Depois, `ui_ready` falso duas
    vezes: o laço insiste nele (não volta a `boot_completed`) e só então segue para o preparo e entra no ar."""
    rt, chamadas = _preparar(harness, monkeypatch, boot=_sequencia(False, False, False, True),
                             ui=_sequencia(False, False, True))
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, time.monotonic(), warm=False)       # noqa: SLF001

    assert ok is True and rt.state == InstanceState.online
    assert chamadas == ["boot_completed"] * 4 + ["ui_ready"] * 3 + ["prepare"]
    assert rt.readiness_phase == "ready"


async def test_erro_do_adb_numa_sonda_nao_derruba_o_boot_so_adia(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """`adb` ainda não respondendo no começo do boot é o normal: `AdbError` e `DriverError` nas sondas são engolidos e a
    sonda é repetida — não viram `error` nem encerram o laço."""
    rt, chamadas = _preparar(harness, monkeypatch,
                             boot=_sequencia(AdbError("error: device offline"), DriverError("sem sessão"), True),
                             ui=_sequencia(AdbError("error: closed"), True))
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, time.monotonic(), warm=False)       # noqa: SLF001

    assert ok is True and rt.state == InstanceState.online
    assert chamadas == ["boot_completed"] * 3 + ["ui_ready"] * 2 + ["prepare"]


async def test_sem_interface_120s_depois_do_boot_o_laco_segue_adiante(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """`ui_ready` nunca fica verdadeiro: 120 s depois de `boot_completed` o laço desiste de esperar a interface e passa
    ao preparo e à escada (que é quem decide se o Android está de fato responsivo). Relógio injetável, sem dormir 120 s."""
    relogio = _RelogioInjetavel()
    monkeypatch.setattr(manager_mod, "time", relogio)
    harness.cfg.file.instances.overrides["android-01"] = {"boot_timeout_s": 400}

    def ui_sempre_falso() -> bool:
        relogio.agora += 50.0              # cada sonda lenta custa 50 s de relógio
        return False
    rt, chamadas = _preparar(harness, monkeypatch, boot=True, ui=ui_sempre_falso)
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, relogio.monotonic(), warm=False)    # noqa: SLF001

    assert chamadas.count("ui_ready") == 3, "50 s, 100 s e 150 s: passou de 120 s na terceira"
    assert chamadas[-1] == "prepare" and ok is True and rt.state == InstanceState.online


async def test_boot_completed_que_nunca_vem_estoura_o_prazo_com_a_ultima_fase(harness: Harness,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """`boot_completed` sempre falso: o laço roda até o prazo, nunca chama `ui_ready` nem o preparo, e o aparelho vai a
    `error` dizendo que o boot excedeu o prazo."""
    relogio = _RelogioInjetavel()
    monkeypatch.setattr(manager_mod, "time", relogio)
    harness.cfg.file.instances.overrides["android-01"] = {"boot_timeout_s": 100}

    def boot_falso() -> bool:
        relogio.agora += 40.0
        return False
    rt, chamadas = _preparar(harness, monkeypatch, boot=boot_falso, ui=True)
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, relogio.monotonic(), warm=False)    # noqa: SLF001

    assert ok is False and rt.state == InstanceState.error and "Boot excedeu 100s" in (rt.state_detail or "")
    assert set(chamadas) == {"boot_completed"}


@pytest.mark.parametrize("erro", [AdbError("error: device offline"), DriverError("sessão perdida")])
async def test_preparo_que_falha_rapido_nao_impede_a_entrada_no_ar(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                                  erro: Exception) -> None:
    """`prepare_for_automation` com erro conhecido e rápido: vai ao log e a escada de prontidão, que decide, roda
    depois dele. Aparelho saudável entra no ar; o preparo foi tentado uma só vez, depois de `ui_ready`."""
    def falha() -> None:
        raise erro
    rt, chamadas = _preparar(harness, monkeypatch, boot=True, ui=True, preparo=falha)
    s = harness.state
    assert s is not None
    ok = await s.devices._wait_boot(rt, time.monotonic(), warm=False)       # noqa: SLF001

    assert ok is True and rt.state == InstanceState.online
    assert chamadas == ["boot_completed", "ui_ready", "prepare"]


async def test_preparo_que_mudou_apps_de_fundo_vira_evento_do_aparelho(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """O retorno do preparo (`AjusteDosApps`) é registrado: mudança vira uma linha de log do aparelho; passagem sem
    mudança não publica nada."""
    s = harness.state
    assert s is not None
    publicados: list[str] = []
    monkeypatch.setattr(s.devices, "publish", lambda _rt, msg=None, *_a, **_k: publicados.append(msg or ""))

    rt, _ = _preparar(harness, monkeypatch, boot=True, ui=True,
                      preparo=lambda: AjusteDosApps(desativados=("com.exemplo.pesado",)))
    assert await s.devices._wait_boot(rt, time.monotonic(), warm=False) is True   # noqa: SLF001
    assert any("com.exemplo.pesado" in m for m in publicados), publicados

    publicados.clear()
    rt, _ = _preparar(harness, monkeypatch, boot=True, ui=True, preparo=lambda: AjusteDosApps())
    assert await s.devices._wait_boot(rt, time.monotonic(), warm=False) is True   # noqa: SLF001
    assert not any("exemplo" in m for m in publicados)
