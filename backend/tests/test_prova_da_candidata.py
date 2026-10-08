"""31.271: a prova da receita CANDIDATA (contrato aditivo `prova_da_candidata`, lido pela aba Aprendido no 31.270).

Antes, o banco não guardava a última consulta por receita, e a 222 ficou candidata com 0 de 2 concordâncias sem ninguém
ver por quê (31.262). Agora a loja grava, na própria receita, quando foi a última consulta e o resultado
(`concordou`, `divergiu`, `nao_aplicavel`, `outro_escopo`, `quarentena`; migração 129), e a linha do Livro traz
`prova_da_candidata` SÓ na candidata: as concordâncias seguidas, as necessárias (`ai.recipes_promote_after`), a última
consulta e a ATIVA da mesma chave que ela assume ao ser promovida.

Nível de prova: `simulated` (SQLite, loja e Livro reais, nenhum aparelho nem IA). `real`: `not_run`, pede o deploy e a
primeira operação com uma candidata em prova (o GET `/api/aprendizado` da candidata).
"""
from __future__ import annotations

from pathlib import Path

from app.db import Database
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.presentation.livro import _entrada
from app.taskqueue.recipes import NAO_APLICAVEL_CONTA_APOS, RecipeStore

from .fake_skills import banco as banco_migrado

PKG = "com.pocqa.messenger"
VERSAO = "1.0(1)"
CHAVE = {"signature": "", "variant": "en-US/xhdpi"}
ACOES = [{"tool": "tap", "commit": False, "why": "abrir", "args": {}, "selectors": [{"kind": "rid", "rid": "app:id/abrir"}]}]


def _candidata(store: RecipeStore, hash_: str = "h-abrir") -> int:
    rid = store.save(package=PKG, app_version=VERSAO, step_hash=hash_, step_key="abrir", learned_from="r-x:a:v1:abrir",
                     actions=ACOES, candidate=True, **CHAVE)
    assert rid is not None
    return rid


def _consulta(db: Database, rid: int) -> tuple[str | None, str | None]:
    r = db.one("SELECT ultima_consulta_em, ultima_consulta_resultado FROM recipes WHERE id=?", (rid,))
    assert r is not None
    return r["ultima_consulta_em"], r["ultima_consulta_resultado"]


def _ativa_da_mesma_chave(db: Database, hash_: str = "h-abrir") -> int:
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (PKG, VERSAO, CHAVE["signature"], CHAVE["variant"], hash_, "abrir", 7, "active", "[]", "r-x:a:v0:abrir",
                "2026-10-06T00:00:00.000Z"))
    return int(db.scalar("SELECT id FROM recipes WHERE status='active' AND step_hash=?", (hash_,)))


def test_o_veredito_da_sombra_grava_a_ultima_consulta(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "prova1.sqlite3")
    store = RecipeStore(db)
    rid = _candidata(store)
    assert _consulta(db, rid) == (None, None)                             # nunca consultada
    store.shadow(rid, True, promote_after=5)
    em, resultado = _consulta(db, rid)
    assert resultado == "concordou" and em
    store.shadow(rid, False, promote_after=5)
    assert _consulta(db, rid)[1] == "divergiu"
    db.close()


def test_a_concordancia_simulada_que_nao_conta_nao_grava_consulta(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "prova2.sqlite3")
    store = RecipeStore(db)
    rid = _candidata(store)
    store.shadow(rid, True, promote_after=5, simulada=True)               # RA-19 B: não soma à prova
    assert _consulta(db, rid) == (None, None)
    row = db.one("SELECT shadow_agree FROM recipes WHERE id=?", (rid,))
    assert row is not None and int(row["shadow_agree"]) == 0
    db.close()


