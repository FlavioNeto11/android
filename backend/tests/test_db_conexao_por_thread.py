"""31.320: uma conexão por thread e uma trava só de escrita.

Os despejos do vigia de 10/10/2026 (16:33Z, 18:06Z, 18:31Z, 18:50Z) mostram o mesmo defeito: a thread do laço de eventos chama o banco de
forma síncrona e espera o `RLock` único que uma thread do pool segura numa consulta longa. O que se prova aqui:
- uma leitura de 5 s numa thread NÃO para o laço (maior intervalo sem batida < 1 s; o esperado é bem abaixo) nem a escrita dele;
- uma escrita curta numa thread faz o laço esperar só o tempo dela, sem `database is locked` e sem linha perdida;
- a leitura vê o último estado COMITADO, nunca o meio da transação de outra thread; `depois_do_commit` é da thread;
- a trava de escrita só é pega por instrução que pode escrever (borda do primeiro token) e o aviso diz QUEM a segura;
- a conexão é por thread, some com a thread, e `:memory:` vira arquivo temporário da instância.

Nível de prova: `simulated` (SQLite em arquivo; o PostgreSQL pela fábrica quando há `TEST_DATABASE_URL`). `real`: `not_run`.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import Database

from .conftest import Harness
from .test_db import _banco

LENTA_S = 5.0                         # a consulta lenta artificial do desenho
LIMITE_DO_LACO_S = 1.0                # o maior intervalo sem batida que o teste aceita; o esperado é bem abaixo


def _e_postgres(db: Database) -> bool:
    return db.dialect == "postgres"


def _consulta_lenta(db: Database, segundos: float):  # noqa: ANN202
    """A leitura lenta artificial, NESTA thread: `pg_sleep` no PostgreSQL; no SQLite, uma função que dorme, registrada na conexão dela."""
    if _e_postgres(db):
        return db.scalar(f"SELECT pg_sleep({segundos})")
    db._conn.create_function("sleep_s", 1, lambda s: (time.sleep(s), 1)[1])    # noqa: SLF001
    return db.scalar("SELECT sleep_s(?)", (segundos,))


def _preparar(db: Database) -> None:
    db.migrate()
    db.execute("CREATE TABLE IF NOT EXISTS t_conc (x INTEGER)")


# ===================================================================== a leitura lenta não para o laço
@pytest.mark.asyncio
async def test_leitura_de_5_s_numa_thread_nao_para_o_laco_nem_a_escrita_dele(tmp_path: Path) -> None:
    """O experimento do desenho: uma thread lê por 5 s; a thread do laço faz `query`, `INSERT` e `tx()` a cada 100 ms. Antes (uma conexão
    e um lock) o maior intervalo era ~5 s; agora tem de ficar abaixo de 1 s, e nenhuma escrita se perde."""
    db = _banco(tmp_path)
    try:
        _preparar(db)
        lenta = asyncio.create_task(asyncio.to_thread(_consulta_lenta, db, LENTA_S))
        await asyncio.sleep(0.3)                                   # a thread já está dentro da consulta
        inicio = time.monotonic()
        ultimo = inicio
        maior = 0.0
        escritas = 0
        while time.monotonic() - inicio < LENTA_S - 1.0:
            assert db.scalar("SELECT 1") == 1
            db.execute("INSERT INTO t_conc(x) VALUES (?)", (escritas,))
            with db.tx():
                db.execute("INSERT INTO t_conc(x) VALUES (?)", (-escritas,))
            escritas += 2
            agora = time.monotonic()
            maior = max(maior, agora - ultimo)
            ultimo = agora
            await asyncio.sleep(0.1)
        print(f"maior intervalo sem batida do laço: {maior * 1000:.0f} ms em {escritas} escritas, com uma leitura de {LENTA_S:.0f} s ao lado")
        assert maior < LIMITE_DO_LACO_S, f"o laço ficou {maior:.2f} s sem batida durante a leitura lenta de outra thread"
        assert not lenta.done(), "a consulta lenta já tinha acabado: o teste não mediu nada"
        await lenta
        assert db.scalar("SELECT COUNT(*) FROM t_conc") == escritas
    finally:
        db.close()


@pytest.mark.asyncio
async def test_escrita_curta_numa_thread_faz_o_laco_esperar_so_o_tempo_dela(tmp_path: Path) -> None:
    """Uma transação de escrita de 0,6 s numa thread: o `INSERT` do laço espera por ela (um escritor só), sem `database is locked`, e as
    duas linhas existem no fim."""
    db = _banco(tmp_path)
    segurou = threading.Event()
    try:
        _preparar(db)

        def escritora() -> None:
            with db.tx():
                db.execute("INSERT INTO t_conc(x) VALUES (1)")
                segurou.set()
                time.sleep(0.6)

        tarefa = asyncio.create_task(asyncio.to_thread(escritora))
        while not segurou.is_set():
            await asyncio.sleep(0.01)
        t0 = time.monotonic()
        db.execute("INSERT INTO t_conc(x) VALUES (2)")
        espera = time.monotonic() - t0
        await tarefa
        assert espera < 3.0
        assert sorted(r["x"] for r in db.query("SELECT x FROM t_conc")) == [1, 2]
    finally:
        db.close()


# ===================================================================== o que cada thread vê
def test_a_leitura_de_outra_thread_nao_ve_o_meio_da_transacao_e_ve_o_commit(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        _preparar(db)
        escreveu, pode_comitar, visto = threading.Event(), threading.Event(), {}

        def escritora() -> None:
            with db.tx():
                db.execute("INSERT INTO t_conc(x) VALUES (7)")
                visto["dentro"] = db.scalar("SELECT COUNT(*) FROM t_conc")      # a própria thread vê o que escreveu
                escreveu.set()
                pode_comitar.wait(5)

        t = threading.Thread(target=escritora)
        t.start()
        assert escreveu.wait(5)
        assert db.scalar("SELECT COUNT(*) FROM t_conc") == 0                  # o meio da transação alheia não aparece
        pode_comitar.set()
        t.join(5)
        assert visto["dentro"] == 1
        assert db.scalar("SELECT COUNT(*) FROM t_conc") == 1                  # depois do COMMIT, aparece na instrução seguinte
    finally:
        db.close()


def test_depois_do_commit_e_da_thread_que_pediu(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        _preparar(db)
        rodou: list[str] = []
        a_pediu, b_comitou = threading.Event(), threading.Event()

        def a() -> None:
            with db.tx():
                db.depois_do_commit(lambda: rodou.append("a"))
                a_pediu.set()
                b_comitou.wait(5)
            rodou.append("a-saiu")

        ta = threading.Thread(target=a)
        ta.start()
        assert a_pediu.wait(5)
        if _e_postgres(db):                                                   # no SQLite a escrita de B esperaria A (um escritor só)
            with db.tx():
                db.execute("INSERT INTO t_conc(x) VALUES (1)")
            b_comitou.set()
            ta.join(5)
            assert rodou == ["a", "a-saiu"]
        else:
            b_comitou.set()
            ta.join(5)
            with db.tx():
                db.execute("INSERT INTO t_conc(x) VALUES (1)")
            assert rodou == ["a", "a-saiu"], "o commit de uma thread não dispara o efeito de outra"
    finally:
        db.close()


# ===================================================================== a trava de escrita: borda do primeiro token
@pytest.mark.parametrize("sql, leitura", [
    ("SELECT 1", True),
    ("  select 1", True),
    ("\n\t SELECT * FROM t", True),
    ("  /* c */ select 1", True),
    ("-- comentário\nSELECT 1", True),
    ("/* a */ -- b\n /* c */ SELECT 1", True),
    ("EXPLAIN QUERY PLAN SELECT 1", True),
    ("explain SELECT 1", True),
    ("WITH x AS (SELECT 1) SELECT * FROM x", False),
    ("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", False),
    ("PRAGMA table_info(t)", False),
    ("INSERT INTO t(a) VALUES (1) RETURNING a", False),
    ("UPDATE t SET a=1", False),
    ("DELETE FROM t", False),
    ("(SELECT 1)", False),
    ("/* comentário sem fim SELECT 1", False),
    ("", False),
    ("   ", False),
    ("SELECTED_X", False),
])
def test_so_select_e_explain_sao_leitura(sql: str, leitura: bool) -> None:
    assert db_mod._e_leitura(sql) is leitura                             # noqa: SLF001


def test_leitura_nao_pega_a_trava_de_escrita_e_o_resto_pega(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    if _e_postgres(db):
        db.close()
        pytest.skip("a trava de escrita existe só no SQLite; no PostgreSQL o servidor arbitra")
    try:
        _preparar(db)
        with db.tx():
            # em outra thread, a leitura passa mesmo com a escrita presa; a escrita de outra thread espera
            resultados: dict[str, float] = {}

            def leitor() -> None:
                t0 = time.monotonic()
                db.scalar("SELECT COUNT(*) FROM t_conc")
                resultados["leitura"] = time.monotonic() - t0

            t = threading.Thread(target=leitor)
            t.start()
            t.join(2)
            assert not t.is_alive() and resultados["leitura"] < 0.5
        # fora da transação: a trava está livre
        assert db._escrita.acquire(blocking=False)                          # noqa: SLF001
        db._escrita.release()                                               # noqa: SLF001
    finally:
        db.close()


# ===================================================================== o aviso diz QUEM segura
@pytest.mark.asyncio
async def test_o_aviso_da_espera_no_laco_diz_quem_segura_a_escrita(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                   caplog: pytest.LogCaptureFixture) -> None:
    db = _banco(tmp_path)
    if _e_postgres(db):
        db.close()
        pytest.skip("a trava de escrita existe só no SQLite")
    segurou = threading.Event()
    try:
        _preparar(db)
        monkeypatch.setattr(db_mod, "LIMITE_DA_CONSULTA_NO_LACO_S", 0.3)
        monkeypatch.setattr(db_mod, "LIMITE_DA_POSSE_DA_ESCRITA_S", 0.3)

        def retencao_de_mentira() -> None:
            with db.tx():
                db.execute("INSERT INTO t_conc(x) VALUES (1)")
                segurou.set()
                time.sleep(1.0)

        tarefa = asyncio.create_task(asyncio.to_thread(retencao_de_mentira))
        while not segurou.is_set():
            await asyncio.sleep(0.01)
        with caplog.at_level(logging.WARNING, logger="poc.db"):
            db.execute("INSERT INTO t_conc(x) VALUES (2)")                   # a thread do laço espera ~1 s
            await tarefa
        espera = [r.getMessage() for r in caplog.records if "consulta síncrona no laço" in r.getMessage()]
        assert len(espera) == 1
        assert "a trava de escrita estava com" in espera[0] and "retencao_de_mentira" in espera[0]
        assert "test_db_conexao_por_thread.py" in espera[0]
        posse = [r.getMessage() for r in caplog.records if "ficou presa" in r.getMessage()]
        assert len(posse) == 1 and "retencao_de_mentira" in posse[0]
        assert db.consultas_lentas_no_laco >= 1
    finally:
        db.close()


# ===================================================================== a conexão é da thread
def test_cada_thread_tem_a_sua_conexao_e_ela_some_com_a_thread(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        _preparar(db)
        assert db.conexoes_abertas == 1
        ids: list[int] = []
        prontas, solta = threading.Barrier(4), threading.Event()

        def uma() -> None:
            db.scalar("SELECT 1")
            ids.append(id(db._conn))                                          # noqa: SLF001
            prontas.wait(5)
            solta.wait(5)

        ts = [threading.Thread(target=uma) for _ in range(3)]
        for t in ts:
            t.start()
        prontas.wait(5)
        assert db.conexoes_abertas == 4 and len(set(ids)) == 3
        solta.set()
        for t in ts:
            t.join(5)
        del ts, t
        gc.collect()
        assert db.conexoes_abertas == 1, "a conexão de uma thread que acabou não pode ficar aberta"
    finally:
        db.close()


def test_passar_do_teto_de_conexoes_avisa_uma_vez(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    db = _banco(tmp_path)
    try:
        _preparar(db)
        monkeypatch.setattr(db_mod, "LIMITE_DE_CONEXOES", 2)
        solta = threading.Event()
        abertas = threading.Barrier(4)

        def uma() -> None:
            db.scalar("SELECT 1")
            abertas.wait(5)
            solta.wait(5)

        with caplog.at_level(logging.WARNING, logger="poc.db"):
            ts = [threading.Thread(target=uma) for _ in range(3)]
            for t in ts:
                t.start()
            abertas.wait(5)
            avisos = [r.getMessage() for r in caplog.records if "conexões abertas com o banco" in r.getMessage()]
            solta.set()
            for t in ts:
                t.join(5)
        assert len(avisos) == 1 and "acima de 2" in avisos[0]
    finally:
        db.close()


def test_a_reconexao_e_da_conexao_da_thread(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        _preparar(db)
        antes = db._conn                                                      # noqa: SLF001
        outra: dict[str, object] = {}

        def na_outra() -> None:
            outra["antes"] = db._conn                                         # noqa: SLF001
            db._reabrir()                                                     # noqa: SLF001
            outra["depois"] = db._conn                                        # noqa: SLF001

        t = threading.Thread(target=na_outra)
        t.start()
        t.join(5)
        assert db._conn is antes                                              # a desta thread não mudou
        assert outra["antes"] is not outra["depois"] and db.reconexoes == 1
    finally:
        db.close()


# ===================================================================== :memory: vira arquivo temporário
def test_memory_vira_arquivo_temporario_da_instancia_e_some_no_close() -> None:
    db = Database(":memory:")
    pasta = Path(db.path).parent
    try:
        assert db.path != ":memory:" and Path(db.path).exists()
        db.execute("CREATE TABLE m (x INTEGER)")
        db.execute("INSERT INTO m(x) VALUES (3)")
        visto: list[object] = []
        t = threading.Thread(target=lambda: visto.append(db.scalar("SELECT x FROM m")))
        t.start()
        t.join(5)
        assert visto == [3], "duas threads têm de ver o MESMO banco (por isso não é :memory: de verdade)"
    finally:
        db.close()
    assert not pasta.exists()


# ===================================================================== etapa 2: apagar em fatias
def test_apagar_em_fatias_apaga_so_o_que_casa_em_lotes_e_pausa_entre_eles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _banco(tmp_path)
    try:
        _preparar(db)
        db.execute("CREATE TABLE IF NOT EXISTS t_fatias (id INTEGER PRIMARY KEY, ts TEXT)")
        with db.tx():
            for i in range(1, 26):
                db.execute("INSERT INTO t_fatias(id, ts) VALUES (?, ?)", (i, "velho" if i <= 23 else "novo"))
        pausas: list[float] = []
        monkeypatch.setattr(db_mod.time, "sleep", lambda s: pausas.append(s))
        total = db.apagar_em_fatias("t_fatias", "ts = ?", ("velho",), lote=5, pausa_s=0.07)
        assert total == 23
        assert pausas == [0.07] * 4, "23 linhas em lotes de 5 são 5 comandos e 4 pausas entre eles"
        assert [r["id"] for r in db.query("SELECT id FROM t_fatias ORDER BY id")] == [24, 25]
        pausas.clear()
        assert db.apagar_em_fatias("t_fatias", "ts = ?", ("velho",), lote=5) == 0 and pausas == []      # nada a apagar: nenhuma pausa
        with pytest.raises(TypeError):
            db.apagar_em_fatias("t_fatias", "ts = :ts", {"ts": "novo"})
    finally:
        db.close()


def test_outro_escritor_entra_entre_as_fatias(tmp_path: Path) -> None:
    """A razão de existir da pausa: uma purga de 4000 linhas em lotes de 200 NÃO segura a trava do começo ao fim; uma escrita de outra thread
    termina antes de a purga acabar."""
    db = _banco(tmp_path)
    try:
        _preparar(db)
        db.execute("CREATE TABLE IF NOT EXISTS t_fatias (id INTEGER PRIMARY KEY, ts TEXT)")
        with db.tx():
            for i in range(1, 4001):
                db.execute("INSERT INTO t_fatias(id, ts) VALUES (?, 'velho')", (i,))
        fim_da_purga: list[float] = []
        comecou = threading.Event()

        def purga() -> None:
            comecou.set()
            db.apagar_em_fatias("t_fatias", "ts = 'velho'", (), lote=200, pausa_s=0.02)
            fim_da_purga.append(time.monotonic())

        t = threading.Thread(target=purga)
        t.start()
        assert comecou.wait(5)
        time.sleep(0.05)
        db.execute("INSERT INTO t_conc(x) VALUES (1)")                       # escrita de outra thread, no meio da purga
        fim_da_escrita = time.monotonic()
        t.join(30)
        assert fim_da_purga, "a purga não terminou"
        assert fim_da_escrita < fim_da_purga[0], "a escrita só passou depois da purga inteira: a trava ficou presa de ponta a ponta"
        assert db.scalar("SELECT COUNT(*) FROM t_fatias") == 0
    finally:
        db.close()


# ===================================================================== o /health mostra os dois contadores
def test_o_health_mostra_as_conexoes_abertas_e_as_consultas_lentas_no_laco(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    antes = st.saude.health().database
    assert antes is not None and antes.open_connections >= 1 and antes.slow_queries_in_loop == 0
    st.db.consultas_lentas_no_laco += 3                                     # o que o aviso do laço conta
    liberar, prontas = threading.Event(), threading.Event()

    def outra() -> None:
        st.db.scalar("SELECT 1")
        prontas.set()
        liberar.wait(5)

    t = threading.Thread(target=outra)
    t.start()
    assert prontas.wait(5)
    depois = st.saude.health().database
    liberar.set()
    t.join(5)
    assert depois is not None and depois.slow_queries_in_loop == 3
    assert depois.open_connections == antes.open_connections + 1, "a conexão da thread nova conta enquanto ela vive"
