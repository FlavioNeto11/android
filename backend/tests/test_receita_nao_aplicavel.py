"""30.80: a receita que "não se aplicou" não conta como falha dela.

Achado real (05/10, r-20261005133833-122345, a prova do 31.79): a execução no android-12 partiu de dentro de uma conversa;
a receita 194, ensinada no modo treinamento, divergiu na AÇÃO 1 ("alvo ausente ou ambíguo nesta tela"), a IA comprovou
a etapa, e a receita saiu com `replay_fail=1`, uma falha que era da tela de partida, não dela. Agora:
- divergência na ação 1, antes de a receita agir, por alvo ausente, com a etapa COMPROVADA: não é veredito sobre a
  receita (nem `replay_ok` nem `replay_fail`); quem conduziu é a IA (`driven_by='ai'`), e o evento diz "tela de partida
  diferente";
- se a IA assume e a etapa falha, é a falha comum da receita, como antes;
- a partir da 3ª "não se aplicou" SEGUIDA, cada uma conta como falha comum, sem zerar a série (um 1º seletor quebrado
  não fica isento: a quarentena chega na 5ª, e não na 9ª — R1 da segunda leitura); o ok e a falha comum zeram a série;
- o seletor AMBÍGUO (casa mais de um elemento) não é "não se aplicou": deixou de ser único, e isso é defeito da receita.

Nível de prova: `simulated` (harness na porta 5640, aparelho falso; nenhuma IA paga).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.vocabulario import LivroKind
from app.planning.provider import Decision, Usage
from app.taskqueue.recipes import NAO_APLICAVEL_CONTA_APOS, AlvoAusente, RecipeDiverged, RecipeStore, Replayer

from .conftest import Harness
from .fake_skills import banco as banco_migrado
from .test_d1_receitas import DONO, Mundo
from .test_recipes import XML

PKG = "com.pocqa.messenger"
CHAVE = {"signature": "", "variant": "en-US/xhdpi"}


def _salva(store: RecipeStore) -> int:
    rid = store.save(package=PKG, app_version="1.0(1)", step_hash="h-abrir", step_key="abrir", learned_from="training:t1",
                     actions=[{"tool": "tap", "commit": False, "why": "abrir", "args": {},
                               "selectors": [{"kind": "rid", "rid": "app:id/abrir"}]}], **CHAVE)
    assert rid
    return rid


def _uso(store: RecipeStore, rid: int) -> tuple[int, int, int, int]:
    r = store.db.one("SELECT replay_ok, replay_fail, consecutive_fail, nao_aplicavel_seguidas FROM recipes WHERE id=?",
                     (rid,))
    assert r is not None
    return (int(r["replay_ok"]), int(r["replay_fail"]), int(r["consecutive_fail"]), int(r["nao_aplicavel_seguidas"]))


def test_da_terceira_seguida_em_diante_cada_uma_conta_e_o_ok_e_a_falha_zeram(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "nao-aplicavel.sqlite3")
    store = RecipeStore(db)
    rid = _salva(store)
    assert NAO_APLICAVEL_CONTA_APOS == 3
    assert store.nao_aplicavel(rid) == (False, False) and _uso(store, rid) == (0, 0, 0, 1)
    assert store.nao_aplicavel(rid) == (False, False) and _uso(store, rid) == (0, 0, 0, 2)
    assert store.result(rid, True) is False and _uso(store, rid) == (1, 0, 0, 0)       # o ok zera a série
    store.nao_aplicavel(rid)
    store.nao_aplicavel(rid)
    assert store.nao_aplicavel(rid) == (True, False)                                   # a 3ª seguida é falha comum
    assert _uso(store, rid) == (1, 1, 1, 3)                                            # ...e NÃO zera a série
    assert store.nao_aplicavel(rid) == (True, False) and _uso(store, rid) == (1, 2, 2, 4)   # a 4ª também conta
    assert store.result(rid, True) is False and _uso(store, rid) == (2, 2, 0, 0)       # o ok zera as duas
    store.nao_aplicavel(rid)
    assert store.result(rid, False) is False and _uso(store, rid) == (2, 3, 1, 0)      # a falha comum zera a série
    db.close()


def test_so_nao_se_aplicou_leva_a_quarentena_na_quinta(tmp_path: Path) -> None:
    """R1 da segunda leitura: com a série zerando na 3ª, um 1º seletor quebrado de vez só chegava à quarentena na 9ª."""
    db = banco_migrado(tmp_path, "quinta.sqlite3")
    store = RecipeStore(db)
    rid = _salva(store)
    assert [store.nao_aplicavel(rid) for _ in range(5)] == [(False, False), (False, False), (True, False),
                                                             (True, False), (True, True)]
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (rid,)) == "quarantined"
    db.close()


def test_reativar_pelo_livro_zera_a_serie(tmp_path: Path) -> None:
    """N3 da segunda leitura: a reativação pelo livro zera `consecutive_fail` e também a série de "não se aplicou"."""
    db = banco_migrado(tmp_path, "reativar.sqlite3")
    mundo = Mundo(db)
    rid = _salva(mundo.store)
    for _ in range(5):
        mundo.store.nao_aplicavel(rid)
    assert mundo.status(rid) == "quarantined" and _uso(mundo.store, rid)[2:] == (3, 5)
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), SkillState.PUBLISHED, by=DONO, reason="consertei o app")
    assert mundo.status(rid) == "active" and _uso(mundo.store, rid)[2:] == (0, 0)
    db.close()


def test_alvo_ambiguo_nao_e_tela_de_partida_diferente() -> None:
    """N1 da segunda leitura: o mesmo texto cobre o ausente e o ambíguo; só o ausente pode ser a tela de partida."""
    tree = parse_hierarchy(XML)

    def proxima(rid: str) -> None:
        Replayer(recipe_id=1, version=1, variables={},
                 actions=[{"tool": "tap", "selectors": [{"kind": "rid", "rid": rid}]}]).next(tree)

    with pytest.raises(AlvoAusente, match="alvo ausente ou ambíguo"):
        proxima("app:id/nao_existe")
    with pytest.raises(RecipeDiverged, match="alvo ausente ou ambíguo") as caso:
        proxima("app:id/conversation_name")                                   # duas linhas: deixou de ser único
    assert not isinstance(caso.value, AlvoAusente)


def test_falhas_e_nao_aplicaveis_seguidos_ainda_levam_a_quarentena(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "quarentena.sqlite3")
    store = RecipeStore(db)
    rid = _salva(store)
    store.result(rid, False)
    store.result(rid, False)
    assert [store.nao_aplicavel(rid) for _ in range(3)] == [(False, False), (False, False), (True, True)]
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (rid,)) == "quarantined"
    db.close()


# ------------------------------------------------------------------ no executor: o aviso que cobre a tela de partida
def _replay(h: Harness) -> None:
    h.cfg.file.ai.recipes = "replay"


async def _aprende(h: Harness) -> int:
    _replay(h)
    assert (await h.wait_run(h.run(["android-01"]).id)).status == "completed"
    rid = h.state.db.scalar("SELECT id FROM recipes WHERE step_key='open_conversation' AND status='active'")  # type: ignore[union-attr]
    assert rid
    return int(rid)


def _parte_de_outra_conversa(h: Harness, iid: str, index: int, monkeypatch: pytest.MonkeyPatch) -> Any:
    """O caso real: a etapa de abrir a conversa parte de DENTRO de outra conversa. Quando a receita dela é achada (no
    começo da tentativa, antes da 1ª observação), o aparelho passa à conversa com QA-003; a receita procura QA-001 na
    lista e não acha."""
    fake = h._factory(type("RT", (), {"id": iid, "index": index})())
    find0 = RecipeStore.find

    def find(self: RecipeStore, *a: Any, **kw: Any) -> Any:
        row = find0(self, *a, **kw)
        if row is not None and row["step_key"] == "open_conversation" and fake.screen == "home":
            fake.screen, fake.contact = "chat", "QA-003"
        return row

    monkeypatch.setattr(RecipeStore, "find", find)
    return fake


def _etapa(h: Harness, run_id: str) -> Any:
    return h.state.db.one("SELECT driven_by, status FROM steps WHERE run_id=? AND key='open_conversation'",  # type: ignore[union-attr]
                          (run_id,))


def _eventos(h: Harness, run_id: str) -> list[str]:
    return [r["message"] for r in h.state.db.query(  # type: ignore[union-attr]
        "SELECT message FROM events WHERE run_id=? AND kind='decision'", (run_id,))]


async def test_tela_de_partida_diferente_com_a_etapa_comprovada_nao_conta(harness: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    rid = await _aprende(harness)
    store = RecipeStore(harness.state.db)                                     # type: ignore[union-attr]
    antes = _uso(store, rid)
    _parte_de_outra_conversa(harness, "android-03", 3, monkeypatch)
    run = await harness.wait_run(harness.run(["android-03"]).id)
    assert run.status == "completed"
    depois = _uso(store, rid)
    assert depois[:3] == antes[:3] and depois[3] == 1                          # nem ok nem falha: uma "não se aplicou"
    etapa = _etapa(harness, run.id)
    assert etapa["status"] == "succeeded" and etapa["driven_by"] == "ai"
    assert any("tela de partida diferente" in m and "não conta como falha" in m for m in _eventos(harness, run.id))
    # contável sem ler texto: o código estável e os ids no `data` do evento
    [dados] = [json.loads(r["data"]) for r in harness.state.db.query(  # type: ignore[union-attr]
        "SELECT data FROM events WHERE run_id=? AND kind='decision' AND data LIKE '%receita_nao_aplicavel%'", (run.id,))]
    assert (dados["kind"], dados["recipe_id"], dados["contou_como_falha"]) == ("receita_nao_aplicavel", rid, False)
    assert dados["step_id"] == harness.state.db.scalar(  # type: ignore[union-attr]
        "SELECT id FROM steps WHERE run_id=? AND key='open_conversation'", (run.id,))
    # `attempts.recipe_id` segue apontando a receita tentada (ela foi consultada); a evidência é que a ignora
    assert harness.state.db.scalar(  # type: ignore[union-attr]
        "SELECT a.recipe_id FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=? AND s.key='open_conversation'"
        " ORDER BY a.number DESC LIMIT 1", (run.id,)) == rid
    # o aprendizado não lê evidência contra (a linha só sai de `recipe`, `recipe+ai` e `sem_ator`)
    from app.modules.learning.infrastructure.reproducao_sql import _ETAPAS
    linhas = harness.state.db.query(_ETAPAS + " AND s.run_id=?", (run.id,))  # type: ignore[union-attr]
    assert not any(int(l["receita"]) == rid for l in linhas)
    # a receita roda de fato noutro aparelho (a tela de partida certa): o ok zera a série
    monkeypatch.undo()
    assert (await harness.wait_run(harness.run(["android-02"]).id)).status == "completed"
    assert _uso(store, rid)[0] == antes[0] + 1 and _uso(store, rid)[3] == 0


async def test_a_ia_assume_e_a_etapa_falha_conta_como_falha_comum(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    rid = await _aprende(harness)
    store = RecipeStore(harness.state.db)                                     # type: ignore[union-attr]
    antes = _uso(store, rid)
    _parte_de_outra_conversa(harness, "android-03", 3, monkeypatch)
    decide0 = harness.ai.inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "open_conversation":
            return Decision(tool="step_blocked", args={"rationale": "não dá", "kind": "unexpected_screen",
                                                       "reason": "tela inesperada", "needs_user": False}), Usage()
        return await decide0(req)

    harness.ai.inner.decide = decide
    run = await harness.wait_run(harness.run(["android-03"]).id, statuses=("completed", "completed_with_issues",
                                                                           "failed", "waiting_user", "needs_input"))
    assert run.status != "completed"
    depois = _uso(store, rid)
    assert depois[1] > antes[1] and depois[3] == 0                             # falha comum, como antes do 30.80
    assert not any("tela de partida diferente" in m and "não conta" in m for m in _eventos(harness, run.id))
