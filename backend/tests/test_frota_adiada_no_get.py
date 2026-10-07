"""O alvo adiado pelo espaçamento da frota aparece no GET da operação com o motivo e a hora da retomada.

Com o 31.240, as contas aprovadas juntas sobre o mesmo alvo saem em série (120 s + jitter cada); o painel via o alvo só
"em curso" durante a espera. Agora `alvos[].motivo` = "espaçamento da frota" e `alvos[].retomada_em` = a retomada da
etapa represada. O motivo da etapa traz o @ do alvo: do GET só sai o vocabulário fixo e a hora.

Nível de prova: `simulated` (harness na porta 5640; a leitura do alvo trocada no teste).
"""
from __future__ import annotations

import json

from app.modules.operacoes.domain.estagios import Leitura
from app.modules.operacoes.infrastructure.servico import ESPACAMENTO_DA_FROTA, AlvoPedido
from app.social.policy import ESPACO_DA_FROTA
from app.util import now_iso

from .conftest import Harness
from .test_operacoes import _alvo, _conta, _pedido, _persona, _servico

RETOMADA = "2026-10-07T13:04:31.000Z"


def _aprovada(st: object, pid: str, run_id: str) -> None:
    """A ação preparada aprovada (é depois do sim que a porta reserva o alvo e a frota adia)."""
    db = st.db  # type: ignore[attr-defined]
    db.execute("INSERT INTO pending_approvals(id, profile_id, run_id, capability, status, created_at)"
               " VALUES (?,?,?,?,?,?)", (f"apr-{run_id}", pid, run_id, "CREATE_COMMENT", "approved", now_iso()))


def _etapa_represada(st: object, run_id: str, instancia: str, detalhe: str) -> None:
    db = st.db  # type: ignore[attr-defined]
    oid = f"{run_id}:{instancia}"
    if db.one("SELECT id FROM objectives WHERE id=?", (oid,)) is None:
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
                   " VALUES (?,?,?,'running',1,'{}')", (oid, run_id, instancia))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, status_detail,"
        " next_retry_at) VALUES (?,?,?,?,1,1,'c1','Comentar','comentar','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'retry_wait','CREATE_COMMENT',?,?)",
        (f"{oid}:v1:c1", run_id, oid, instancia, detalhe, RETOMADA))


async def test_o_alvo_represado_pela_frota_diz_o_motivo_e_a_retomada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, "qa-user-81", sessao_em="android-02")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-frota-adiada", acao_final="executar"))
    s._ler_alvo = lambda op_, a, d: (Leitura("acao_preparada", "em_curso", None, ()), None)  # type: ignore[method-assign]
    run_id = str(_alvo(op, pid)["run_id"])
    _aprovada(st, pid, run_id)
    assert (s.ler(op["id"])["alvos"][0]["motivo"], s.ler(op["id"])["alvos"][0]["retomada_em"]) == (None, None)
    _etapa_represada(st, run_id, "android-02",
                     f"outra conta da frota está agindo sobre @pagina.alvo agora; {ESPACO_DA_FROTA} (31.240)")
    lida = s.ler(op["id"])
    alvo = lida["alvos"][0]
    assert (alvo["estado"], alvo["motivo"], alvo["retomada_em"]) == ("em_curso", ESPACAMENTO_DA_FROTA, RETOMADA)
    assert lida["status"] == "em_curso"
    assert "pagina.alvo" not in json.dumps(lida, ensure_ascii=False)        # o @ do motivo da etapa não sai


async def test_a_espera_por_outro_limite_nao_vira_espacamento_da_frota(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, "qa-user-82", sessao_em="android-02")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-frota-outro-limite", acao_final="executar"))
    s._ler_alvo = lambda op_, a, d: (Leitura("acao_preparada", "em_curso", None, ()), None)  # type: ignore[method-assign]
    _aprovada(st, pid, str(_alvo(op, pid)["run_id"]))
    _etapa_represada(st, str(_alvo(op, pid)["run_id"]), "android-02", "limite de comentários por hora deste perfil")
    alvo = s.ler(op["id"])["alvos"][0]
    assert (alvo["motivo"], alvo["retomada_em"]) == (None, None)


async def test_o_alvo_que_espera_resposta_traz_a_pergunta_e_o_selo_conta(harness: Harness) -> None:
    """Pedido do Portal (onda 2 de 07/10, op-20261007100019-681b9b): as execuções em `needs_input` ("qual é o @ da
    página alvo…") apareciam "Em andamento". Agora `alvos[].aguarda_resposta = {pergunta, desde}` e
    `capacidade.aguardando_resposta` conta os alvos assim; o que não espera fica `null`."""
    st = harness.state
    assert st is not None
    pids = [_persona(harness, nome, inst) for nome, inst in (("Olivia", "android-02"), ("Nina", "android-03"))]
    for pid, (conta, inst) in zip(pids, (("qa-user-83", "android-02"), ("qa-user-84", "android-03"))):
        _conta(harness, pid, conta, sessao_em=inst)
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-aguarda-resposta", acao_final="executar"))
    s._ler_alvo = lambda op_, a, d: (Leitura("sessao", "pendente", None, ()), None)  # type: ignore[method-assign]
    run_id = str(_alvo(op, pids[0])["run_id"])
    st.db.execute("UPDATE runs SET status='needs_input', status_detail=? WHERE id=?",
                  ("Qual é o @ da página alvo em que devo comentar no primeiro post?", run_id))
    st.bus.emit("run.updated", f"Execução {run_id}: faltam informações", run_id=run_id)
    lida = s.ler(op["id"])
    espera = _alvo(lida, pids[0])["aguarda_resposta"]
    assert espera["pergunta"] == "Qual é o @ da página alvo em que devo comentar no primeiro post?" and espera["desde"]
    assert _alvo(lida, pids[1])["aguarda_resposta"] is None
    assert lida["capacidade"]["aguardando_resposta"] == 1
