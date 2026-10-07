"""31.200: o Livro ganha o escopo de ASSUNTO (`learning_items.scope_subject`, migração 128, adendo v1.113).

O fato da pesquisa de uma operação (31.190) nascia no escopo do app inteiro: publicado, iria a todo texto do escritor
no app, sobre qualquer post. Agora ele nasce com o assunto da operação, e a lição com assunto só vai a quem pede o mesmo.

O que estes testes protegem:
* a chave do veto (`scope_key`) do item SEM assunto fica igual, byte a byte, à de antes da 128 (a trilha e os
  desligamentos já gravados continuam valendo); com assunto, ela o leva;
* o assunto canônico: maiúscula, acento, pontuação e espaço não mudam o assunto;
* o consumo (`licoes.nivel`): a lição com assunto não vai ao pedido sem assunto nem ao de outro assunto; vai ao do mesmo
  assunto, escrito de outro jeito; a lição sem assunto segue indo a todos;
* o fato sem assunto (ou com identificador no assunto) não nasce; o assunto é parte da identidade do item: o mesmo fato
  em duas operações do mesmo assunto é um item, e em outro assunto é outro;
* `GET /api/aprendizado`: o campo `assunto` e o filtro `?assunto=` (na forma canônica).

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from app.main import create_app
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.fatos_da_operacao import FatoDaOperacao, candidata
from app.modules.learning.domain.licoes import Nivel, Pedido, nivel
from app.modules.learning.domain.livro import Escopo, ItemDeAprendizado, assunto_canonico
from app.modules.learning.domain.vocabulario import LivroKind, Papel, SourceKind
from app.modules.learning.infrastructure.fatos_da_operacao_sql import FatosDaOperacaoParaOLivro
from app.util import now_iso

from .conftest import Harness
from .test_fatos_da_operacao_no_livro import FATO, FUTURO, PACOTE, _memoria

AGORA = "2026-10-07T00:00:00.000Z"


def test_a_chave_do_item_sem_assunto_nao_muda() -> None:
    sem = Escopo(app=PACOTE, capability="*", step_hash="h1", role="actor", profile_id="p1")
    assert sem.chave(LivroKind.LICAO) == f"licao|{PACOTE}|*|h1|actor|p1"              # a de antes da 128
    com = replace(sem, subject="festival de inverno")
    assert com.chave(LivroKind.LICAO) == f"licao|{PACOTE}|*|h1|actor|p1|assunto:festival de inverno"


def test_o_assunto_canonico() -> None:
    assert assunto_canonico("  Festival de INVERNO! ") == "festival de inverno"
    assert assunto_canonico("Festa junina — São João") == assunto_canonico("festa junina sao joao")
    assert assunto_canonico("...") == assunto_canonico(None) == ""
    assert len(assunto_canonico("a " * 200)) <= 120


def _licao(subject: str) -> ItemDeAprendizado:
    return ItemDeAprendizado(
        id="li-1", kind=LivroKind.LICAO, state=SkillState.PUBLISHED, state_detail=None,
        escopo=Escopo(app=PACOTE, role=Papel.WRITER.value, subject=subject), app_version=None, side_effect=False,
        human_origin=True, content={"fato": FATO}, content_hash="x", summary=FATO, tokens=10,
        source_kind=SourceKind.FATO_DA_OPERACAO, provenance={}, evidence_for=0, evidence_against=0, distinct_runs=0,
        distinct_devices=0, parent_id=None, created_by="sistema", created_at=AGORA, updated_at=None, state_at=AGORA,
        state_by="dono", last_used_at=None)


def test_a_licao_com_assunto_so_vai_ao_mesmo_assunto() -> None:
    def pedido(assunto: str) -> Pedido:
        return Pedido(papel=Papel.WRITER, unidade="u", run_id="r", app=PACOTE, capability="", step_hash="",
                      simulated=True, assunto=assunto)

    com = _licao("festival de inverno")
    assert nivel(com, pedido("")) is None                                    # o texto sem assunto não a recebe
    assert nivel(com, pedido("eleições da cidade")) is None                  # nem o de outro assunto
    assert nivel(com, pedido("Festival de Inverno!")) is Nivel.APP            # o mesmo, escrito de outro jeito
    sem = _licao("")
    assert nivel(sem, pedido("")) is Nivel.APP and nivel(sem, pedido("qualquer")) is Nivel.APP


def test_o_fato_nasce_com_o_assunto_e_sem_ele_nao_nasce() -> None:
    base = dict(operacao_id="op", chave="pesquisa.a1", tipo="descoberta", texto=FATO, confianca="confirmado",
                frescor_ate=FUTURO, pacote=PACOTE, assunto="Festival de Inverno")
    item = candidata(FatoDaOperacao(**base), AGORA)                                   # type: ignore[arg-type]
    assert item is not None and item.escopo.subject == "festival de inverno"
    assert item.provenance["assunto"] == "Festival de Inverno"                        # o texto cru fica na proveniência
    for assunto in ("", "!!!", "o post de @fulano"):
        assert candidata(FatoDaOperacao(**{**base, "assunto": assunto}), AGORA) is None, assunto  # type: ignore[arg-type]


def _operacao(db, op: str, assunto: str) -> None:      # type: ignore[no-untyped-def]
    agora = now_iso()
    db.execute("INSERT INTO operacoes(id, command, app_id, acao_final, max_usd, assunto, fontes, status,"
               " idempotency_key, corpo_sha256, created_at, updated_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (op, "comente", "qa-messenger", "preparar", 1.0, assunto, "[]", "concluida", f"k-{op}", "x", agora,
                agora, agora))


async def test_o_assunto_e_parte_da_identidade_e_o_livro_o_mostra_e_filtra(harness: Harness) -> None:
    st = harness.state
    db = st.db
    _operacao(db, "op-1", "Festival de Inverno")
    _memoria(db, "op-1", "pesquisa.f1", FATO)
    _operacao(db, "op-2", "festival de inverno!")                    # o mesmo assunto, escrito de outro jeito
    _memoria(db, "op-2", "pesquisa.f1", FATO)
    _operacao(db, "op-3", "Eleições da cidade")                       # outro assunto, o MESMO texto
    _memoria(db, "op-3", "pesquisa.f1", FATO)
    passo = FatosDaOperacaoParaOLivro(st.learning, st.learning._repo, db)               # type: ignore[attr-defined]
    assert passo.executar(datetime.now(timezone.utc)) == 2
    linhas = db.query("SELECT scope_subject, provenance FROM learning_items WHERE source_kind='operation_fact'"
                      " ORDER BY scope_subject")
    assert [r["scope_subject"] for r in linhas] == ["eleicoes da cidade", "festival de inverno"]
    assert json.loads(linhas[1]["provenance"])["operacao"] == "op-1"
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        todos = (await c.get("/api/aprendizado", params={"kind": "licao", "rotulo": "todos"})).json()["itens"]
        assert sorted(i["assunto"] for i in todos if i["assunto"]) == ["eleicoes da cidade", "festival de inverno"]
        so = (await c.get("/api/aprendizado", params={"kind": "licao", "rotulo": "todos",
                                                      "assunto": "FESTIVAL de inverno"})).json()
        assert [i["assunto"] for i in so["itens"]] == ["festival de inverno"] and so["total"] == 1
        assert (await c.get("/api/aprendizado", params={"assunto": "x" * 201})).status_code == 422
