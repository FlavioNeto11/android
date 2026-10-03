"""29.50: a pergunta (`needs_input`) sem resposta por 24 h expira PELO SISTEMA.

Antes, a execução esperava para sempre. Só o cancelamento pela rota a tirava do ar, e ele é um sinal de PESSOA
(`cancelou_execucao`, ADR-054) que ninguém deu: 13 execuções de QA ficaram assim no android-05 e no android-09 (02 e
03/10). Agora a execução fecha como `cancelled`, com o motivo humano no texto, a marca `expirada` num campo próprio
do `run.updated`, e sem sinal de pessoa.

Prova `simulated`: o harness com o provedor simulado. O relógio é o `agora` passado ao serviço; o laço passa `now()`.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from app.taskqueue.assistente import ComandoAssistido, RunSuccessorBody
from app.taskqueue.service import NEEDS_INPUT_EXPIRA_H
from app.util import now, parse_iso, to_iso

from .conftest import COMMAND, Harness

INCOMPLETO = "Abra o QA Messenger e envie uma mensagem"


async def _pergunta(h: Harness) -> tuple[str, Any]:
    """Uma execução real em `needs_input` e o instante em que ela entrou lá (o `run.updated` da transição)."""
    run = h.run(["android-01"], command=INCOMPLETO, mode="plan")
    await h.wait_run(run.id, ("needs_input",))
    assert h.state is not None
    entrada = h.state.db.scalar("SELECT MAX(ts) FROM events WHERE run_id=? AND kind='run.updated'", (run.id,))
    return run.id, parse_iso(entrada)


def _sinais(h: Harness, kind: str) -> list[dict[str, Any]]:
    assert h.state is not None
    return [dict(r) for r in h.state.db.query("SELECT * FROM learning_signals WHERE kind=? ORDER BY id", (kind,))]


def _ultimo_run_updated(h: Harness, run_id: str) -> dict[str, Any]:
    assert h.state is not None
    row = h.state.db.one("SELECT data FROM events WHERE run_id=? AND kind='run.updated' ORDER BY id DESC LIMIT 1",
                         (run_id,))
    return json.loads(row["data"])


async def test_a_pergunta_sem_resposta_expira_em_24_h_pelo_sistema_sem_sinal_de_pessoa(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id, entrada = await _pergunta(harness)
    # Um minuto antes do prazo, nada muda.
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=NEEDS_INPUT_EXPIRA_H, minutes=-1)) == []
    assert st.repo.run_row(run_id)["status"] == "needs_input"
    # Passado o prazo, o sistema encerra.
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=NEEDS_INPUT_EXPIRA_H, seconds=1)) == [run_id]
    run = st.repo.run_row(run_id)
    assert (run["status"], run["cancel_requested"]) == ("cancelled", 1)
    assert run["finished_at"] is not None
    # O texto é para o dono: o motivo humano, sem código nem id.
    assert run["status_detail"].startswith("Sem resposta em 24 h: a pergunta expirou")
    for cru in ("needs_input", "sem_resposta", "expirada", run_id):
        assert cru not in run["status_detail"]
    # A marca para máquina vai num campo próprio do `run.updated` da transição.
    dados = _ultimo_run_updated(harness, run_id)
    assert dados["run"]["status"] == "cancelled"
    assert dados["expirada"] == {"motivo": "sem_resposta", "horas": 24, "desde": to_iso(entrada)}
    # Ninguém fez o gesto: nenhum sinal de pessoa.
    assert _sinais(harness, "cancelou_execucao") == []
    # Idempotente: a segunda volta não acha nada.
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=NEEDS_INPUT_EXPIRA_H + 1)) == []


async def test_o_relogio_e_a_entrada_em_needs_input_e_sem_evento_vale_a_criacao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id, entrada = await _pergunta(harness)
    # Criada há 3 dias, mas a pergunta é recente: o prazo conta da entrada, não da criação.
    st.db.execute("UPDATE runs SET created_at=? WHERE id=?", (to_iso(entrada - timedelta(days=3)), run_id))
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=1)) == []
    assert st.repo.run_row(run_id)["status"] == "needs_input"
    # Sem evento nenhum da execução (anterior aos eventos), vale a criação: 3 dias passam do prazo.
    st.db.execute("DELETE FROM events WHERE run_id=?", (run_id,))
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=1)) == [run_id]
    assert st.repo.run_row(run_id)["status"] == "cancelled"


async def test_a_resposta_da_pessoa_no_meio_da_varredura_ganha_da_expiracao(harness: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """A pessoa responde entre a leitura da varredura e o cancelamento: a resposta vale e o texto dela fica."""
    st = harness.state
    assert st is not None
    run_id, entrada = await _pergunta(harness)
    db, original, respondida = st.db, st.db.scalar, []

    def scalar_com_resposta(sql: str, params: Any = ()) -> Any:
        valor = original(sql, params)
        if "MAX(ts) FROM events" in sql and not respondida:
            respondida.append(ComandoAssistido(st.runs).sucessora(run_id, RunSuccessorBody(command=COMMAND,
                                                                                          mode="plan")))
        return valor

    monkeypatch.setattr(db, "scalar", scalar_com_resposta)
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=NEEDS_INPUT_EXPIRA_H + 1)) == []
    assert respondida
    run = st.repo.run_row(run_id)
    assert run["status"] == "cancelled"
    assert run["status_detail"].startswith("Respondida: continua na execução")
    assert "expirada" not in _ultimo_run_updated(harness, run_id)


async def test_a_volta_do_laco_expira_com_o_relogio_de_verdade(harness: Harness) -> None:
    """O caminho do laço (`AppState._expiracao_uma_vez`, sob a trava da retenção) com `now()`: a pergunta é envelhecida
    no banco, em vez de o relógio ser adiantado."""
    st = harness.state
    assert st is not None
    run_id, _ = await _pergunta(harness)
    velho = to_iso(now() - timedelta(hours=NEEDS_INPUT_EXPIRA_H + 1))
    st.db.execute("UPDATE runs SET created_at=? WHERE id=?", (velho, run_id))
    st.db.execute("UPDATE events SET ts=? WHERE run_id=?", (velho, run_id))
    assert await st._expiracao_uma_vez() is True                     # noqa: SLF001
    assert st.repo.run_row(run_id)["status"] == "cancelled"
