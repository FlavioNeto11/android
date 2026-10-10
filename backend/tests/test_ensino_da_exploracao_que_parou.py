"""31.312 (adendo v1.138): a exploração que parou vira pedido de ensino.

A IA explorou um app fora do catálogo (ADR-084) e `parou_no_teto` ou não chegou lá (`failed`). Hoje isso só vira linha de
relatório; aqui a pessoa ensina o caminho uma vez, pelo "Ensinar a corrigir" que já existe (31.111), e todas as personas passam a
usá-lo. O que muda no backend, sem migração e sem IA:

* `GET …/ensino-sugerido` e `POST /training/from-run`: a intenção é «Ensinar à IA como fazer: <frase da chave>» (vocabulário fechado:
  nunca o pedido, que pode ter um nome, e que viajaria na intenção da sessão), e a pergunta é a da exploração;
* `origin.exploracao` na sessão de ensino (o painel rotula);
* o aviso do Telegram de uma exploração que parou lembra que dá para ensinar.

Prova `simulated` (harness, aparelho falso, nenhuma IA). `real`: `not_run`.
"""
from __future__ import annotations

from typing import Any

from app.modules.avisos.domain.mensagem import aviso_de_evento
from app.modules.learning.domain import ensino_da_falha as ef

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_a_partir_da_falha import _com_controle, _execucao

PEDIDO_COM_NOME = "Ver o perfil de joao.silva123"
CHAVE = "explorar_ver_perfil"


def _explorada(st: Any, *, status: str = "failed", detalhe: str = "Teto da exploração: 30 chamadas de IA", tentativas: int = 1):
    run, step = _execucao(st, status, tentativas=tentativas)
    st.db.execute("UPDATE steps SET key=?, title=?, exploratoria=1, status_detail=? WHERE id=?",
                  (CHAVE, f"Explorar o QA: {PEDIDO_COM_NOME}", detalhe, step))
    return run, step


def test_as_frases_sao_so_do_vocabulario_da_chave() -> None:
    assert ef.frase_da_exploracao(CHAVE) == "ver perfil"
    assert ef.intencao_da_exploracao(CHAVE) == "Ensinar à IA como fazer: ver perfil"
    assert "teto" in ef.pergunta_da_exploracao(True) and "teto" not in ef.pergunta_da_exploracao(False)
    assert "todas as personas" in ef.pergunta_da_exploracao(False)


async def test_a_sugestao_da_exploracao_que_parou_no_teto_nao_leva_o_pedido(harness: Harness) -> None:
    st = harness.state
    run, step = _explorada(st)
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")).json()
    assert corpo["exploracao"] is True and corpo["parou_no_teto"] is True
    assert corpo["intent"] == "Ensinar à IA como fazer: ver perfil"
    assert corpo["pergunta"] == ef.pergunta_da_exploracao(True)
    assert "joao" not in str(corpo)


async def test_a_exploracao_que_so_nao_chegou_la_nao_diz_teto(harness: Harness) -> None:
    st = harness.state
    run, step = _explorada(st, detalhe="a tela não mostrou o que o pedido diz")
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")).json()
    assert corpo["exploracao"] is True and corpo["parou_no_teto"] is False
    assert corpo["pergunta"] == ef.pergunta_da_exploracao(False)


async def test_a_falha_de_sempre_segue_igual(harness: Harness) -> None:
    st = harness.state
    run, step = _execucao(st, tentativas=1)
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/runs/{run}/steps/{step}/ensino-sugerido")).json()
    assert "exploracao" not in corpo and corpo["intent"].startswith("Corrigir a etapa «Enviar a mensagem»")


async def test_a_sessao_de_ensino_nasce_da_exploracao_com_a_intencao_da_chave(harness: Harness) -> None:
    st, _rt, lease = await _com_controle(harness)
    run, step = _explorada(st)
    async with _cliente(harness) as c:
        r = await c.post("/api/training/from-run", json={"run_id": run, "step_id": step, "lease_id": lease})
        assert r.status_code == 201, r.text
        sessao = r.json()
        assert sessao["intent"] == "Ensinar à IA como fazer: ver perfil"
        assert sessao["origin"]["exploracao"] is True and sessao["origin"]["step_key"] == CHAVE
        lido = (await c.get(f"/api/training/{sessao['id']}")).json()
        assert lido["origin"]["exploracao"] is True
    assert "joao" not in sessao["intent"]


def test_o_aviso_da_exploracao_que_parou_lembra_que_da_para_ensinar() -> None:
    run = "r-20261010140000-abcdef"

    def fim(resultado: str) -> Any:
        return aviso_de_evento("exploracao.concluida", {"run_id": run, "resultado": resultado}, 7, "http://painel")

    for parou in ("parou_no_teto", "falhou"):
        a = fim(parou)
        assert a is not None and "Ensinar a corrigir" in a.corpo and "todas as personas" in a.corpo, parou
    assert "Ensinar a corrigir" not in fim("concluida").corpo
    assert "Ensinar a corrigir" not in fim("cancelada").corpo
