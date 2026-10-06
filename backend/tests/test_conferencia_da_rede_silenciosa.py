"""29.163: a conferência periódica da rede (varredura de 60 s, a cada `rede.deriva_s`) não publica "IA assumiu/liberou o aparelho".

Em 06/10 o android-02, 03, 05 e 06 (os de política de rede exigida) mostravam "IA assumiu/liberou" a cada ~16 min, de 3 a 20 s, sem passo nem execução.
`simulated`: o `ai_begin`/`ai_end` do manager e a varredura de `vitrine.convergir_ligados`, com doubles.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app import vitrine
from app.devices.manager import ControlOwner, DeviceManager
from app.models import InstanceState


def _gerente() -> tuple[Any, list[str]]:
    eventos: list[str] = []
    return SimpleNamespace(_control_event=lambda rt, msg, **_k: eventos.append(msg), touch=lambda rt: None), eventos


def _rt() -> Any:
    return SimpleNamespace(id="android-05", control=ControlOwner.none, control_since=None, takeover_requested=False,
                           pending_lease_id=None, pending_dono=None, controle_silencioso=False, clock_state="ok",
                           executor=SimpleNamespace(has_zombie=False))


def test_trabalho_comum_segue_anunciando_assumiu_e_liberou() -> None:
    g, eventos = _gerente()
    rt = _rt()
    assert DeviceManager.ai_begin(g, rt) is True
    assert rt.control == ControlOwner.ai
    DeviceManager.ai_end(g, rt)
    assert rt.control == ControlOwner.none
    assert eventos == ["IA assumiu o aparelho", "IA liberou o aparelho"]


def test_trabalho_silencioso_segura_o_aparelho_sem_anunciar() -> None:
    g, eventos = _gerente()
    rt = _rt()
    assert DeviceManager.ai_begin(g, rt, silencioso=True) is True
    assert rt.control == ControlOwner.ai and rt.control_since is not None     # segura o aparelho como qualquer outro
    assert DeviceManager.ai_begin(g, rt) is False                              # e ninguém passa na frente
    DeviceManager.ai_end(g, rt)
    assert rt.control == ControlOwner.none and rt.controle_silencioso is False
    assert eventos == []


def test_o_silencio_nao_vaza_para_o_trabalho_seguinte() -> None:
    g, eventos = _gerente()
    rt = _rt()
    DeviceManager.ai_begin(g, rt, silencioso=True)
    DeviceManager.ai_end(g, rt)
    DeviceManager.ai_begin(g, rt)
    DeviceManager.ai_end(g, rt)
    assert eventos == ["IA assumiu o aparelho", "IA liberou o aparelho"]


def _estado(chamadas: list[dict[str, Any]]) -> Any:
    rt = SimpleNamespace(id="android-05", store=False, state=InstanceState.online)

    def run_device_job(_rt: Any, factory: Any, **kw: Any) -> bool:
        chamadas.append({"factory": factory, **kw})
        return True

    return SimpleNamespace(repo=SimpleNamespace(dispatchable_objectives=lambda: []),
                           devices=SimpleNamespace(devices={rt.id: rt}),
                           scheduler=SimpleNamespace(workers={}, run_device_job=run_device_job),
                           adotar_promovidas=lambda _rt: None)


async def test_varredura_so_de_conferencia_roda_rotulada_e_silenciosa_com_log_da_duracao(monkeypatch: pytest.MonkeyPatch,
                                                                                         caplog: pytest.LogCaptureFixture) -> None:
    async def conferir() -> None:
        return None

    monkeypatch.setattr(vitrine, "_montar_trabalho", lambda *_a: (conferir, True))
    chamadas: list[dict[str, Any]] = []
    assert vitrine.convergir_ligados(_estado(chamadas)) == ["android-05"]
    assert chamadas[0]["label"] == "conferência da rede" and chamadas[0]["silencioso"] is True
    with caplog.at_level(logging.INFO, logger=vitrine.log.name):
        await chamadas[0]["factory"]()
    assert any("android-05: conferência da rede em" in r.getMessage() and r.getMessage().endswith(" s") for r in caplog.records)


def test_varredura_com_entrega_ou_passo_de_rede_continua_como_antes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def entregar() -> None:
        return None

    monkeypatch.setattr(vitrine, "_montar_trabalho", lambda *_a: (entregar, False))
    chamadas: list[dict[str, Any]] = []
    assert vitrine.convergir_ligados(_estado(chamadas)) == ["android-05"]
    assert chamadas[0]["label"] == "entrega do que foi distribuído" and "silencioso" not in chamadas[0]
    assert chamadas[0]["factory"] is entregar


def test_sem_trabalho_a_varredura_nao_dispara_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vitrine, "_montar_trabalho", lambda *_a: (None, False))
    chamadas: list[dict[str, Any]] = []
    assert vitrine.convergir_ligados(_estado(chamadas)) == []
    assert chamadas == []
