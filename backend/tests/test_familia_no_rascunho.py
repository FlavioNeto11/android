"""31.53, o caminho do texto GERADO: o rascunho escrito na porta (`_policy_gate`, depois do `_draft_gate`) que cita outra
conta do mesmo pedido entre personas faz a etapa pedir aprovação, com o motivo UMA vez só no cartão e sem o @.

O texto literal já é pego pelo `check` (a prévia vê o mesmo; `test_familia_por_objeto.py`). Aqui o plano tem só o
briefing; o texto nasce no rascunho (um falso), e a família vem do contexto do pedido (um falso, como o laço montaria).

Nível de prova: `simulated` (harness, catálogo do Instagram, banco de teste). Nada real.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app import state as state_mod
from app.models import ProfileCreate
from app.social.policy import MOTIVO_CITA_A_FAMILIA, ContextoDoPedido

from .test_capabilities import SENHA
from .test_porta_do_plano import DM, _gate, _plano, _sem_iniciar


def _irma(state: Any, *, conta_noutro_app: str | None = None) -> str:
    """Outra persona do mesmo pedido, sem aparelho; com uma conta noutro app quando pedido."""
    pid = state.social.create_profile(ProfileCreate(username="bia.souza91182", password=SENHA)).id
    if conta_noutro_app:
        state.db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at)"
                         " VALUES ('pa-irma', ?, 'outlook', ?, '2026-10-04T10:00:00Z', '2026-10-04T10:00:00Z')",
                         (pid, conta_noutro_app))
    return pid


def _com_familia(monkeypatch: Any, state: Any, irma: str) -> None:
    def contexto(_db: Any, _run_id: str) -> ContextoDoPedido:
        dono = state.db.scalar("SELECT profile_id FROM objectives")
        return ContextoDoPedido(raiz="ped-1", familia=frozenset({dono, irma}))

    monkeypatch.setattr(state_mod, "contexto_do_pedido", contexto)


def _rascunho(monkeypatch: Any, state: Any, texto: str) -> None:
    async def draft_response(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(content=texto, refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.social, "draft_response", draft_response)


async def test_rascunho_que_cita_outra_conta_do_pedido_pede_aprovacao_com_o_motivo_uma_vez(harness: Any,
                                                                                          monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": {"username": DM["username"],
                                                                     "content": "cumprimente a pessoa"}}])
    _com_familia(monkeypatch, state, _irma(state))
    escrito = "Oi! A @bia.souza91182 mandou um abraço"
    _rascunho(monkeypatch, state, escrito)
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed
    assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE key='dm'"))["content"] == escrito
    pedido = state.db.one("SELECT summary, generated_content FROM pending_approvals WHERE status='pending'")
    assert pedido is not None and pedido["generated_content"] == escrito
    for onde in (pedido["summary"] or "", veredito.reason or ""):
        assert onde.count(MOTIVO_CITA_A_FAMILIA) == 1, onde
        assert "bia.souza91182" not in onde.replace(escrito, "")


async def test_conta_da_irma_noutro_app_tambem_conta_e_sem_citar_nao_ha_motivo(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": {"username": DM["username"],
                                                                     "content": "cumprimente a pessoa"}}])
    _com_familia(monkeypatch, state, _irma(state, conta_noutro_app="bia.contato"))
    _rascunho(monkeypatch, state, "Fala com a bia.contato depois")
    veredito = await _gate(state, "dm")
    assert veredito is not None and MOTIVO_CITA_A_FAMILIA in (veredito.reason or "")


async def test_rascunho_sem_citar_a_familia_nao_ganha_o_motivo(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": {"username": DM["username"],
                                                                     "content": "cumprimente a pessoa"}}])
    _com_familia(monkeypatch, state, _irma(state))
    _rascunho(monkeypatch, state, "Oi! Tudo bem por aí?")
    veredito = await _gate(state, "dm")
    assert MOTIVO_CITA_A_FAMILIA not in ((veredito.reason if veredito else "") or "")
