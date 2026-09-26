"""Receita divergida escala o modelo e conta o retorno à IA.

**Depende do patch de `backend/app/taskqueue/executor.py` entregue pela frente F3** (evolução de desempenho, onda 1):
sem ele, estes testes falham — e é isso que eles provam. O comentário de `AiCfg.strong_model_for_side_effect`
(config.py) promete que "receita divergida … continua escalando em qualquer modo"; a fórmula do `tier` em
`StepExecutor._run_step` nunca olhou `rr.diverged` (desde bdceacc). O mesmo ponto — a primeira decisão da IA depois
da divergência — é onde `receita.retorno_ia{motivo}` é contado, uma vez por etapa.
"""
from __future__ import annotations

from app.db import dumps, loads
from app.metricas import metricas

from .conftest import Harness


async def _aprende_e_diverge(harness: Harness) -> tuple[set[str], list[dict[str, object]]]:
    """android-01 aprende; a receita de `open_conversation` passa a apontar para um alvo que não existe (o que uma
    atualização de tela faz de verdade) e android-02 a reproduz: diverge na 1ª ação e a IA assume a etapa."""
    harness.cfg.file.ai.recipes = "replay"
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    db = harness.state.db                                                   # type: ignore[union-attr]
    receita = db.one("SELECT id, actions FROM recipes WHERE step_key='open_conversation' AND status='active'")
    acoes = loads(receita["actions"])
    acoes[0]["selectors"] = [{"kind": "rid", "rid": "app:id/nao_existe_mais"}]
    acoes[0].pop("scroll", None)
    db.execute("UPDATE recipes SET actions=? WHERE id=?", (dumps(acoes), receita["id"]))
    harness.ai.calls.clear()
    metricas.limpar()
    run = await harness.wait_run(harness.run(["android-02"]).id)
    assert run.status == "completed" and len(harness.fakes["android-02"].messages) == 1
    driven = {r["key"]: r["driven_by"] for r in db.query("SELECT key, driven_by FROM steps WHERE run_id=?", (run.id,))}
    assert driven["open_conversation"] == "recipe+ai" and driven["send_message"] == "recipe"   # só AQUELA etapa
    decisoes = [c for c in harness.ai.calls if c["role"] == "decide" and c["instance"] == "android-02"]
    return {"open_conversation"}, decisoes


async def test_decisao_depois_da_divergencia_sobe_para_o_modelo_de_escalonamento(harness: Harness) -> None:
    com_receita, decisoes = await _aprende_e_diverge(harness)
    divergidas = [c for c in decisoes if c["step"] in com_receita]    # etapa COM receita que ainda assim chamou a IA
    assert divergidas, "o cenário precisa fazer uma receita divergir"
    assert all(c["tier"] == 1 for c in divergidas), divergidas
    db = harness.state.db                                                   # type: ignore[union-attr]
    linhas = [r["message"] for r in db.query("SELECT message FROM events WHERE kind='decision' AND instance_id=?",
                                                ("android-02",))]
    assert any("decisão escalonada" in m and "receita divergiu" in m for m in linhas), linhas


async def test_retorno_a_ia_e_contado_uma_vez_por_etapa_com_motivo_fechado(harness: Harness) -> None:
    com_receita, decisoes = await _aprende_e_diverge(harness)
    etapas_divergidas = {c["step"] for c in decisoes if c["step"] in com_receita}
    assert etapas_divergidas
    assert metricas.total("receita.retorno_ia") == len(etapas_divergidas)      # uma vez por etapa, não por decisão
    series = [c for c in metricas.snapshot()["contadores"] if c["nome"] == "receita.retorno_ia"]
    assert {c["rotulos"]["motivo"] for c in series} == {"alvo_ausente"}
