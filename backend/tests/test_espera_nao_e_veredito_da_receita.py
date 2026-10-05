"""30.70: a espera por uma pessoa não é veredito sobre a receita.

Achado da leitura do 30.69: em `StepExecutor._after_step`, o `veredito` só excluía defeito do plano, `retry` e trava da
conta. Uma etapa que a receita reproduziu (ou de que divergiu) e que terminou em `waiting_user` — aviso do app,
autenticação, conta errada, falta de informação — chamava `RecipeStore.result(id, False)`: somava no contador de
QUARENTENA e gravava `driven_by='recipe+ai'`, que o aprendizado lê como evidência CONTRA a receita
(`reproducao_sql._ETAPAS`). Com o 29.90 (a releitura que segura o toque marca a divergência; o aviso que cobre o botão
termina em `waiting_user`), um aviso do app passaria a contar contra uma receita boa.

Os testes chamam o `_after_step` como o laço do executor chama, sobre uma receita aprendida de verdade no harness.
"""
from __future__ import annotations

from typing import Any

from app.db import Database, loads
from app.modules.learning.domain.vocabulario import Posicao
from app.modules.learning.infrastructure.reproducao_sql import ReproducoesSql
from app.taskqueue.executor import Outcome, StepOutcome, _RecipeRun
from app.taskqueue.recipes import Replayer
from app.taskqueue.repository import Repository

from .conftest import Harness

AVISO = "Um aviso cobre o botão de efeito; uma pessoa precisa fechá-lo"


async def _receita_aprendida(harness: Harness) -> tuple[Database, Any, Any, str]:
    """android-01 aprende; devolve o banco, a receita de `open_conversation`, a etapa dela e a tentativa."""
    harness.cfg.file.ai.recipes = "replay"
    run = await harness.wait_run(harness.run(["android-01"]).id)
    assert run.status == "completed"
    db = harness.state.db                                                   # type: ignore[union-attr]
    receita = db.one("SELECT * FROM recipes WHERE step_key='open_conversation' AND status='active'")
    assert receita is not None
    etapa = db.one("SELECT * FROM steps WHERE run_id=? AND key='open_conversation'", (run.id,))
    tentativa = db.one("SELECT id FROM attempts WHERE step_id=? ORDER BY number DESC LIMIT 1", (etapa["id"],))
    return db, receita, etapa, str(tentativa["id"])


def _rr(receita: Any, *, diverged: str | None = None) -> _RecipeRun:
    """A tentativa que reproduziu a 1ª ação da receita (e, com `diverged`, divergiu depois)."""
    rep = Replayer(recipe_id=int(receita["id"]), version=int(receita["version"]), actions=loads(receita["actions"]),
                   variables={}, idx=1, done_actions=1)
    return _RecipeRun(mode="replay", row=receita, replayer=rep, diverged=diverged, exercised=["recipe"])


def _contadores(db: Database, receita_id: int) -> tuple[int, int, int, str]:
    r = db.one("SELECT replay_ok, replay_fail, consecutive_fail, status FROM recipes WHERE id=?", (receita_id,))
    return int(r["replay_ok"]), int(r["replay_fail"]), int(r["consecutive_fail"]), str(r["status"])


async def test_espera_pela_pessoa_nao_conta_contra_a_receita_e_a_retomada_conta_a_favor(harness: Harness) -> None:
    harness.pular_o_tempo()   # T.2: relógio virtual; o que se confere não depende de tempo real
    db, receita, etapa, tentativa = await _receita_aprendida(harness)
    ex = harness.state.scheduler.executor                                   # type: ignore[union-attr]
    dto = Repository.step_dto(etapa)
    rid = int(receita["id"])
    antes = _contadores(db, rid)
    driven_antes = etapa["driven_by"]

    # Reproduzida e parada esperando a pessoa; depois, o caso do 29.90: divergida (o toque segurado) e parada.
    for diverged in (None, "tela mudou antes do toque: cobertura"):
        ex._after_step(_rr(receita, diverged=diverged), StepOutcome(Outcome.waiting_user, AVISO), etapa["run_id"],
                       "android-01", dto, tentativa, None)
        assert _contadores(db, rid) == antes, diverged                      # nem replay_fail, nem quarentena
        assert db.scalar("SELECT driven_by FROM steps WHERE id=?", (etapa["id"],)) == driven_antes

    # A pessoa resolveu e a etapa retomada terminou pela receita sozinha: aí sim, veredito — a favor.
    ex._after_step(_rr(receita), StepOutcome(Outcome.succeeded), etapa["run_id"], "android-01", dto, tentativa, None)
    ok, fail, seguidas, status = _contadores(db, rid)
    assert (ok, fail, seguidas, status) == (antes[0] + 1, antes[1], 0, antes[3])
    assert db.scalar("SELECT driven_by FROM steps WHERE id=?", (etapa["id"],)) == "recipe"


async def test_a_falha_de_verdade_continua_contando_contra(harness: Harness) -> None:
    """O conserto não afrouxa o veredito: a etapa que FALHOU depois da receita segue contando contra."""
    harness.pular_o_tempo()
    db, receita, etapa, tentativa = await _receita_aprendida(harness)
    ex = harness.state.scheduler.executor                                   # type: ignore[union-attr]
    antes = _contadores(db, int(receita["id"]))
    ex._after_step(_rr(receita, diverged="alvo ausente"), StepOutcome(Outcome.failed, "não comprovou"),
                   etapa["run_id"], "android-01", Repository.step_dto(etapa), tentativa, None)
    assert _contadores(db, int(receita["id"]))[1:3] == (antes[1] + 1, antes[2] + 1)
    assert db.scalar("SELECT driven_by FROM steps WHERE id=?", (etapa["id"],)) == "recipe+ai"


async def test_leitura_de_reproducoes_ignora_a_etapa_que_espera_a_pessoa(harness: Harness) -> None:
    """As etapas gravadas antes do conserto (`recipe+ai` em `waiting_user`) não viram AGAINST no digest; retomada e
    terminada pela receita, a mesma etapa vira só FOR."""
    harness.pular_o_tempo()
    db, receita, etapa, tentativa = await _receita_aprendida(harness)
    db.execute("UPDATE attempts SET recipe_id=? WHERE id=?", (receita["id"], tentativa))
    db.execute("UPDATE steps SET driven_by='recipe+ai', status='waiting_user' WHERE id=?", (etapa["id"],))
    leitor = ReproducoesSql(db)
    assert [r for r in leitor.da_execucao(etapa["run_id"]) if r.receita == receita["id"]] == []
    assert not [r for r in leitor.faltantes() if r.receita == receita["id"]]

    db.execute("UPDATE steps SET driven_by='recipe', status='succeeded' WHERE id=?", (etapa["id"],))
    posicoes = {r.posicao for r in leitor.da_execucao(etapa["run_id"]) if r.receita == receita["id"]}
    assert posicoes == {Posicao.FOR}
