"""RA-20 (item 29.40): a causa do "ausente" medida e a herança da receita provada, na loja (`RecipeStore.find`).

Prova `simulated`: banco migrado de teste, sem aparelho. O ciclo inteiro (herdar, concordar, voltar a agir) está em
`test_recipes.py::test_herda_da_versao_anterior`; aqui ficam as guardas.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.db import Database, dumps
from app.metricas import metricas
from app.taskqueue.recipes import MudancaDaReceita, ReceitaVista, RecipeStore

from .fake_skills import banco as banco_migrado

PKG = "com.pocqa.messenger"
HASH = "h-abrir"
AQUI = {"signature": "cd924c49", "variant": "en-US/xhdpi"}
ACOES = [{"tool": "tap", "commit": False, "why": "abrir", "args": {}, "selectors": [{"kind": "rid", "rid": "app:id/x"}]}]


def _ligada() -> bool:
    """A loja sozinha não herda (quem liga é o executor, com a prova em sombra): aqui a herança é ligada à mão."""
    return True


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "heranca.sqlite3")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def _metricas_limpas() -> None:
    metricas.limpar()


def _receita(db: Database, *, status: str = "active", versao: str = "1.0(1)", signature: str = "cd924c49",
             variant: str = "en-US/xhdpi", acoes: list[dict[str, Any]] | None = None) -> int:
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (PKG, versao, signature, variant, HASH, "abrir", 1, status, dumps(acoes or ACOES), "run-x:a1:v1:abrir",
         "2026-09-17T18:00:00Z")) or 0)


def _procura(store: RecipeStore, versao: str = "2.0(7)", **chave: str) -> Any:
    return store.find(PKG, versao, HASH, **{**AQUI, **chave})


def _herdeiras(db: Database) -> list[dict[str, Any]]:
    return [dict(r) for r in db.query("SELECT * FROM recipes WHERE app_version='2.0(7)' ORDER BY id")]


class Ouvinte:
    def __init__(self, *, veta: bool = False) -> None:
        self.veta = veta
        self.mudancas: list[MudancaDaReceita] = []

    def vetada(self, receita: ReceitaVista) -> bool:
        return self.veta

    def exige_o_dono(self, recipe_id: int, receita: ReceitaVista) -> bool | None:
        return False

    def mudou(self, mudanca: MudancaDaReceita) -> None:
        self.mudancas.append(mudanca)


def test_a_legada_de_17_09_doa_para_a_chave_completa(db: Database) -> None:
    legada = _receita(db, signature="", variant="")                 # as 19 de 17/09: sem assinatura nem variante
    ouvinte = Ouvinte()
    store = RecipeStore(db, ouvinte, herdar=_ligada)
    row = _procura(store, versao="1.0(1)")
    assert row is not None and row["status"] == "candidate" and row["id"] != legada
    assert (row["app_signature"], row["variant"], row["learned_from_step"]) == ("cd924c49", "en-US/xhdpi",
                                                                                 "run-x:a1:v1:abrir")
    assert [m.motivo for m in ouvinte.mudancas] == [f"herdada da receita {legada} (legada); em prova (sombra)"]
    assert metricas.valor("receita.consulta", resultado="herdada") == 1
    assert metricas.valor("receita.ausente", causa="legada") == 1
    # a próxima consulta já acha a herdeira como a candidata de sempre, e ninguém herda de novo
    assert _procura(store, versao="1.0(1)")["id"] == row["id"]
    assert metricas.valor("receita.consulta", resultado="candidata") == 1
    assert db.scalar("SELECT COUNT(*) FROM recipes") == 2


def test_herda_da_versao_anterior_e_a_ativa_mais_nova(db: Database) -> None:
    _receita(db, versao="0.9(1)")
    nova = _receita(db, versao="1.0(1)", acoes=[{**ACOES[0], "why": "da 1.0"}])
    row = _procura(RecipeStore(db, herdar=_ligada))
    assert row is not None and row["status"] == "candidate"
    assert row["actions"] == dumps([{**ACOES[0], "why": "da 1.0"}]) and nova
    assert metricas.valor("receita.ausente", causa="versao") == 1


@pytest.mark.parametrize("caso", ["quarentena", "candidata", "outra_assinatura", "espera_o_dono", "posta_de_lado",
                                  "desligado", "vetada", "sem_receita"])
def test_quem_nunca_doa(db: Database, caso: str) -> None:
    ouvinte = Ouvinte(veta=caso == "vetada")
    if caso == "quarentena":
        _receita(db, status="quarantined")
    elif caso == "candidata":
        _receita(db, status="candidate")                             # só a provada doa
    elif caso == "outra_assinatura":
        _receita(db, signature="outro-apk")
    elif caso == "espera_o_dono":
        _receita(db)
        _receita(db, status="validated", versao="2.0(7)")            # a chave espera o dono: não se contorna
    elif caso == "posta_de_lado":
        _receita(db)
        _receita(db, status="superseded", versao="2.0(7)")
    elif caso in ("desligado", "vetada"):
        _receita(db)
    store = RecipeStore(db, ouvinte, herdar=lambda: caso != "desligado")
    assert _procura(store) is None
    assert [r["status"] for r in _herdeiras(db)] == {"espera_o_dono": ["validated"],
                                                      "posta_de_lado": ["superseded"]}.get(caso, [])
    assert metricas.valor("receita.consulta", resultado="ausente") == 1
    causa = {"quarentena": "sem_receita", "candidata": "versao", "outra_assinatura": "assinatura",
             "espera_o_dono": "espera_o_dono", "posta_de_lado": "desligada", "desligado": "versao", "vetada": "versao",
             "sem_receita": "sem_receita"}[caso]
    assert metricas.valor("receita.ausente", causa=causa) == 1


def test_dois_aparelhos_herdando_juntos_dividem_a_mesma_candidata(db: Database) -> None:
    _receita(db)
    a, b = RecipeStore(db, herdar=_ligada), RecipeStore(db, herdar=_ligada)
    primeira = _procura(a)
    segunda = _procura(b)
    assert primeira["id"] == segunda["id"] and len(_herdeiras(db)) == 1


def test_falha_na_heranca_vira_ausente_e_nao_derruba_a_consulta(db: Database) -> None:
    _receita(db)

    def quebra() -> bool:
        raise RuntimeError("config ilegível")

    assert _procura(RecipeStore(db, herdar=quebra)) is None
    assert metricas.valor("receita.consulta", resultado="ausente") == 1 and _herdeiras(db) == []


def test_a_herdeira_provada_na_chave_completa_aposenta_a_legada(db: Database) -> None:
    """As 19 de 17/09: a legada ativa não casava mais nenhuma consulta. Quando a herdeira se prova na chave completa
    e passa a agir, a legada sai (`superseded`) com a trilha; antes disso (e na que espera o dono) ela fica."""
    legada = _receita(db, signature="", variant="")
    ouvinte = Ouvinte()
    store = RecipeStore(db, ouvinte, herdar=_ligada)
    herdeira = _procura(store, versao="1.0(1)")
    assert store.shadow(herdeira["id"], True, promote_after=2) is False
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (legada,)) == "active"
    assert store.shadow(herdeira["id"], True, promote_after=2) is True
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (legada,)) == "superseded"
    assert ouvinte.mudancas[-1].recipe_id == legada and "provou-se na chave completa" in ouvinte.mudancas[-1].motivo
    assert db.scalar("SELECT COUNT(*) FROM recipes WHERE status='active' AND variant=''") == 0


def test_com_efeito_a_legada_espera_a_decisao_do_dono(db: Database) -> None:
    envio = [{**ACOES[0], "commit": True}]
    legada = _receita(db, signature="", variant="", acoes=envio)
    store = RecipeStore(db, Ouvinte(), herdar=_ligada)
    herdeira = _procura(store, versao="1.0(1)")
    for _ in range(2):
        assert store.shadow(herdeira["id"], True, promote_after=2) is False
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (herdeira["id"],)) == "validated"
    assert db.scalar("SELECT status FROM recipes WHERE id=?", (legada,)) == "active"


def test_aposenta_so_a_legada_da_familia_da_provada(db: Database) -> None:
    """A ativa de OUTRA assinatura com a variante vazia e a de OUTRA variante sem assinatura atendem a consulta de quem
    tem aquelas chaves: não são a legada desta chave e ficam (achado da Android no PR #131)."""
    legada = _receita(db, signature="", variant="")
    sem_variante = _receita(db, signature=AQUI["signature"], variant="")
    outra_assinatura = _receita(db, signature="ff00ff00", variant="")
    outra_variante = _receita(db, signature="", variant="pt-BR/hdpi")
    store = RecipeStore(db, Ouvinte(), herdar=_ligada)
    herdeira = _procura(store, versao="1.0(1)")
    for _ in range(2):
        store.shadow(herdeira["id"], True, promote_after=2)
    status = {r["id"]: r["status"] for r in db.query("SELECT id, status FROM recipes")}
    assert status[herdeira["id"]] == "active"
    assert (status[legada], status[sem_variante]) == ("superseded", "superseded")
    assert (status[outra_assinatura], status[outra_variante]) == ("active", "active")
