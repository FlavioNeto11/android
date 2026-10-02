"""Item 28.1 — trava de líder dos laços periódicos (design `pedidos-persistentes.md` §7.3).

Dois "processos" são dois `Database` no mesmo banco (conexões separadas, como dois backends) e um relógio falso
dividido pelos dois — o papel do relógio do banco. Em PostgreSQL, com `TEST_DATABASE_URL`; sem ela, SQLite.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.planning import saldos
from app.taskqueue.travas import CURADORIA, RETENCAO, SALDOS, TRAVA_TTL_S, TRAVAS_DOS_LACOS, Lideranca, TravaPerdida
from app.util import now, to_iso

from .conftest import Harness, make_config

AQUI = "servidor-a"
LAH = "servidor-b"


class Relogio:
    """O relógio do banco, que os dois backends leem igual. Anda só quando o teste manda."""

    def __init__(self) -> None:
        self.t = now()

    def __call__(self) -> datetime:
        return self.t

    def avancar(self, s: float) -> None:
        self.t += timedelta(seconds=s)


def _dois_bancos(tmp_path: Path) -> tuple[Database, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    a = Database(cfg.db_dsn)       # `db_dsn` e não `db_path`: segue `TEST_DATABASE_URL` quando ela existe
    a.migrate()
    return a, Database(cfg.db_dsn)


def test_um_so_lider_e_tomada_idempotente(tmp_path: Path) -> None:
    da, db_ = _dois_bancos(tmp_path)
    r = Relogio()
    a, b = Lideranca(da, dono=AQUI, relogio=r), Lideranca(db_, dono=LAH, relogio=r)
    assert a.tomar(SALDOS) == 1                       # linha nasce sob demanda, primeiro mandato
    assert b.tomar(SALDOS) is None, "dois líderes ao mesmo tempo"
    assert a.tomar(SALDOS) == 1, "tomar de novo abriu mandato novo: invalidaria as escritas do próprio líder"
    assert b.tomar(RETENCAO) == 1                     # travas independentes por nome
    linha = da.one("SELECT dono, token FROM travas WHERE nome=?", (SALDOS,))
    assert linha["dono"] == AQUI and int(linha["token"]) == 1


def test_lider_que_parou_de_renovar_perde_e_o_token_antigo_e_recusado(tmp_path: Path) -> None:
    da, db_ = _dois_bancos(tmp_path)
    r = Relogio()
    a, b = Lideranca(da, dono=AQUI, relogio=r), Lideranca(db_, dono=LAH, relogio=r)
    velho = a.tomar(SALDOS)
    r.avancar(TRAVA_TTL_S - 1)
    assert b.tomar(SALDOS) is None                    # ainda no prazo
    r.avancar(2)                                      # A ficou pausado: o prazo venceu sem ele saber
    novo = b.tomar(SALDOS)
    assert novo == 2 and velho == 1                   # mandato novo, token maior

    # A acorda e tenta escrever com o token velho: a cerca recusa, e nada é gravado.
    with pytest.raises(TravaPerdida):
        with a.cercada(SALDOS, velho):
            da.execute("INSERT INTO travas(nome, token) VALUES ('escrita-do-velho', 0)")
    assert da.one("SELECT nome FROM travas WHERE nome='escrita-do-velho'") is None
    with b.cercada(SALDOS, novo):                     # o líder novo escreve
        pass
    # e o velho, ao tentar renovar, descobre que perdeu (sem tomar de volta a trava viva)
    assert a.tomar(SALDOS) is None


def test_renovar_impede_a_tomada(tmp_path: Path) -> None:
    da, db_ = _dois_bancos(tmp_path)
    r = Relogio()
    a, b = Lideranca(da, dono=AQUI, relogio=r), Lideranca(db_, dono=LAH, relogio=r)
    a.tomar(CURADORIA)
    for _ in range(10):                               # 10 × 100 s = bem além de um prazo, renovando a cada volta
        r.avancar(100)
        assert a.manter([CURADORIA]) == {CURADORIA: 1}
        assert b.tomar(CURADORIA) is None


def test_soltar_na_saida_limpa_mantem_o_token_crescente(tmp_path: Path) -> None:
    da, db_ = _dois_bancos(tmp_path)
    r = Relogio()
    a, b = Lideranca(da, dono=AQUI, relogio=r), Lideranca(db_, dono=LAH, relogio=r)
    a.tomar(SALDOS)
    assert a.soltar_todas() == 1
    assert b.tomar(SALDOS) == 2, "soltar zerou o token: a cerca deixaria passar o escritor do mandato 1"
    assert a.soltar(SALDOS) is False                  # não tem o que soltar: não apaga o dono alheio
    assert da.one("SELECT dono FROM travas WHERE nome=?", (SALDOS,))["dono"] == LAH


def test_reinicio_com_o_mesmo_dono_assume_na_hora(tmp_path: Path) -> None:
    da, db_ = _dois_bancos(tmp_path)
    r = Relogio()
    caiu = Lideranca(da, dono=AQUI, relogio=r)
    assert caiu.manter(TRAVAS_DOS_LACOS) == {n: 1 for n in TRAVAS_DOS_LACOS}
    voltou = Lideranca(db_, dono=AQUI, relogio=r)      # processo novo, mesmo OWNER_ID, sem o prazo vencer
    assert voltou.tomar(SALDOS) is None               # sem a faxina de partida, esperaria o prazo
    assert voltou.soltar_da_queda() == len(TRAVAS_DOS_LACOS)
    assert voltou.tomar(SALDOS) == 2
    assert Lideranca(da, dono=LAH, relogio=r).tomar(SALDOS) is None


def test_corrida_de_varios_backends_da_um_so_mandato(tmp_path: Path) -> None:
    """Oito conexões disparando a tomada no mesmo instante: exatamente uma ganha, nenhuma quebra."""
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    primeiro = Database(cfg.db_dsn)
    primeiro.migrate()
    bancos = [primeiro] + [Database(cfg.db_dsn) for _ in range(7)]
    lideres = [Lideranca(d, dono=f"backend-{i}") for i, d in enumerate(bancos)]
    largada = threading.Barrier(len(lideres))
    tokens: list[int | None] = [None] * len(lideres)
    erros: list[BaseException] = []

    def disputar(i: int) -> None:
        try:
            largada.wait()
            tokens[i] = lideres[i].tomar(SALDOS)
        except BaseException as exc:  # noqa: BLE001 - o teste quer ver qualquer falha
            erros.append(exc)

    fios = [threading.Thread(target=disputar, args=(i,)) for i in range(len(lideres))]
    for f in fios:
        f.start()
    for f in fios:
        f.join(30)
    assert not erros, erros
    assert [t for t in tokens if t is not None] == [1], tokens
    assert int(primeiro.scalar("SELECT COUNT(*) FROM travas")) == 1


def _parar_lacos(estado: object) -> None:
    """Os laços de fundo do harness ficam de fora: o teste chama uma volta de cada vez, com o relógio dele."""
    for t in getattr(estado, "_bg"):
        if t.get_name() in ("retention", "saldos-de-ia", "aprendizado-curadoria", "travas-de-lider"):
            t.cancel()


@pytest.mark.asyncio
async def test_dois_appstate_no_mesmo_banco_um_so_roda_os_lacos(tmp_path: Path) -> None:
    """O aceite do 28.1: dois backends com scheduler no mesmo banco; um só líder; o outro assume depois do prazo; o
    fechamento do dia (não idempotente) não acontece duas vezes, nem pelo líder que perdeu o mandato no meio."""
    a = Harness(tmp_path / "a", 1, owner_id=AQUI)
    sa = await a.boot()
    b = Harness(tmp_path / "b", 1, owner_id=LAH, db_dsn=a.cfg.db_dsn)
    sb = await b.boot()
    try:
        _parar_lacos(sa)
        _parar_lacos(sb)
        await asyncio.sleep(0.2)                      # a volta de partida dos laços já cancelados termina
        r = Relogio()
        sa.lideranca.relogio = r
        sb.lideranca.relogio = r

        # A subiu primeiro e é o líder das três; B pula as voltas, sem erro.
        assert await sa._retencao_uma_vez() is True
        assert await sb._retencao_uma_vez() is False
        assert await sb._curadoria_uma_vez() is False
        assert await sb._saldos_uma_vez() is False

        # Âncora velha: há um fechamento do dia a fazer. A tira o token e "pausa" além do prazo.
        saldos.registrar_leitura(sa.db, "gemini", 29.37, observed_at=to_iso(now() - timedelta(hours=30)))
        token_de_a = sa.lideranca.tomar(SALDOS)
        assert token_de_a is not None
        r.avancar(TRAVA_TTL_S + 1)
        sb._manter_travas()                           # a renovação de B encontra a trava vencida e assume
        with pytest.raises(TravaPerdida):
            sa._fechar_dia_cercado(token_de_a)        # A acorda e tenta fechar o dia com o mandato velho
        assert await sb._saldos_uma_vez() is True     # B fecha
        assert await sa._saldos_uma_vez() is False    # e A, agora seguidor, pula
        fechamentos = sa.db.scalar("SELECT COUNT(*) FROM ai_balance_snapshots WHERE source='fechamento'")
        assert int(fechamentos) == 1, "o dia foi fechado duas vezes"

        # Saída limpa de B devolve as travas: A assume na próxima volta, sem esperar o prazo.
        await b.crash()
        assert await sa._retencao_uma_vez() is True
    finally:
        if b.state is not None:
            await b.state.stop()
        await sa.stop()
