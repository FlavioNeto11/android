"""Item 28.23: o teto de autonomia chega à execução (migração 100).

Achado de segurança da frente Canais: a autonomia do pedido persistente não chegava à execução (`laco._requisicao` a
criava sem teto) e um pedido `observar` com objetivo de efeito dependia só da política da persona. Aqui, os três níveis
e o nulo, no QA Messenger falso (o plano simulado de sempre tem uma etapa com efeito: `send_message`):
- nulo e `agir`: idênticos a hoje (a mensagem sai);
- `observar`: o plano com efeito é recusado (`plan.refused`, `acima_da_autonomia`, execução `failed`); e, como defesa,
  se a etapa com efeito chegar ao despacho, a execução para antes dela e do PREENCHIMENTO dela, com o objetivo `failed`;
- `preparar`: a etapa com efeito não roda sozinha (sem ação do catálogo para pedir aprovação, é segurada).

Nível de prova: `simulated` (harness na porta 5640, provedor simulado; nenhuma IA paga).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.models import RunCreate

from .conftest import COMMAND, Harness

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user", "needs_input", "cancelled", "uncertain")


def _run(h: Harness, teto: str | None) -> Any:
    assert h.state is not None
    return h.state.runs.create(RunCreate(command=COMMAND, instance_ids=["android-01"], mode="execute",
                                         idempotency_key=f"test-{uuid.uuid4()}", teto_de_autonomia=teto))  # type: ignore[arg-type]


def _envios(h: Harness, run_id: str) -> int:
    return int(h.state.db.scalar(                                                      # type: ignore[union-attr]
        "SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id "
        "WHERE s.run_id=? AND s.key IN ('compose_message','send_message') AND a.status='done' "
        "AND a.tool IN ('tap','type_text')", (run_id,)) or 0)


async def test_nulo_e_agir_sao_identicos_a_hoje(harness: Harness) -> None:
    for teto in (None, "agir"):
        run = _run(harness, teto)
        await harness.wait_run(run.id, statuses=TERMINAIS)
        linha = harness.state.repo.run_row(run.id)                                    # type: ignore[union-attr]
        assert linha["teto_de_autonomia"] == teto
        assert linha["status"] == "completed", (teto, linha["status_detail"])
        assert harness.state.repo.run_summary(linha).teto_de_autonomia == teto       # type: ignore[union-attr]


async def test_observar_recusa_o_plano_com_efeito(harness: Harness) -> None:
    run = _run(harness, "observar")
    await harness.wait_run(run.id, statuses=TERMINAIS)
    linha = harness.state.repo.run_row(run.id)                                        # type: ignore[union-attr]
    assert linha["status"] == "failed" and "observar" in linha["status_detail"]
    eventos = [json.loads(r["data"]) for r in harness.state.db.query(                 # type: ignore[union-attr]
        "SELECT data FROM events WHERE run_id=? AND kind='plan.refused'", (run.id,))]
    assert eventos and eventos[-1]["motivo"] == "acima_da_autonomia" and "send_message" in eventos[-1]["etapas"]
    assert _envios(harness, run.id) == 0
    assert harness.state.db.scalar("SELECT COUNT(*) FROM attempts t JOIN steps s ON s.id=t.step_id "  # type: ignore[union-attr]
                                   "WHERE s.run_id=?", (run.id,)) == 0                # nenhuma etapa rodou


async def test_observar_no_despacho_para_antes_do_preenchimento_e_falha(harness: Harness, monkeypatch: Any) -> None:
    """A defesa: um plano com efeito que passou da porta do plano (fluxo, skill, revisão) para antes no despacho."""
    monkeypatch.setattr(type(harness.state.runs), "_teto_observar", lambda self, run_id: False)  # type: ignore[union-attr]
    run = _run(harness, "observar")
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert _envios(harness, run.id) == 0                                       # nada digitado nem enviado
    etapas = {r["key"]: r for r in harness.state.db.query(                             # type: ignore[union-attr]
        "SELECT key, status, attempts, status_detail FROM steps WHERE run_id=?", (run.id,))}
    assert etapas["compose_message"]["status"] == "skipped" and etapas["compose_message"]["attempts"] == 0
    assert etapas["send_message"]["status"] == "skipped"
    assert "Teto de autonomia observar" in etapas["compose_message"]["status_detail"]
    assert harness.state.db.scalar("SELECT status FROM objectives WHERE run_id=?", (run.id,)) == "failed"  # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] != "completed"                  # type: ignore[union-attr]


async def test_preparar_segura_o_efeito_que_nao_tem_como_pedir_aprovacao(harness: Harness) -> None:
    run = _run(harness, "preparar")
    await harness.wait(lambda: harness.state.db.scalar(                               # type: ignore[union-attr]
        "SELECT COUNT(*) FROM objectives WHERE run_id=? AND status IN ('waiting_user','failed','succeeded')",
        (run.id,)) == 1, what="o objetivo parar")
    assert harness.state.db.scalar("SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id "  # type: ignore[union-attr]
                                   "JOIN steps s ON s.id=t.step_id WHERE s.run_id=? AND s.key='send_message'",
                                   (run.id,)) == 0                                  # o envio nunca começou
    obj = harness.state.db.one("SELECT status, status_detail FROM objectives WHERE run_id=?", (run.id,))  # type: ignore[union-attr]
    assert obj["status"] == "waiting_user" and "preparar" in (obj["status_detail"] or "")
