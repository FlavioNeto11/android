"""31.233: a receita ATIVA que divergiu e caiu em quarentena, com a IA completando a etapa, ensina a candidata.

Medido na onda 2 (07/10, `op-20261007100755-096a28`, lido em `mode=ro`): a receita 111 (`open_post`, a 1ª publicação da
grade, aprendida no perfil nosso) rolou, não achou o alvo no perfil de terceiro e a IA terminou a etapa nos 3 alvos:
US$ 0,1415, 39 % da onda. Na 3ª divergência seguida ela foi à quarentena, e nada se aprendeu: o executor só aprendia
quando quem divergia era uma CANDIDATA. Agora, quando a ativa cai em quarentena NESTA tentativa e a IA a completou, o
caminho que rodou (o trecho da receita feito e o da IA) vira candidata, em prova como qualquer outra.

O que estes testes protegem:
* a 3ª divergência seguida com a etapa comprovada: a ativa vai à quarentena (e depois a `superseded`), a candidata nasce
  com as ações da receita (`source='recipe'`, feitas) e as da IA, e a trilha diz "a partir da vN ativa";
* antes da quarentena (a ativa ainda segura a chave) nada nasce; com a etapa NÃO comprovada, nada nasce;
* com `ai.candidata_da_ativa_que_divergiu: false`, como antes;
* a destilação com o trecho da receita: o gesto `rejected` da receita fica fora, e sem a chave a ação da receita recusa.

Nível de prova: `simulated` (harness com provedor e aparelho falsos).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.db import Database, loads
from app.taskqueue.executor import Outcome, StepOutcome, _RecipeRun
from app.taskqueue.recipes import QUARANTINE_AFTER, Replayer, distill
from app.taskqueue.repository import Repository

from .conftest import Harness
from .test_espera_nao_e_veredito_da_receita import _receita_aprendida


def _rr(receita: Any, variaveis: dict[str, str]) -> _RecipeRun:
    """A tentativa em que a receita fez a 1ª ação, divergiu na seguinte e a IA assumiu."""
    rep = Replayer(recipe_id=int(receita["id"]), version=int(receita["version"]), actions=loads(receita["actions"]),
                   variables={}, idx=1, done_actions=1)
    return _RecipeRun(mode="replay", row=receita, replayer=rep, diverged="o alvo da ação 2 não apareceu",
                      exercised=["recipe", "ai_actor"], variables=variaveis, app_version=receita["app_version"],
                      step_hash=receita["step_hash"], signature=receita["app_signature"], variant=receita["variant"])


async def _preparar(harness: Harness, *, seguidas: int) -> tuple[Database, Any, Any, str]:
    harness.pular_o_tempo()
    db, receita, etapa, tentativa = await _receita_aprendida(harness)
    # a 1ª ação gravada da tentativa foi da receita; as seguintes, da IA
    primeira = db.scalar("SELECT MIN(seq) FROM actions WHERE attempt_id=? AND tool NOT IN ('observe_screen',"
                         " 'step_done', 'find_element', 'wait_for', 'verify_state')", (tentativa,))
    db.execute("UPDATE actions SET source='recipe' WHERE attempt_id=? AND seq=?", (tentativa, primeira))
    db.execute("UPDATE recipes SET consecutive_fail=? WHERE id=?", (seguidas, receita["id"]))
    harness.cfg.file.ai.recipes_promote_after = 2      # o harness aprende ativa (0); em operação, o padrão é 2
    return db, receita, etapa, tentativa


def _depois(harness: Harness, db: Database, receita: Any, etapa: Any, tentativa: str, outcome: Outcome) -> None:
    ex = harness.state.scheduler.executor                                   # type: ignore[union-attr]
    parametros = db.scalar("SELECT parameters FROM objectives WHERE id=?", (etapa["objective_id"],))
    ex._after_step(_rr(receita, {**loads(parametros, {}), "instance_id": "android-01", "run_id": etapa["run_id"]}),
                   StepOutcome(outcome), etapa["run_id"], "android-01", Repository.step_dto(etapa),
                   tentativa, SimpleNamespace(package=receita["app_package"], id="qa-messenger"))


def _candidatas(db: Database, receita: Any) -> list[Any]:
    return db.query("SELECT * FROM recipes WHERE step_key=? AND status='candidate'", (receita["step_key"],))


async def test_a_ativa_que_cai_em_quarentena_ensina_a_candidata(harness: Harness) -> None:
    db, receita, etapa, tentativa = await _preparar(harness, seguidas=QUARANTINE_AFTER - 1)
    _depois(harness, db, receita, etapa, tentativa, Outcome.succeeded)
    nova = _candidatas(db, receita)
    assert len(nova) == 1 and nova[0]["learned_from_step"] == etapa["id"]
    assert len(loads(nova[0]["actions"])) == len(loads(receita["actions"]))     # o trecho da receita + o da IA
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (receita["id"],)) in ("quarantined", "superseded")
    assert db.scalar("SELECT driven_by FROM steps WHERE id=?", (etapa["id"],)) == "recipe+ai"
    trilha = [r["message"] for r in db.query("SELECT message FROM events WHERE step_id=? ORDER BY id", (etapa["id"],))]
    assert any(f"a partir da v{receita['version']} ativa, que divergiu e foi à quarentena (31.233)" in t for t in trilha)


@pytest.mark.parametrize("seguidas, outcome, ligada", [
    (0, Outcome.succeeded, True),                      # a ativa ainda segura a chave: nada nasce
    (QUARANTINE_AFTER - 1, Outcome.failed, True),       # a etapa não foi comprovada: nada nasce
    (QUARANTINE_AFTER - 1, Outcome.succeeded, False),   # desligado: como antes
])
async def test_quando_nada_nasce(harness: Harness, seguidas: int, outcome: Outcome, ligada: bool) -> None:
    harness.cfg.file.ai.candidata_da_ativa_que_divergiu = ligada
    db, receita, etapa, tentativa = await _preparar(harness, seguidas=seguidas)
    _depois(harness, db, receita, etapa, tentativa, outcome)
    assert _candidatas(db, receita) == []


def _linha(seq: int, tool: str, source: str, status: str = "done", **kw: Any) -> dict[str, Any]:
    return {"id": seq, "seq": seq, "tool": tool, "status": status, "source": source, "args": kw.get("args", "{}"),
            "target": None, "side_effect": 0, "rationale": ""}


def test_a_destilacao_com_o_trecho_da_receita() -> None:
    linhas = [_linha(1, "scroll", "recipe", args='{"direction": "down"}'), _linha(2, "tap", "recipe", "rejected"),
              _linha(3, "press_key", "ai", args='{"key": "enter"}')]
    sem, motivo = distill(linhas, {})                                              # type: ignore[arg-type]
    assert sem is None and "não foi limpa" in motivo
    _, motivo = distill(linhas, {}, com_trecho_da_receita=True)                    # type: ignore[arg-type]
    assert "não foi limpa" not in motivo, motivo                                   # o rejected da receita fica fora
    falhou = [*linhas[:1], _linha(2, "tap", "recipe", "failed"), linhas[2]]
    _, motivo = distill(falhou, {}, com_trecho_da_receita=True)                    # type: ignore[arg-type]
    assert "não foi limpa (tap: failed/recipe)" in motivo
