"""30.53: a conferência do QA espera uma mensagem por etapa de efeito comprovada; a `revalidada` desfaz a inválida errada.

A prova real do 30.48 (r-20261004132450-38f68a, android-10, amostra de 2 de 8, 13/13 etapas) fechou `efeito_repetido`:
a conferência (#203) contava as mensagens da execução no app inteiro e esperava uma. As duas mensagens legítimas, uma
por contato, viraram "o efeito saiu 2 vezes", e quatro linhas `invalida` foram gravadas (o fluxo e três receitas).

O que se prova:
- a leitura da conferência: N de N esperadas não repete; só acima delas há cópias (e o fato guarda as `esperadas`);
- a `revalidada` só NEUTRALIZA a `invalida` da mesma (item, origem) em `promocao.efetivas` e nos leitores SQL; não
  conta nem a favor nem contra;
- o passo da curadoria revalida a inválida da conferência antiga com cópias dentro do esperado (o fluxo e a receita da
  mesma execução), sem nascer `for`, sem tocar o `steps.result`, com a decisão na execução, e de forma idempotente;
- contraprova: a inválida legítima (cópias acima do esperado), o fato da regra nova (com `esperadas`) e o do
  verificador NÃO se revalidam.

Armação: banco migrado (`fake_skills.banco`) com a execução assentada à mão. Nível de prova: `simulated`.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.nativos import RevalidacaoDaConferencia
from app.modules.learning.domain.promocao import Evidencia, efetivas
from app.modules.learning.domain.prova import conferencia_revalida
from app.modules.learning.domain.vocabulario import Posicao
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.ligar_nativos import LeituraSql
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.taskqueue.oraculo_qa import leitura_da_conferencia

from .fake_skills import banco as banco_migrado

RUN = "r-38f68a"
FLUXO = "fluxo:levantar-todos"
RECEITA = "receita:64"


# ------------------------------------------------------------------ a leitura da conferência
def test_n_de_n_esperadas_nao_repete_e_so_acima_delas_ha_copias() -> None:
    assert leitura_da_conferencia(1, 1) == ("1 de 1 esperadas: o efeito saiu uma vez", None)
    assert leitura_da_conferencia(2, 2) == ("2 de 2 esperadas: o efeito saiu uma vez", None)    # o laço de 2 itens
    assert leitura_da_conferencia(1, 2)[1] is None                                              # faltou: não repete
    texto, copias = leitura_da_conferencia(3, 2)
    assert copias == 2 and "3 mensagens desta execução no app, 2 esperadas" in texto
    assert leitura_da_conferencia(2, 1)[1] == 2
    assert leitura_da_conferencia(0, 2)[1] is None


def test_a_regra_pura_so_absolve_a_conferencia_antiga_dentro_do_esperado() -> None:
    assert conferencia_revalida([{"copias": 2, "fonte": "provedor"}], 2)
    assert not conferencia_revalida([{"copias": 2, "fonte": "provedor"}], 1)        # repetição legítima
    assert not conferencia_revalida([{"copias": 2, "fonte": "provedor", "esperadas": 1}], 2)   # já é a regra nova
    assert not conferencia_revalida([{"copias": 2, "fonte": "verificador"}], 2)
    assert not conferencia_revalida([{"copias": 2, "fonte": "provedor"}, {"copias": 2, "fonte": "acoes"}], 2)
    assert not conferencia_revalida([], 2)


# ------------------------------------------------------------------ a neutralização
def _ev(stance: Posicao, origem: str = f"run:{RUN}") -> Evidencia:
    return Evidencia(item_ref=FLUXO, stance=stance, origin_ref=origem, run_id=RUN, instance_id="android-10",
                     app_version=None, simulated=False, detail=None, observed_at="2026-10-04T13:26:00Z")


def test_a_revalidada_so_neutraliza_a_invalida_da_mesma_origem() -> None:
    lista = [_ev(Posicao.FOR), _ev(Posicao.INVALIDA)]
    assert [e.stance for e in efetivas(lista) if e.stance is Posicao.FOR] == []                # a inválida tira o for
    lista.append(_ev(Posicao.REVALIDADA))
    assert [e.stance for e in efetivas(lista) if e.stance is Posicao.FOR] == [Posicao.FOR]     # volta a valer
    outra = [_ev(Posicao.FOR), _ev(Posicao.INVALIDA), _ev(Posicao.REVALIDADA, "run:outra")]
    assert [e.stance for e in efetivas(outra) if e.stance is Posicao.FOR] == []                # outra origem: nada


# ------------------------------------------------------------------ o passo da curadoria
@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "conferencia.sqlite3")
    yield d
    d.close()


def _execucao(db: Database, fato: dict[str, object], *, itens: int = 2) -> None:
    """A execução da prova com `for_each` de `itens` envios comprovados na versão 2, o fato na última etapa de efeito, e
    as `invalida` gravadas pela conferência antiga (no fluxo e na receita)."""
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?, 'validacao:lv-x', 'cmd {run_id}', 'execute', 'completed', 0, '[\"android-10\"]',"
               " '2026-10-04T13:24:50Z')", (RUN,))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
               (f"{RUN}:o", RUN, "android-10", "succeeded", 2))
    for i in range(1, itens + 1):
        resultado = {"verified": True, **({"efeito_repetido": fato} if i == itens else {})}
        db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                   " side_effect, postcondition, timeout_s, max_attempts, status, result)"
                   " VALUES (?,?,?,?,?,?,?,?,?,1,'{}',60,1,'succeeded',?)",
                   (f"{RUN}:s{i}", RUN, f"{RUN}:o", "android-10", 2, i, f"send_message_i{i}", "enviar", "enviar",
                    json.dumps(resultado)))
    for item, origem, detalhe in ((FLUXO, f"run:{RUN}", "[33b23e8b0224] invalida:efeito_repetido — o efeito saiu 2 vezes"),
                                  (RECEITA, f"reproducao:{RUN}", "invalida:efeito_repetido — o efeito saiu 2 vezes")):
        db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, simulated, detail,"
                   " observed_at) VALUES (?, 'invalida', ?, ?, 'android-10', 0, ?, '2026-10-04T13:26:31Z')",
                   (item, origem, RUN, detalhe))


def _linhas(db: Database, stance: str) -> list[tuple[str, str, str]]:
    return [(str(r["item_ref"]), str(r["origin_ref"]), str(r["detail"])) for r in db.query(
        "SELECT item_ref, origin_ref, detail FROM learning_evidence WHERE stance=? ORDER BY id", (stance,))]


def _passo(db: Database) -> tuple[RevalidacaoDaConferencia, list[str]]:
    decisoes: list[str] = []
    passo = RevalidacaoDaConferencia(SqlLearningRepository(db, precos=dict), LeituraSql(db),
                                     decidir=lambda texto, run: decisoes.append(f"{run}: {texto}"))
    return passo, decisoes


def test_a_conferencia_antiga_dentro_do_esperado_se_revalida_sem_nascer_for(db: Database) -> None:
    _execucao(db, {"copias": 2, "fonte": "provedor"})
    antes = str(db.scalar("SELECT result FROM steps WHERE id=?", (f"{RUN}:s2",)))
    passo, decisoes = _passo(db)
    assert passo.executar(None) == 2  # type: ignore[arg-type]
    revalidadas = _linhas(db, "revalidada")
    assert [(i, o) for i, o, _ in revalidadas] == [(FLUXO, f"run:{RUN}"), (RECEITA, f"reproducao:{RUN}")]
    assert revalidadas[0][2].startswith("[33b23e8b0224] revalidada:") and "2 mensagens para 2 etapas" in revalidadas[0][2]
    assert _linhas(db, "for") == [] and _linhas(db, "against") == []                # nenhum for retroativo
    assert len(_linhas(db, "invalida")) == 2                                        # o log só cresce
    assert str(db.scalar("SELECT result FROM steps WHERE id=?", (f"{RUN}:s2",))) == antes   # o fato fica intacto
    assert len(decisoes) == 2 and all(d.startswith(f"{RUN}: Aprendizado: a inválida de") for d in decisoes)
    assert passo.executar(None) == 0  # type: ignore[arg-type]                  # idempotente


def test_o_for_que_a_invalida_tirava_volta_a_valer_no_sql(db: Database) -> None:
    _execucao(db, {"copias": 2, "fonte": "provedor"})
    db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, detail, observed_at)"
               " VALUES (?, 'for', ?, ?, 0, 'prova antiga', '2026-10-04T13:26:30Z')", (FLUXO, f"run:{RUN}", RUN))
    conta = f"SELECT COUNT(*) FROM learning_evidence e WHERE e.item_ref=? AND {linhas.favor_efetivo('e')}"
    assert int(db.scalar(conta, (FLUXO,))) == 0
    _passo(db)[0].executar(None)  # type: ignore[arg-type]
    assert int(db.scalar(conta, (FLUXO,))) == 1


@pytest.mark.parametrize(("fato", "itens"), [({"copias": 2, "fonte": "provedor"}, 1),            # repetição legítima
                                             ({"copias": 2, "fonte": "provedor", "esperadas": 1}, 2),   # regra nova
                                             ({"copias": 2, "fonte": "verificador"}, 2)])
def test_contraprova_a_invalida_legitima_nao_se_revalida(db: Database, fato: dict[str, object], itens: int) -> None:
    _execucao(db, fato, itens=itens)
    passo, decisoes = _passo(db)
    assert passo.executar(None) == 0  # type: ignore[arg-type]
    assert _linhas(db, "revalidada") == [] and decisoes == []
