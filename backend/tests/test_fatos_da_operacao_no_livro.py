"""31.190: o fato confirmado da pesquisa de uma operação encerrada vira candidata do escritor no Livro.

A memória da operação (125) decide a confiança por código e o frescor; o fato morria com a operação, e a próxima sobre o
mesmo assunto pagava a pesquisa de novo.

O que estes testes protegem:
* só o fato de pesquisa confirmado, dentro do frescor, de operação ENCERRADA nasce no Livro: a hipótese, o vencido, a
  leitura do alvo, a fonte, o estado da pesquisa e o fato de operação aberta, não;
* nasce `candidate`, papel `writer`, escopo do pacote do app, sem evidência de repetição: a esteira das lições não o
  valida nem o expõe ao prompt; a proveniência diz operação, assunto, domínios, frescor e uso no texto;
* o passo é idempotente, e o MESMO fato em outra operação cai no mesmo item.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from app.main import create_app
from app.modules.learning.domain.fatos_da_operacao import FatoDaOperacao, candidata, proveniencia_do_fato
from app.modules.learning.infrastructure.fatos_da_operacao_sql import FatosDaOperacaoParaOLivro
from app.util import now_iso

from .conftest import Harness

PACOTE = "com.pocqa.messenger"
FATO = "O festival de inverno da cidade acontece em julho desde 1970."
FUTURO = "2099-01-01T00:00:00.000Z"


def _operacao(db, op: str, *, encerrada: bool = True) -> None:      # type: ignore[no-untyped-def]
    agora = now_iso()
    db.execute("INSERT INTO operacoes(id, command, app_id, acao_final, max_usd, assunto, fontes, status,"
               " idempotency_key, corpo_sha256, created_at, updated_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (op, "comente", "qa-messenger", "preparar", 1.0, "festival de inverno", "[]",
                "concluida" if encerrada else "em_curso", f"k-{op}", "x", agora, agora, agora if encerrada else None))


def _memoria(db, op: str, chave: str, valor: str, *, tipo: str = "descoberta", confianca: str = "confirmado",
             frescor: str | None = FUTURO, evidencia: list[str] | None = None) -> None:      # type: ignore[no-untyped-def]
    db.execute("INSERT INTO pedido_memoria(id, operacao_id, chave, tipo, valor, atualizada_em, origem, confianca,"
               " evidencia, frescor_ate) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (f"m-{op}-{chave}", op, chave, tipo, valor, now_iso(), "pesquisa", confianca,
                json.dumps(evidencia or []), frescor))


def _fonte(db, op: str, oid: str, url: str) -> None:      # type: ignore[no-untyped-def]
    db.execute("INSERT INTO pedido_observacoes(id, operacao_id, ocorrencia_id, nome, tipo, situacao, valor,"
               " capturado_em) VALUES (?,?,?,?,?,?,?,?)", (oid, op, "r-1", f"fonte_{oid}", "url", "observado", url,
                                                          now_iso()))


def test_so_o_fato_confirmado_de_pesquisa_dentro_do_frescor() -> None:
    base = dict(operacao_id="op", chave="pesquisa.a1", tipo="descoberta", texto=FATO, confianca="confirmado",
                frescor_ate=FUTURO, pacote=PACOTE)
    agora = "2026-10-07T00:00:00.000Z"
    item = candidata(FatoDaOperacao(**base), agora)                                   # type: ignore[arg-type]
    assert item is not None and item.escopo.role == "writer" and item.escopo.app == PACOTE
    assert item.content == {"modelo": "fato_da_operacao", "fato": FATO}
    assert item.human_origin                                       # trava do D1: só o dono publica (revisão de segredos)
    com_arroba = FatoDaOperacao(**{**base, "assunto": "o post de @fulano"})                # type: ignore[arg-type]
    assert candidata(com_arroba, agora).provenance["assunto"] == ""                     # type: ignore[union-attr]
    for troca in ({"confianca": "hipotese"}, {"frescor_ate": "2026-10-06T00:00:00.000Z"},
                  {"chave": "alvo.conteudo"}, {"chave": "pesquisa.estado"}, {"tipo": "fonte"},
                  {"pacote": ""}, {"texto": "x" * 300}, {"texto": "A conta @fulano.oficial anunciou o festival."},
                  {"texto": "Contato do festival: festival@exemplo.org."}):
        assert candidata(FatoDaOperacao(**{**base, **troca}), agora) is None, troca   # type: ignore[arg-type]


async def test_o_passo_leva_ao_livro_uma_vez_e_so_a_candidata(harness: Harness) -> None:
    st = harness.state
    db = st.db
    _operacao(db, "op-1")
    _fonte(db, "op-1", "ob-1", "https://www.exemplo.org/a")
    _fonte(db, "op-1", "ob-2", "https://outro.exemplo.net/b")
    _memoria(db, "op-1", "pesquisa.f1", FATO, evidencia=["ob-1", "ob-2"])
    _memoria(db, "op-1", "pesquisa.h1", "Talvez o festival mude de data.", confianca="hipotese")
    _memoria(db, "op-1", "pesquisa.v1", "Fato vencido do festival.", frescor="2020-01-01T00:00:00.000Z")
    _memoria(db, "op-1", "alvo.conteudo", "o post fala do festival")
    db.execute("INSERT INTO operacao_alvos(operacao_id, seq, profile_id, estagio, estado, marcas, updated_at)"
               " VALUES (?,?,?,?,?,?,?)", ("op-1", 1, "p1", "resultado_verificado", "concluido",
                                          json.dumps({"conhecimento_ids": ["fato:pesquisa.f1"]}), now_iso()))
    _operacao(db, "op-aberta", encerrada=False)
    _memoria(db, "op-aberta", "pesquisa.f9", "Outro fato confirmado de operação aberta.")
    passo = FatosDaOperacaoParaOLivro(st.learning, st.learning._repo, db)               # type: ignore[attr-defined]
    agora = datetime.now(timezone.utc)
    assert passo.executar(agora) == 1
    assert passo.executar(agora) == 0                                                  # idempotente
    (linha,) = db.query("SELECT * FROM learning_items WHERE source_kind='operation_fact'")
    assert (linha["kind"], linha["state"], linha["scope_role"], linha["scope_app"]) == ("licao", "candidate",
                                                                                       "writer", PACOTE)
    assert linha["summary"] == FATO and linha["human_origin"] == 1
    prov = json.loads(linha["provenance"])
    assert prov["operacao"] == "op-1" and prov["assunto"] == "festival de inverno" and prov["usado_em"] == 1
    assert prov["fontes"] == ["exemplo.org", "outro.exemplo.net"] and prov["frescor_ate"] == FUTURO
    assert db.scalar("SELECT COUNT(*) FROM learning_evidence WHERE item_ref=?", (f"licao:{linha['id']}",)) == 0
    # o MESMO fato em outra operação encerrada cai no mesmo item
    _operacao(db, "op-2")
    _memoria(db, "op-2", "pesquisa.f1", FATO)
    assert passo.executar(agora) == 0
    assert db.scalar("SELECT COUNT(*) FROM learning_items WHERE source_kind='operation_fact'") == 1


async def test_o_livro_mostra_a_proveniencia_do_fato_v1_117(harness: Harness) -> None:
    """Adendo v1.117 (31.214 da Portal): `source_kind` na lista e `proveniencia` no detalhe, só do fato da operação,
    com chaves fechadas (a regra e a chave da memória não saem)."""
    st = harness.state
    db = st.db
    _operacao(db, "op-1")
    _fonte(db, "op-1", "ob-1", "https://www.exemplo.org/a")
    _memoria(db, "op-1", "pesquisa.f1", FATO, evidencia=["ob-1"])
    FatosDaOperacaoParaOLivro(st.learning, st.learning._repo, db).executar(  # type: ignore[attr-defined]
        datetime.now(timezone.utc))
    item_id = db.scalar("SELECT id FROM learning_items WHERE source_kind='operation_fact'")
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        lista = (await c.get("/api/aprendizado", params={"kind": "licao", "rotulo": "todos"})).json()["itens"]
        detalhe = (await c.get(f"/api/aprendizado/licao/{item_id}")).json()
    assert [i["source_kind"] for i in lista if i["ref"] == item_id] == ["operation_fact"]
    assert detalhe["proveniencia"] == {"operacao": "op-1", "assunto": "festival de inverno", "fontes": ["exemplo.org"],
                                       "frescor_ate": FUTURO, "usado_em": 0, "execucoes": [],
                                       "confianca": "confirmado"}
    assert proveniencia_do_fato({"modelo": "contraste"}, {"operacao": "op-1"}) is None    # só o fato da operação
