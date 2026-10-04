"""31.49: a execução honra o sim dado na prévia do plano (30.61) e só pergunta pelo que só existe lá.

A trava mínima do #319 já descarta o sim do plano vencido, gasto ou com a chave da etapa relida diferente. Este item
fecha o resto do contrato com a Aprendizado (`.claude/handoffs/chave-da-aprovacao-30-61.md`):
- aprovação sem texto nunca libera escrita (a conferência explícita, depois da chave);
- o `_draft_gate` não trata o sim do plano como texto já mostrado: briefing com sim do plano escreve o texto, e o sim
  (que não cobre briefing) sai de cena;
- o pedido novo da execução diz por que o sim do plano não valeu;
- a mensagem repetida que surge DEPOIS do sim (outra execução mandou o mesmo texto ao mesmo alvo) descarta o sim: é
  estado mudado que o dono não viu (F1 da revisão). A que já existia antes do sim segue coberta por ele
  (`test_porta_do_plano.py::test_dm_editada_para_um_texto_ja_enviado_segue_no_cadeado_com_a_regra_real`).
O caso do texto com chave solta (`:-{`) aprovado no plano e não perguntado de novo está em
`test_porta_do_plano.py::test_chave_solta_aprovada_no_plano_e_honrada_pela_porta_na_execucao`; aqui ele se repete com
a conferência do texto ligada.

Nível de prova: `simulated` (harness, catálogo do Instagram, banco de teste; o rascunho é um falso). Nada real.
"""
from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from app.porta_do_plano import AprovarPlanoBody, ItemAprovado, aprovar_plano, previa_da_porta

from app.util import now, to_iso

from .test_porta_do_plano import ALVO, DM, _gate, _plano, _por_chave, _sem_iniciar


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
    state.db.execute("UPDATE pending_approvals SET origem='plano', status='approved', chave_sha256='x', expires_at=?,"
                     " decided_at=? WHERE id=?", (to_iso(now() + timedelta(hours=1)), to_iso(now()), pedido.id))
    escrito = "Oi! Tudo bem por aí?"

    async def draft_response(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(content=escrito, refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.social, "draft_response", draft_response)
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed
    assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE key='dm'"))["content"] == escrito
    linhas = {r["origem"]: r for r in state.db.query("SELECT * FROM pending_approvals")}
    # Passou da validade e chegou à chave: a etapa agora tem o texto escrito, e a chave dela não é a do sim forçado.
    assert linhas["plano"]["status"] == "expired" and "chave divergiu" in linhas["plano"]["decided_note"]
    assert linhas["execucao"]["status"] == "pending" and linhas["execucao"]["generated_content"] == escrito


async def test_a_mesma_dm_mandada_depois_do_sim_faz_a_execucao_perguntar_de_novo(harness: Any,
                                                                                 monkeypatch: Any) -> None:
    """F1: sim dado no plano; depois dele, outra execução manda a MESMA DM ao mesmo alvo. A chave do item é a mesma, mas
    a repetição é nova: a execução aprovada para e pergunta, com o motivo da repetição."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    item = _aprovado_no_plano(state, DM)
    state.social_repo.record_interaction(str(item["profile_id"]), type="dm_sent", direction="outbound",
                                         status="confirmed", counterparty=ALVO, outgoing_content=DM["content"],
                                         app_id="ig", run_id="r-outra", occurred_at=to_iso(now() + timedelta(seconds=1)))
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"
    linhas = {r["origem"]: r for r in state.db.query("SELECT * FROM pending_approvals")}
    assert linhas["plano"]["status"] == "expired"
    assert "a repetição surgiu depois do sim" in linhas["plano"]["decided_note"]
    assert linhas["execucao"]["status"] == "pending"
    # A frase da repetição (com o alvo) vem do motivo da porta, uma vez só; o descarte só diz quando ela surgiu.
    assert linhas["execucao"]["summary"].count("esta conta já mandou ESTA mensagem") == 1
    assert "o sim dado no plano não vale: a repetição surgiu depois do sim" in linhas["execucao"]["summary"]


async def test_pendente_de_outra_execucao_aprovado_depois_do_sim_faz_perguntar_de_novo(harness: Any,
                                                                                    monkeypatch: Any) -> None:
    """G1: o pedido PENDENTE de outra execução, com a mesma DM ao mesmo alvo, foi criado ANTES do sim. A prévia só conta
    aprovado, então o dono não o viu. Aprovado DEPOIS do sim, ele é repetição nova: conta pela decisão, não pela criação."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM}])
    pid = str(state.db.scalar("SELECT profile_id FROM objectives"))
    outro = state.approvals.open(profile_id=pid, capability="SEND_MESSAGE", summary="dm de outra execução",
                                 target=ALVO, content=DM["content"], run_id="r-outra")
    state.db.execute("UPDATE pending_approvals SET created_at=? WHERE id=?",
                     (to_iso(now() - timedelta(minutes=5)), outro.id))
    item = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    assert item["selo"] == "aprovacao" and "repetição" not in item["motivo"]     # o pendente não aparece na prévia
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=item["step_id"], chave=item["chave"])]),
                  por="flavio")
    state.db.execute("UPDATE pending_approvals SET status='approved', decided_at=? WHERE id=?",
                     (to_iso(now() + timedelta(seconds=1)), outro.id))
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"
    plano = state.db.one("SELECT status, decided_note FROM pending_approvals WHERE origem='plano'")
    assert plano["status"] == "expired" and "a repetição surgiu depois do sim" in plano["decided_note"]
    novo = state.db.one("SELECT summary FROM pending_approvals WHERE origem='execucao' AND step_id IS NOT NULL")
    assert "já foi aprovada" in novo["summary"]


