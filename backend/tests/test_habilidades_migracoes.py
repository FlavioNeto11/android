"""Migrações 042–046 (habilidades versionadas, casos de validação, ensino v2, trilha, versão congelada).

O que se prova aqui, nos dois bancos (o PostgreSQL quando `TEST_DATABASE_URL` existe):
- ATUALIZAÇÃO de um banco em 041 com dados: aplica exatamente as cinco, não toca o legado e as colunas novas nascem
  nulas nas linhas antigas;
- o esquema das tabelas novas é o MESMO num banco criado do zero e num atualizado (a lição da 008);
- o banco recusa duas publicadas da mesma habilidade, o mesmo comando publicado duas vezes, o mesmo fluxo adotado
  duas vezes e a mesma gravação em dois ensinos;
- a 046 recusa mudar ou apagar versão fora de rascunho e deixa passar a mudança de estado;
- a ferramenta de cópia entre bancos acha uma ordem por FK sem ciclo com as tabelas novas.

A comparação de esquema é por `db.columns()` e pelos nomes dos índices, e não por `sqlite_master`, para o teste
ser honesto também no PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import INTEGRITY_ERRORS, Database
from app.tools.migrate_data import _dependencias, ordem_por_fk, tabelas_a_copiar

from .test_db import _banco, _copia_das_migracoes, _Falso, _tem_indice

NOVAS = ("042_habilidades_versionadas", "043_casos_de_validacao", "044_ensino_v2", "045_trilha_da_habilidade",
         "046_versao_congelada")
TABELAS_NOVAS = ("skill_definitions", "skill_versions", "skill_version_transitions", "skill_version_apps",
                 "skill_scope", "skill_validation_cases", "skill_validation_results", "teaching_sessions",
                 "teaching_demonstrations", "teaching_turns", "teaching_candidates")
INDICES_NOVOS = ("ux_skill_definitions_fluxo", "ux_skill_versions_publicada", "ux_skill_versions_comando",
                 "ix_skill_versions_estado", "ix_skill_version_transitions", "ix_skill_version_apps_app",
                 "ix_skill_scope_skill", "ix_skill_validation_cases_skill", "ix_skill_validation_results",
                 "ix_skill_validation_results_run", "ix_teaching_sessions_status", "ux_teaching_demo_gravacao",
                 "ix_teaching_turns", "idx_runs_habilidade", "idx_steps_habilidade")
LEGADO = ("flows", "flow_required_apps", "flow_scope", "recipes", "training_sessions", "training_inputs", "runs",
          "objectives", "steps", "attempts", "ai_calls")
TS = "2026-09-20T12:00:00Z"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"


def _assinatura(db: Database, tabela: str) -> str:
    linhas = db.query(f"SELECT * FROM {tabela} ORDER BY 1")          # noqa: S608 - nome fixo do teste
    return hashlib.sha256(json.dumps(linhas, sort_keys=True, default=str).encode()).hexdigest()


def _semear_legado(db: Database) -> None:
    """Um retrato do que produção tem em 041: fluxo com app exigido e escopo, receita, treino, execução por fluxo."""
    plano = {"summary": "Curtir o post de {perfil}", "app_id": "instagram", "parameters": {"perfil": "{perfil}"},
             "steps": [{"key": "abrir", "title": "Abrir", "goal": "abrir",
                        "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}],
             "planner": {"provider": "fluxo", "model": "m", "simulated": True}}
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, created_at, source)"
               " VALUES (?,?,?,?,?,?,?,?)", ("curtir", "Curtir", "curtir {perfil}", "curtir {perfil}",
                                             json.dumps(plano), "instagram", TS, "run"))
    db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES ('curtir','instagram')")
    db.execute("INSERT INTO flow_scope(flow_id, profile_id) VALUES ('curtir','p1')")
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("com.instagram.android", "447", "sig", "pt/420", "h1", "abrir", 1, "active", "[]", TS))
    db.execute("INSERT INTO training_sessions(id, instance_id, intent, status, created_at, updated_at, flow_id)"
               " VALUES ('trn-1','android-01','curtir','saved',?,?,'curtir')", (TS, TS))
    db.execute("INSERT INTO training_inputs(session_id, seq, ts, type, x, y) VALUES ('trn-1', 1, ?, 'tap', 1, 2)",
               (TS,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, flow_id)"
               " VALUES ('r1','k1','curtir @nasa','execute','completed','[\"android-01\"]',?,'curtir')", (TS,))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES ('r1:android-01','r1','android-01',"
               "'succeeded')")
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("r1:android-01:v1:abrir", "r1", "r1:android-01", "android-01", 1, 0, "abrir", "Abrir", "abrir",
                "{}", 60, 3, "succeeded"))
    db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,1,'succeeded',?)",
               ("r1:android-01:v1:abrir:a1", "r1:android-01:v1:abrir", TS))
    db.execute("INSERT INTO ai_calls(ts, run_id, objective_id, step_id, role, model, tier, input_tokens,"
               " output_tokens, with_image, ms, ok) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (TS, "r1", "r1:android-01", "r1:android-01:v1:abrir", "decide", "m", 0, 10, 5, 0, 100, 1))


def _indices(db: Database) -> set[str]:
    return {n for n in INDICES_NOVOS if _tem_indice(db, n)}


def _gatilhos(db: Database) -> set[str]:
    if db.dialect == "sqlite":
        return {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='trigger'")}
    return {r["tgname"] for r in db.query(
        "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE NOT t.tgisinternal AND n.nspname = ANY (current_schemas(false))")}


def _esquema(db: Database) -> dict[str, list[str]]:
    tabelas = (*TABELAS_NOVAS, "runs", "objectives", "steps", "attempts", "ai_calls")
    return {t: sorted(db.columns(t)) for t in tabelas} | {"@indices": sorted(_indices(db)),
                                                         "@gatilhos": sorted(_gatilhos(db))}


def test_atualizacao_de_041_para_046_nao_toca_o_legado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate="041_loja_de_apps")
    db = _banco(tmp_path)
    try:
        assert "041_loja_de_apps" in db.migrate()
        _semear_legado(db)
        antes = {t: _assinatura(db, t) for t in LEGADO}
        for nome in NOVAS:
            shutil.copy2(ORIGEM / f"{nome}.sql", destino / f"{nome}.sql")
        assert db.migrate() == list(NOVAS)
        assert db.divergencias() == []
        # As colunas novas existem e nascem NULAS nas linhas antigas: execução anterior não ganha trilha inventada.
        assert {"skill_id", "skill_version", "skill_hash"} <= db.columns("runs")
        assert {"skill_id", "skill_version", "node_id", "strategy", "capability", "driven_by"} <= db.columns("steps")
        assert {"strategy", "recipe_id"} <= db.columns("attempts")
        assert "resource_plan" in db.columns("objectives")
        assert "attempt_id" in db.columns("ai_calls")
        assert "app_snapshot" in db.columns("teaching_demonstrations")
        assert db.one("SELECT skill_id, skill_version, skill_hash FROM runs WHERE id='r1'") == {
            "skill_id": None, "skill_version": None, "skill_hash": None}
        assert db.scalar("SELECT attempt_id FROM ai_calls") is None
        # O legado atravessa sem mudar (a assinatura inclui as colunas novas, todas nulas, nas tabelas alteradas).
        depois = {t: _assinatura(db, t) for t in LEGADO}
        mudaram = [t for t in LEGADO if antes[t] != depois[t] and t not in ("runs", "objectives", "steps",
                                                                           "attempts", "ai_calls")]
        assert mudaram == []
        for t in ("runs", "objectives", "steps", "attempts", "ai_calls"):
            linhas = db.query(f"SELECT * FROM {t}")                      # noqa: S608 - nome fixo do teste
            novas = db.columns(t) & {"skill_id", "skill_version", "skill_hash", "resource_plan", "node_id",
                                     "strategy", "recipe_id", "attempt_id"}
            assert novas and all(linha[c] is None for linha in linhas for c in novas), t
        assert db.migrate() == []                                    # e nada se repete
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_o_mesmo_esquema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate="041_loja_de_apps")
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    _semear_legado(atualizado)
    for nome in NOVAS:
        shutil.copy2(ORIGEM / f"{nome}.sql", destino / f"{nome}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-5:] == list(NOVAS)
        esquema = _esquema(novo)
        assert esquema == _esquema(atualizado)
        assert esquema["@indices"] == sorted(INDICES_NOVOS)
        assert {"skill_versions_congelada"} <= set(esquema["@gatilhos"])
        assert novo.divergencias() == [] and atualizado.divergencias() == []
    finally:
        novo.close()
        atualizado.close()


@pytest.fixture
def banco(tmp_path: Path) -> Database:
    db = _banco(tmp_path)
    db.migrate()
    db.execute("INSERT INTO skill_definitions(id, name, created_at, updated_at) VALUES ('ig.curtir','Curtir',?,?)",
               (TS, TS))
    db.execute("INSERT INTO skill_definitions(id, name, created_at, updated_at) VALUES ('ig.outra','Outra',?,?)",
               (TS, TS))
    return db


def _versao(db: Database, sid: str, v: int, estado: str, chave: str | None = "curtir {perfil}") -> None:
    db.execute("INSERT INTO skill_versions(id, skill_id, version, state, content, content_hash, match_key,"
               " source_kind, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (f"{sid}@{v}", sid, v, estado, "{}", "h", chave, "manual", TS, TS))


def test_o_banco_recusa_duas_publicadas_da_mesma_habilidade(banco: Database) -> None:
    try:
        _versao(banco, "ig.curtir", 1, "published")
        with pytest.raises(INTEGRITY_ERRORS):
            _versao(banco, "ig.curtir", 2, "published", "outro comando")
        _versao(banco, "ig.curtir", 2, "deprecated")                 # fora de `published`, quantas quiser
        _versao(banco, "ig.curtir", 3, "draft")
        assert banco.scalar("SELECT COUNT(*) FROM skill_versions WHERE skill_id='ig.curtir'") == 3
    finally:
        banco.close()


def test_o_banco_recusa_o_mesmo_comando_publicado_em_duas_habilidades(banco: Database) -> None:
    try:
        _versao(banco, "ig.curtir", 1, "published")
        with pytest.raises(INTEGRITY_ERRORS):
            _versao(banco, "ig.outra", 1, "published")
        _versao(banco, "ig.outra", 1, "validated")                   # o mesmo comando em rascunho ou validada: ok
        _versao(banco, "ig.outra", 2, "published", None)             # sem comando (só composição): não conflita
    finally:
        banco.close()


def test_o_banco_recusa_o_mesmo_fluxo_adotado_duas_vezes_e_a_mesma_gravacao_em_dois_ensinos(banco: Database) -> None:
    try:
        banco.execute("UPDATE skill_definitions SET legacy_flow_id='curtir' WHERE id='ig.curtir'")
        with pytest.raises(INTEGRITY_ERRORS):
            banco.execute("UPDATE skill_definitions SET legacy_flow_id='curtir' WHERE id='ig.outra'")
        banco.execute("INSERT INTO training_sessions(id, instance_id, intent, status, created_at, updated_at)"
                      " VALUES ('trn-1','android-01','curtir','recorded',?,?)", (TS, TS))
        for tid in ("ens-1", "ens-2"):
            banco.execute("INSERT INTO teaching_sessions(id, instruction, created_at, updated_at) VALUES (?,?,?,?)",
                          (tid, "curtir", TS, TS))
        banco.execute("INSERT INTO teaching_demonstrations(id, teaching_id, seq, training_session_id, created_at)"
                      " VALUES ('d1','ens-1',1,'trn-1',?)", (TS,))
        with pytest.raises(INTEGRITY_ERRORS):
            banco.execute("INSERT INTO teaching_demonstrations(id, teaching_id, seq, training_session_id,"
                          " created_at) VALUES ('d2','ens-2',1,'trn-1',?)", (TS,))
    finally:
        banco.close()


def test_a_046_recusa_mudar_ou_apagar_versao_fora_de_rascunho(banco: Database) -> None:
    try:
        _versao(banco, "ig.curtir", 1, "published")
        _versao(banco, "ig.curtir", 2, "draft")
        banco.execute("UPDATE skill_versions SET content='{\"a\":1}', content_hash='h2' WHERE id='ig.curtir@2'")
        # Transição de estado reescrevendo a linha inteira, sem mudar conteúdo: passa nos dois bancos.
        banco.execute("UPDATE skill_versions SET content=content, content_hash=content_hash WHERE id='ig.curtir@1'")
        banco.execute("UPDATE skill_versions SET state='deprecated', state_at=? WHERE id='ig.curtir@1'", (TS,))
        for sql in ("UPDATE skill_versions SET content='{}x' WHERE id='ig.curtir@1'",
                    "UPDATE skill_versions SET content_hash='outro' WHERE id='ig.curtir@1'",
                    "UPDATE skill_versions SET match_key='outro' WHERE id='ig.curtir@1'",
                    "UPDATE skill_versions SET version=9 WHERE id='ig.curtir@1'",
                    "DELETE FROM skill_versions WHERE id='ig.curtir@1'"):
            with pytest.raises(INTEGRITY_ERRORS):
                banco.execute(sql)
        assert banco.scalar("SELECT content FROM skill_versions WHERE id='ig.curtir@1'") == "{}"
        banco.execute("DELETE FROM skill_versions WHERE id='ig.curtir@2'")        # rascunho se apaga
        assert banco.one("SELECT id FROM skill_versions WHERE id='ig.curtir@2'") is None
    finally:
        banco.close()


def test_a_definicao_com_versao_nao_se_apaga(banco: Database) -> None:
    """Desligar, nunca apagar (§10.1): a FK sem `ON DELETE` recusa apagar a definição que tem versão."""
    try:
        _versao(banco, "ig.curtir", 1, "draft")
        with pytest.raises(INTEGRITY_ERRORS):
            banco.execute("DELETE FROM skill_definitions WHERE id='ig.curtir'")
        banco.execute("DELETE FROM skill_definitions WHERE id='ig.outra'")          # sem versão: pode
    finally:
        banco.close()


@pytest.mark.parametrize("dialeto, esperado", [("postgres", 2), ("sqlite", 2)])
def test_a_046_divide_em_duas_instrucoes_em_cada_dialeto(dialeto: str, esperado: int) -> None:
    falso = _Falso(dialeto)
    instrucoes = Database._instrucoes(falso.render((ORIGEM / "046_versao_congelada.sql").read_text("utf-8")))
    assert len(instrucoes) == esperado
    if dialeto == "postgres":
        assert instrucoes[0].startswith("CREATE OR REPLACE FUNCTION") and instrucoes[0].rstrip().endswith("$$")
        assert not any("RAISE(ABORT" in i for i in instrucoes)


@pytest.mark.parametrize("nome", NOVAS)
def test_cada_migracao_nova_renderiza_sem_marca_sobrando(nome: str) -> None:
    for dialeto in ("sqlite", "postgres"):
        texto = _Falso(dialeto).render((ORIGEM / f"{nome}.sql").read_text("utf-8"))
        assert "{{" not in texto
        assert Database._instrucoes(texto)


def test_a_copia_entre_bancos_acha_ordem_por_fk_com_as_tabelas_novas(banco: Database) -> None:
    try:
        tabelas = set(tabelas_a_copiar(banco, banco))
        assert set(TABELAS_NOVAS) <= tabelas
        dependencias = _dependencias(banco, tabelas)
        ordem = ordem_por_fk(banco, tabelas)
        for filho in TABELAS_NOVAS:
            assert all(ordem.index(pai) < ordem.index(filho) for pai in dependencias[filho] if pai != filho), filho
    finally:
        banco.close()
