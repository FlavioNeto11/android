"""31.241: a operação com `acao_final=executar` reabre quando a ação é aprovada por fora do liberar, e fecha de novo.

Onda 2 de 07/10 (op-20261007100755-096a28): a operação fechou às 10:09:29Z em "aguarda liberação" ×3; as aprovações
vieram pelas Pendências às 10:12:51Z, os comentários saíram e foram verificados até 10:14:00Z, mas o banco ficou com
`concluida_com_bloqueios`, o `finished_at` do preparo e nenhum `operacao.encerrada` novo. A reabertura do `ler` só
cobria `preparar`.

O que estes testes protegem:
* o alvo que volta a correr reabre a operação fechada (status `em_curso`, sem `finished_at`), e o fechamento seguinte
  grava o fim do último estágio real e avisa `operacao.encerrada` de novo;
* sem leitura no meio, a operação fechada cujos alvos terminaram com outro resultado fecha de novo com o fim real;
* a cancelada nunca reabre; a leitura repetida não grava nem avisa de novo.

Nível de prova: `simulated` (harness na porta 5640; a leitura do alvo trocada no teste).
"""
from __future__ import annotations

from typing import Any

from app.db import loads
from app.modules.operacoes.domain.estagios import Leitura
from app.modules.operacoes.infrastructure.servico import AlvoPedido, ServicoDeOperacoes
from app.util import now_iso

from .conftest import Harness
from .test_operacoes import _alvo, _conta, _pedido, _persona, _servico

PREPARO = "2026-10-07T10:09:29.918Z"
FIM = "2026-10-07T10:14:00.417Z"
PREPARADA = Leitura("acao_preparada", "em_curso", None, (("acao_preparada", PREPARO),))
VERIFICADA = Leitura("resultado_verificado", "concluido", None,
                     (("acao_preparada", PREPARO), ("resultado_verificado", FIM)))


def _encerradas(st: Any, op_id: str) -> list[str]:
    return [str(loads(r["data"], {}).get("status")) for r in st.db.query(
        "SELECT data FROM events WHERE kind='operacao.encerrada' ORDER BY id") if loads(r["data"], {}).get(
        "operacao_id") == op_id]


def _fechada_aguardando(harness: Harness, chave: str) -> tuple[Any, ServicoDeOperacoes, dict[str, Any], str]:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, f"qa-user-{chave[-2:]}", sessao_em="android-02")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave=chave, acao_final="executar"))
    s._ler_alvo = lambda op_, a, d: (PREPARADA, None)  # type: ignore[method-assign]
    lida = s.ler(op["id"])
    assert (lida["status"], lida["alvos"][0]["motivo"]) == ("concluida_com_bloqueios", "aguarda liberação")
    assert lida["finished_at"] and _encerradas(st, op["id"]) == ["concluida_com_bloqueios"]
    # A aprovação POR FORA do liberar (Pendências): sem a nota do liberar, direto no pedido da execução do alvo
    st.db.execute("INSERT INTO pending_approvals(id, profile_id, run_id, capability, status, created_at)"
                  " VALUES (?,?,?,?,?,?)", (f"apr-{chave}", pid, _alvo(op, pid)["run_id"], "CREATE_COMMENT",
                                            "approved", now_iso()))
    return st, s, op, pid


async def test_o_alvo_que_volta_a_correr_reabre_e_o_fechamento_tem_o_fim_real(harness: Harness) -> None:
    st, s, op, _ = _fechada_aguardando(harness, "teste-op-31241-a1")
    lida = s.ler(op["id"])                                        # aprovado: o alvo segue a execução, em curso
    assert (lida["status"], lida["finished_at"]) == ("em_curso", None)
    linha = st.db.one("SELECT status, finished_at FROM operacoes WHERE id=?", (op["id"],))
    assert (linha["status"], linha["finished_at"]) == ("em_curso", None)
    s._ler_alvo = lambda op_, a, d: (VERIFICADA, None)  # type: ignore[method-assign]
    lida = s.ler(op["id"])
    assert (lida["status"], lida["finished_at"]) == ("concluida", FIM)
    linha = st.db.one("SELECT status, finished_at FROM operacoes WHERE id=?", (op["id"],))
    assert (linha["status"], linha["finished_at"]) == ("concluida", FIM)
    assert _encerradas(st, op["id"]) == ["concluida_com_bloqueios", "concluida"]
    s.ler(op["id"])                                               # a leitura repetida não grava nem avisa de novo
    assert _encerradas(st, op["id"]) == ["concluida_com_bloqueios", "concluida"]


async def test_sem_leitura_no_meio_fecha_de_novo_com_o_fim_real(harness: Harness) -> None:
    st, s, op, _ = _fechada_aguardando(harness, "teste-op-31241-b2")
    s._ler_alvo = lambda op_, a, d: (VERIFICADA, None)  # type: ignore[method-assign]
    lida = s.ler(op["id"])
    assert (lida["status"], lida["finished_at"]) == ("concluida", FIM)
    linha = st.db.one("SELECT status, finished_at FROM operacoes WHERE id=?", (op["id"],))
    assert (linha["status"], linha["finished_at"]) == ("concluida", FIM)
    assert _encerradas(st, op["id"]) == ["concluida_com_bloqueios", "concluida"]


async def test_a_cancelada_nunca_reabre(harness: Harness) -> None:
    st, s, op, _ = _fechada_aguardando(harness, "teste-op-31241-c3")
    st.db.execute("UPDATE operacoes SET status='cancelada' WHERE id=?", (op["id"],))
    gravado = st.db.scalar("SELECT finished_at FROM operacoes WHERE id=?", (op["id"],))
    lida = s.ler(op["id"])
    assert lida["status"] == "cancelada"
    linha = st.db.one("SELECT status, finished_at FROM operacoes WHERE id=?", (op["id"],))
    assert (linha["status"], linha["finished_at"]) == ("cancelada", gravado)
    assert _encerradas(st, op["id"]) == ["concluida_com_bloqueios"]