def test_nao_aplicavel_grava_ate_a_serie_virar_divergencia(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "prova3.sqlite3")
    store = RecipeStore(db)
    rid = _candidata(store)
    for _ in range(NAO_APLICAVEL_CONTA_APOS - 1):
        assert store.nao_aplicavel_em_prova(rid) is False
        assert _consulta(db, rid)[1] == "nao_aplicavel"
    assert store.nao_aplicavel_em_prova(rid) is True                      # a 3ª seguida: o executor leva ao shadow
    assert _consulta(db, rid)[1] == "nao_aplicavel"                       # esta chamada não grava; o shadow grava
    store.shadow(rid, False, promote_after=5)
    assert _consulta(db, rid)[1] == "divergiu"
    db.close()


def test_a_consulta_de_outro_escopo_e_a_quarentena_gravam_na_receita_achada(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "prova4.sqlite3")
    store = RecipeStore(db)
    rid = _candidata(store)
    store._escopos[rid] = "proprio"                                       # aprendida no post da própria conta
    assert store.find(PKG, VERSAO, "h-abrir", escopo="terceiro", **CHAVE) is None
    assert _consulta(db, rid)[1] == "outro_escopo"
    # a quarentena é a consulta que não acha receita viva e acha a posta de lado
    db.execute("UPDATE recipes SET status='quarantined' WHERE id=?", (rid,))
    assert store.find(PKG, VERSAO, "h-abrir", **CHAVE) is None
    assert _consulta(db, rid)[1] == "quarentena"
    db.close()


def test_a_gravacao_que_falha_nao_derruba_a_consulta(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "prova5.sqlite3")
    store = RecipeStore(db)
    rid = _candidata(store)
    store._escopos[rid] = "proprio"

    def quebrado(*_a: object, **_k: object) -> None:
        raise RuntimeError("banco indisponível")

    db.execute = quebrado  # type: ignore[method-assign]
    assert store.find(PKG, VERSAO, "h-abrir", escopo="terceiro", **CHAVE) is None   # a consulta segue sem a marca
    db.close()


def test_o_livro_traz_a_prova_so_na_candidata_com_a_ativa_que_ela_substitui(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "prova6.sqlite3")
    store = RecipeStore(db)
    rid = _candidata(store)
    ativa = _ativa_da_mesma_chave(db)            # a ativa da mesma chave (a loja só grava candidata sem ativa)
    store.shadow(rid, True, promote_after=5)
    fontes = FontesSql(db, necessarias=lambda efeito: 2)

    entradas = {e.ref: e for e in fontes.receitas()}
    prova = entradas[str(rid)].prova_da_candidata
    assert prova is not None
    assert (prova.concordancias, prova.necessarias) == (1, 2)
    assert prova.ultima_consulta_resultado == "concordou" and prova.ultima_consulta_em
    assert (prova.substitui_ref, prova.substitui_versao) == (str(ativa), 7)
    assert entradas[str(ativa)].prova_da_candidata is None                # a ativa não tem prova a mostrar

    unica = fontes.receita(str(rid))                                      # o item aberto: a mesma resposta
    assert unica is not None and unica.prova_da_candidata == prova

    corpo = _entrada(entradas[str(rid)])
    assert corpo["prova_da_candidata"] == {
        "concordancias": 1, "necessarias": 2,
        "ultima_consulta": {"em": prova.ultima_consulta_em, "resultado": "concordou"},
        "substitui": {"ref": str(ativa), "versao": 7, "estado": "active"}}
    assert "prova_da_candidata" not in _entrada(entradas[str(ativa)])
    db.close()


def test_candidata_sem_consulta_nem_ativa_sai_com_nulos_e_sem_a_configuracao_nao_inventa_o_total(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "prova7.sqlite3")
    rid = _candidata(RecipeStore(db), "h-sozinha")
    entrada = FontesSql(db).receita(str(rid))                             # sem `necessarias`
    assert entrada is not None
    assert _entrada(entrada)["prova_da_candidata"] == {
        "concordancias": 0, "necessarias": None, "ultima_consulta": None, "substitui": None}
    db.close()
