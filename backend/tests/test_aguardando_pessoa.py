"""29.93: a execução que espera a pessoa não aparece mais como encerrada.

Antes, quando o trabalho automático acabava com um objetivo em `waiting_user` (um gesto da pessoa no aparelho: login,
desafio, aprovação), `recompute_run` levava a execução a `completed_with_issues`, que está em `RUN_TERMINAL`: o painel,
o Telegram e o contrato a davam por encerrada, e a retomada a "reabria". Agora ela fica em `awaiting_person`, que NÃO é
terminal e grava `finished_at` (o fim do trabalho automático, de onde o vencimento do 31.50 conta).

Decisões da orquestradora (05/10): só `waiting_user` leva ao estado novo; execução só com `uncertain` segue
`completed_with_issues`. O snapshot traz `awaiting_person` por 7 dias depois de `finished_at` (com o vencimento
desligado nada a fecharia). Nenhum digest do aprendizado enquanto ela espera: ele roda na saída do estado (30.69, PR
empilhado da Aprendizado). A ocorrência de pedido fecha como antes (o domínio da Canais não muda neste item).

Diferença para `needs_input` (condição C): `needs_input` é a pergunta ANTES de agir (o plano não rodou); `awaiting_person`
é o objetivo parado esperando um gesto depois de agir. Nenhum consumidor do `needs_input` pega o estado novo.

Prova `simulated`: o harness com aparelho e provedor simulados.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.api import _SQL_RUNS_DO_SNAPSHOT, _STATUS_DO_SNAPSHOT, AGUARDANDO_NO_SNAPSHOT_D
from app.models import RUN_SEM_TRABALHO, RUN_TERMINAL, RunStatus
from app.modules.avisos.infrastructure.entrada import TERMINAIS
from app.modules.avisos.infrastructure.portas_da_central import _DESFECHO
from app.modules.execution.domain.states import RUN_TRANSITIONS
from app.modules.pedidos.domain.fechamento import Fechamento, ObjetivoVisto, fechar
from app.util import now, to_iso

from .conftest import Harness
from .test_pergunta_vence import ligado_ha_muito


async def _esperando_login(h: Harness, aparelho: str = "android-01") -> str:
    """Uma execução real que para num objetivo `waiting_user` (a tela de login pede a pessoa)."""
    h.fakes[aparelho].screen = "launcher"                 # o app reabre do zero: a tela de login aparece
    h.fakes[aparelho].require_login = True
    run = h.run([aparelho])
    await h.wait_run(run.id)
    return str(run.id)


def _status_da_execucao(h: Harness, run_id: str) -> str:
    assert h.state is not None
    return str(h.state.repo.run_row(run_id)["status"])


# ------------------------------------------------------------------ o estado novo no caminho de verdade
async def test_objetivo_esperando_a_pessoa_deixa_a_execucao_aguardando_e_nao_terminal(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _esperando_login(harness)
    assert st.db.scalar("SELECT status FROM objectives WHERE run_id=?", (run_id,)) == "waiting_user"
    run = st.repo.run_row(run_id)
    assert run["status"] == "awaiting_person"
    assert RunStatus.awaiting_person not in RUN_TERMINAL and RunStatus.awaiting_person in RUN_SEM_TRABALHO
    # `finished_at` é o fim do trabalho automático: gravado na entrada, como no terminal.
    assert run["finished_at"]
    # O `run.updated` diz o estado novo (é o que o painel, o Telegram e o 28.40 leem).
    ultimo = st.db.scalar("SELECT data FROM events WHERE run_id=? AND kind='run.updated' ORDER BY id DESC LIMIT 1",
                          (run_id,))
    assert '"status": "awaiting_person"' in ultimo or '"status":"awaiting_person"' in ultimo
    # Nenhuma transição fora da tabela no caminho (a máquina de estados conhece o valor novo).
    assert st.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND message LIKE '%fora da tabela%'", (run_id,)) == 0


@pytest.mark.parametrize(("objetivos", "cancelar", "esperado"), [
    (("waiting_user", "succeeded"), False, "awaiting_person"),
    (("failed", "waiting_user"), False, "awaiting_person"),
    (("uncertain", "waiting_user"), False, "awaiting_person"),
    (("uncertain", "succeeded"), False, "completed_with_issues"),     # só incerto: segue como hoje (decisão de 05/10)
    (("failed", "succeeded"), False, "completed_with_issues"),
    (("succeeded", "succeeded"), False, "completed"),
    (("uncertain", "cancelled"), True, "completed_with_issues"),      # o incerto que `_finish_cancel` não fecha
    (("cancelled", "cancelled"), True, "cancelled"),
])
async def test_recompute_run_com_cada_mistura(harness: Harness, objetivos: tuple[str, str], cancelar: bool,
                                              esperado: str) -> None:
    st = harness.state
    assert st is not None
    run = harness.run(["android-01", "android-02"])
    await harness.wait_run(run.id)
    oids = [str(r["id"]) for r in st.db.query("SELECT id FROM objectives WHERE run_id=? ORDER BY id", (run.id,))]
    assert len(oids) == 2
    for oid, status in zip(oids, objetivos, strict=True):
        st.db.execute("UPDATE objectives SET status=? WHERE id=?", (status, oid))
    st.db.execute("UPDATE runs SET status='running', finished_at=NULL, cancel_requested=? WHERE id=?",
                  (int(cancelar), run.id))
    assert st.repo.recompute_run(run.id) == RunStatus(esperado)
    assert _status_da_execucao(harness, run.id) == esperado


async def test_a_retomada_reabre_a_execucao_aguardando(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _esperando_login(harness)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    # Retomar o item devolve trabalho automático: a execução sai de `awaiting_person` e limpa o fim.
    st.db.execute("UPDATE objectives SET status='pending' WHERE id=?", (oid,))
    assert st.repo.recompute_run(run_id) == RunStatus.running
    run = st.repo.run_row(run_id)
    assert run["status"] in ("running", "paused") and run["finished_at"] is None


async def test_cancelar_a_execucao_aguardando_e_aceito(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _esperando_login(harness)
    st.runs.cancel(run_id, por="operador-teste")
    await harness.wait_run(run_id, ("cancelled", "completed_with_issues"))
    # `_finish_cancel` fecha o `waiting_user`: o cancelamento nunca deixa a execução esperando a pessoa.
    assert st.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=? AND status='waiting_user'", (run_id,)) == 0


# ------------------------------------------------------------------ o vencimento (31.50) segue igual com o estado novo
async def test_o_vencimento_corre_e_fecha_o_objetivo_da_execucao_aguardando(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _esperando_login(harness)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    velho = to_iso(now() - timedelta(hours=30))
    st.db.execute("UPDATE objectives SET finished_at=? WHERE id=?", (velho, oid))
    st.db.execute("UPDATE runs SET finished_at=? WHERE id=?", (velho, run_id))
    ligado_ha_muito(harness)
    # O relógio corre: o objetivo parado de execução aguardando tem `vence_em`, como o de execução terminada.
    assert st.repo.objective_dto(st.repo.objective_row(oid)).vence_em is not None
    assert st.runs.vencer_objetivos_parados(now()) == [oid]
    assert st.repo.objective_row(oid)["status"] == "cancelled"
    # Sem ninguém esperando, a execução fecha: `completed_with_issues`, nunca cancelamento da pessoa.
    run = st.repo.run_row(run_id)
    assert (run["status"], run["cancel_requested"]) == ("completed_with_issues", 0)
    assert run["finished_at"] == velho                    # o fim do trabalho automático não anda


async def test_o_lembrete_antes_de_vencer_ve_o_objetivo_da_execucao_aguardando(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _esperando_login(harness)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    quase = to_iso(now() - timedelta(hours=23))           # vence na próxima hora: dentro da janela do lembrete
    st.db.execute("UPDATE objectives SET finished_at=? WHERE id=?", (quase, oid))
    st.db.execute("UPDATE runs SET finished_at=? WHERE id=?", (quase, run_id))
    ligado_ha_muito(harness)
    saidos = st.runs.lembrar_antes_de_vencer(now())
    assert any(oid in chave for chave in saidos)


# ------------------------------------------------------------------ snapshot (condição B)
def _clonar_execucao(h: Harness, run_id: str, n: int) -> None:
    """`n` execuções novas e já concluídas (cópias da linha), para empurrar `run_id` para fora das 20 recentes."""
    assert h.state is not None
    linha = dict(h.state.repo.run_row(run_id))
    colunas = list(linha)
    for i in range(n):
        copia = {**linha, "id": f"{run_id}-copia-{i}", "status": "completed", "created_at": to_iso(now()),
                 "idempotency_key": f"copia-{run_id}-{i}"}
        h.state.db.execute(f"INSERT INTO runs ({', '.join(colunas)}) VALUES ({', '.join('?' for _ in colunas)})",
                           tuple(copia[c] for c in colunas))


async def test_o_snapshot_traz_a_aguardando_por_sete_dias_e_corta_a_mais_velha(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    recente, velha = await _esperando_login(harness, "android-01"), await _esperando_login(harness, "android-02")
    antigo = to_iso(now() - timedelta(days=30))
    st.db.execute("UPDATE runs SET created_at=? WHERE id IN (?, ?)", (antigo, recente, velha))
    st.db.execute("UPDATE runs SET finished_at=? WHERE id=?",
                  (to_iso(now() - timedelta(days=AGUARDANDO_NO_SNAPSHOT_D + 1)), velha))
    _clonar_execucao(harness, recente, 21)
    corte = to_iso(now() - timedelta(days=AGUARDANDO_NO_SNAPSHOT_D))
    ids = {str(r["id"]) for r in st.db.query(_SQL_RUNS_DO_SNAPSHOT, (corte,))}
    assert recente in ids                                  # fora das 20 recentes, mas dentro dos 7 dias
    assert velha not in ids                                # mais velha que 7 dias: só viria entre as 20 recentes
    # O resto do snapshot não muda: as não terminais de sempre seguem sem corte de idade.
    assert "needs_input" in _STATUS_DO_SNAPSHOT and "awaiting_person" not in _STATUS_DO_SNAPSHOT


# ------------------------------------------------------------------ condição C: `needs_input` não pega o estado novo
async def test_consumidores_do_needs_input_nao_pegam_a_execucao_aguardando(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id = await _esperando_login(harness)
    velho = to_iso(now() - timedelta(days=10))
    st.db.execute("UPDATE runs SET created_at=?, finished_at=? WHERE id=?", (velho, velho, run_id))
    ligado_ha_muito(harness)
    # A expiração da pergunta (29.50/31.43) só cancela `needs_input`: a execução aguardando fica.
    assert run_id not in st.runs.expirar_sem_resposta(now())
    assert _status_da_execucao(harness, run_id) == "awaiting_person"
    # A máquina de estados: `needs_input` não vai a `awaiting_person` (só a `cancelled`).
    assert RUN_TRANSITIONS["needs_input"] == frozenset({"cancelled"})
    # O lembrete das perguntas lê `needs_input` por status: a execução aguardando não entra como "execucao".
    eventos = st.db.query("SELECT data FROM events WHERE kind='pendencia.vence_em' AND run_id=?", (run_id,))
    assert all('"o_que": "execucao"' not in str(e["data"]) for e in eventos)


# ------------------------------------------------------------------ Telegram e pedidos
def test_o_telegram_conta_a_execucao_aguardando_como_desfecho_com_frase_propria() -> None:
    assert "awaiting_person" in TERMINAIS                 # o desfecho é a única linha (28.36), como no terminal
    assert _DESFECHO[RunStatus.awaiting_person] == "espera você no aparelho"
    assert all(v != _DESFECHO[RunStatus.awaiting_person] for k, v in _DESFECHO.items() if k != RunStatus.awaiting_person)


def test_a_ocorrencia_do_pedido_fecha_como_o_completed_with_issues_de_antes() -> None:
    """Escolha (a) da orquestradora: o domínio da Canais não muda neste item (o "esperando você" na ocorrência é 28.40)."""
    objetivos = [ObjetivoVisto(id="o1", status="waiting_user", iniciado=True),
                 ObjetivoVisto(id="o2", status="succeeded", iniciado=True)]
    comum: dict[str, Any] = {"estado_atual": "rodando", "run_id": "r1", "objetivos": objetivos}
    antes = fechar(run_status="completed_with_issues", **comum)
    depois = fechar(run_status="awaiting_person", **comum)
    assert isinstance(depois, Fechamento) and depois == antes


# ------------------------------------------------------------------ aprendizado: nada de digest enquanto espera (30.69)
async def test_a_execucao_aguardando_nao_assenta_e_nao_e_digerida_enquanto_espera(harness: Harness) -> None:
    """O digest roda no assentamento (`Scheduler._settle_run` → `on_run_settled`). `awaiting_person` não assenta: o
    aprendizado digere na SAÍDA do estado (30.69, o gancho em `Repository.set_run_status`, num PR empilhado)."""
    st = harness.state
    assert st is not None
    assentadas: list[str] = []
    st.scheduler.on_run_settled = assentadas.append
    run_id = await _esperando_login(harness)
    assert _status_da_execucao(harness, run_id) == "awaiting_person"
    assert run_id not in assentadas


async def test_toda_saida_da_execucao_aguardando_passa_por_set_run_status(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """O ponto único onde o 30.69 liga o digest: o vencimento (31.50) e o cancelamento saem de `awaiting_person` por
    `Repository.set_run_status`, que lê o estado anterior. Nenhum `UPDATE runs SET status` fora dele no código."""
    st = harness.state
    assert st is not None
    saidas: list[tuple[str, str]] = []
    original = st.repo.set_run_status

    def espiao(run_id: str, status: RunStatus, *args: Any, **kwargs: Any) -> bool:
        anterior = str(st.repo.run_row(run_id)["status"])
        if anterior == "awaiting_person":
            saidas.append((run_id, str(status.value)))
        return original(run_id, status, *args, **kwargs)

    monkeypatch.setattr(st.repo, "set_run_status", espiao)
    vence = await _esperando_login(harness, "android-01")
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (vence,)))
    velho = to_iso(now() - timedelta(hours=30))
    st.db.execute("UPDATE objectives SET finished_at=? WHERE id=?", (velho, oid))
    st.db.execute("UPDATE runs SET finished_at=? WHERE id=?", (velho, vence))
    ligado_ha_muito(harness)
    assert st.runs.vencer_objetivos_parados(now()) == [oid]
    cancela = await _esperando_login(harness, "android-02")
    st.runs.cancel(cancela, por="operador-teste")
    assert (vence, "completed_with_issues") in saidas
    assert (cancela, "cancelling") in saidas
