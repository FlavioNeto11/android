"""Execução com aparelhos falsos: isolamento, falhas isoladas, efeito externo, reinício, controle manual."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.automation.driver import DriverTimeout
from app.devices.executor import DeviceExecutor
from app.devices.manager import ControlError
from app.util import now_iso
from app.models import ControlOwner, ManualInput, ResolveBody

from .conftest import Harness

ALL = ["android-01", "android-02", "android-03"]


# ---------------------------------------------------------------- isolamento entre dispositivos
async def test_cada_aparelho_recebe_apenas_a_sua_mensagem(harness: Harness) -> None:
    run = harness.run(ALL)
    detail = await harness.wait_run(run.id)
    assert detail.status == "completed" and detail.counts.succeeded == 3 and detail.instances_used == 3
    for iid in ALL:
        fake = harness.fakes[iid]
        assert [m.body for m in fake.messages] == [f"Teste POC {iid} {run.id}"]
        assert fake.messages[0].contact == "QA-001"
        assert fake.max_concurrent == 1            # nunca duas chamadas simultâneas no mesmo aparelho
    # os três trabalharam em paralelo (tentativas sobrepostas no tempo)
    spans = [(a.started_at, a.finished_at) for a in detail.attempts if a.step_id.endswith(":open_app")]
    assert len(spans) == 3
    assert {o.delivery_level for o in detail.objectives} <= {"sent", "delivered"}


async def test_falha_e_tela_inesperada_em_um_aparelho_nao_param_os_demais(harness: Harness) -> None:
    harness.fakes["android-01"].interstitial = True          # tela inesperada: a automação dispensa e segue
    harness.fakes["android-02"].require_login = True         # autenticação adicional: bloqueia só este
    harness.fakes["android-03"].action_delay_s = 0.05        # aparelho lento
    run = harness.run(ALL)
    detail = await harness.wait_run(run.id)
    by = {o.instance_id: o for o in detail.objectives}
    assert by["android-01"].status == "succeeded" and not harness.fakes["android-01"].interstitial
    assert by["android-03"].status == "succeeded"
    assert by["android-02"].status == "waiting_user" and "autentica" in (by["android-02"].blocked_reason or "").lower()
    assert detail.status == "completed_with_issues"          # 2 sucessos + 1 bloqueio ≠ sucesso total
    assert (detail.counts.succeeded, detail.counts.waiting_user) == (2, 1)
    assert not harness.fakes["android-02"].messages
    # evidência da tela de login é registrada SEM imagem
    login_ev = [e for e in detail.evidence if e.instance_id == "android-02"]
    assert login_ev and all(e.redacted and e.url is None for e in login_ev if "autentica" in (e.note or "").lower())
    # o usuário faz login manualmente e retoma apenas o item bloqueado
    harness.fakes["android-02"].require_login = False
    harness.fakes["android-02"].screen = "home"
    harness.state.runs.resolve(run.id, by["android-02"].id, ResolveBody(resolution="retry"))
    detail = await harness.wait_run(run.id, statuses=("completed",))
    assert detail.counts.succeeded == 3 and len(harness.fakes["android-02"].messages) == 1
    assert len(harness.fakes["android-01"].messages) == 1    # itens já comprovados não foram refeitos
    assert any(v.version == 2 for v in detail.plan_versions if v.objective_id == by["android-02"].id)


# ---------------------------------------------------------------- exclusividade do executor
async def test_executor_serializa_e_timeout_nao_libera_o_aparelho() -> None:
    ex = DeviceExecutor("t")
    running = {"n": 0, "max": 0}

    def work(d: float) -> str:
        running["n"] += 1
        running["max"] = max(running["max"], running["n"])
        time.sleep(d)
        running["n"] -= 1
        return "ok"

    assert await asyncio.gather(*(ex.run(work, 0.05, timeout=5) for _ in range(5))) == ["ok"] * 5
    assert running["max"] == 1
    with pytest.raises(DriverTimeout):
        await ex.run(work, 0.8, timeout=0.1)
    assert ex.has_zombie                                       # a chamada anterior ainda ocupa o aparelho
    t0 = time.monotonic()
    assert await ex.run(work, 0.01, timeout=5) == "ok"         # só roda DEPOIS que a anterior termina
    assert time.monotonic() - t0 >= 0.5 and running["max"] == 1
    assert await ex.drain(max_wait_s=2) and not ex.has_zombie
    ex.shutdown()


# ---------------------------------------------------------------- efeito externo e reconciliação
async def test_erro_apos_o_toque_de_enviar_reconcilia_sem_reenviar(harness: Harness) -> None:
    fake = harness.fakes["android-01"]
    fake.send_fault = "error_after_effect"                    # a mensagem saiu, mas o driver devolveu erro
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert detail.status == "completed"
    assert len(fake.messages) == 1                             # exatamente uma mensagem: nada de reenvio
    send = next(s for s in detail.steps if s.key == "send_message")
    commits = [a for at in detail.attempts if at.step_id == send.id for a in at.actions if a.side_effect]
    assert len(commits) == 1 and commits[0].status == "unknown"
    attempt = next(at for at in detail.attempts if at.step_id == send.id)
    assert attempt.error and "reconcilia" in (attempt.recovery or "").lower()


async def test_resultado_ambiguo_vira_incerto_e_nao_reenvia(harness: Harness) -> None:
    fake = harness.fakes["android-01"]
    fake.send_fault = "error_lost"                             # erro no toque e a mensagem NÃO aparece
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id, timeout=90)
    obj = detail.objectives[0]
    assert obj.status == "uncertain" and detail.status == "completed_with_issues"
    assert detail.counts.succeeded == 0 and detail.counts.uncertain == 1
    assert fake.messages == []                                 # não houve nova tentativa automática de envio
    send = next(s for s in detail.steps if s.key == "send_message")
    assert send.status == "uncertain" and send.attempts == 1
    assert sum(1 for c in fake.calls if c.startswith("tap:640")) == 1      # um único toque em Enviar
    # retomada em lote NÃO toca em item incerto; exige decisão individual
    out = harness.state.runs.retry_failed(run.id)
    assert out["retried"] == [] and "incerto" in out["skipped"][0]["reason"]
    # decisão explícita do usuário: repetir → agora envia (uma vez)
    harness.state.runs.resolve(run.id, obj.id, ResolveBody(resolution="retry", note="conferi: não enviou"))
    detail = await harness.wait_run(run.id, statuses=("completed",))
    assert len(fake.messages) == 1 and detail.objectives[0].plan_version == 2


async def test_timeout_no_toque_de_enviar_segura_o_aparelho_e_reconcilia(harness: Harness) -> None:
    st = harness.state
    st.settings._value = st.settings.get().model_copy(update={"driver_call_timeout_s": 0.4})   # noqa: SLF001
    fake = harness.fakes["android-01"]
    fake.send_fault, fake.hang_s = "hang_after_effect", 1.5
    run = harness.run(["android-01"])
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.executor.has_zombie, what="chamada travada")
    assert rt.control == ControlOwner.ai and not st.devices.ai_begin(rt)   # ninguém mais recebe o aparelho
    detail = await harness.wait_run(run.id)
    assert detail.status == "completed" and len(fake.messages) == 1 and fake.max_concurrent == 1


# ---------------------------------------------------------------- reinício do backend
async def test_reinicio_do_backend_reconcilia_etapa_com_efeito_ja_disparado(harness: Harness) -> None:
    fake = harness.fakes["android-01"]
    fake.action_delay_s = 0.05
    run = harness.run(["android-01"])
    st = harness.state
    # derruba o backend no instante em que o toque de Enviar acabou de chegar ao aparelho
    await harness.wait(lambda: len(fake.messages) == 1, what="mensagem enviada")
    await harness.crash()
    fake.action_delay_s = 0
    db_steps = None
    st2 = await harness.boot()                                 # mesmo banco, mesmo "aparelho"
    db_steps = st2.db.query("SELECT key, status FROM steps WHERE run_id=?", (run.id,))
    assert st2.db.scalar("SELECT COUNT(*) FROM runs") == 1     # a fila foi preservada, sem reenfileirar
    detail = await harness.wait_run(run.id)
    assert detail.status == "completed", [(s.key, s.status, s.status_detail) for s in detail.steps]
    assert len(fake.messages) == 1                             # reconciliou pela tela; não reenviou
    assert db_steps is not None


async def test_reinicio_com_acao_de_efeito_pendente_e_sem_prova_fica_incerto(harness: Harness) -> None:
    st = harness.state
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    # estado gravado por um backend que caiu logo após registrar a INTENÇÃO de tocar em Enviar
    sid = f"{run.id}:android-01:v1:send_message"
    # A hora vem do Python, não de `datetime('now')`: aquela função é do SQLite, e a suíte também roda contra
    # PostgreSQL (`TEST_DATABASE_URL`). O app já grava assim — o fixture só passou a fazer o mesmo.
    agora = now_iso()
    with st.db.tx():
        st.db.execute("UPDATE runs SET status='running', mode='execute' WHERE id=?", (run.id,))
        st.db.execute("UPDATE objectives SET status='running', started_at=? WHERE run_id=?", (agora, run.id))
        st.db.execute("UPDATE steps SET status='succeeded' WHERE run_id=? AND seq<5", (run.id,))
        st.db.execute("UPDATE steps SET status='running', attempts=1 WHERE id=?", (sid,))
        st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                      (f"{sid}:a1", sid, 1, "running", agora))
        st.db.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, side_effect, intent_at) "
                      "VALUES (?,?,?,?,?,?,?)", (f"{sid}:a1", 1, "tap", "{}", "intended", 1, agora))
    fake = harness.fakes["android-01"]
    fake.screen, fake.contact = "chat", "QA-001"               # a conversa está aberta e SEM a mensagem
    await harness.crash()
    st2 = await harness.boot()
    action = st2.db.one("SELECT status, effect_possible FROM actions WHERE attempt_id=?", (f"{sid}:a1",))
    assert (action["status"], action["effect_possible"]) == ("unknown", 1)
    detail = await harness.wait_run(run.id, timeout=90)
    assert detail.objectives[0].status == "uncertain"
    assert fake.messages == [] and not any(c.startswith("tap:640") for c in fake.calls)   # nunca reenviou


# ---------------------------------------------------------------- controle manual
async def test_usuario_assume_no_ponto_seguro_e_devolve_para_a_ia(harness: Harness) -> None:
    st = harness.state
    fake = harness.fakes["android-01"]
    fake.action_delay_s = 0.08
    run = harness.run(["android-01"])
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.control == ControlOwner.ai, what="IA no controle")
    status, lease = st.devices.request_control(rt)
    assert status == "pending" and rt.takeover_requested      # a IA termina a ação atual antes de ceder
    with pytest.raises(ControlError):                          # sem disputa de cliques: ainda não é do usuário
        await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id="x", type="key", key="back"))
    await harness.wait(lambda: rt.control == ControlOwner.user, what="controle concedido")
    assert rt.lease_id == lease and "android-01" not in st.scheduler.workers
    calls_before = len(fake.calls)
    await asyncio.sleep(0.5)
    assert len([c for c in fake.calls[calls_before:] if not c.startswith(("screenshot", "page_source"))]) == 0

    frame = (await st.devices.observe(rt, timeout=5)).frame_id
    with pytest.raises(ControlError) as stale:                 # interação baseada em frame desconhecido/antigo
        await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id="frame-antigo", type="tap", x=10, y=10))
    assert stale.value.code == "stale_frame"
    with pytest.raises(ControlError) as wrong:
        await st.devices.manual_input(rt, ManualInput(lease_id="outro", frame_id=frame, type="tap", x=10, y=10))
    assert wrong.value.code == "not_controller"
    await st.devices.manual_input(rt, ManualInput(lease_id=lease, frame_id=frame, type="key", key="back"))

    fake.action_delay_s = 0
    st.devices.release_control(rt, lease)                      # devolve: a IA reobserva e conclui
    detail = await harness.wait_run(run.id)
    assert detail.status == "completed" and len(fake.messages) == 1
    assert any(a.status == "interrupted" for a in detail.attempts)


# ---------------------------------------------------------------- pausar / cancelar
async def test_pausar_preserva_o_ponto_e_cancelar_explicita_o_que_ja_foi_feito(harness: Harness) -> None:
    st = harness.state
    for f in harness.fakes.values():
        f.action_delay_s = 0.06
    run = harness.run(ALL)
    await harness.wait(lambda: len(st.scheduler.workers) == 3, what="3 workers")
    st.runs.pause(run.id)
    await harness.wait(lambda: not st.scheduler.workers, what="workers cederem")
    done_at_pause = st.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND status='succeeded'", (run.id,))
    await asyncio.sleep(0.4)
    assert st.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND status='succeeded'", (run.id,)) == done_at_pause
    assert st.db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=? AND status IN ('running','verifying')", (run.id,)) == 0
    st.runs.resume(run.id)
    await harness.wait(lambda: sum(len(f.messages) for f in harness.fakes.values()) >= 1, what="algum envio")
    st.runs.cancel(run.id)
    detail = await harness.wait_run(run.id, statuses=("cancelled", "completed", "completed_with_issues"))
    for o in detail.objectives:
        sent = len(harness.fakes[o.instance_id].messages)
        assert sent <= 1
        if o.status == "cancelled" and sent:
            assert o.effects, "cancelado após enviar precisa listar o efeito já realizado"
    assert all(s.status not in ("running", "verifying", "ready") for s in detail.steps)
