"""31.262: a candidata que não se aplica na tela de partida não perde a prova nem é trocada.

Medido em 07/10 (só leitura, central `8552b160`). A chave genérica do `open_profile` de perfil de terceiro passou pelas
candidatas 118, 166, 222 e 223 sem nunca ficar ativa (são 2 concordâncias seguidas para promover). Na rodada das 12:55,
os 3 aparelhos começaram a etapa com a folha de comentários que a operação anterior deixou aberta. A IA voltou (2 a 5
decisões) e a 222, em sombra, contou divergência. No replanejamento do android-06 (13:02), a etapa começou fora do app:
a IA abriu o Instagram, a 222 divergiu na ação 1 e virou a 223, que é o mesmo caminho com um `open_app` na frente.

A reprodução já tratava isso desde o 30.80: o alvo da AÇÃO 1 ausente na tela de partida, com a etapa comprovada, não é
veredito sobre a receita. A sombra não tratava. Agora, com o alvo da ação 1 ausente, a comparação para (sem veredito),
a prova da candidata continua, o caminho da IA não a substitui, e a série `nao_aplicavel_seguidas` sobe. Na 3ª
seguida, conta como divergência, como antes.

Nível de prova: `simulated` (harness na porta 5640, aparelho falso; nenhuma IA paga). `real`: `not_run` (a próxima
etapa com candidata em prova que comece fora do estado dela: evento `receita_nao_aplicavel` com `em_prova`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.taskqueue.recipes import NAO_APLICAVEL_CONTA_APOS, RecipeStore

from .conftest import Harness
from .fake_skills import banco as banco_migrado
from .test_receita_nao_aplicavel import CHAVE, PKG, _parte_de_outra_conversa


def _prova(store: RecipeStore, rid: int) -> tuple[str, int, int, int]:
    r = store.db.one("SELECT status, shadow_agree, shadow_total, nao_aplicavel_seguidas FROM recipes WHERE id=?",
                     (rid,))
    assert r is not None
    return str(r["status"]), int(r["shadow_agree"]), int(r["shadow_total"]), int(r["nao_aplicavel_seguidas"])


def test_a_serie_em_prova_conta_na_terceira_e_a_sombra_a_zera(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "sombra.sqlite3")
    store = RecipeStore(db)
    rid = store.save(package=PKG, app_version="1.0(1)", step_hash="h-abrir", step_key="abrir", learned_from="r-x:a:v1:abrir",
                     actions=[{"tool": "tap", "commit": False, "why": "abrir", "args": {},
                               "selectors": [{"kind": "rid", "rid": "app:id/abrir"}]}], candidate=True, **CHAVE)
    assert rid and _prova(store, rid)[0] == "candidate"
    assert NAO_APLICAVEL_CONTA_APOS == 3
    assert [store.nao_aplicavel_em_prova(rid) for _ in range(2)] == [False, False]
    store.shadow(rid, True, promote_after=5)                                  # concordou: a série zera
    assert _prova(store, rid)[3] == 0
    assert [store.nao_aplicavel_em_prova(rid) for _ in range(3)] == [False, False, True]
    store.shadow(rid, False, promote_after=5)                                 # divergiu: a série zera também
    assert _prova(store, rid)[3] == 0
    db.close()


async def _candidata(h: Harness) -> int:
    """A receita do `open_conversation` aprendida como CANDIDATA (a suíte usa `recipes_promote_after: 0`)."""
    h.cfg.file.ai.recipes = "replay"
    h.cfg.file.ai.recipes_promote_after = 2
    assert (await h.wait_run(h.run(["android-01"]).id)).status == "completed"
    rid = h.state.db.scalar("SELECT id FROM recipes WHERE step_key='open_conversation' AND status='candidate'")  # type: ignore[union-attr]
    assert rid
    return int(rid)


def _eventos(h: Harness, run_id: str) -> list[dict[str, Any]]:
    return [json.loads(r["data"]) for r in h.state.db.query(  # type: ignore[union-attr]
        "SELECT data FROM events WHERE run_id=? AND kind='decision' AND data LIKE '%receita_nao_aplicavel%'", (run_id,))]


async def test_candidata_que_nao_se_aplica_na_partida_segue_em_prova(harness: Harness,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    rid = await _candidata(harness)
    db = harness.state.db  # type: ignore[union-attr]
    store = RecipeStore(db)
    db.execute("UPDATE recipes SET shadow_agree=1, shadow_total=1 WHERE id=?", (rid,))   # uma concordância antes
    _parte_de_outra_conversa(harness, "android-03", 3, monkeypatch)
    run = await harness.wait_run(harness.run(["android-03"]).id)
    assert run.status == "completed"
    assert _prova(store, rid) == ("candidate", 1, 1, 1)                      # a prova não zerou; uma "não se aplicou"
    assert db.scalar("SELECT count(*) FROM recipes WHERE step_key='open_conversation' AND id<>?", (rid,)) == 0
    [dados] = _eventos(harness, run.id)
    assert (dados["kind"], dados["recipe_id"], dados["em_prova"], dados["contou_como_falha"]) == (
        "receita_nao_aplicavel", rid, True, False)
    etapa = db.one("SELECT driven_by, status FROM steps WHERE run_id=? AND key='open_conversation'", (run.id,))
    assert etapa is not None and (etapa["status"], etapa["driven_by"]) == ("succeeded", "ai")


async def test_a_terceira_seguida_conta_como_divergencia(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    rid = await _candidata(harness)
    db = harness.state.db  # type: ignore[union-attr]
    store = RecipeStore(db)
    db.execute("UPDATE recipes SET shadow_agree=1, shadow_total=1, nao_aplicavel_seguidas=? WHERE id=?",
               (NAO_APLICAVEL_CONTA_APOS - 1, rid))
    _parte_de_outra_conversa(harness, "android-03", 3, monkeypatch)
    run = await harness.wait_run(harness.run(["android-03"]).id)
    assert run.status == "completed"
    assert _prova(store, rid)[1:] == (0, 0, 0)                               # divergência: a prova recomeça
    assert _eventos(harness, run.id) == []
