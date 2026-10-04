"""`scripts/latencia-por-etapa.py` (item 31.24): a leitura da latência por etapa, só números e ids.

Nível de prova: `simulated` — um banco de teste com linhas montadas à mão (o esquema da 088 pela fábrica, e um esquema
de antes dela escrito aqui). Nada disto mede a latência do ambiente real.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Database  # noqa: E402

_spec = importlib.util.spec_from_file_location("latencia_por_etapa", ROOT / "scripts" / "latencia-por-etapa.py")
assert _spec is not None and _spec.loader is not None
lat = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lat          # o `@dataclass` do script procura o próprio módulo aqui
_spec.loader.exec_module(lat)

T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
SEGREDO = "comando-com-texto-do-dono"


def t(s: float) -> str:
    return lat.iso(T0 + timedelta(seconds=s))


def _banco_088(tmp: Path) -> Path:
    caminho = tmp / "poc.sqlite3"
    db = Database(caminho)               # o script só lê SQLite: o esquema é o das migrações de verdade
    db.migrate()
    db.close()
    return caminho


def _povoar(caminho: Path, *, com_088: bool = True) -> None:
    c = sqlite3.connect(caminho)
    run = ("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, started_at,"
           " finished_at) VALUES (?,?,?,?,?,?,?,?,?)")
    c.execute(run, ("r-1", f"validacao:lv-1:{SEGREDO}", SEGREDO, "plan", "completed", "[]", t(0), t(0), t(30)))
    c.execute(run, ("r-bat", f"eval-1:{SEGREDO}", SEGREDO, "plan", "completed", "[]", t(0), t(0), t(10)))
    c.execute("INSERT INTO objectives(id, run_id, instance_id, status, finished_at)"
              " VALUES ('o-1','r-1','a1','succeeded',?)", (t(30),))
    c.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
              " postcondition, timeout_s, max_attempts, status)"
              " VALUES ('s-1','r-1','o-1','a1',1,1,'k','t','g','{}',60,3,'succeeded')")
    c.execute("INSERT INTO attempts(id, step_id, number, status, started_at, finished_at, strategy"
              + (", juiz_espera_ms, verificacao_ms, evidencia_ms" if com_088 else "") + ") VALUES ('a-1','s-1',1,"
              "'succeeded',?,?,'ai_actor'" + (",1500,4000,600" if com_088 else "") + ")", (t(6), t(20)))
    novas = (", started_at, vaga_ms, prep_settle_ms, prep_observacao_ms, prep_arvore_ms, prep_imagem_ms,"
             " prep_prompt_ms")
    cols = "id, ts, run_id, attempt_id, role, origem, model, ms, ok" + (novas if com_088 else "")
    marcas = ",".join("?" * (16 if com_088 else 9))
    extra_plano = (t(1), 0, None, None, None, None, None) if com_088 else ()
    c.execute(f"INSERT INTO ai_calls({cols}) VALUES ({marcas})",
              (1, t(5), "r-1", None, "plan", "execucao", "claude-sonnet-5-5", 4000, 1, *extra_plano))
    # 1º decide: entregue em 7, volta em 9 (1,8 s de ida e volta); preparo desde o início da tentativa (6): 1 s
    extra_1 = (t(7), 5, None, 900, 700, None, 40) if com_088 else ()
    c.execute(f"INSERT INTO ai_calls({cols}) VALUES ({marcas})",
              (2, t(9), "r-1", "a-1", "decide", "execucao", "claude-sonnet-5", 1800, 1, *extra_1))
    # 2º decide: entregue em 10,5 (a ação acabou em 9,2: reobservar 1,3 s), volta em 12
    extra_2 = (t(10.5), 0, 600, 600, 500, None, 30) if com_088 else ()
    c.execute(f"INSERT INTO ai_calls({cols}) VALUES ({marcas})",
              (3, t(12), "r-1", "a-1", "decide", "execucao", "claude-sonnet-5", 1500, 1, *extra_2))
    c.execute(f"INSERT INTO ai_calls({cols}) VALUES ({marcas})",
              (4, t(14), "r-1", "a-1", "verify", "execucao", "claude-haiku-4-5", 1500, 1,
               *((t(12.5), 0, None, None, None, None, None) if com_088 else ())))
    c.execute(f"INSERT INTO ai_calls({cols}) VALUES ({marcas})",           # a bateria: fora com --sem-bateria
              (5, t(4), "r-bat", None, "plan", "execucao", "claude-opus-5-5", 9000, 1,
               *((t(-5), 0, None, None, None, None, None) if com_088 else ())))
    acao = ("INSERT INTO actions(attempt_id, seq, tool, args, status, side_effect, intent_at, done_at, source"
            + (", ai_call_id" if com_088 else "") + ") VALUES (" + ",".join("?" * (10 if com_088 else 9)) + ")")
    c.execute(acao, ("a-1", 1, "tap", "{}", "done", 0, t(9.002), t(9.2), "ai", *((2,) if com_088 else ())))
    c.execute(acao, ("a-1", 2, "step_done", "{}", "done", 0, t(12.001), t(12.002), "ai", *((3,) if com_088 else ())))
    if com_088:
        c.execute("INSERT INTO esperas(run_id, objective_id, motivo, inicio, fim) VALUES ('r-1','o-1','aparelho',?,?)",
                  (t(5), t(6)))
        c.execute("INSERT INTO esperas(run_id, objective_id, motivo, inicio, fim) VALUES ('r-1','o-1','pessoa',?,NULL)",
                  (t(21),))
    c.commit()
    c.close()


def _rel(caminho: Path, **kw: object) -> dict[str, object]:
    with lat.abrir(caminho) as conn:
        return lat.montar(conn, desde=T0 - timedelta(hours=1), ate=T0 + timedelta(hours=1), **kw)


def test_fases_somam_a_parede_e_a_acao_casa_pela_chamada(tmp_path: Path) -> None:
    caminho = _banco_088(tmp_path)
    _povoar(caminho)
    rel = _rel(caminho, sem_bateria=True)
    assert rel["colunas_ausentes"] == [] and rel["execucoes_por_classe"] == {"validacao": 1, "bateria": 1}
    normais = rel["parede"]["normais"]
    assert normais["n"] == 1                                             # a bateria saiu
    fases = {k: v["total_s"] for k, v in normais["fases"].items()}
    assert abs(sum(fases.values()) - 30.0) < 1e-6                        # as fases somam a parede
    assert abs(fases["plano"] - 4.0) < 1e-6 and abs(fases["antes_do_plano"] - 1.0) < 1e-6
    # dentro da tentativa (6→20): ator 7→9 e 10,5→12, juiz 12,5→14 (com a 088, a chamada conta desde a entrega)
    assert abs(fases["tent_ia"] - 5.0) < 1e-6 and abs(fases["tent_aparelho"] - 0.198) < 1e-3
    dpa = rel["decisao_para_acao"]
    assert dpa["metodo"] == "actions.ai_call_id"
    assert dpa["fim_da_acao_ate_proximo_decide_ms"]["p50"] == 1300.0     # 9,2 → 10,5
    assert dpa["fim_do_decide_ate_intencao_ms"]["p50"] == 1.0    # 2 ms e 1 ms: o posto mais próximo (31.60), não 1,5
    prep = rel["preparo"]["partes_ms"]
    assert prep["total"]["n"] == 2 and prep["total"]["soma"] == 1000.0 + 1300.0
    assert prep["outros"]["soma"] == (1000 - 900 - 40 - 5) + (1300 - 600 - 600 - 30)
    assert rel["tentativa"]["ai_actor succeeded"]["verificacao_ms"]["p50"] == 4000.0
    esp = rel["esperas"]
    assert esp["abertas"] == 1 and esp["por_motivo_s"]["aparelho"]["soma"] == 1.0
    assert esp["por_motivo_s"]["pessoa"]["soma"] == 9.0                  # aberta: fecha no fim do objetivo (30)
    pen = rel["pendurada"]
    assert pen["confiavel"] is True and pen["penduradas"] == []


def test_banco_antes_da_088_degrada_e_diz_o_que_falta(tmp_path: Path) -> None:
    caminho = tmp_path / "antes.sqlite3"
    c = sqlite3.connect(caminho)
    c.executescript("""
        CREATE TABLE runs(id TEXT, idempotency_key TEXT, command TEXT, mode TEXT, status TEXT, instance_ids TEXT,
                          created_at TEXT, started_at TEXT, finished_at TEXT);
        CREATE TABLE objectives(id TEXT, run_id TEXT, instance_id TEXT, status TEXT, finished_at TEXT);
        CREATE TABLE steps(id TEXT, run_id TEXT, objective_id TEXT, instance_id TEXT, plan_version INT, seq INT,
                           key TEXT, title TEXT, goal TEXT, postcondition TEXT, timeout_s INT, max_attempts INT,
                           status TEXT);
        CREATE TABLE attempts(id TEXT, step_id TEXT, number INT, status TEXT, started_at TEXT, finished_at TEXT,
                              strategy TEXT);
        CREATE TABLE ai_calls(id INTEGER PRIMARY KEY, ts TEXT, run_id TEXT, attempt_id TEXT, role TEXT, origem TEXT,
                              model TEXT, ms INT, ok INT);
        CREATE TABLE actions(id INTEGER PRIMARY KEY, attempt_id TEXT, seq INT, tool TEXT, args TEXT, status TEXT,
                             side_effect INT, intent_at TEXT, done_at TEXT, source TEXT);
        CREATE TABLE measurements(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, data TEXT);
        CREATE TABLE pending_approvals(id TEXT, objective_id TEXT, created_at TEXT, decided_at TEXT);
    """)
    c.close()
    _povoar(caminho, com_088=False)
    rel = _rel(caminho, sem_bateria=False)
    assert "ai_calls.started_at" in rel["colunas_ausentes"] and "esperas.motivo" in rel["colunas_ausentes"]
    assert rel["preparo"] == {} and rel["tentativa"] == {} and rel["esperas"]["por_motivo_s"] == {}
    assert rel["decisao_para_acao"]["metodo"].startswith("tentativa e ordem")
    assert rel["parede"]["normais"]["n"] == 2                            # sem --sem-bateria, a bateria fica
    assert rel["pendurada"]["confiavel"] is False and rel["pendurada"]["nota"]


def test_main_grava_json_e_md_sem_texto_de_comando(tmp_path: Path) -> None:
    caminho = _banco_088(tmp_path)
    _povoar(caminho)
    saida, md = tmp_path / "saida.json", tmp_path / "saida.md"
    assert lat.main(["--db", str(caminho), "--desde", t(-3600), "--ate", t(3600), "--sem-bateria",
                     "--json", str(saida), "--md", str(md)]) == 0
    texto = saida.read_text(encoding="utf-8") + md.read_text(encoding="utf-8")
    assert SEGREDO not in texto and "lv-1" not in texto                 # da chave, só o prefixo vira classe
    rel = json.loads(saida.read_text(encoding="utf-8"))
    assert rel["ia"]["por_funcao"]["ator"]["n"] == 2
    assert "| ator · claude-sonnet-5 | 2 |" in md.read_text(encoding="utf-8")


def test_banco_inexistente_nao_e_criado(tmp_path: Path) -> None:
    falta = tmp_path / "nao-existe.sqlite3"
    try:
        lat.abrir(falta)
    except SystemExit as exc:
        assert "não encontrado" in str(exc)
    assert not falta.exists()


def test_31_60_percentil_e_o_posto_mais_proximo_do_backend() -> None:
    """31.60: o script usa o posto mais próximo de `app.metricas.percentil` (K-085), não a interpolação de antes. n=2, 6 e
    10: o p50 e o p95 são amostras que aconteceram, iguais às do backend."""
    from app.metricas import percentil as do_backend

    for n in (2, 6, 10):
        xs = [float(10 * (i + 1)) for i in range(n)]
        for q in (0.5, 0.95):
            assert lat.percentil(xs, q) == do_backend(xs, q * 100)
            assert lat.percentil(xs, q) in xs                      # nunca um valor interpolado
    assert lat.percentil([10.0, 20.0], 0.5) == 10.0               # K-085: n=2 no p50 é o menor (o banqueiro dava 20)
    assert lat.percentil([10.0, 20.0, 30.0, 40.0, 50.0, 60.0], 0.5) == 30.0     # interpolado era 35
    assert lat.percentil([float(x) for x in range(10, 110, 10)], 0.95) == 100.0  # interpolado era 95,5
    assert lat.percentil([], 0.5) is None
