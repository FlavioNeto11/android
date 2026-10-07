"""29.199: o diagnóstico "onde o servidor PG gasta CPU" (scripts/pg-diagnostico.py), com docker e pytest de mentira: nada de contêiner nem de carga."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("pg_diagnostico", Path(__file__).resolve().parents[1] / "pg-diagnostico.py")
diag = importlib.util.module_from_spec(_SPEC)
sys.modules["pg_diagnostico"] = diag
_SPEC.loader.exec_module(diag)


def _ok(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


def _linha(query: str, total_ms: float, calls: int = 10, rows: int = 0) -> dict:
    return {"query": query, "total_ms": total_ms, "calls": calls, "media_ms": total_ms / calls, "rows": rows}


# Os comandos reais do harness (tests/esquema_do_worker.py), na forma normalizada do pg_stat_statements.
HARNESS = {
    "truncate": 'TRUNCATE "w1"."a", "w1"."b" RESTART IDENTITY CASCADE',
    "impressao": "SELECT md5(coalesce(string_agg(x, $1 ORDER BY x), $2)) AS h FROM (SELECT $3 || table_name FROM information_schema.columns WHERE table_schema = $4) q",
    "reseed": 'INSERT INTO "w1"."a" ("id", "nome") OVERRIDING SYSTEM VALUE SELECT "id", "nome" FROM "w1m"."a"',
    "sequencia": "SELECT setval($1::regclass, $2, $3)",
    "terminate": "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE application_name = $1 AND pid <> pg_backend_pid()",
    "migracoes": 'SELECT version, checksum FROM "w1".schema_migrations ORDER BY version',
}


@pytest.mark.parametrize("categoria,query", list(HARNESS.items()))
def test_os_comandos_do_harness_caem_na_categoria_certa(categoria, query):
    assert diag.classificar(query) == categoria


@pytest.mark.parametrize("query,categoria", [
    ("INSERT INTO instances (id, nome) VALUES ($1, $2)", "aplicacao"),
    ("SELECT * FROM executions WHERE id = $1", "aplicacao"),
    ("UPDATE steps SET status = $1 WHERE id = $2", "aplicacao"),
    ("CREATE TABLE x (id int)", "ddl"),
    ("ALTER TABLE x ADD COLUMN y int", "ddl"),
    ("SET lock_timeout = '2s'", "sessao"),
    ("BEGIN", "sessao"),
    ("COMMIT", "sessao"),
])
def test_o_resto_e_aplicacao_ddl_ou_sessao(query, categoria):
    assert diag.classificar(query) == categoria


def test_resumo_soma_por_categoria_e_a_porcentagem_fecha_em_100():
    linhas = [_linha(HARNESS["truncate"], 4000), _linha(HARNESS["impressao"], 3000), _linha("SELECT * FROM executions WHERE id = $1", 3000)]
    r = diag.resumir(linhas)
    assert r["total_ms"] == 10000
    assert r["categorias"]["truncate"]["pct"] == pytest.approx(40.0) and r["categorias"]["aplicacao"]["pct"] == pytest.approx(30.0)
    assert sum(e["pct"] for e in r["categorias"].values()) == pytest.approx(100.0)
    assert r["harness_pct"] == pytest.approx(70.0) and r["topo"][0]["categoria"] == "truncate"


@pytest.mark.parametrize("harness_ms,aplicacao_ms,trecho", [
    (400, 600, "VALE o código"),            # 40 %
    (350, 650, "VALE o código"),            # 35 %: o limite vale
    (100, 900, "O ITEM MORRE"),             # 10 %
    (149, 851, "O ITEM MORRE"),             # 14,9 %
    (150, 850, "ZONA CINZENTA"),            # 15 %: ainda não morre
    (250, 750, "ZONA CINZENTA"),
])
def test_veredito_pelos_limites_do_29_199(harness_ms, aplicacao_ms, trecho):
    r = diag.resumir([_linha(HARNESS["truncate"], harness_ms), _linha("SELECT * FROM executions WHERE id = $1", aplicacao_ms)])
    assert trecho in r["veredito"]


def test_sem_dados_nao_conclui_nada():
    r = diag.resumir([])
    assert r["total_ms"] == 0 and "sem dados" in r["veredito"]


def test_ddl_e_sessao_nao_contam_como_harness():
    r = diag.resumir([_linha("CREATE TABLE x (id int)", 500), _linha("BEGIN", 500)])
    assert r["harness_pct"] == 0 and "O ITEM MORRE" in r["veredito"]


def test_tabela_md_traz_veredito_categorias_e_os_comandos_mais_caros():
    linhas = [_linha(HARNESS["truncate"], 5000, calls=1000), _linha("SELECT * FROM a WHERE x = $1 | y", 1000, calls=50)]
    md = diag.tabela_md(diag.resumir(linhas), quantos=5)
    assert "**Veredito:** VALE o código" in md and "| truncate (harness) | 5.0 | 83.3 | 1000 |" in md
    assert md.index("TRUNCATE") < md.index("SELECT * FROM a"), "ordem por tempo"
    assert "x = $1 \\| y" in md, "a barra vertical do comando não quebra a tabela"


def _pg_com_raiz(tmp_path):
    pg = diag._carregar_pg_rapido()
    pg.RAIZ = tmp_path
    return pg


def test_docker_run_do_diagnostico_tem_nome_porta_tmpfs_e_a_extensao(tmp_path):
    cmd = diag.comando_docker_run(_pg_com_raiz(tmp_path))
    assert cmd[cmd.index("--name") + 1] == "farm-pg-diag" and cmd[cmd.index("-p") + 1] == "127.0.0.1:55440:5432"
    assert cmd[cmd.index("--tmpfs") + 1].endswith("size=2g")
    assert "shared_preload_libraries=pg_stat_statements" in cmd and "wal_level=minimal" in cmd and "fsync=off" in cmd
    assert "farm-pg-rapido" not in cmd, "não toca o contêiner da suíte"


def test_simular_nao_chama_docker_e_diz_o_plano(tmp_path, monkeypatch, capsys):
    pg = _pg_com_raiz(tmp_path)
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    (tmp_path / "backend" / "tests" / "test_a.py").write_text("")
    lista = tmp_path / "lista.txt"
    lista.write_text("tests/test_a.py\ntests/sumiu.py\n", encoding="utf-8")
    monkeypatch.setattr(diag, "_carregar_pg_rapido", lambda: pg)
    monkeypatch.setattr(pg, "_executar", lambda cmd: pytest.fail(f"docker chamado: {cmd}"))
    assert diag.main(["--lista", str(lista), "--workers", "6", "--simular"]) == 0
    out = capsys.readouterr().out
    assert "docker run" in out and "farm-pg-diag" in out and "pytest -n 6 em 1 arquivos" in out and "parar" in out


def test_lista_sem_teste_existente_nao_roda(tmp_path, monkeypatch, capsys):
    pg = _pg_com_raiz(tmp_path)
    lista = tmp_path / "lista.txt"
    lista.write_text("tests/nao_existe.py\n", encoding="utf-8")
    monkeypatch.setattr(diag, "_carregar_pg_rapido", lambda: pg)
    monkeypatch.setattr(pg, "_executar", lambda cmd: pytest.fail("docker chamado"))
    assert diag.main(["--lista", str(lista)]) == 2
    assert "NÃO RODOU" in capsys.readouterr().out


def test_ler_devolve_as_linhas_do_json_e_falha_alto_se_o_psql_falha():
    saida = json.dumps([_linha("SELECT 1", 5.0)])
    assert diag.ler(lambda cmd: _ok(saida))[0]["query"] == "SELECT 1"
    assert diag.ler(lambda cmd: _ok("")) == []
    with pytest.raises(RuntimeError, match="falhou"):
        diag.ler(lambda cmd: _ok(rc=1, stderr="no such container"))


def test_main_ler_imprime_a_tabela_e_grava_md_e_json(tmp_path, monkeypatch, capsys):
    pg = _pg_com_raiz(tmp_path)
    saida = json.dumps([_linha(HARNESS["truncate"], 4000), _linha("SELECT * FROM executions WHERE id = $1", 6000)])
    monkeypatch.setattr(diag, "_carregar_pg_rapido", lambda: pg)
    monkeypatch.setattr(pg, "_executar", lambda cmd: _ok(saida))
    alvo = tmp_path / "saida" / "tabela.md"
    assert diag.main(["--ler", "--saida", str(alvo)]) == 0
    assert "VALE o código" in capsys.readouterr().out and "VALE o código" in alvo.read_text(encoding="utf-8")
    dados = json.loads(alvo.with_suffix(".json").read_text(encoding="utf-8"))
    assert dados["harness_pct"] == pytest.approx(40.0) and "categorias" in dados


def test_main_ler_sem_dados_sai_com_2(tmp_path, monkeypatch):
    pg = _pg_com_raiz(tmp_path)
    monkeypatch.setattr(diag, "_carregar_pg_rapido", lambda: pg)
    monkeypatch.setattr(pg, "_executar", lambda cmd: _ok("[]"))
    assert diag.main(["--ler"]) == 2


def test_subir_cria_a_extensao_zera_as_estatisticas_e_recusa_o_que_falha(tmp_path):
    pg = _pg_com_raiz(tmp_path)
    chamadas: list[list[str]] = []

    def docker(cmd, rc_ext=0):
        chamadas.append(list(cmd))
        if "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" in cmd:
            return _ok(rc=rc_ext, stderr="could not open extension")
        return _ok()
    linhas: list[str] = []
    assert diag.subir(pg, docker, lambda s: None, linhas.append)
    textos = [" ".join(c) for c in chamadas]
    assert textos[0] == "docker rm -f farm-pg-diag" and textos[1].startswith("docker run")
    assert any("pg_isready" in t for t in textos) and textos[-1].endswith("pg_stat_statements_reset()")
    assert not diag.subir(pg, lambda cmd: docker(cmd, rc_ext=1), lambda s: None, linhas.append)
    assert any("CREATE EXTENSION" in ln for ln in linhas)
    assert not diag.subir(pg, lambda cmd: _ok(rc=125, stderr="port is already allocated") if cmd[1] == "run" else _ok(), lambda s: None, linhas.append)
    assert any("port is already allocated" in ln for ln in linhas)
