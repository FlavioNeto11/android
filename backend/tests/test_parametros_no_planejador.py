"""31.236: os parâmetros fixos da operação chegam ao planejador e respondem a pergunta pelo mesmo nome.

A onda 2 de 07/10 (op-20261007100019-681b9b) parou: a operação tinha `parametros.username`, mas o planejador só via o
comando, perguntou "qual é o @ da página alvo" e as três execuções foram a `needs_input`. Os fixos só entravam DEPOIS
do plano (`plano_da_operacao.fixar_parametros`), e o `missing` do plano não era tocado.

O que estes testes protegem:
* o pedido ao planejador leva `parametros_fixos` na execução de operação, e o texto de usuário dos três modos de
  planejamento traz o bloco; fora de operação, o pedido e o texto ficam os de antes;
* a pergunta (`missing`) cujo campo é o nome de um fixo é respondida por ele: a execução segue para o objetivo, com o
  valor fixo nos parâmetros, e a decisão registra só o NOME; a pergunta por outro campo ainda vai a `needs_input`.

Nível de prova: `simulated` (harness na porta 5640, provedor simulado com o plano trocado no teste).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.db import loads
from app.models import MissingInfo, Plan, PlannerInfo
from app.modules.operacoes.infrastructure.servico import AlvoPedido
from app.planning.prompts import parametros_fixos_block, planner_user
from app.planning.provider import PlanRequest
from app.taskqueue import plano_da_operacao as pdo

from .conftest import Harness
from .test_operacoes import _alvo, _conta, _pedido, _persona, _servico

FIXOS = {"contato": "QA-001"}


def test_a_pergunta_pelo_nome_de_um_fixo_sai_do_plano_e_a_outra_fica() -> None:
    plano = Plan(summary="x", planner=PlannerInfo(provider="t", model="t", simulated=True), missing=[
        MissingInfo(field="Contato", question="Qual é o contato?"),
        MissingInfo(field="mensagem", question="O que escrever?")])
    novo, respondidos = pdo.sem_perguntas_dos_fixos(plano, FIXOS)
    assert respondidos == ["Contato"] and [m.field for m in novo.missing] == ["mensagem"]
    assert pdo.sem_perguntas_dos_fixos(plano, {}) == (plano, [])


def test_o_bloco_vai_ao_texto_do_planejador_so_com_fixos() -> None:
    sem = PlanRequest(command="mande oi", run_id="r-1", instances=[], apps=[])
    com = PlanRequest(command="mande oi", run_id="r-1", instances=[], apps=[], parametros_fixos=dict(FIXOS))
    assert parametros_fixos_block({}) == "" and "parametros_da_operacao" not in planner_user(sem, 10)
    texto = planner_user(com, 10)
    assert "<parametros_da_operacao" in texto and "- contato = QA-001" in texto
    # o bloco é o único acréscimo: tirá-lo devolve o texto de antes, byte a byte (o cache do prompt não muda fora dele)
    assert texto.replace(parametros_fixos_block(FIXOS), "") == planner_user(sem, 10)


def _com_pergunta(st: Any, monkeypatch: pytest.MonkeyPatch, campo: str) -> list[PlanRequest]:
    pedidos: list[PlanRequest] = []
    original = st.runs.provider.plan

    async def planejador(req: PlanRequest) -> Any:
        pedidos.append(req)
        plano, uso = await original(req)
        return plano.model_copy(update={"missing": [MissingInfo(field=campo, question="Qual é o contato alvo?")]}), uso
    monkeypatch.setattr(st.runs.provider, "plan", planejador)
    return pedidos


async def test_a_operacao_com_o_fixo_nao_para_na_pergunta(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    pedidos = _com_pergunta(st, monkeypatch, "contato")
    pid = _persona(harness, "Nina", "android-01")
    _conta(harness, pid, "qa-user-01", sessao_em="android-01")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-31-236", parametros=dict(FIXOS)))
    run_id = _alvo(op, pid)["run_id"]
    await harness.wait(lambda: st.db.one("SELECT id FROM objectives WHERE run_id=?", (run_id,)) is not None,
                       what="plano da operação materializado sem needs_input")
    assert st.db.scalar("SELECT status FROM runs WHERE id=?", (run_id,)) != "needs_input"
    assert [p.parametros_fixos for p in pedidos] == [FIXOS]
    params = loads(st.db.scalar("SELECT parameters FROM objectives WHERE run_id=?", (run_id,)), {})
    assert params.get("contato") == "QA-001"
    decisoes = [str(r["message"]) for r in st.db.query("SELECT message FROM events WHERE run_id=? AND kind='decision'",
                                                       (run_id,))]
    respondida = [d for d in decisoes if "respondida pelo parâmetro fixo" in d]
    assert respondida and "QA-001" not in respondida[0]                      # só o nome vai ao texto


async def test_a_pergunta_por_outro_campo_ainda_espera_a_pessoa(harness: Harness,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    _com_pergunta(st, monkeypatch, "mensagem")
    pid = _persona(harness, "Nina", "android-01")
    _conta(harness, pid, "qa-user-01", sessao_em="android-01")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-31-236-outra", parametros=dict(FIXOS)))
    run_id = _alvo(op, pid)["run_id"]
    await harness.wait(lambda: st.db.scalar("SELECT status FROM runs WHERE id=?", (run_id,)) == "needs_input",
                       what="a pergunta que nenhum fixo responde")


async def test_fora_de_operacao_o_pedido_nao_leva_fixos(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    pedidos: list[PlanRequest] = []
    original = st.runs.provider.plan

    async def planejador(req: PlanRequest) -> Any:
        pedidos.append(req)
        return await original(req)
    monkeypatch.setattr(st.runs.provider, "plan", planejador)
    run = harness.run(["android-02"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=30)
    assert [p.parametros_fixos for p in pedidos] == [{}]
