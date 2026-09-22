"""Item 3.4 — achado #65: `health.updated` passa a ser emitido quando `health()` muda, em vez de nunca."""
from __future__ import annotations

from typing import Any

from .conftest import Harness


async def test_check_health_emite_apenas_quando_o_resultado_muda(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    vistos: list[dict[str, Any]] = []
    original_emit = state.bus.emit

    def espiao(kind: str, message: str, **kw: Any) -> Any:
        if kind == "health.updated":
            vistos.append(kw.get("data") or {})
        return original_emit(kind, message, **kw)

    state.bus.emit = espiao  # type: ignore[method-assign]
    state.appium.is_up = lambda timeout=1.0: True  # type: ignore[assignment]  # ponto de partida estável

    state._check_health()
    assert len(vistos) == 1
    assert vistos[0]["health"]["status"] in ("ok", "degraded", "error")

    # Nada mudou: a segunda checagem não deve emitir de novo.
    state._check_health()
    assert len(vistos) == 1

    # Mudança real (Appium fica indisponível): agora sim, novo evento.
    state.appium.is_up = lambda timeout=1.0: False  # type: ignore[assignment]
    state.appium.detail = "simulado: fora do ar para o teste"
    state._check_health()
    assert len(vistos) == 2
    assert vistos[1]["health"]["status"] != "ok"
