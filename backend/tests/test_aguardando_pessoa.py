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

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from app.api import _SQL_RUNS_DO_SNAPSHOT, _STATUS_DO_SNAPSHOT, AGUARDANDO_NO_SNAPSHOT_D
from app.models import RUN_SEM_TRABALHO, RUN_TERMINAL, ResolveBody, RunStatus
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
    assert _DESFECHO[RunStatus.awaiting_person] == "parou"
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


async def test_a_purga_de_eventos_poupa_a_execucao_aguardando(harness: Harness) -> None:
    """A purga por idade (`log_retention_days`) poupava só execução com `finished_at` nulo; a aguardando tem
    `finished_at` e segue aberta. A linha do tempo dela fica; a da execução que fechou de verdade sai, como antes."""
    st = harness.state
    assert st is not None
    espera = await _esperando_login(harness, "android-01")
    fechada = await _esperando_login(harness, "android-02")
    st.runs.cancel(fechada, por="operador-teste")
    await harness.wait_run(fechada, statuses=("cancelled",))
    velho = to_iso(now() - timedelta(days=30))
    st.db.execute("UPDATE events SET ts=? WHERE run_id IN (?, ?)", (velho, espera, fechada))
    st.bus.purge_older_than(to_iso(now() - timedelta(days=14)))
    assert st.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=?", (espera,)) > 0
    assert st.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=?", (fechada,)) == 0


