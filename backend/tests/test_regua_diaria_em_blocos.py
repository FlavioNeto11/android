"""31.293: a régua diária do curador não segura o lock do `Database` por um intervalo inteiro.

Sintoma real (10/10/2026, 13:01Z, 13:07Z e 13:24Z): o laço de eventos ficou ~10 s sem batida com a pilha
curar → _regua_diaria → recalcular_diario → _agregar → db.query (`data/logs/laco-travado-20261010T130127Z-1.txt` e
`...T132412Z-1.txt`). A correção lê, grava e solta o lock UM DIA POR VEZ, com uma pausa entre os dias; o resultado
gravado é o mesmo de antes, e cada consulta larga fica limitada a um dia.

Prova `simulated`: banco migrado de teste. Que a pausa entregue o lock ao laço na máquina carregada é `not_run`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db import Database
from app.modules.learning.infrastructure import sql_repository
from app.modules.learning.infrastructure.sql_repository import _dias_do_intervalo

from .test_learning_repositorio import AGORA, repo, semear_execucao
from .test_learning_repositorio import db  # noqa: F401  (fixture)


def test_os_dias_do_intervalo() -> None:
    assert _dias_do_intervalo("2026-10-08", "2026-10-11") == [
        ("2026-10-08", "2026-10-09"), ("2026-10-09", "2026-10-10"), ("2026-10-10", "2026-10-11")]
    assert _dias_do_intervalo("2026-10-10", "2026-10-10") == []
    assert _dias_do_intervalo("2026-10-11", "2026-10-10") == []
    assert _dias_do_intervalo("2026-02-27", "2026-03-02")[-1] == ("2026-03-01", "2026-03-02")


def _semear_tres_dias(db: Database) -> tuple[str, str]:  # noqa: F811
    for n, dias in enumerate((3, 2, 1)):
        semear_execucao(db, f"r-{n}", dias_atras=dias, etapas=[
            ("abrir", "OPEN_PROFILE", "succeeded", [("succeeded", None)], 1),
            ("ler", "READ_MAIL", "failed", [("failed", "Pós-condição não comprovada: x")], 0)])
    desde = (AGORA - timedelta(days=3)).strftime("%Y-%m-%d")
    return desde, (AGORA + timedelta(days=1)).strftime("%Y-%m-%d")


def _tabela(db: Database) -> list[tuple]:  # noqa: F811
    return [tuple(r[c] for c in ("day", "app_package", "capability", "failure_kind", "driven_by", "attempts", "steps",
                                 "ai_calls", "usd", "seconds", "interventions", "human_negative"))
            for r in db.query("SELECT * FROM learning_daily ORDER BY day, app_package, capability, failure_kind,"
                              " driven_by")]


def test_o_resultado_por_dia_e_o_mesmo_da_agregacao_do_intervalo_inteiro(db: Database) -> None:  # noqa: F811
    desde, ate = _semear_tres_dias(db)
    r = repo(db)
    r.recalcular_diario(desde, ate)
    por_dia = _tabela(db)
    assert por_dia, "a semente deveria gerar linhas"
    # a agregação do intervalo inteiro (o que o código fazia numa tacada) dá exatamente as mesmas linhas
    grupos = r._agregar(desde, ate)
    esperado = sorted((d, p, a, t, c, g.attempts, g.steps, g.ai_calls, round(g.usd, 6), round(g.seconds, 3),
                       g.interventions, g.human_negative) for (d, p, a, t, c), g in grupos.items())
    assert sorted(por_dia) == esperado
    # recalcular de novo não duplica nem muda nada
    assert r.recalcular_diario(desde, ate) == len(esperado) and _tabela(db) == por_dia


def test_cada_consulta_larga_cobre_no_maximo_um_dia_e_os_dias_se_soltam_um_a_um(
        db: Database, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    desde, ate = _semear_tres_dias(db)
    r = repo(db)
    janelas: list[tuple[str, str]] = []
    original = r._agregar

    def espiar(d: str, a: str):
        janelas.append((d, a))
        return original(d, a)

    pausas: list[float] = []
    monkeypatch.setattr(r, "_agregar", espiar)
    monkeypatch.setattr(sql_repository.time, "sleep", lambda s: pausas.append(s))
    r.recalcular_diario(desde, ate)
    dias = (datetime.strptime(ate, "%Y-%m-%d") - datetime.strptime(desde, "%Y-%m-%d")).days
    assert len(janelas) == dias == len(pausas)
    for d, a in janelas:
        assert datetime.strptime(a, "%Y-%m-%d") - datetime.strptime(d, "%Y-%m-%d") == timedelta(days=1)
    assert all(p > 0 for p in pausas)          # pausa de verdade entre os dias, para o lock mudar de mãos


def test_um_dia_que_falha_no_meio_nao_apaga_os_outros(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    desde, ate = _semear_tres_dias(db)
    r = repo(db)
    r.recalcular_diario(desde, ate)
    antes = _tabela(db)
    original = r._agregar
    chamadas = {"n": 0}

    def quebra_no_segundo(d: str, a: str):
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise RuntimeError("disco")
        return original(d, a)

    monkeypatch.setattr(r, "_agregar", quebra_no_segundo)
    with pytest.raises(RuntimeError):
        r.recalcular_diario(desde, ate)
    # o primeiro dia foi regravado inteiro e o resto ficou como estava: nada de linha pela metade
    assert _tabela(db) == antes
