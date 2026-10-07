"""31.217: curadoria por operação encerrada: o passo do 31.190 roda para a operação assim que ela encerra, com o relatório.

Antes, o fato da pesquisa só chegava ao Livro na volta periódica da curadoria (`aprendizado.curadoria_s`, 15 min), e
ninguém sabia o que ela tinha promovido ou recusado.

O que estes testes protegem:
* o motivo da recusa é um vocabulário fechado, na mesma régua da `candidata` (o fato que nasce não tem motivo);
* `da_operacao` dá, para UMA operação encerrada: as candidatas que nasceram (ids), as que já estavam no Livro, as
  recusadas por motivo, as vetadas e os fatos vivos do Livro do mesmo app cujo frescor venceu; a operação aberta ou
  desconhecida dá None; o relatório só tem ids e contagens, nunca o texto do fato;
* o laço da curadoria ouve `operacao.encerrada` e publica `aprendizado.curadoria_da_operacao` sem esperar a volta.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.modules.learning.domain.fatos_da_operacao import FatoDaOperacao, MotivoDaRecusa, candidata, recusa, vencida
from app.modules.learning.infrastructure.fatos_da_operacao_sql import FatosDaOperacaoParaOLivro

from .conftest import Harness
from .test_fatos_da_operacao_no_livro import FATO, FUTURO, PACOTE, _memoria, _operacao

AGORA = "2026-10-07T00:00:00.000Z"


def test_o_motivo_da_recusa_e_a_mesma_regua_da_candidata() -> None:
    base = dict(operacao_id="op", chave="pesquisa.a1", tipo="descoberta", texto=FATO, confianca="confirmado",
                frescor_ate=FUTURO, pacote=PACOTE, assunto="festival de inverno")
    casos = {
        None: {},
        MotivoDaRecusa.NAO_E_FATO: {"chave": "fonte.x"},
        MotivoDaRecusa.HIPOTESE: {"confianca": "hipotese"},
        MotivoDaRecusa.VENCIDO: {"frescor_ate": "2026-10-06T00:00:00.000Z"},
        MotivoDaRecusa.SEM_APP: {"pacote": ""},
        MotivoDaRecusa.LONGO: {"texto": "x" * 300},
        MotivoDaRecusa.IDENTIFICADOR: {"texto": "A conta @fulano.oficial anunciou o festival."},
        MotivoDaRecusa.SEM_ASSUNTO: {"assunto": "!!!"},
    }
    for motivo, troca in casos.items():
        f = FatoDaOperacao(**{**base, **troca})                                       # type: ignore[arg-type]
        assert recusa(f, AGORA) is motivo, troca
        assert (candidata(f, AGORA) is None) is (motivo is not None), troca
    assert vencida({"frescor_ate": "2026-10-06T00:00:00.000Z"}, AGORA)
    assert not vencida({"frescor_ate": FUTURO}, AGORA) and not vencida({}, AGORA)


async def test_o_relatorio_de_uma_operacao(harness: Harness) -> None:
    st = harness.state
    db = st.db
    _operacao(db, "op-1")
    _memoria(db, "op-1", "pesquisa.f1", FATO)
    _memoria(db, "op-1", "pesquisa.h1", "Talvez o festival mude de data.", confianca="hipotese")
    _memoria(db, "op-1", "pesquisa.v1", "Fato vencido do festival.", frescor="2020-01-01T00:00:00.000Z")
    _memoria(db, "op-1", "pesquisa.a1", "A conta @fulano.oficial anunciou o festival.")
    _operacao(db, "op-aberta", encerrada=False)
    passo = FatosDaOperacaoParaOLivro(st.learning, st.learning._repo, db)               # type: ignore[attr-defined]
    agora = datetime.now(timezone.utc)
    rel = passo.da_operacao("op-1", agora)
    assert rel is not None
    (nascida,) = rel["nascidas"]                                                         # type: ignore[misc]
    assert rel["ja_no_livro"] == 0 and rel["vetadas"] == 0 and rel["app"] == PACOTE
    assert rel["recusadas"] == {"hipotese": 1, "identificador": 1, "vencido": 1}
    assert rel["vencidas_no_livro"] == []
    assert FATO not in json.dumps(rel, ensure_ascii=False)                               # só ids e contagens
    # a segunda passada acha o item; o frescor do item no Livro vence e aparece
    db.execute("UPDATE learning_items SET provenance=? WHERE id=?",
               (json.dumps({"frescor_ate": "2020-01-01T00:00:00.000Z"}), nascida))
    de_novo = passo.da_operacao("op-1", agora)
    assert de_novo is not None and de_novo["nascidas"] == [] and de_novo["ja_no_livro"] == 1
    assert de_novo["vencidas_no_livro"] == [nascida]
    assert passo.da_operacao("op-aberta", agora) is None and passo.da_operacao("op-nada", agora) is None
    pelo_servico = st.learning.curar_operacao("op-1")                                 # os passos por operação
    assert pelo_servico is not None and list(pelo_servico) == ["fatos_da_operacao"]
    assert st.learning.curar_operacao("op-aberta") is None


async def test_o_laco_ouve_a_operacao_encerrada_e_publica_o_relatorio(harness: Harness) -> None:
    st = harness.state
    db = st.db
    _operacao(db, "op-2")
    _memoria(db, "op-2", "pesquisa.f1", FATO)
    st.bus.emit("operacao.encerrada", "Operação op-2 encerrada: concluida.",
                data={"operacao_id": "op-2", "status": "concluida"})

    def publicado() -> bool:
        return db.scalar("SELECT COUNT(*) FROM events WHERE kind='aprendizado.curadoria_da_operacao'") == 1

    await harness.wait(publicado, 10.0, "relatório da curadoria da operação")
    (dados,) = [json.loads(r["data"]) for r in db.query(
        "SELECT data FROM events WHERE kind='aprendizado.curadoria_da_operacao'")]
    assert dados["operacao_id"] == "op-2" and len(dados["fatos_da_operacao"]["nascidas"]) == 1
    assert db.scalar("SELECT COUNT(*) FROM learning_items WHERE source_kind='operation_fact'") == 1
