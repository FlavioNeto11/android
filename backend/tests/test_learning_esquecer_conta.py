"""`esquecer_conta` do aprendizado (item 29.23, frente Aprendizado): o rastro textual da conta removida vira
`[conta removida]` nas tabelas `learning_*`, sem apagar linha, sem tocar hash, id nem receitas/fluxos/memória, dentro
da transação de quem chama.

Nível de prova: `simulated` (banco de teste migrado; valores fictícios, nenhuma conta real).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning import esquecer_conta as exportada
from app.modules.learning.infrastructure.esquecer_conta import MARCADOR, esquecer_conta

from .fake_skills import banco as banco_migrado

TS = "2026-10-02T12:00:00Z"
HANDLE = "ana_silva"
ACCOUNT = "acc-fake1234"
TABELAS = {"learning_items", "learning_evidence", "learning_transitions", "learning_backlog", "learning_reviews",
           "learning_signals"}


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return banco_migrado(tmp_path, "esquecer-conta.sqlite3")


def _chamar(db: Database, handle: str = HANDLE, account_id: str = ACCOUNT) -> dict[str, int]:
    return esquecer_conta(db, profile_id="per-fake", account_id=account_id, handle=handle, app_id="com.fake.app")


def _item(db: Database, ident: str, *, content: str, summary: str, provenance: str = "{}",
          content_hash: str = "h-1", profile: str = "per-fake") -> None:
    db.execute(
        "INSERT INTO learning_items (id, kind, scope_app, scope_profile_id, content, content_hash, summary, "
        "source_kind, provenance, created_by, created_at) VALUES (?, 'voz', 'com.fake.app', ?, ?, ?, ?, 'teste', ?, "
        "'sistema', ?)", (ident, profile, content, content_hash, summary, provenance, TS))


def _evidencia(db: Database, item: str, origem: str, detail: str | None, stance: str = "for") -> None:
    db.execute("INSERT INTO learning_evidence (item_ref, stance, origin_ref, simulated, detail, observed_at) "
               "VALUES (?, ?, ?, 1, ?, ?)", (item, stance, origem, detail, TS))


def _transicao(db: Database, item: str, motivo: str) -> None:
    db.execute("INSERT INTO learning_transitions (item_ref, item_kind, to_state, reason, decided_by, decided_at) "
               "VALUES (?, 'voz', 'published', ?, 'sistema', ?)", (item, motivo, TS))


def _backlog(db: Database, ident: str, *, title: str, notes: str | None, tela: str | None = None,
             baseline: str | None = None) -> None:
    db.execute("INSERT INTO learning_backlog (id, category, cluster_key, title, failure_screen, notes, baseline, "
               "first_seen, last_seen) VALUES (?, 'falha', ?, ?, ?, ?, ?, ?, ?)",
               (ident, f"chave-{ident}", title, tela, notes, baseline, TS, TS))


def _revisao(db: Database, ident: str, *, dossie: str, saida: str | None, motivo: str | None) -> None:
    db.execute("INSERT INTO learning_reviews (id, created_at, item_ref, item_kind, gatilho, dossie_hash, dossie, "
               "template_id, template_versao, saida, override_motivo) VALUES (?, ?, 'li-1', 'voz', 'manual', ?, ?, "
               "'t', '1', ?, ?)", (ident, TS, f"dh-{ident}", dossie, saida, motivo))


def _sinal(db: Database, nota: str | None, dados: str, fonte: str = "run:r1") -> None:
    db.execute("INSERT INTO learning_signals (kind, note, source_ref, created_by, data, instance_id, profile_id, "
               "created_at) VALUES ('feedback', ?, ?, 'sistema', ?, 'inst-1', 'per-fake', ?)",
               (nota, fonte, dados, TS))


def _popular(db: Database) -> None:
    _item(db, "li-1", content=json.dumps({"texto": f"seguir @{HANDLE} e {ACCOUNT}"}), summary=f"curtir {HANDLE}",
          provenance=json.dumps({"conta": ACCOUNT}))
    _evidencia(db, "li-1", "signal:1", f"visto em @{HANDLE}")
    _transicao(db, "li-1", f"publicado porque {HANDLE} aprovou")
    _backlog(db, "fk-1", title=f"falha na conta {HANDLE}", notes=f"ver {ACCOUNT}", tela="tela do @" + HANDLE,
             baseline=json.dumps({"quem": HANDLE}))
    _revisao(db, "lr-1", dossie=json.dumps({"conta": f"@{HANDLE}"}), saida=json.dumps({"risco": f"{HANDLE} bloqueada"}),
             motivo=f"{ACCOUNT} caiu")
    _sinal(db, f"nota sobre @{HANDLE}", json.dumps({"id": ACCOUNT}))


def _linha(db: Database, tabela: str, ident: str | int) -> dict:
    row = db.one(f"SELECT * FROM {tabela} WHERE id = ?", (ident,))
    assert row is not None
    return row


def test_exportada_no_pacote() -> None:
    assert exportada is esquecer_conta


def test_rastro_em_cada_tabela_vira_marcador(db: Database) -> None:
    _popular(db)

    retorno = _chamar(db)

    assert retorno == {t: 1 for t in TABELAS}
    item = _linha(db, "learning_items", "li-1")
    assert json.loads(item["content"]) == {"texto": f"seguir {MARCADOR} e {MARCADOR}"}   # continua JSON válido
    assert item["summary"] == f"curtir {MARCADOR}"
    assert json.loads(item["provenance"]) == {"conta": MARCADOR}
    ev = db.one("SELECT detail, origin_ref FROM learning_evidence")
    assert ev == {"detail": f"visto em {MARCADOR}", "origin_ref": "signal:1"}
    assert db.scalar("SELECT reason FROM learning_transitions") == f"publicado porque {MARCADOR} aprovou"
    bk = _linha(db, "learning_backlog", "fk-1")
    assert (bk["title"], bk["notes"], bk["failure_screen"]) == (f"falha na conta {MARCADOR}", f"ver {MARCADOR}",
                                                               f"tela do {MARCADOR}")
    assert json.loads(bk["baseline"]) == {"quem": MARCADOR}
    rv = _linha(db, "learning_reviews", "lr-1")
    assert json.loads(rv["dossie"]) == {"conta": MARCADOR}
    assert json.loads(rv["saida"]) == {"risco": f"{MARCADOR} bloqueada"}
    assert rv["override_motivo"] == f"{MARCADOR} caiu"
    sinal = db.one("SELECT note, data FROM learning_signals")
    assert sinal["note"] == f"nota sobre {MARCADOR}"
    assert json.loads(sinal["data"]) == {"id": MARCADOR}


def test_nao_apaga_linha_nem_muda_hash_nem_ids(db: Database) -> None:
    _popular(db)
    antes = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in TABELAS}
    chaves_antes = (db.one("SELECT id, content_hash, scope_profile_id, scope_app, state FROM learning_items"),
                    db.one("SELECT dossie_hash, item_ref FROM learning_reviews"),
                    db.one("SELECT source_ref, instance_id, profile_id FROM learning_signals"),
                    db.one("SELECT cluster_key, app_package FROM learning_backlog"))

    _chamar(db)

    assert {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in TABELAS} == antes
    assert (db.one("SELECT id, content_hash, scope_profile_id, scope_app, state FROM learning_items"),
            db.one("SELECT dossie_hash, item_ref FROM learning_reviews"),
            db.one("SELECT source_ref, instance_id, profile_id FROM learning_signals"),
            db.one("SELECT cluster_key, app_package FROM learning_backlog")) == chaves_antes


def test_fronteira_de_palavra_nao_estraga_banana(db: Database) -> None:
    textos = ["banana e bananas", "ana.silva e ana_silva2 e luciana", "foo@ana.com", "x.ana", "ana2"]
    for i, t in enumerate(textos):
        _item(db, f"li-{i}", content="{}", summary=t, content_hash=f"h-{i}")
    _item(db, "li-alvo", content="{}", summary="falei com ana. Depois (@ana), ana! e @Ana?", content_hash="h-alvo")

    retorno = _chamar(db, handle="ana", account_id="")

    assert retorno["learning_items"] == 1
    for i, t in enumerate(textos):
        assert _linha(db, "learning_items", f"li-{i}")["summary"] == t
    assert _linha(db, "learning_items", "li-alvo")["summary"] == (
        f"falei com {MARCADOR}. Depois ({MARCADOR}), {MARCADOR}! e {MARCADOR}?")


def test_handle_com_arroba_e_sublinhado_e_ponto(db: Database) -> None:
    _item(db, "li-a", content="{}", summary="@ana.silva e ana_silva e ana.silva.", content_hash="h-a")
    _item(db, "li-b", content="{}", summary="ana.silva2 e ana.silvas e ana silva", content_hash="h-b")

    r1 = _chamar(db, handle="@ana.silva", account_id="")
    r2 = _chamar(db, handle="ana_silva", account_id="")

    assert (r1["learning_items"], r2["learning_items"]) == (1, 1)
    assert _linha(db, "learning_items", "li-a")["summary"] == f"{MARCADOR} e {MARCADOR} e {MARCADOR}."
    assert _linha(db, "learning_items", "li-b")["summary"] == "ana.silva2 e ana.silvas e ana silva"


def test_id_da_conta_casa_exato(db: Database) -> None:
    _item(db, "li-a", content="{}", summary="acc-fake1234 e acc-fake12345 e acc-fake1234-x e Acc-Fake1234",
          content_hash="h-a")

    _chamar(db, handle="", account_id=ACCOUNT)

    assert _linha(db, "learning_items", "li-a")["summary"] == (
        f"{MARCADOR} e acc-fake12345 e acc-fake1234-x e Acc-Fake1234")


def test_handle_vazio_ou_so_arroba_nao_faz_nada(db: Database) -> None:
    _popular(db)
    antes = db.query("SELECT * FROM learning_items")

    for vazio in ("", "@", "  @@ "):
        assert _chamar(db, handle=vazio, account_id="") == {t: 0 for t in TABELAS}

    assert db.query("SELECT * FROM learning_items") == antes


def test_idempotente_e_sem_rastro_no_retorno(db: Database) -> None:
    _popular(db)

    primeira = _chamar(db)
    segunda = _chamar(db)

    assert sum(primeira.values()) == len(TABELAS)
    assert segunda == {t: 0 for t in TABELAS}
    assert HANDLE not in json.dumps(primeira) and ACCOUNT not in json.dumps(primeira)
    assert HANDLE not in json.dumps(segunda) and ACCOUNT not in json.dumps(segunda)


def test_idempotente_mesmo_com_handle_dentro_do_marcador(db: Database) -> None:
    """O handle `conta` casaria com o próprio `[conta removida]`; a troca não pode aninhar o marcador."""
    _item(db, "li-a", content="{}", summary="a conta saiu; @conta caiu", content_hash="h-a")

    _chamar(db, handle="conta", account_id="")
    _chamar(db, handle="conta", account_id="")

    assert _linha(db, "learning_items", "li-a")["summary"] == f"a {MARCADOR} saiu; {MARCADOR} caiu"


def test_nao_toca_receitas_fluxos_nem_memoria(db: Database) -> None:
    _popular(db)
    db.execute("INSERT INTO recipes (app_package, app_version, step_hash, step_key, actions, created_at) "
               "VALUES ('com.fake.app', '1', 'sh', ?, ?, ?)", (f"curtir {HANDLE}", f'["abrir @{HANDLE}"]', TS))
    # K-084: `flows.id` é TEXT PRIMARY KEY sem padrão; o SQLite aceitava o id nulo, a PG não.
    db.execute("INSERT INTO flows (id, name, match_key, command_template, plan, created_at) VALUES ('fluxo-fake', ?, 'mk', "
               "?, ?, ?)", (f"fluxo {HANDLE}", f"curtir {HANDLE}", f'["{ACCOUNT}"]', TS))
    db.execute("INSERT INTO instagram_profiles (id, created_at, updated_at) VALUES ('per-fake', ?, ?)", (TS, TS))
    db.execute("INSERT INTO memory_items (id, profile_id, subject, content, source, fingerprint, created_at, "
               "updated_at) VALUES ('mem-1', 'per-fake', 'fato', ?, 'teste', 'fp', ?, ?)",
               (f"{HANDLE} gosta de café", TS, TS))
    antes = (db.query("SELECT * FROM recipes"), db.query("SELECT * FROM flows"), db.query("SELECT * FROM memory_items"))

    _chamar(db)

    assert (db.query("SELECT * FROM recipes"), db.query("SELECT * FROM flows"),
            db.query("SELECT * FROM memory_items")) == antes


def test_nao_comita_a_transacao_do_chamador(db: Database) -> None:
    _popular(db)

    with pytest.raises(RuntimeError):
        with db.tx():
            assert _chamar(db)["learning_items"] == 1
            assert db.scalar("SELECT summary FROM learning_items") == f"curtir {MARCADOR}"
            raise RuntimeError("o chamador desfaz")

    assert db.scalar("SELECT summary FROM learning_items") == f"curtir {HANDLE}"
    assert db.scalar("SELECT reason FROM learning_transitions") == f"publicado porque {HANDLE} aprovou"


def test_sem_nada_a_fazer_devolve_zeros(db: Database) -> None:
    assert _chamar(db) == {t: 0 for t in TABELAS}


def test_troca_que_colidiria_no_indice_unico_e_pulada(db: Database) -> None:
    """`learning_evidence` é única em (item, origem, posição): duas origens que só diferem pela conta não podem virar
    a mesma, ou o `IntegrityError` derrubaria a transação do chamador."""
    _evidencia(db, "li-1", f"conta:{ACCOUNT}", None)
    _evidencia(db, "li-1", f"conta:{ACCOUNT}-x", "depois do prefixo")
    _evidencia(db, "li-1", f"conta:{MARCADOR}", f"detalhe {HANDLE}")

    retorno = _chamar(db)

    assert retorno["learning_evidence"] == 1                      # só a linha do detalhe; a origem colidiria
    origens = sorted(r["origin_ref"] for r in db.query("SELECT origin_ref FROM learning_evidence"))
    assert origens == sorted([f"conta:{ACCOUNT}", f"conta:{ACCOUNT}-x", f"conta:{MARCADOR}"])
    assert _chamar(db)["learning_evidence"] == 0
