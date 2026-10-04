"""31.43: a pergunta parada vence sozinha, no prazo do config, PELO SISTEMA.

Estende a 29.50 (`needs_input` sem resposta cancela a execução) de duas maneiras: o prazo vem de `execucao.pergunta_vence_h`
(com a chave `execucao.vencimento_ligado`), e o objetivo em `waiting_user` de uma execução JÁ TERMINADA, que ninguém
retomou, também é fechado (`RunService.vencer_objetivos_parados`). Antes, esse objetivo ficava `waiting_user` para sempre
(22 assim no banco central em 04/10). Só muda estado: nada responde, digita, toca aparelho ou chama IA, e não há o sinal de
PESSOA `cancelou_execucao` (ADR-054).

O formato do evento é fixo, porque a Canais o lê por um adaptador: `dados.vencimento` com EXATAMENTE as quatro chaves
`regra`, `motivo`, `horas` e `desde`, no `run.updated` (pergunta) e no `objective.updated` (objetivo parado).

Prova `simulated`: o harness com o provedor simulado; o estado "esperando pessoa" é posto no banco depois de uma execução
de verdade. O relógio é o `agora` passado ao serviço; o laço passa `now()`.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from app.util import now, parse_iso, to_iso

from .conftest import Harness

INCOMPLETO = "Abra o QA Messenger e envie uma mensagem"
MOTIVO = "vencido_sem_resposta"


async def _pergunta(h: Harness) -> tuple[str, Any]:
    """Uma execução real em `needs_input` e o instante em que ela entrou lá (o `run.updated` da transição)."""
    run = h.run(["android-01"], command=INCOMPLETO, mode="plan")
    await h.wait_run(run.id, ("needs_input",))
    assert h.state is not None
    entrada = h.state.db.scalar("SELECT MAX(ts) FROM events WHERE run_id=? AND kind='run.updated'", (run.id,))
    return run.id, parse_iso(entrada)


async def _parado(h: Harness, *, espera_h: float, fim_h: float, run_status: str = "completed_with_issues") -> tuple[str, str]:
    """Uma execução que terminou de verdade e é posta no estado de hoje no banco central: o objetivo em `waiting_user`
    (entrou há `espera_h` horas), uma etapa dele esperando a pessoa e a execução terminal (terminou há `fim_h` horas)."""
    st = h.state
    assert st is not None
    run = h.run(["android-01"])
    await h.wait_run(run.id)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run.id,)))
    espera, fim = to_iso(now() - timedelta(hours=espera_h)), to_iso(now() - timedelta(hours=fim_h))
    st.db.execute("UPDATE objectives SET status='waiting_user', finished_at=? WHERE id=?", (espera, oid))
    st.db.execute("UPDATE steps SET status='waiting_user' WHERE id=(SELECT MAX(id) FROM steps WHERE objective_id=?)",
                  (oid,))
    st.db.execute("UPDATE runs SET status=?, finished_at=?, cancel_requested=0 WHERE id=?", (run_status, fim, run.id))
    return run.id, oid


def _sinais(h: Harness, kind: str) -> list[dict[str, Any]]:
    assert h.state is not None
    return [dict(r) for r in h.state.db.query("SELECT * FROM learning_signals WHERE kind=? ORDER BY id", (kind,))]


def _ultimo_evento(h: Harness, kind: str, **onde: str) -> dict[str, Any]:
    assert h.state is not None
    coluna, valor = next(iter(onde.items()))
    row = h.state.db.one(f"SELECT data FROM events WHERE {coluna}=? AND kind=? ORDER BY id DESC LIMIT 1", (valor, kind))
    return json.loads(row["data"])


def _status(h: Harness, run_id: str, oid: str) -> tuple[str, str, int]:
    assert h.state is not None
    objetivo = h.state.repo.objective_row(oid)
    run = h.state.repo.run_row(run_id)
    return objetivo["status"], run["status"], run["cancel_requested"]


# ------------------------------------------------------------------ (a) o prazo vem do config
async def test_a_pergunta_vence_no_prazo_do_config_com_o_motivo_para_maquina(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    harness.cfg.file.execucao.pergunta_vence_h = 1
    run_id, entrada = await _pergunta(harness)
    # Um minuto antes do prazo de 1 h, nada muda.
    assert st.runs.expirar_sem_resposta(entrada + timedelta(minutes=59)) == []
    assert st.repo.run_row(run_id)["status"] == "needs_input"
    # Passado o prazo, o sistema encerra, com o texto humano e a marca para máquina com a regra.
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=1, minutes=1)) == [run_id]
    run = st.repo.run_row(run_id)
    assert (run["status"], run["cancel_requested"]) == ("cancelled", 1)
    assert run["status_detail"].startswith("Sem resposta em 1 h: a pergunta expirou")
    assert MOTIVO not in run["status_detail"] and run_id not in run["status_detail"]
    dados = _ultimo_evento(harness, "run.updated", run_id=run_id)
    # Formato fixo (a Canais lê por um adaptador): exatamente estas quatro chaves.
    assert dados["vencimento"] == {"regra": "31.43", "motivo": MOTIVO, "horas": 1, "desde": to_iso(entrada)}
    # A marca da 29.50 continua, para quem já a lê.
    assert dados["expirada"] == {"motivo": "sem_resposta", "horas": 1, "desde": to_iso(entrada)}
    # Ninguém fez o gesto: nenhum sinal de pessoa.
    assert _sinais(harness, "cancelou_execucao") == []


def test_o_padrao_do_config_e_o_de_24_horas() -> None:
    from app.config import ExecucaoCfg
    from app.taskqueue.service import NEEDS_INPUT_EXPIRA_H
    cfg = ExecucaoCfg()
    assert (cfg.vencimento_ligado, cfg.pergunta_vence_h) == (True, NEEDS_INPUT_EXPIRA_H)


async def test_a_pergunta_ainda_dentro_do_prazo_fica(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id, entrada = await _pergunta(harness)
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=23)) == []
    assert st.repo.run_row(run_id)["status"] == "needs_input"


# ------------------------------------------------------------------ a chave
async def test_com_a_chave_desligada_nada_vence(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_pergunta, entrada = await _pergunta(harness)
    run_parado, oid = await _parado(harness, espera_h=100, fim_h=100)
    harness.cfg.file.execucao.vencimento_ligado = False
    longe = entrada + timedelta(days=30)
    assert st.runs.expirar_sem_resposta(longe) == []
    assert st.runs.vencer_objetivos_parados(longe) == []
    assert st.repo.run_row(run_pergunta)["status"] == "needs_input"
    assert _status(harness, run_parado, oid)[:2] == ("waiting_user", "completed_with_issues")
    # Ligada de novo, o mesmo estado vence (a chave é a única diferença).
    harness.cfg.file.execucao.vencimento_ligado = True
    assert st.runs.vencer_objetivos_parados(longe) == [oid]


# ------------------------------------------------------------------ (b) o objetivo parado de execução terminada
async def test_o_objetivo_waiting_user_de_execucao_terminada_vence_pelo_sistema(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=30, fim_h=30)
    assert _status(harness, run_id, oid) == ("waiting_user", "completed_with_issues", 0)
    # O relógio esperado: o mais tardio entre a espera do objetivo e o fim da execução (lidos antes de fechar).
    desde = max(str(st.repo.objective_row(oid)["finished_at"]), str(st.repo.run_row(run_id)["finished_at"]))
    assert st.runs.vencer_objetivos_parados(now()) == [oid]
    objetivo = st.repo.objective_row(oid)
    assert objetivo["status"] == "cancelled"
    # O texto é para o dono: humano, sem código nem id.
    assert objetivo["status_detail"].startswith("Sem resposta em 24 h: o pedido venceu e foi encerrado pelo sistema")
    for cru in ("waiting_user", MOTIVO, oid, run_id):
        assert cru not in objetivo["status_detail"]
    # A etapa que esperava a pessoa também fecha.
    assert st.db.scalar("SELECT COUNT(*) FROM steps WHERE objective_id=? AND status='waiting_user'", (oid,)) == 0
    # O evento leva a regra, o motivo e o relógio.
    dados = _ultimo_evento(harness, "objective.updated", objective_id=oid)
    assert dados["objective"]["status"] == "cancelled"
    # Formato fixo (a Canais lê por um adaptador): exatamente estas quatro chaves.
    assert dados["vencimento"] == {"regra": "31.43", "motivo": MOTIVO, "horas": 24, "desde": desde}
    # A execução fica com o que `recompute_run` deriva: não é cancelamento da pessoa.
    run = st.repo.run_row(run_id)
    assert (run["status"], run["cancel_requested"]) == ("completed_with_issues", 0)
    assert "1 cancelado" in run["status_detail"]
    assert _sinais(harness, "cancelou_execucao") == []
    # Idempotente: o objetivo já não espera ninguém.
    assert st.runs.vencer_objetivos_parados(now() + timedelta(days=5)) == []


async def test_o_objetivo_parado_recente_fica(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=2, fim_h=2)
    assert st.runs.vencer_objetivos_parados(now()) == []
    assert _status(harness, run_id, oid)[:2] == ("waiting_user", "completed_with_issues")


async def test_o_relogio_e_o_mais_tardio_entre_a_espera_e_o_fim_da_execucao(harness: Harness) -> None:
    """Outro item da execução retomado e refechado põe o fim da execução recente: o prazo recomeça; nunca adianta."""
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=100, fim_h=1)
    assert st.runs.vencer_objetivos_parados(now()) == []
    run_id2, oid2 = await _parado(harness, espera_h=1, fim_h=100)
    assert st.runs.vencer_objetivos_parados(now()) == []
    assert _status(harness, run_id, oid)[0] == _status(harness, run_id2, oid2)[0] == "waiting_user"


async def test_a_execucao_ainda_viva_com_objetivo_waiting_user_nao_e_tocada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    for estado in ("running", "paused", "needs_input"):
        run_id, oid = await _parado(harness, espera_h=100, fim_h=100, run_status=estado)
        assert st.runs.vencer_objetivos_parados(now()) == [], estado
        assert _status(harness, run_id, oid)[:2] == ("waiting_user", estado)


@pytest.mark.parametrize("corrida", ["objetivo_retomado", "execucao_retomada"])
async def test_a_retomada_no_meio_da_varredura_ganha_do_vencimento(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                                 corrida: str) -> None:
    """A pessoa retoma o item (ou a execução) entre a leitura dos candidatos e a escrita: a escrita é condicional e não
    sobrescreve nada."""
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=100, fim_h=100)
    original, feito = st.db.query, []

    def query_com_retomada(sql: str, params: Any = ()) -> Any:
        linhas = list(original(sql, params))
        if "FROM objectives o JOIN runs r" in sql and not feito:
            feito.append(True)
            if corrida == "objetivo_retomado":
                st.db.execute("UPDATE objectives SET status='pending' WHERE id=?", (oid,))
            else:
                st.db.execute("UPDATE runs SET status='running', finished_at=NULL WHERE id=?", (run_id,))
        return linhas

    monkeypatch.setattr(st.db, "query", query_com_retomada)
    assert st.runs.vencer_objetivos_parados(now()) == []
    assert feito
    objetivo, run, _ = _status(harness, run_id, oid)
    assert objetivo == ("pending" if corrida == "objetivo_retomado" else "waiting_user")
    assert run == ("completed_with_issues" if corrida == "objetivo_retomado" else "running")
    assert st.db.scalar("SELECT COUNT(*) FROM events WHERE objective_id=? AND data LIKE '%31.43%'", (oid,)) == 0


async def test_a_volta_do_laco_vence_os_dois_com_o_relogio_de_verdade(harness: Harness) -> None:
    """O caminho do laço (`AppState._expiracao_uma_vez`, sob a trava da retenção): a pergunta e o objetivo parado."""
    st = harness.state
    assert st is not None
    run_pergunta, _ = await _pergunta(harness)
    velho = to_iso(now() - timedelta(hours=25))
    st.db.execute("UPDATE runs SET created_at=? WHERE id=?", (velho, run_pergunta))
    st.db.execute("UPDATE events SET ts=? WHERE run_id=?", (velho, run_pergunta))
    run_parado, oid = await _parado(harness, espera_h=30, fim_h=30)
    assert await st._expiracao_uma_vez() is True
    assert st.repo.run_row(run_pergunta)["status"] == "cancelled"
    assert _status(harness, run_parado, oid)[:2] == ("cancelled", "completed_with_issues")


async def test_o_objetivo_vencido_deixa_de_contar_como_aberto(harness: Harness) -> None:
    """A fila da pessoa (`api._OBJETIVO_ABERTO`: pending, running, waiting_user) não segura mais o item vencido."""
    from app.api import _OBJETIVO_ABERTO
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=30, fim_h=30)
    assert st.repo.objective_row(oid)["status"] in _OBJETIVO_ABERTO
    st.runs.vencer_objetivos_parados(now())
    assert st.repo.objective_row(oid)["status"] not in _OBJETIVO_ABERTO
