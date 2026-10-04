"""31.49: a execução honra o sim dado na prévia do plano (30.61) e só pergunta pelo que só existe lá.

A trava mínima do #319 já descarta o sim do plano vencido, gasto ou com a chave da etapa relida diferente. Este item
fecha o resto do contrato com a Aprendizado (`.claude/handoffs/chave-da-aprovacao-30-61.md`):
- aprovação sem texto nunca libera escrita (a conferência explícita, depois da chave);
- o `_draft_gate` não trata o sim do plano como texto já mostrado: briefing com sim do plano escreve o texto, e o sim
  (que não cobre briefing) sai de cena;
- o pedido novo da execução diz por que o sim do plano não valeu.
O caso do texto com chave solta (`:-{`) aprovado no plano e não perguntado de novo está em
`test_porta_do_plano.py::test_chave_solta_aprovada_no_plano_e_honrada_pela_porta_na_execucao`; aqui ele se repete com
a conferência do texto ligada.

Nível de prova: `simulated` (harness, catálogo do Instagram, banco de teste; o rascunho é um falso). Nada real.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.porta_do_plano import AprovarPlanoBody, ItemAprovado, aprovar_plano, previa_da_porta

from .test_porta_do_plano import DM, _gate, _plano, _por_chave, _sem_iniciar


def _aprovado_no_plano(state: Any, bindings: dict[str, Any]) -> dict[str, Any]:
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": bindings}])
    item = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    assert item["selo"] == "aprovacao" and item["chave"]
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=item["step_id"], chave=item["chave"])]),
                  por="flavio")
    return item


async def test_chave_solta_aprovada_no_plano_segue_sem_perguntar_com_a_conferencia_do_texto(harness: Any,
                                                                                         monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _aprovado_no_plano(state, {**DM, "content": "oi :-{ até logo"})
    assert await _gate(state, "dm") is None
    assert [r["origem"] for r in state.db.query("SELECT origem FROM pending_approvals")] == ["plano"]


async def test_sim_do_plano_sem_o_texto_nao_libera_a_escrita(harness: Any, monkeypatch: Any) -> None:
    """Mesma chave, mas o sim gravado não traz o texto que o dono viu: a execução pergunta de novo."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _aprovado_no_plano(state, DM)
    state.db.execute("UPDATE pending_approvals SET generated_content=NULL, approved_content=NULL WHERE origem='plano'")
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"
    linhas = {r["origem"]: r for r in state.db.query("SELECT * FROM pending_approvals")}
    assert linhas["plano"]["status"] == "expired" and "não traz o texto" in linhas["plano"]["decided_note"]
    assert linhas["execucao"]["status"] == "pending"


async def test_o_pedido_novo_diz_por_que_o_sim_do_plano_nao_valeu(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _aprovado_no_plano(state, DM)
    state.db.execute("UPDATE steps SET bindings=? WHERE key='dm'", (json.dumps({**DM, "content": "oi!"}),))
    assert await _gate(state, "dm") is not None
    novo = state.db.one("SELECT summary, generated_content FROM pending_approvals WHERE origem='execucao'")
    assert "o sim dado no plano não vale: o item mudou desde a aprovação" in novo["summary"]
    assert novo["generated_content"] == "oi!"                   # o cartão novo mostra o texto que vai sair


async def test_briefing_com_sim_do_plano_escreve_o_texto_e_pergunta_com_ele(harness: Any, monkeypatch: Any) -> None:
    """O sim do plano só existe para texto final. Um sim de origem `plano` numa etapa com briefing (gravado à mão aqui:
    a prévia nunca o daria) não pode fechar o rascunho: o texto é escrito, e a execução pergunta COM ele."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": {"username": DM["username"],
                                                                     "content": "cumprimente a pessoa"}}])
    assert _por_chave(previa_da_porta(state, "run-p"))["dm"]["selo"] == "na_execucao"   # briefing não tem chave
    etapa = state.db.one("SELECT * FROM steps WHERE key='dm'")
    pedido = state.approvals.open(profile_id=state.db.scalar("SELECT profile_id FROM objectives"),
                                  capability="SEND_MESSAGE", summary="dm", target=DM["username"],
                                  content="cumprimente a pessoa", run_id="run-p", objective_id=etapa["objective_id"],
                                  step_id=etapa["id"])
    state.db.execute("UPDATE pending_approvals SET origem='plano', status='approved', chave_sha256='x' WHERE id=?",
                     (pedido.id,))
    escrito = "Oi! Tudo bem por aí?"

    async def draft_response(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(content=escrito, refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.social, "draft_response", draft_response)
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed
    assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE key='dm'"))["content"] == escrito
    linhas = {r["origem"]: r for r in state.db.query("SELECT * FROM pending_approvals")}
    assert linhas["plano"]["status"] == "expired"
    assert linhas["execucao"]["status"] == "pending" and linhas["execucao"]["generated_content"] == escrito
