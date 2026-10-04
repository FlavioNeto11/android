"""Item 28.25 — o registro único das decisões automáticas (migração 102) e a porta `registrar_decisao`.

Prova `simulated` (`arquivo::teste`): SQLite (ou PostgreSQL com `TEST_DATABASE_URL`) pela fábrica configurada, relógio
falso. Nada de rede nem de aparelho.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.modules.decisoes.infrastructure.registro_sql import RegistroSql
from app.shared.decisoes import NovaDecisao, decisao_valida, fatos_limpos, registrar_decisao
from app.util import now, to_iso

from .conftest import make_config


class Relogio:
    def __init__(self) -> None:
        self.t = now()

    def __call__(self) -> datetime:
        return self.t

    def avancar(self, s: float) -> None:
        self.t += timedelta(seconds=s)


@pytest.fixture
def cena(tmp_path: Path) -> tuple[Database, RegistroSql, Relogio]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    rel = Relogio()
    return db, RegistroSql(db, relogio=rel), rel


def _nova(origem: str = "run:r1:31.43-pergunta-24h", **kw: object) -> NovaDecisao:
    base: dict[str, object] = dict(fila="pergunta", item_ref="r1", origem_ref=origem, regra="31.43-pergunta-24h",
                                   efeito="Pergunta sem resposta havia 24 h foi encerrada.", fatos={"horas": 24})
    base.update(kw)
    return NovaDecisao(**base)  # type: ignore[arg-type]


def test_registra_e_le_de_volta(cena: tuple[Database, RegistroSql, Relogio]) -> None:
    _, reg, _ = cena
    assert reg.registrar(_nova(fatos={"horas": 24, "desde": "2026-10-03T10:00:00.000Z"})) is True
    (d,) = reg.listar()
    assert (d.fila, d.item_ref, d.regra) == ("pergunta", "r1", "31.43-pergunta-24h")
    assert d.fatos == {"horas": 24, "desde": "2026-10-03T10:00:00.000Z"}
    assert d.desfeita is False and d.resumida_em is None


def test_o_mesmo_fato_duas_vezes_e_uma_linha_so(cena: tuple[Database, RegistroSql, Relogio]) -> None:
    db, reg, _ = cena
    assert reg.registrar(_nova()) is True
    assert reg.registrar(_nova(efeito="outro texto", fatos={"horas": 99})) is False
    assert db.scalar("SELECT COUNT(*) FROM decisoes_automaticas") == 1
    # contraprova: outra origem é outra linha
    assert reg.registrar(_nova("run:r2:31.43-pergunta-24h", item_ref="r2")) is True
    assert db.scalar("SELECT COUNT(*) FROM decisoes_automaticas") == 2
    # e a primeira gravação é a que vale
    assert reg.listar(limite=10)[-1].fatos == {"horas": 24}


def test_porta_registrar_decisao_usa_o_relogio_do_registro(cena: tuple[Database, RegistroSql, Relogio]) -> None:
    _, reg, rel = cena
    assert registrar_decisao(reg, "aprendizado", "licao:x", "aprendizado:7", "auto:qa_revisar v1",
                             "Aprendizado publicado sem o dono.", {"usos": 5}) is True
    assert registrar_decisao(reg, "aprendizado", "licao:x", "aprendizado:7", "auto:qa_revisar v1", "x") is False
    (d,) = reg.listar()
    assert d.decidida_em == to_iso(rel.t)
    # a hora do fato, quando o produtor a conhece, vale mais que a da leitura
    registrar_decisao(reg, "pedido", "p1", "pedido:p1:v", "r", "e", decidida_em="2026-10-01T00:00:00.000Z")
    assert reg.listar(fila="pedido")[0].decidida_em == "2026-10-01T00:00:00.000Z"


@pytest.mark.parametrize("mudar", [{"fila": "outra"}, {"item_ref": " "}, {"origem_ref": ""}, {"regra": ""},
                                   {"efeito": "   "}])
def test_decisao_malformada_e_recusada_e_nao_grava(cena: tuple[Database, RegistroSql, Relogio], mudar: dict) -> None:
    db, reg, _ = cena
    with pytest.raises(ValueError):
        reg.registrar(_nova(**mudar))
    assert db.scalar("SELECT COUNT(*) FROM decisoes_automaticas") == 0


def test_fatos_so_planos_curtos_e_sem_estrutura() -> None:
    f = fatos_limpos({"horas": 24, "ok": True, "Maiuscula": 1, "lista": [1], "dict": {"a": 1}, "nada": None,
                      "longo": "x" * 500, "com\nquebra": "a", "texto": "duas\nlinhas   juntas"})
    assert f == {"horas": 24, "ok": True, "longo": "x" * 80, "texto": "duas linhas juntas"}
    assert len(fatos_limpos({f"k{i}": i for i in range(40)})) == 12
    d = decisao_valida(_nova(efeito="e" * 999, regra="r" * 999))
    assert len(d.efeito) == 240 and len(d.regra) == 120


def test_desfazer_marca_uma_vez_so(cena: tuple[Database, RegistroSql, Relogio]) -> None:
    _, reg, rel = cena
    reg.registrar(_nova())
    (d,) = reg.listar()
    assert reg.marcar_desfeita(d.id, por="dono", motivo="foi engano") is True
    rel.avancar(60)
    assert reg.marcar_desfeita(d.id, por="outro", motivo="de novo") is False
    depois = reg.obter(d.id)
    assert depois is not None and depois.desfeita
    assert (depois.desfeita_por, depois.motivo_do_desfazer) == ("dono", "foi engano")


def test_listar_filtra_por_regra_fila_periodo_e_desfeitas(cena: tuple[Database, RegistroSql, Relogio]) -> None:
    _, reg, _ = cena
    reg.registrar(_nova("a", decidida_em="2026-10-01T10:00:00.000Z"))
    reg.registrar(_nova("b", fila="objetivo", regra="outra", decidida_em="2026-10-02T10:00:00.000Z"))
    reg.registrar(_nova("c", decidida_em="2026-10-03T10:00:00.000Z"))
    reg.marcar_desfeita(reg.listar(regra="outra")[0].id, por="dono", motivo=None)
    assert [d.origem_ref for d in reg.listar()] == ["c", "b", "a"]
    assert [d.origem_ref for d in reg.listar(regra="31.43-pergunta-24h")] == ["c", "a"]
    assert [d.origem_ref for d in reg.listar(fila="objetivo")] == ["b"]
    assert [d.origem_ref for d in reg.listar(desde="2026-10-02T00:00:00.000Z", ate="2026-10-03T00:00:00.000Z")] == ["b"]
    assert [d.origem_ref for d in reg.listar(desfeitas="sim")] == ["b"]
    assert [d.origem_ref for d in reg.listar(desfeitas="nao")] == ["c", "a"]
    assert reg.regras() == ["31.43-pergunta-24h", "outra"]