async def test_excecao_presa_depois_do_sim_tira_o_sim_de_aprovado(harness: Any, monkeypatch: Any) -> None:
    """Nota da revisão: com a exceção 30.65 presa à etapa DEPOIS do sim, o sim do plano não vale e sai como `expired`;
    antes ficava `approved` até a faxina e contava como "aprovada e não enviada" no `_repetido` de outras etapas."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _aprovado_no_plano(state, DM)
    etapa = state.db.one("SELECT * FROM steps WHERE key='dm'")
    obj = state.db.one("SELECT * FROM objectives WHERE id=?", (etapa["objective_id"],))
    depois = to_iso(now() + timedelta(seconds=1))
    state.db.execute("INSERT INTO excecoes_de_politica(id, regra, profile_id, alvo, capability, motivo, autorizacao,"
                     " autor, criada_em, expira_em, step_id, presa_em) VALUES ('exc-3149','uma_conta_por_alvo',?,?,"
                     "'SEND_MESSAGE','m','a','orquestradora',?,?,?,?)",
                     (obj["profile_id"], ALVO, depois, to_iso(now() + timedelta(hours=1)), etapa["id"], depois))
    from app.planning.capabilities import capability_of
    cap = capability_of("com.instagram.android", "SEND_MESSAGE")
    assert cap is not None
    veredito = state._approval_gate(obj, etapa, cap, str(obj["profile_id"]), excecao="exc-3149",  # noqa: SLF001
                                    pacote="com.instagram.android")
    assert veredito is not None and not veredito.allowed
    plano = state.db.one("SELECT status, decided_note FROM pending_approvals WHERE origem='plano'")
    assert plano["status"] == "expired" and "exceção 30.65" in plano["decided_note"]


async def test_a_mesma_dm_mandada_antes_do_sim_segue_coberta_por_ele(harness: Any, monkeypatch: Any) -> None:
    """Controle do F1: a repetição que já existia antes do sim estava no motivo da prévia; o sim a cobre."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM}])
    pid = str(state.db.scalar("SELECT profile_id FROM objectives"))
    state.social_repo.record_interaction(pid, type="dm_sent", direction="outbound", status="confirmed",
                                         counterparty=ALVO, outgoing_content=DM["content"], app_id="ig",
                                         run_id="r-antiga", occurred_at=to_iso(now() - timedelta(days=1)))
    item = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    assert item["selo"] == "aprovacao" and "repetição passa por confirmação" in item["motivo"]
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=item["step_id"], chave=item["chave"])]),
                  por="flavio")
    assert await _gate(state, "dm") is None
    assert [r["origem"] for r in state.db.query("SELECT origem FROM pending_approvals")] == ["plano"]
