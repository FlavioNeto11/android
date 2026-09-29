"""Receita nasce CANDIDATA e só passa a agir depois de provar-se por repetição (pedido do dono, rodada pós-ADR-053).

Antes, uma única execução limpa gravava a receita já `active`: a execução seguinte repetia por seletores, sem IA, um
caminho visto uma vez. O modo sombra media a concordância e nada o usava; `superseded` nunca era gravado.

Agora:
- a receita aprendida pela IA nasce `candidate`. Enquanto candidata, a IA decide a etapa e a receita só é COMPARADA
  (sombra) — o mesmo custo de uma etapa sem receita;
- a comparação vale por EXECUÇÃO da etapa (não por decisão): concordar é a IA ter feito exatamente o caminho da
  receita, até declarar a etapa pronta onde ela acaba, e a etapa ter sido comprovada;
- `ai.recipes_promote_after` concordâncias seguidas promovem a `active`; uma divergência recomeça a prova;
- a receita nova de uma chave marca a anterior (candidata que divergiu, ou em quarentena) como `superseded`;
- receitas `active` que já existiam continuam ativas — o parque não regride.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.automation.hierarchy import parse_hierarchy
from app.db import dumps, loads
from app.metricas import metricas
from app.planning.provider import Decision
from app.taskqueue.executor import _RecipeRun
from app.taskqueue.recipes import RecipeStore, Replayer

from .conftest import Harness
from .test_desbravador import _decisoes
from .test_recipes import XML
from .test_revisao_receitas import _rotulos_fechados

PKG = "com.pocqa.messenger"
CHAVE = {"signature": "", "variant": "en-US/xhdpi"}
PASSOS = ("open_conversation", "compose_message", "send_message")


def _acoes(rid: str = "app:id/conversation_name") -> list[dict[str, Any]]:
    return [{"tool": "tap", "commit": False, "why": "abrir", "args": {}, "selectors": [{"kind": "rid", "rid": rid}]}]


def _salva(store: RecipeStore, **kw: Any) -> int | None:
    return store.save(package=PKG, app_version="1.0(1)", step_hash="h-abrir", step_key="abrir",
                      actions=kw.pop("actions", _acoes()), learned_from=kw.pop("learned_from", "s1"), **CHAVE, **kw)


def _procura(store: RecipeStore) -> Any:
    return store.find(PKG, "1.0(1)", "h-abrir", **CHAVE)


def _linha(harness: Harness, rid: int) -> Any:
    return harness.state.db.one("SELECT * FROM recipes WHERE id=?", (rid,))       # type: ignore[union-attr]


# ================================================================== RecipeStore
async def test_receita_nova_nasce_candidata_e_a_busca_diz_que_e_candidata(harness: Harness) -> None:
    metricas.limpar()
    store = RecipeStore(harness.state.db)                                   # type: ignore[union-attr]
    rid = _salva(store, candidate=True)
    assert rid
    assert _linha(harness, rid)["status"] == "candidate"
    achada = _procura(store)
    assert achada is not None and achada["id"] == rid and achada["status"] == "candidate"
    # o funil não a conta como "encontrada": encontrada promete um veredito de reprodução, e candidata não reproduz
    assert metricas.valor("receita.consulta", resultado="candidata") == 1
    assert metricas.valor("receita.consulta", resultado="encontrada") == 0


async def test_duas_concordancias_seguidas_promovem_a_ativa(harness: Harness) -> None:
    store = RecipeStore(harness.state.db)                                   # type: ignore[union-attr]
    rid = _salva(store, candidate=True)
    assert rid
    assert store.shadow(rid, True, promote_after=2) is False
    linha = _linha(harness, rid)
    assert (linha["status"], linha["shadow_agree"], linha["shadow_total"]) == ("candidate", 1, 1)
    assert store.shadow(rid, True, promote_after=2) is True                 # 2ª concordância seguida: promovida
    linha = _linha(harness, rid)
    assert (linha["status"], linha["shadow_agree"], linha["shadow_total"]) == ("active", 2, 2)
    metricas.limpar()
    assert _procura(store)["id"] == rid
    assert metricas.valor("receita.consulta", resultado="encontrada") == 1


async def test_divergencia_zera_a_contagem_da_candidata(harness: Harness) -> None:
    store = RecipeStore(harness.state.db)                                   # type: ignore[union-attr]
    rid = _salva(store, candidate=True)
    assert rid
    assert store.shadow(rid, True, promote_after=2) is False
    assert store.shadow(rid, False, promote_after=2) is False               # divergiu: a prova recomeça
    linha = _linha(harness, rid)
    assert (linha["status"], linha["shadow_agree"], linha["shadow_total"]) == ("candidate", 0, 0)
    assert store.shadow(rid, True, promote_after=2) is False                # 1 depois da divergência não basta
    assert _linha(harness, rid)["status"] == "candidate"
    assert store.shadow(rid, True, promote_after=2) is True
    assert _linha(harness, rid)["status"] == "active"


async def test_receita_nova_da_mesma_chave_marca_a_antiga_como_substituida(harness: Harness) -> None:
    store = RecipeStore(harness.state.db)                                   # type: ignore[union-attr]
    v1 = _salva(store, candidate=True)
    assert v1
    # outro aparelho aprendendo em paralelo, sem ter comparado com a v1: não troca a candidata que está em prova
    assert _salva(store, candidate=True, learned_from="s2") is None
    assert _linha(harness, v1)["status"] == "candidate"
    # a v1 divergiu e a IA comprovou outro caminho: ele a substitui
    v2 = _salva(store, candidate=True, learned_from="s3", replaces=v1, actions=_acoes("app:id/outra_linha"))
    assert v2 and v2 != v1
    assert _linha(harness, v1)["status"] == "superseded"
    assert (_linha(harness, v2)["status"], _linha(harness, v2)["version"]) == ("candidate", 2)
    assert _procura(store)["id"] == v2
    # substituída não é promovida, nem se uma concordância atrasada (outro aparelho) chegar depois
    assert store.shadow(v1, True, promote_after=1) is False
    assert _linha(harness, v1)["status"] == "superseded"

    # ativa que caiu em quarentena: a próxima receita aprendida a substitui
    assert store.shadow(v2, True, promote_after=1) is True
    assert [store.result(v2, False) for _ in range(3)] == [False, False, True]
    assert _linha(harness, v2)["status"] == "quarantined"
    v3 = _salva(store, candidate=True, learned_from="s4")
    assert v3
    assert _linha(harness, v2)["status"] == "superseded"
    assert _linha(harness, v3)["status"] == "candidate"
    statuses = [r["status"] for r in harness.state.db.query(                # type: ignore[union-attr]
        "SELECT status FROM recipes WHERE step_hash='h-abrir' ORDER BY version")]
    assert statuses == ["superseded", "superseded", "candidate"]           # uma só receita viva por chave


async def test_ativas_que_ja_existiam_continuam_ativas(harness: Harness) -> None:
    db = harness.state.db                                                   # type: ignore[union-attr]
    # receita gravada antes desta mudança: INSERT sem status (o padrão da tabela é 'active')
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " actions, created_at) VALUES (?,?,?,?,?,?,1,?,?)",
               (PKG, "1.0(1)", "", "en-US/xhdpi", "h-abrir", "abrir", dumps(_acoes()), "2026-09-20T10:00:00+00:00"))
    antiga = db.one("SELECT id FROM recipes WHERE step_hash='h-abrir'")["id"]
    store = RecipeStore(db)
    assert _salva(store, candidate=True) is None                            # a ativa só sai por quarentena
    metricas.limpar()
    achada = _procura(store)
    assert achada["id"] == antiga and achada["status"] == "active"
    assert metricas.valor("receita.consulta", resultado="encontrada") == 1
    # comparada em sombra (modo `shadow` global), a ativa só acumula a taxa: nem zera, nem muda de status
    assert store.shadow(antiga, True, promote_after=2) is False
    assert store.shadow(antiga, False, promote_after=2) is False
    linha = _linha(harness, antiga)
    assert (linha["status"], linha["shadow_agree"], linha["shadow_total"]) == ("active", 1, 2)


async def test_ensino_da_pessoa_segue_nascendo_ativa_e_substitui_a_candidata(harness: Harness) -> None:
    """`save` sem `candidate` é o caminho do modo treinamento (a pessoa demonstrou) e o de `recipes_promote_after: 0`."""
    store = RecipeStore(harness.state.db)                                   # type: ignore[union-attr]
    v1 = _salva(store, candidate=True)
    v2 = _salva(store, learned_from="training:t1")
    assert v1 and v2
    assert _linha(harness, v2)["status"] == "active"
    assert _linha(harness, v1)["status"] == "superseded"


# ================================================================== comparação da sombra, decisão a decisão
_ACOES_DA_CONVERSA = [
    {"tool": "tap", "commit": False, "why": "abrir", "args": {},
     "selectors": [{"kind": "rid+text", "rid": "app:id/conversation_name", "text": "{recipient}"}]},
    {"tool": "type_text", "commit": False, "why": "digitar",
     "args": {"text": "{message}", "clear_first": True, "press_enter": False},
     "selectors": [{"kind": "rid", "rid": "app:id/message_input"}]},
]


def _compara(harness: Harness, decisoes: list[Decision]) -> _RecipeRun:
    """O que o laço do executor faz em sombra: compara cada decisão da IA enquanto a receita não divergiu."""
    ex = harness.state.scheduler.executor                                   # type: ignore[union-attr]
    rr = _RecipeRun(mode="shadow", replayer=Replayer(recipe_id=1, version=1, actions=_ACOES_DA_CONVERSA,
                                                     variables={"recipient": "QA-002", "message": "Oi android-02"}))
    obs = SimpleNamespace(tree=parse_hierarchy(XML))
    for d in decisoes:
        if rr.diverged:
            break
        ex._shadow_compare(rr, obs, d)
    return rr


def _d(tool: str, **args: Any) -> Decision:
    return Decision(tool=tool, args={"rationale": "teste", **args})


async def test_sombra_ignora_leitura_de_tela_e_confere_o_texto_digitado(harness: Harness) -> None:
    # e2 = linha "QA-002", e4 = campo da mensagem (XML de test_recipes)
    caminho = [_d("tap", element_id="e2"), _d("type_text", element_id="e4", text="Oi android-02")]
    # olhar a tela (observe/wait/verify) não é caminho: a receita nunca grava leitura — concordou até o fim
    rr = _compara(harness, [_d("observe_screen"), caminho[0], _d("wait_for", text=None, seconds=1), caminho[1],
                            _d("verify_state"), _d("step_done")])
    assert rr.diverged is None and rr.replayer is not None and rr.replayer.exhausted
    # mesmo campo, outro texto: a receita digitaria o que a IA não digitou
    rr = _compara(harness, [caminho[0], _d("type_text", element_id="e4", text="outra coisa")])
    assert rr.diverged
    # outra conversa
    assert _compara(harness, [_d("tap", element_id="e1")]).diverged
    # declarou pronta antes do fim da receita
    assert _compara(harness, [caminho[0], _d("step_done")]).diverged
    # ação a mais depois do fim da receita
    assert _compara(harness, [*caminho, _d("tap", element_id="e3")]).diverged
    # aparelho já no estado final (K-004): pronta sem nenhuma ação — nem concorda nem diverge
    rr = _compara(harness, [_d("step_done")])
    assert rr.diverged is None and rr.replayer is not None and not rr.replayer.exhausted


async def test_veredito_por_execucao_comprovada_no_meio_da_receita_e_divergencia(harness: Harness) -> None:
    """Sem isto a candidata ficaria sem veredito para sempre: nunca concorda (a receita não se esgota), nunca diverge
    (a IA não fez nada diferente) — nem promovida nem trocada."""
    ex = harness.state.scheduler.executor                                   # type: ignore[union-attr]
    rid = _salva(ex.recipes, candidate=True, actions=_ACOES_DA_CONVERSA)
    assert rid
    assert ex.recipes.shadow(rid, True, promote_after=2) is False           # já tinha uma concordância
    etapa = SimpleNamespace(title="abrir", id="s-1")

    rr = _compara(harness, [_d("step_done")])                                # já no estado final: neutro
    rr.row = _linha(harness, rid)
    ex._veredito_da_sombra(rr, True, "r-1", "android-01", etapa)
    assert rr.diverged is None
    assert (_linha(harness, rid)["shadow_agree"], _linha(harness, rid)["shadow_total"]) == (1, 1)

    rr = _compara(harness, [_d("tap", element_id="e2")])                     # comprovada sem a 2ª ação da receita
    rr.row = _linha(harness, rid)
    ex._veredito_da_sombra(rr, True, "r-1", "android-01", etapa)
    assert rr.diverged and "1 de 2" in rr.diverged
    linha = _linha(harness, rid)
    assert (linha["status"], linha["shadow_agree"], linha["shadow_total"]) == ("candidate", 0, 0)

    rr = _compara(harness, [_d("tap", element_id="e2")])                     # falhou no meio: sem veredito
    rr.row = _linha(harness, rid)
    ex._veredito_da_sombra(rr, False, "r-1", "android-01", etapa)
    assert rr.diverged is None and _linha(harness, rid)["shadow_total"] == 0


# ================================================================== executor, de ponta a ponta
def _receitas(h: Harness) -> dict[str, Any]:
    return {r["step_key"]: r for r in h.state.db.query(                     # type: ignore[union-attr]
        "SELECT * FROM recipes WHERE status <> 'superseded' ORDER BY version")}


def _driven(h: Harness, run_id: str) -> dict[str, Any]:
    return {r["key"]: r["driven_by"] for r in h.state.db.query(             # type: ignore[union-attr]
        "SELECT key, driven_by FROM steps WHERE run_id=?", (run_id,))}


async def test_candidata_so_age_depois_de_concordar_com_a_ia_em_execucoes_seguidas(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.ai.recipes_promote_after = 2
    metricas.limpar()

    r1 = await harness.wait_run(harness.run(["android-01"]).id)
    assert r1.status == "completed"
    receitas = _receitas(harness)
    assert set(PASSOS) <= set(receitas)
    assert {receitas[k]["status"] for k in PASSOS} == {"candidate"}
    assert any("candidata" in m for m in _decisoes(harness, r1.id))

    # 2ª execução: a candidata NÃO age — a IA conduz cada etapa e a receita só é comparada
    harness.ai.calls.clear()
    r2 = await harness.wait_run(harness.run(["android-02"]).id)
    assert r2.status == "completed" and len(harness.fakes["android-02"].messages) == 1
    for k in PASSOS:
        assert harness.ai.count("decide", step=k) >= 1, k
    assert {v for k, v in _driven(harness, r2.id).items() if k in PASSOS} == {"ai"}
    receitas = _receitas(harness)
    for k in PASSOS:
        assert (receitas[k]["status"], receitas[k]["shadow_agree"], receitas[k]["shadow_total"]) == ("candidate", 1, 1), k
    # a sombra não é reprodução: nenhuma ação saiu da receita e nenhuma tentativa aponta para ela
    db = harness.state.db                                                   # type: ignore[union-attr]
    assert db.scalar("SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON"
                     " s.id=t.step_id WHERE s.run_id=? AND a.source='recipe'", (r2.id,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM attempts t JOIN steps s ON s.id=t.step_id WHERE s.run_id=?"
                     " AND t.recipe_id IS NOT NULL", (r2.id,)) == 0
    assert metricas.total("receita.reproducao") == 0

    # 3ª: segunda concordância seguida → ativa, e a linha do tempo diz por quê
    r3 = await harness.wait_run(harness.run(["android-03"]).id)
    assert r3.status == "completed"
    receitas = _receitas(harness)
    assert {receitas[k]["status"] for k in PASSOS} == {"active"}
    assert sum("promovida" in m for m in _decisoes(harness, r3.id)) >= len(PASSOS)

    # 4ª: agora sim, repete por seletores, sem IA nas etapas com receita
    harness.ai.calls.clear()
    r4 = await harness.wait_run(harness.run(["android-01"]).id)
    assert r4.status == "completed"
    for k in PASSOS:
        assert harness.ai.count("decide", step=k) == 0, k
    driven = _driven(harness, r4.id)
    assert {driven[k] for k in PASSOS} == {"recipe"}
    assert metricas.valor("receita.consulta", resultado="candidata") >= 2 * len(PASSOS)
    _rotulos_fechados()


async def test_candidata_que_diverge_e_substituida_pelo_caminho_que_a_ia_comprovou(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.ai.recipes_promote_after = 2
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    db = harness.state.db                                                   # type: ignore[union-attr]
    v1 = db.one("SELECT id, version, actions FROM recipes WHERE step_key='open_conversation' AND status='candidate'")
    acoes = loads(v1["actions"])
    acoes[0]["selectors"] = [{"kind": "rid", "rid": "app:id/nao_existe_mais"}]     # a tela mudou para a candidata
    acoes[0].pop("scroll", None)
    db.execute("UPDATE recipes SET actions=? WHERE id=?", (dumps(acoes), v1["id"]))

    r2 = await harness.wait_run(harness.run(["android-02"]).id)
    assert r2.status == "completed"
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (v1["id"],)) == "superseded"
    v2 = db.one("SELECT * FROM recipes WHERE step_key='open_conversation' AND status='candidate'")
    assert v2 is not None and v2["version"] == v1["version"] + 1
    assert (v2["shadow_agree"], v2["shadow_total"]) == (0, 0)               # a substituta começa a prova do zero
    assert "nao_existe_mais" not in v2["actions"]
    # as outras etapas concordaram e seguem em prova, sem troca
    for k in ("compose_message", "send_message"):
        linha = db.one("SELECT status, version, shadow_agree FROM recipes WHERE step_key=? AND status<>'superseded'", (k,))
        assert (linha["status"], linha["version"], linha["shadow_agree"]) == ("candidate", 1, 1), k


async def test_com_promocao_ligada_as_ativas_de_antes_seguem_reproduzindo(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.ai.recipes_promote_after = 0                           # como antes: aprendida já nasce ativa
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    assert {r["status"] for r in _receitas(harness).values()} == {"active"}

    harness.cfg.file.ai.recipes_promote_after = 2                           # a mudança chega ao parque
    harness.ai.calls.clear()
    r2 = await harness.wait_run(harness.run(["android-02"]).id)
    assert r2.status == "completed"
    for k in PASSOS:
        assert harness.ai.count("decide", step=k) == 0, k
    assert {_driven(harness, r2.id)[k] for k in PASSOS} == {"recipe"}
    assert {r["status"] for r in _receitas(harness).values()} == {"active"}
    assert harness.state.db.scalar(                                         # type: ignore[union-attr]
        "SELECT COUNT(*) FROM recipes WHERE status IN ('candidate','superseded')") == 0
