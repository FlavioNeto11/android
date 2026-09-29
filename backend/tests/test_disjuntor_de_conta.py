"""Disjuntor de conta (ADR-055, 29/09/2026): conta bloqueada para o que está em curso, e a frota que mexeu no mesmo
alvo para junto.

O dono: "toda vez que para na tela de confirmar se você é humano é uma confirmação que a conta está bloqueada". Cinco
das oito contas do Instagram estão bloqueadas. Antes, o bloqueio do perfil só valia para o PRÓXIMO despacho: o objetivo
em curso daquela conta seguia agindo (`_stop_reason` só olhava cancelar/pausar/assumir), e as outras contas que tinham
acabado de agir sobre a mesma pessoa seguiam no mesmo ritmo que derrubou a primeira.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.models import ProfileCreate
from app.planning.simulated_provider import SimulatedProvider
from app.util import iso_in

from .conftest import Harness

SENHA = "$a=B7ee1#<b-C?S-{"


class BloqueiaNaPrimeiraDecisao:
    """Provedor que, na primeira decisão, bloqueia o perfil — é a conta caindo no desafio por outro caminho (o outro
    aparelho da mesma pessoa, a tela desmentindo a sessão) enquanto este objetivo age."""

    def __init__(self, inner: SimulatedProvider, ao_decidir: Callable[[], None]):
        self.inner = inner
        self.ao_decidir = ao_decidir
        self.calls = 0
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def status(self) -> object:
        return self.inner.status()

    async def decide(self, req: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            self.ao_decidir()
        return await self.inner.decide(req)

    async def verify(self, req: Any) -> Any:
        return await self.inner.verify(req)

    async def plan(self, req: Any) -> Any:
        return await self.inner.plan(req)

    async def generate_social_response(self, req: Any) -> Any:
        raise NotImplementedError


async def test_perfil_bloqueado_no_meio_da_execucao_para_o_objetivo_com_o_motivo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    pid: dict[str, str] = {}

    def bloquear() -> None:
        assert h.state is not None
        h.state.social_repo.update_profile(pid["p"], {"status": "blocked"})

    provedor = BloqueiaNaPrimeiraDecisao(SimulatedProvider(), bloquear)
    h.ai = provedor  # type: ignore[assignment]
    await h.boot()
    assert h.state is not None
    try:
        pid["p"] = h.state.social.create_profile(ProfileCreate(username="juliana.teste", password=SENHA,
                                                                instance_id="android-01")).id
        run = h.run(["android-01"])
        detalhe = await h.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "cancelled"))
        obj = detalhe.objectives[0]
        assert h.state.repo.objective_row(obj.id)["profile_id"] == pid["p"]
        # Parou no ponto seguro seguinte, com o motivo NO OBJETIVO — e não seguiu até concluir a tarefa.
        assert obj.status == "waiting_user", obj.status
        assert "bloqueada" in (obj.blocked_reason or "") and "@juliana.teste" in (obj.blocked_reason or "")
        assert provedor.calls == 1                        # nenhuma decisão a mais depois do bloqueio
    finally:
        await h.state.stop()


async def test_objetivo_de_conta_bloqueada_nao_e_despachado_mesmo_sem_porta_de_sessao(harness: Harness) -> None:
    """A porta de sessão só existe para app com login gerenciado (o Instagram). Um objetivo de conta bloqueada num
    app sem provedor (o QA Messenger aqui; o Chrome no parque) passava direto e agia pela pessoa bloqueada."""
    state = harness.state
    assert state is not None
    pid = state.social.create_profile(ProfileCreate(username="mariana.teste", password=SENHA,
                                                    instance_id="android-01")).id
    # Bloqueada ANTES de a execução existir, com o disjuntor já disparado (nada a pausar): quem segura o item aqui é
    # o despacho, não a pausa.
    await harness.ticks(2)
    state.social_repo.update_profile(pid, {"status": "blocked"})
    await harness.ticks(2)
    antes = harness.ai.count("decide")
    run = harness.run(["android-01"])
    detalhe = await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "cancelled"))
    obj = detalhe.objectives[0]
    assert obj.status == "waiting_user", obj.status
    assert "bloqueada" in (obj.blocked_reason or "")
    assert all(s.attempts == 0 for s in detalhe.steps)   # nenhuma etapa assumida, nada tocado
    assert harness.ai.count("decide") == antes          # e nenhuma decisão de IA paga por ela


def _execucao(db: Any, run_id: str, instance_id: str, profile_id: str) -> None:
    """Uma execução em curso com um objetivo PENDENTE e sem etapas: o despacho não a pega (não há etapa pronta), e
    `recompute_run` não a encerra (há objetivo ativo). É o estado exato que o disjuntor precisa pausar."""
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,?,'seguir @alvo','execute','running',1,?,?)",
               (run_id, f"k-{run_id}", f'["{instance_id}"]', iso_in(0)))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES (?,?,?,'pending',1,'{}',?)", (f"{run_id}:{instance_id}", run_id, instance_id, profile_id))


async def test_bloqueio_pausa_as_execucoes_da_conta_e_das_que_agiram_no_mesmo_alvo_nas_48h(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    social, repo, db = state.social_repo, state.repo, state.db

    def perfil(nome: str) -> str:
        return state.social.create_profile(ProfileCreate(username=nome, password=None)).id

    p, q, r, s = perfil("beatriz.teste"), perfil("felipe.teste"), perfil("lucas.teste"), perfil("bruno.teste")
    # P e Q seguiram a MESMA pessoa nas últimas 48 h; R agiu sobre outra; S agiu sobre a mesma, mas há 60 h.
    social.record_interaction(p, type="followed", direction="outbound", status="confirmed", counterparty="alvo",
                              occurred_at=iso_in(-10 * 3600))
    social.record_interaction(q, type="followed", direction="outbound", status="uncertain", counterparty="alvo",
                              occurred_at=iso_in(-20 * 3600))
    social.record_interaction(r, type="followed", direction="outbound", status="confirmed", counterparty="outra",
                              occurred_at=iso_in(-3600))
    social.record_interaction(s, type="followed", direction="outbound", status="confirmed", counterparty="alvo",
                              occurred_at=iso_in(-60 * 3600))
    # Mensagem RECEBIDA de @alvo não é "agir no alvo": quem só recebeu não entra.
    social.record_interaction(r, type="dm_received", direction="inbound", status="confirmed", counterparty="alvo",
                              occurred_at=iso_in(-3600))
    for run_id, iid, dono in (("run-p", "android-01", p), ("run-q", "android-02", q), ("run-r", "android-03", r),
                              ("run-s", "android-03", s)):
        _execucao(db, run_id, iid, dono)
    await harness.ticks(2)                                 # a linha de base do disjuntor já foi tirada

    social.update_profile(p, {"status": "blocked"})
    await harness.wait(lambda: repo.run_row("run-q")["pause_requested"] == 1, what="execução de @felipe pausada")
    await harness.ticks(2)

    run_p, run_q = repo.run_row("run-p"), repo.run_row("run-q")
    assert run_p["pause_requested"] == 1 and run_p["status"] == "paused"
    assert "@beatriz.teste" in (run_p["status_detail"] or "")
    assert run_q["status"] == "paused"
    assert "@felipe.teste" in (run_q["status_detail"] or "") and "@alvo" in (run_q["status_detail"] or "")
    assert repo.run_row("run-r")["pause_requested"] == 0   # outro alvo
    assert repo.run_row("run-s")["pause_requested"] == 0   # mesmo alvo, fora da janela de 48 h

    eventos = [e for e in db.query("SELECT data FROM events WHERE data LIKE '%disjuntor_de_conta%'")]
    assert len(eventos) == 1

    # Uma pessoa confere e retoma a execução de @felipe: o disjuntor não a pausa de novo pelo MESMO bloqueio.
    state.runs.resume("run-q")
    await harness.ticks(3)
    assert repo.run_row("run-q")["pause_requested"] == 0


async def test_marcador_de_conta_travada_do_aparelho_para_o_objetivo(harness: Harness) -> None:
    """A porta do marcador de conta travada (o pacote de quarentena a liga): com ele aceso, o objetivo daquele
    aparelho para no ponto seguro, mesmo com o perfil ainda `active` — e a porta desligada não muda nada."""
    state = harness.state
    assert state is not None
    sched = state.scheduler
    rt = state.devices.get("android-01")
    _execucao(state.db, "run-m", "android-01", "sem-perfil")
    assert sched._stop_reason("run-m", rt, "run-m:android-01") is None                     # noqa: SLF001
    sched.conta_travada_no_aparelho = lambda iid: ("a conta logada em android-01 está travada"
                                                   if iid == "android-01" else None)
    motivo = sched._stop_reason("run-m", rt, "run-m:android-01")                            # noqa: SLF001
    assert motivo is not None and "travada" in motivo
    assert sched._stop_reason("run-m", state.devices.get("android-02"), None) is None       # noqa: SLF001