# ------------------------------------------------------------------ A1: o assentamento sai uma vez, por qualquer saída
# A main assentava UMA vez, no `finally` do worker, quando a parada virava `completed_with_issues`: o digest, a trava
# de rascunho (`_draft_locks`) e o acordar dos pedidos. Agora a parada (`awaiting_person`) solta a trava e acorda os
# pedidos na MESMA hora de antes, sem o digest; o assentamento inteiro sai na saída do estado, com ou sem worker.
def _gravar(st: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Grava os três efeitos pelo caminho de verdade (`_execucao_parada`/`_execucao_assentada`), trocando só as
    pontas: o digest do aprendizado e o acordar do laço de pedidos."""
    g: dict[str, list[str]] = {"digest": [], "pedidos": [], "sem_worker": []}
    monkeypatch.setattr(st.learning, "digerir_execucao", lambda run_id, *a, **k: g["digest"].append(run_id))
    monkeypatch.setattr(st.pedidos, "ao_assentar", lambda run_id: g["pedidos"].append(run_id))
    original = st.repo.ao_assentar_sem_worker
    assert original is not None                                  # o AppState liga o gancho de saída

    def sem_worker(run_id: str) -> None:
        g["sem_worker"].append(run_id)
        original(run_id)

    monkeypatch.setattr(st.repo, "ao_assentar_sem_worker", sem_worker)
    return g


async def _assentou(h: Harness, g: dict[str, list[str]], run_id: str) -> None:
    """Espera o digest (que roda numa thread pelo `_digerir`) e mais uma folga, para um segundo não passar calado."""
    await h.wait(lambda: run_id in g["digest"], what="digest da execução")
    await asyncio.sleep(0.3)


async def _parada_gravada(h: Harness, monkeypatch: pytest.MonkeyPatch,
                          aparelho: str = "android-01") -> tuple[str, dict[str, list[str]]]:
    st = h.state
    assert st is not None
    g = _gravar(st, monkeypatch)
    run_id = await _esperando_login(h, aparelho)
    await asyncio.sleep(0.3)
    assert _status_da_execucao(h, run_id) == "awaiting_person"
    for k in g:
        g[k].clear()
    st._draft_locks[run_id] = asyncio.Lock()                    # noqa: SLF001 - a trava que a saída tem de soltar
    return run_id, g


def _um_assentamento(st: Any, g: dict[str, list[str]], run_id: str, *, sem_worker: bool) -> None:
    assert g["digest"] == [run_id]                               # uma vez, nunca em dobro
    assert g["pedidos"] == [run_id]
    assert run_id not in st._draft_locks                         # noqa: SLF001
    assert g["sem_worker"] == ([run_id] if sem_worker else [])
    assert st.repo.run_row(run_id)["assentada_em"]               # a marca do #382 ficou gravada


async def test_a_parada_solta_a_trava_e_acorda_os_pedidos_como_na_main_sem_digest(harness: Harness,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    g = _gravar(st, monkeypatch)
    harness.fakes["android-01"].screen = "launcher"
    harness.fakes["android-01"].require_login = True
    run = harness.run(["android-01"])
    st._draft_locks[str(run.id)] = asyncio.Lock()               # noqa: SLF001 - como se a etapa tivesse escrito
    await harness.wait_run(run.id)
    await asyncio.sleep(0.3)
    assert _status_da_execucao(harness, str(run.id)) == "awaiting_person"
    assert g["pedidos"] == [run.id] and str(run.id) not in st._draft_locks    # noqa: SLF001 - a hora da main
    assert g["digest"] == [] and g["sem_worker"] == []           # nenhum digest enquanto espera


async def test_abandonar_assenta_uma_vez_sem_worker(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    run_id, g = await _parada_gravada(harness, monkeypatch)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    st.runs.resolve(run_id, oid, ResolveBody(resolution="abandon"))
    await _assentou(harness, g, run_id)
    assert _status_da_execucao(harness, run_id) == "completed_with_issues"
    _um_assentamento(st, g, run_id, sem_worker=True)


async def test_confirmar_o_item_assenta_uma_vez_quando_o_worker_fecha(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirmar à mão a etapa parada devolve o objetivo às etapas seguintes: a execução volta a `running`, o worker a
    fecha e assenta no `finally`. O gancho de saída não dispara junto."""
    st = harness.state
    assert st is not None
    run_id, g = await _parada_gravada(harness, monkeypatch)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    harness.fakes["android-01"].require_login = False             # a pessoa logou e confirmou
    harness.fakes["android-01"].screen = "home"
    st.runs.resolve(run_id, oid, ResolveBody(resolution="confirm_done"))
    await harness.wait_run(run_id, statuses=("completed", "completed_with_issues", "failed"))
    await _assentou(harness, g, run_id)
    _um_assentamento(st, g, run_id, sem_worker=False)


async def test_o_vencimento_em_thread_assenta_uma_vez_no_laco(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O vencimento roda em `to_thread` (state.py): fora do laço, o assentamento é agendado nele, e o digest sai."""
    st = harness.state
    assert st is not None
    run_id, g = await _parada_gravada(harness, monkeypatch)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    velho = to_iso(now() - timedelta(hours=30))
    st.db.execute("UPDATE objectives SET finished_at=? WHERE id=?", (velho, oid))
    st.db.execute("UPDATE runs SET finished_at=? WHERE id=?", (velho, run_id))
    ligado_ha_muito(harness)
    assert await asyncio.to_thread(st.runs.vencer_objetivos_parados, now()) == [oid]
    await _assentou(harness, g, run_id)
    assert _status_da_execucao(harness, run_id) == "completed_with_issues"
    _um_assentamento(st, g, run_id, sem_worker=True)


async def test_cancelar_a_execucao_aguardando_assenta_uma_vez(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    run_id, g = await _parada_gravada(harness, monkeypatch)
    st.runs.cancel(run_id, por="operador-teste")
    await harness.wait_run(run_id, statuses=("cancelled",))
    await _assentou(harness, g, run_id)
    _um_assentamento(st, g, run_id, sem_worker=True)             # `awaiting_person` → `cancelling` → `cancelled`


async def test_a_retomada_que_conclui_assenta_uma_vez_pelo_worker_nunca_em_dobro(harness: Harness,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """O caminho COM worker: a retomada limpa o `finished_at`, o worker fecha a execução e assenta no `finally`; o
    gancho de saída não dispara junto (digest e pedidos contados: uma vez cada)."""
    st = harness.state
    assert st is not None
    run_id, g = await _parada_gravada(harness, monkeypatch)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    harness.fakes["android-01"].require_login = False             # a pessoa logou no aparelho
    harness.fakes["android-01"].screen = "home"
    st.runs.resolve(run_id, oid, ResolveBody(resolution="retry", note="loguei no aparelho"))
    await harness.wait_run(run_id, statuses=("completed", "completed_with_issues", "failed"))
    await _assentou(harness, g, run_id)
    _um_assentamento(st, g, run_id, sem_worker=False)


async def test_a_aprovacao_que_retoma_assenta_uma_vez_pelo_worker(harness: Harness,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """A aprovação decidida retoma o objetivo (`resume_objective` + `recompute_run`, approvals.py): é a retomada, e o
    assentamento sai uma vez, no fim do worker."""
    st = harness.state
    assert st is not None
    run_id, g = await _parada_gravada(harness, monkeypatch)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    etapa = st.db.scalar("SELECT id FROM steps WHERE objective_id=? AND status='waiting_user'", (oid,))
    pedido = st.approval_service.store.open(profile_id=None, capability="CREATE_COMMENT", summary="teste 29.93",
                                            run_id=run_id, objective_id=oid, step_id=etapa)
    # O estado da represa de aprovação (`Scheduler._hold` → `_block`): objetivo `waiting_user` com
    # `blocked_kind='approval'` e a etapa ainda por fazer. A etapa parada no login vira a etapa represada.
    st.db.execute("UPDATE steps SET status='ready' WHERE id=?", (etapa,))
    st.db.execute("UPDATE objectives SET blocked_kind='approval' WHERE id=?", (oid,))
    harness.fakes["android-01"].require_login = False
    harness.fakes["android-01"].screen = "home"
    st.approval_service.decide(pedido.id, "approve")
    await harness.wait_run(run_id, statuses=("completed", "completed_with_issues", "failed"))
    await _assentou(harness, g, run_id)
    _um_assentamento(st, g, run_id, sem_worker=False)


async def test_a_execucao_que_ja_nasce_esperando_assenta_uma_vez_na_saida(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """O impedimento no início (`RunService.start`) leva o item a `waiting_user` sem worker nenhum. Na main ela nem
    assentava (nenhum worker rodou); agora assenta uma vez, na saída."""
    st = harness.state
    assert st is not None
    g = _gravar(st, monkeypatch)
    recusa = {"code": "device_off", "motivo": "o aparelho está desligado", "acao": "ligue-o e retome este item"}
    monkeypatch.setattr(st.runs, "pre_voo", lambda ids, **kw: {"android-01": recusa} if kw.get("ao_iniciar") else {})
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    run_id = str(run.id)
    assert _status_da_execucao(harness, run_id) == "awaiting_person"
    assert g["digest"] == [] and g["sem_worker"] == []
    g["pedidos"].clear()
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run_id,)))
    st.runs.resolve(run_id, oid, ResolveBody(resolution="abandon"))
    await _assentou(harness, g, run_id)
    _um_assentamento(st, g, run_id, sem_worker=True)


async def test_a_retencao_de_evidencia_poupa_a_execucao_aguardando(harness: Harness) -> None:
    """A retenção de evidência decidia só por `finished_at`: a aguardando seria tratada como fechada."""
    st = harness.state
    assert st is not None
    espera = await _esperando_login(harness, "android-01")
    fechada = await _esperando_login(harness, "android-02")
    st.runs.cancel(fechada, por="operador-teste")
    await harness.wait_run(fechada, statuses=("cancelled",))
    velho = to_iso(now() - timedelta(days=30))
    for run_id, aparelho in ((espera, "android-01"), (fechada, "android-02")):
        st.repo.add_evidence(run_id=run_id, instance_id=aparelho, step_id=None, attempt_id=None, kind="screenshot",
                             note="teste 29.93", data=b"\xff\xd8jpeg", ext="jpg")
        st.db.execute("UPDATE evidence SET ts=? WHERE run_id=?", (velho, run_id))
        st.db.execute("UPDATE runs SET finished_at=? WHERE id=?", (velho, run_id))
    limpos = st._apagar_evidencias_vencidas(to_iso(now() - timedelta(days=7)))   # noqa: SLF001
    assert fechada in limpos and espera not in limpos
    assert st.db.scalar("SELECT COUNT(*) FROM evidence WHERE run_id=?", (espera,)) > 0


async def test_o_assentamento_sai_depois_do_commit_e_nao_sai_no_rollback(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """O vencimento troca o estado dentro de uma `tx()`: o gancho espera o COMMIT (`Database.depois_do_commit`), e a
    transação desfeita não assenta nada."""
    st = harness.state
    assert st is not None
    run_id, g = await _parada_gravada(harness, monkeypatch)
    with st.db.tx():
        st.repo.set_run_status(run_id, RunStatus.completed_with_issues, "teste do commit")
        assert g["sem_worker"] == []                              # ainda dentro: nada disparou
    assert g["sem_worker"] == [run_id]                            # depois do COMMIT, uma vez
    desfeita, g2 = await _parada_gravada(harness, monkeypatch, "android-02")
    with pytest.raises(RuntimeError):
        with st.db.tx():
            st.repo.set_run_status(desfeita, RunStatus.completed_with_issues, "teste do rollback")
            raise RuntimeError("cai antes do commit")
    assert g2["sem_worker"] == [] and _status_da_execucao(harness, desfeita) == "awaiting_person"


async def test_sem_laco_o_assentamento_solta_e_avisa_que_o_digest_se_perdeu(harness: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch,
                                                                            caplog: pytest.LogCaptureFixture) -> None:
    st = harness.state
    assert st is not None
    g = _gravar(st, monkeypatch)
    monkeypatch.setattr(st, "_laco_principal", None)              # a thread que termina depois do desligamento
    st._draft_locks["run-sem-laco"] = asyncio.Lock()             # noqa: SLF001
    with caplog.at_level("WARNING"):
        await asyncio.to_thread(st._execucao_assentada, "run-sem-laco")                      # noqa: SLF001
    assert g["pedidos"] == ["run-sem-laco"] and "run-sem-laco" not in st._draft_locks      # noqa: SLF001
    assert g["digest"] == []
    assert any("run-sem-laco" in r.getMessage() and "digest" in r.getMessage() for r in caplog.records
               if r.levelname == "WARNING")


async def test_a_excecao_do_assentamento_agendado_diz_a_execucao(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                                caplog: pytest.LogCaptureFixture) -> None:
    st = harness.state
    assert st is not None

    def quebra(run_id: str) -> None:
        raise RuntimeError("falha de teste no assentamento")

    monkeypatch.setattr(st, "_execucao_parada", quebra)
    with caplog.at_level("ERROR"):
        await asyncio.to_thread(st._execucao_assentada, "run-que-quebra")                    # noqa: SLF001
        await harness.wait(lambda: any("run-que-quebra" in r.getMessage() for r in caplog.records),
                           what="log do assentamento agendado")
    assert any(r.levelname == "ERROR" and "assentamento agendado da execução run-que-quebra" in r.getMessage()
               for r in caplog.records)


# ------------------------------------------------------------------ #382: a marca `assentada_em` (migração 113)
async def test_a_execucao_comum_assenta_em_linha_pelo_worker_uma_vez(harness: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """O caminho comum: o worker grava o estado final e a marca na mesma transação e assenta no `finally`, em linha. A
    rede do `set_run_status` chega depois do COMMIT, encontra a marca e não assenta."""
    st = harness.state
    assert st is not None
    g = _gravar(st, monkeypatch)
    pelo_worker: list[str] = []
    original = st.scheduler.on_run_settled
    assert original is not None

    def no_worker(run_id: str) -> None:
        pelo_worker.append(run_id)
        original(run_id)

    monkeypatch.setattr(st.scheduler, "on_run_settled", no_worker)
    harness.fakes["android-01"].screen = "home"
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed"))
    await _assentou(harness, g, run.id)
    assert pelo_worker == [run.id]
    _um_assentamento(st, g, run.id, sem_worker=False)


async def _assentada_pelo_worker(h: Harness, monkeypatch: pytest.MonkeyPatch) -> tuple[str, dict[str, list[str]]]:
    """Uma execução que o worker fechou e assentou, levada a `completed_with_issues` com o objetivo incerto (o estado
    que o worker grava quando a prova não fecha), com a marca de assentada intacta."""
    st = h.state
    assert st is not None
    g = _gravar(st, monkeypatch)
    h.fakes["android-01"].screen = "home"
    run = h.run(["android-01"])
    await h.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed"))
    await _assentou(h, g, run.id)
    assert st.repo.run_row(run.id)["assentada_em"]
    st.db.execute("UPDATE objectives SET status='uncertain' WHERE run_id=?", (run.id,))
    st.db.execute("UPDATE runs SET status='completed_with_issues' WHERE id=?", (run.id,))
    for k in g:
        g[k].clear()
    return run.id, g


async def test_d1_cancelar_a_execucao_incerta_ja_assentada_nao_assenta_de_novo(harness: Harness,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """D1: `completed_with_issues` → `cancelling` → `completed_with_issues` (o incerto que o `_finish_cancel` não
    fecha). O `cancelling` não zera a marca, e a rede perde o compare-and-set: nada sai de novo."""
    st = harness.state
    assert st is not None
    run_id, g = await _assentada_pelo_worker(harness, monkeypatch)
    st.runs.cancel(run_id, por="operador-teste")
    await harness.wait(lambda: _status_da_execucao(harness, run_id) != "cancelling", what="o cancelamento fechar")
    await asyncio.sleep(0.5)
    assert _status_da_execucao(harness, run_id) == "completed_with_issues"
    assert g == {"digest": [], "pedidos": [], "sem_worker": []}


async def test_d2_a_execucao_cancelada_sem_worker_assenta_uma_vez(harness: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """D2 (29.103): uma execução reaberta e pausada, com o item pendente e nenhum worker vivo, é cancelada. O
    `_finish_cancel` fecha o item e a execução; a reabertura zerou a marca, e a rede assenta, uma vez."""
    st = harness.state
    assert st is not None
    run_id, g = await _assentada_pelo_worker(harness, monkeypatch)
    st.db.execute("UPDATE objectives SET status='pending' WHERE run_id=?", (run_id,))
    st.db.execute("UPDATE runs SET pause_requested=1 WHERE id=?", (run_id,))
    assert st.repo.recompute_run(run_id) == RunStatus.running              # reabre como `paused`
    run = st.repo.run_row(run_id)
    assert run["status"] == "paused" and run["assentada_em"] is None       # a reabertura zera a marca
    st._draft_locks[run_id] = asyncio.Lock()                    # noqa: SLF001 - a trava que o assentamento solta
    st.runs.cancel(run_id, por="operador-teste")
    await harness.wait_run(run_id, statuses=("cancelled",))
    await _assentou(harness, g, run_id)
    _um_assentamento(st, g, run_id, sem_worker=True)


async def test_k2_o_worker_que_perde_a_marca_solta_a_trava_deste_processo_sem_digest(harness: Harness,
                                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """29.108 (K2): a marca já foi de outro (outro backend, a rede). O `_settle_run` deste processo não assenta de novo,
    mas solta a trava de rascunho que é dele e acorda os pedidos (o idempotente da parada)."""
    st = harness.state
    assert st is not None
    run_id, g = await _assentada_pelo_worker(harness, monkeypatch)
    st._draft_locks[run_id] = asyncio.Lock()                    # noqa: SLF001 - a trava que ficaria presa
    st.scheduler._settle_run(run_id, venceu=False)              # noqa: SLF001
    assert run_id not in st._draft_locks                         # noqa: SLF001
    assert g["digest"] == [] and g["pedidos"] == [run_id] and g["sem_worker"] == []
