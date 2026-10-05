"""31.64, S1 da revisão do #350 (migração 110): a porta de política marca a etapa cujo efeito ela liberou
(`steps.passou_a_porta`), na mesma passada sem `await` da regra do objeto na família; a que para (aprovação, recusa) não
marca. A contagem da família com a marca está em `test_familia_por_objeto.py`.

Nível de prova: `simulated` (harness, catálogo do Instagram, banco de teste). Nada real.
"""
from __future__ import annotations

from typing import Any

from .test_executor_honra_o_plano import _aprovado_no_plano
from .test_porta_do_plano import DM, _gate, _plano, _sem_iniciar


def _marca(state: Any, chave: str) -> int:
    return int(state.db.scalar("SELECT passou_a_porta FROM steps WHERE id=?", (f"run-p:android-01:v1:{chave}",)))


async def test_a_porta_que_libera_o_efeito_marca_a_etapa(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _aprovado_no_plano(state, DM)
    assert _marca(state, "dm") == 0
    assert await _gate(state, "dm") is None
    assert _marca(state, "dm") == 1


async def test_a_porta_que_pede_aprovacao_nao_marca(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM}])
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed
    assert _marca(state, "dm") == 0
