"""31.223: na etapa com efeito, o modelo de ação navega e só o commit vai ao modelo forte.

Medido na onda 1 (07/10, lido em `mode=ro`): as leituras já decidiam no Sonnet, e o Opus decidia os 2 passos da etapa
de comentário (US$ 0,081 com imagem e 0,031), porque `strong_model_for_side_effect` sobe a etapa INTEIRA. Agora, com
`ai.strong_model_only_on_commit` (padrão `true`), a etapa começa no modelo de ação, e a primeira decisão que dispararia o
efeito é descartada e refeita no forte, que segue até o fim da tentativa. A trava de commit, a política de risco e o
rejulgamento do "sim" com efeito não mudam.

O que estes testes protegem:
* a navegação dentro da etapa com efeito (o toque no campo) decide no tier 0; a decisão de commit do tier 0 NÃO age
  (a mensagem sai uma vez só) e é refeita no tier 1, com a linha "só a decisão do commit (31.223)";
* com a chave em `false`, a etapa inteira decide no tier 1, como antes;
* no limite de ações (`max_actions_per_step: 1`), o commit descartado ainda chega ao forte na MESMA tentativa (a volta
  é reservada; achado da revisão do PR 505);
* `GET /api/ai` mostra a política em vigor (só leitura): `strong_model_for_side_effect` e
  `strong_model_only_on_commit`;
* o registro que a Jev lê por passo (31.229, sem campo novo): na etapa de envio, `ai_calls` tem a navegação no tier 0
  (com ação, sem efeito), o commit do tier 0 descartado (sem ação) e o commit no tier 1 (`escalate=efeito`, ação com
  `side_effect`).

Nível de prova: `simulated` (harness com provedor e aparelho falsos; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

from app.planning.provider import Decision, Usage

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_cost_levers import _eventos_de_escalonamento


def _navega_antes_do_commit(h: Harness) -> None:
    """A 1ª decisão do envio é um toque no campo (navegação, sem commit); depois, o provedor simulado (o toque em
    Enviar, `is_commit_action`)."""
    inner = h.ai.inner
    decide0 = inner.decide
    feito: list[bool] = []

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "send_message" and not feito:
            campo = req.screen.tree.find(resource_id="message_input")
            if campo:
                feito.append(True)
                return Decision(tool="tap", args={"rationale": "[teste] focar o campo", "element_id": campo[0].id,
                                                  "x": None, "y": None, "is_commit_action": False}), Usage()
        return await decide0(req)

    inner.decide = decide


async def test_a_navegacao_no_barato_e_o_commit_no_forte(harness: Harness) -> None:
    harness.cfg.file.ai.strong_model_for_side_effect = True         # o envio do app de prova sobe (senão fica no 0)
    assert harness.cfg.file.ai.strong_model_only_on_commit is True    # o padrão
    _navega_antes_do_commit(harness)
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    ai = harness.ai
    assert ai.count("decide", step="send_message", tier=0) == 2      # o toque no campo e o commit descartado
    assert ai.count("decide", step="send_message", tier=1) == 1      # o commit, refeito no forte
    assert len(harness.fakes["android-01"].messages) == 1             # o commit do barato não agiu
    linhas = _eventos_de_escalonamento(harness, run.id)
    assert len(linhas) == 1 and "só a decisão do commit (31.223)" in linhas[0]
    # o que a Jev lê por passo (31.229): a decisão que agiu tem a ação ligada (`actions.ai_call_id`); a de commit do
    # tier 0 descartada não tem ação; a ação do commit é a gravada com `side_effect`
    db = harness.state.db
    passo = db.scalar("SELECT id FROM steps WHERE run_id=? AND key='send_message'", (run.id,))
    chamadas = db.query("SELECT c.tier, c.escalate, (SELECT MAX(a.side_effect) FROM actions a WHERE a.ai_call_id = c.id"
                        " AND a.status <> 'rejected') efeito FROM ai_calls c WHERE c.step_id=? AND c.role='decide'"
                        " ORDER BY c.ts, c.id", (passo,))
    assert [(r["tier"], r["escalate"], r["efeito"]) for r in chamadas] == [(0, None, 0), (0, None, None),
                                                                          (1, "efeito", 1)]


async def test_desligado_a_etapa_inteira_no_forte(harness: Harness) -> None:
    harness.cfg.file.ai.strong_model_for_side_effect = True
    harness.cfg.file.ai.strong_model_only_on_commit = False
    _navega_antes_do_commit(harness)
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    assert harness.ai.count("decide", step="send_message", tier=0) == 0
    assert harness.ai.count("decide", step="send_message", tier=1) == 2
    assert len(harness.fakes["android-01"].messages) == 1


async def test_o_get_da_ia_mostra_a_politica_em_vigor(harness: Harness) -> None:
    async with _cliente(harness) as c:
        padrao = (await c.get("/api/ai")).json()
        harness.cfg.file.ai.strong_model_for_side_effect = True
        harness.cfg.file.ai.strong_model_only_on_commit = False
        mudado = (await c.get("/api/ai")).json()
    assert (padrao["strong_model_for_side_effect"], padrao["strong_model_only_on_commit"]) == ("by_risk", True)
    assert (mudado["strong_model_for_side_effect"], mudado["strong_model_only_on_commit"]) == ("true", False)


async def test_no_limite_de_acoes_o_commit_ainda_chega_ao_forte(harness: Harness) -> None:
    harness.cfg.file.ai.strong_model_for_side_effect = True
    _navega_antes_do_commit(harness)
    async with _cliente(harness) as c:
        assert (await c.put("/api/settings", json={"max_actions_per_step": 1})).status_code == 200
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    decisoes = [(c["step"], c.get("tier")) for c in harness.ai.calls if c["role"] == "decide"]
    # sem a volta reservada, o commit descartado estourava o limite e a execução recomeçava da conversa
    assert decisoes[:5] == [("open_conversation", 0), ("compose_message", 0), ("send_message", 0),
                            ("send_message", 0), ("send_message", 1)], decisoes
    assert len(harness.fakes["android-01"].messages) == 1
