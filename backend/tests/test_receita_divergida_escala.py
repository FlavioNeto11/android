"""Receita divergida: a IA assume a etapa NO MODELO DE AÇÃO e o retorno à IA é contado.

A frente F3 (evolução de desempenho, 26/09) confirmou que a fórmula do `tier` em `StepExecutor._run_step` nunca leu
`rr.diverged` (desde bdceacc), embora um comentário de `config.py` prometesse escalar. A decisão do coordenador foi
manter o comportamento (escalar é custo sem prova de ganho: 22 etapas `recipe+ai` em 7 dias; os controles de erro e
repetição já escalam) e alinhar o comentário. Estes testes FIXAM a decisão — se o dono escolher escalar, o primeiro
muda junto com o código — e provam o funil: `receita.retorno_ia{motivo}` uma vez por etapa divergida.
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


async def test_divergencia_sozinha_nao_sobe_de_modelo(harness: Harness) -> None:
    harness.pular_o_tempo()   # T.2: relógio virtual; o que se confere não depende de tempo real
    com_receita, decisoes = await _aprende_e_diverge(harness)
    divergidas = [c for c in decisoes if c["step"] in com_receita]    # etapa COM receita que ainda assim chamou a IA
    assert divergidas, "o cenário precisa fazer uma receita divergir"
    assert all(c["tier"] == 0 for c in divergidas), divergidas
    db = harness.state.db                                                   # type: ignore[union-attr]
    linhas = [r["message"] for r in db.query("SELECT message FROM events WHERE kind='decision' AND instance_id=?",
                                                ("android-02",))]
    assert any("receita divergiu" in m and "a IA assume" in m for m in linhas), linhas
    assert not any("decisão escalonada" in m for m in linhas), linhas


async def test_retorno_a_ia_e_contado_uma_vez_por_etapa_com_motivo_fechado(harness: Harness) -> None:
    harness.pular_o_tempo()   # T.2: relógio virtual; o que se confere não depende de tempo real
    com_receita, decisoes = await _aprende_e_diverge(harness)
    etapas_divergidas = {c["step"] for c in decisoes if c["step"] in com_receita}
    assert etapas_divergidas
    assert metricas.total("receita.retorno_ia") == len(etapas_divergidas)      # uma vez por tentativa, não por decisão
    series = [c for c in metricas.snapshot()["contadores"] if c["nome"] == "receita.retorno_ia"]
    assert {c["rotulos"]["motivo"] for c in series} == {"alvo_ausente"}


async def test_toque_de_efeito_da_receita_segurado_pela_releitura_devolve_a_etapa_a_ia(harness: Harness,
                                                                                       monkeypatch: object) -> None:
    """29.90 (D2-R1): a releitura antes do toque segura o toque de efeito que veio da RECEITA. O cursor dela já passou
    dessa ação; sem marcar a divergência, a receita acabaria sem o toque e a etapa iria ao juiz como se a receita a
    tivesse feito. Marcada, a IA assume e o envio sai uma vez."""
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    harness.cfg.file.ai.recipes = "replay"
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    original = modulo.cobertura_nova_no_ponto
    seguradas: list[str] = []

    def uma_vez(antes, depois, ponto, alvo):  # type: ignore[no-untyped-def]
        if not seguradas:
            seguradas.append("x")
            return modulo.MUDANCA_POR_CIMA, "apareceu por cima do alvo (simulado)"
        return original(antes, depois, ponto, alvo)

    monkeypatch.setattr(modulo, "cobertura_nova_no_ponto", uma_vez)  # type: ignore[attr-defined]
    inner = harness.ai.inner
    verify0 = inner.verify
    enviadas_no_juiz: list[int] = []      # quantas mensagens o aparelho tinha em cada juízo do envio

    async def verify(req):  # type: ignore[no-untyped-def]
        if req.ctx.step_key == "send_message":
            enviadas_no_juiz.append(len(harness.fakes["android-02"].messages))
        return await verify0(req)

    inner.verify = verify
    metricas.limpar()
    run = await harness.wait_run(harness.run(["android-02"]).id)
    assert seguradas
    assert enviadas_no_juiz and 0 not in enviadas_no_juiz, enviadas_no_juiz   # nenhum juízo antes do envio
    assert run.status == "completed" and len(harness.fakes["android-02"].messages) == 1
    db = harness.state.db                                                   # type: ignore[union-attr]
    driven = {r["key"]: r["driven_by"] for r in db.query("SELECT key, driven_by FROM steps WHERE run_id=?", (run.id,))}
    assert driven["send_message"] == "recipe+ai"
    assert metricas.valor("receita.retorno_ia", motivo="tela_mudou") == 1
