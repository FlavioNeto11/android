"""30.35: o desfecho medido das revisões do curador 14 dias depois (`learning_reviews.resultado_posterior`), o rótulo 2
do golden set do Jev. Contrato com a orquestradora (03/10), com a emenda do degrau D-5 da saúde.

Nível de prova: `simulated` (fatos montados no domínio; banco migrado com execuções, tentativas e revisões semeadas).
Nenhuma IA, nenhum aparelho.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.resultado_posterior import GravadorDoResultadoPosterior
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.resultado_posterior import (FatosDaJanela, MudancaNaJanela, ResultadoPosterior,
                                                             falhas_seguidas, resultado)
from app.modules.learning.domain.saude import LimiaresDeSaude
from app.modules.learning.infrastructure.resultado_posterior_sql import ResultadoPosteriorSql
from app.util import to_iso

from .fake_skills import TS
from .fake_skills import banco as banco_migrado

S = SkillState
R = ResultadoPosterior
M = MudancaNaJanela
OK, FALHA = True, False


# ------------------------------------------------------------------ a regra
@pytest.mark.parametrize(("fatos", "esperado"), [
    (FatosDaJanela(), R.SEM_DESFECHO),                                              # nenhum fato: não é rótulo
    (FatosDaJanela(usos=(OK,)), R.MANTER),                                          # ≥ 1 uso e ≥ 80 %
    (FatosDaJanela(usos=(OK, OK, OK, OK, FALHA)), R.MANTER),                        # 80 % com 5 usos
    (FatosDaJanela(usos=(OK, FALHA, OK, OK)), R.SEM_DESFECHO),                      # 75 %, < 5 usos: nem D-5 nem manter
    (FatosDaJanela(usos=(OK, OK, FALHA, OK, FALHA)), R.REBAIXAR),                   # 60 % com 5 usos: D-5
    (FatosDaJanela(usos=(OK,) * 9 + (FALHA, FALHA)), R.REBAIXAR),                   # 2 falhas seguidas: D-5
    (FatosDaJanela(mudancas=(M(S.PUBLISHED, S.DISABLED),), usos=(OK,) * 5), R.DESCARTAR),
    (FatosDaJanela(mudancas=(M(S.PUBLISHED, S.CANDIDATE),), usos=(OK,) * 5), R.REBAIXAR),
    (FatosDaJanela(mudancas=(M(S.CANDIDATE, S.VALIDATED),), usos=(OK,)), R.MANTER),  # subir não é desfecho
    (FatosDaJanela(mudancas=(M(S.PUBLISHED, S.DEPRECATED),), usos=(OK,)), R.MANTER),  # absorção: o uso decide
    (FatosDaJanela(mudancas=(M(S.PUBLISHED, S.DEPRECATED),)), R.SEM_DESFECHO),
    (FatosDaJanela(mudancas=(M(S.DISABLED, S.DISABLED),)), R.SEM_DESFECHO),          # só reclassifica (30.23)
])
def test_cada_regra(fatos: FatosDaJanela, esperado: ResultadoPosterior) -> None:
    assert resultado(fatos) is esperado


def test_vale_o_mais_grave() -> None:
    desce_e_desliga = (M(S.PUBLISHED, S.VALIDATED), M(S.VALIDATED, S.DISABLED))
    assert resultado(FatosDaJanela(mudancas=desce_e_desliga)) is R.DESCARTAR
    assert resultado(FatosDaJanela(mudancas=(M(S.PUBLISHED, S.VALIDATED),), usos=(FALHA, FALHA))) is R.REBAIXAR


def test_o_degrau_e_o_da_saude_do_config() -> None:
    usos = (OK, OK, OK, FALHA)                                                       # 75 %
    assert resultado(FatosDaJanela(usos=usos)) is R.SEM_DESFECHO
    assert resultado(FatosDaJanela(usos=usos), LimiaresDeSaude(amostra_minima=4)) is R.REBAIXAR
    assert resultado(FatosDaJanela(usos=usos), LimiaresDeSaude(taxa_minima=0.7)) is R.MANTER
    assert falhas_seguidas((FALHA, OK, FALHA, FALHA, FALHA, OK)) == 3


# ------------------------------------------------------------------ o gravador sobre o banco
AGORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
REVISADA = AGORA - timedelta(days=16)                 # a janela fechou há 2 dias


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "resultado_posterior.sqlite3")
    yield d
    d.close()


def _revisao(db: Database, rid: str, item_ref: str, *, kind: str = "receita", criada: datetime = REVISADA,
             template: str = "curador", simulated: int = 0, validade: str = "ok") -> None:
    db.execute("INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, gatilho, dossie_hash, template_id,"
               " template_versao, simulated, validade) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (rid, to_iso(criada), item_ref, kind, "a_revisar", f"h-{rid}", template, "v1", simulated, validade))


def _uso(db: Database, receita: int, run: str, status: str, quando: datetime, *, simulated: int = 0) -> None:
    if db.one("SELECT id FROM runs WHERE id=?", (run,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                   " VALUES (?,?,?,?,?,?,?,?)", (run, f"k-{run}", "cmd", "execute", "completed", simulated,
                                                 json.dumps(["android-01"]), TS))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                   (f"{run}:o1", run, "android-01", "succeeded", 1))
    n = int(db.scalar("SELECT COUNT(*) AS n FROM steps WHERE run_id=?", (run,)) or 0) + 1
    passo = f"{run}:android-01:v1:p{n}"
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (passo, run, f"{run}:o1", "android-01", 1, n, f"p{n}", "p", "p", "{}", 180, 3, status))
    db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, finished_at, recipe_id)"
               " VALUES (?,?,?,?,?,?,?)", (f"{passo}:a1", passo, 1, status, to_iso(quando), to_iso(quando), receita))


def _gravador(db: Database) -> GravadorDoResultadoPosterior:
    return GravadorDoResultadoPosterior(ResultadoPosteriorSql(db), LimiaresDeSaude)


def _lido(db: Database, rid: str) -> tuple[str | None, str | None]:
    r = db.one("SELECT resultado_posterior, resultado_em FROM learning_reviews WHERE id=?", (rid,))
    assert r is not None
    return r["resultado_posterior"], r["resultado_em"]


def test_grava_uma_vez_depois_que_a_janela_fecha(db: Database) -> None:
    _revisao(db, "lr-1", "receita:7")
    _uso(db, 7, "r-a", "succeeded", REVISADA + timedelta(days=1))
    _uso(db, 7, "r-b", "succeeded", REVISADA + timedelta(days=3))
    assert _gravador(db).executar(REVISADA + timedelta(days=13)) == 0          # janela aberta: nada
    assert _lido(db, "lr-1") == (None, None)
    assert _gravador(db).executar(AGORA) == 1
    assert _lido(db, "lr-1") == ("manter", to_iso(AGORA))
    # Idempotente: a segunda passada não grava, e um fato novo depois não reescreve.
    _uso(db, 7, "r-c", "failed", REVISADA + timedelta(days=5))
    assert _gravador(db).executar(AGORA + timedelta(days=1)) == 0
    assert _lido(db, "lr-1")[0] == "manter"


def test_o_uso_conta_so_dentro_da_janela_real_e_valido(db: Database) -> None:
    _revisao(db, "lr-2", "receita:8")
    _uso(db, 8, "r-antes", "failed", REVISADA - timedelta(hours=1))            # antes da revisão
    _uso(db, 8, "r-depois", "failed", REVISADA + timedelta(days=15))           # depois da janela
    _uso(db, 8, "r-sim", "failed", REVISADA + timedelta(days=1), simulated=1)  # simulada
    _uso(db, 8, "r-20261004120000-abcdef", "failed", REVISADA + timedelta(days=2))               # invalidada (30.23)
    _uso(db, 8, "r-20261004120000-abcdef", "failed", REVISADA + timedelta(days=2, hours=1))
    db.execute("INSERT INTO learning_transitions(item_ref, item_kind, from_state, to_state, decided_by, decided_at,"
               " reason)"
               " VALUES (?,?,?,?,?,?,?)", ("receita:8", "receita", "disabled", "disabled", "sistema",
                                         to_iso(REVISADA - timedelta(days=1)), "evidencia_invalida:r-20261004120000-abcdef"))
    _uso(db, 8, "r-ok", "succeeded", REVISADA + timedelta(days=4))
    assert _gravador(db).executar(AGORA) == 1
    assert _lido(db, "lr-2")[0] == "manter"                                    # só o r-ok contou


def test_desligado_na_janela_descarta_e_a_d5_rebaixa(db: Database) -> None:
    _revisao(db, "lr-3", "receita:9")
    _uso(db, 9, "r-1", "succeeded", REVISADA + timedelta(days=1))
    db.execute("INSERT INTO learning_transitions(item_ref, item_kind, from_state, to_state, decided_by, decided_at,"
               " reason)"
               " VALUES (?,?,?,?,?,?,?)", ("receita:9", "receita", "published", "disabled", "sistema",
                                         to_iso(REVISADA + timedelta(days=2)), "quarentena"))
    _revisao(db, "lr-4", "receita:10")
    for i, status in enumerate(("succeeded", "failed", "failed")):
        _uso(db, 10, f"r-d5-{i}", status, REVISADA + timedelta(days=1, hours=i))
    assert _gravador(db).executar(AGORA) == 2
    assert (_lido(db, "lr-3")[0], _lido(db, "lr-4")[0]) == ("descartar", "rebaixar")


def test_so_as_revisoes_reais_e_validas_do_curador_de_receita_e_licao(db: Database) -> None:
    _revisao(db, "lr-sim", "receita:11", simulated=1)
    _revisao(db, "lr-inv", "receita:11", validade="invalida:formato")
    _revisao(db, "lr-int", "receita:11", template="intencao")
    _revisao(db, "lr-flx", "fluxo:x", kind="fluxo")
    _revisao(db, "lr-li", "li-abc", kind="licao")
    assert _gravador(db).executar(AGORA) == 1
    assert _lido(db, "lr-li")[0] == "sem_desfecho"                             # lição sem exposição na janela
    assert all(_lido(db, x)[0] is None for x in ("lr-sim", "lr-inv", "lr-int", "lr-flx"))


def test_a_licao_le_a_exposicao_no_braco_com_a_licao(db: Database) -> None:
    _revisao(db, "lr-li2", "li-def", kind="licao")
    for i, (braco, desfecho) in enumerate([("with", "succeeded"), ("with", "completed"), ("holdout", "failed"),
                                           ("holdout", "failed")]):
        db.execute("INSERT INTO learning_exposures(item_id, unit_id, role, arm, created_at, outcome, filled_at)"
                   " VALUES (?,?,?,?,?,?,?)", ("li-def", f"u{i}", "actor", braco, to_iso(REVISADA),
                                              desfecho, to_iso(REVISADA + timedelta(days=1, hours=i))))
    assert _gravador(db).executar(AGORA) == 1
    assert _lido(db, "lr-li2")[0] == "manter"                                  # o `holdout` não conta


def test_a_montagem_registra_o_passo(db: Database) -> None:
    from app.config import LearningCfg
    from app.modules.learning.infrastructure.montagem import montar_aprendizado

    cfg = LearningCfg()
    livro = montar_aprendizado(db, config=lambda: cfg, retencao_de_logs_dias=lambda: 14, precos=dict,
                               relogio=lambda: AGORA, commit=lambda: "abc")
    assert "resultado_posterior" in {p.nome for p in livro._passos}  # noqa: SLF001
