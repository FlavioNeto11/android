"""30.42: a execução de PROVA parte de um estado conhecido e não replaneja.

Antes da 1ª etapa: `force-stop` de todos os apps do plano do fluxo e abertura do app principal, só na prova e uma vez
por execução; nunca `pm clear`. Falhou a etapa: a prova fecha com o motivo próprio, sem plano novo (um plano novo não é
mais o fluxo). Nível de prova: `simulated` (aparelho falso `FakeQaDevice`, provedor simulado, nenhum central).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.contracts.origem import PREFIXO_VALIDACAO
from app.models import StepDTO
from app.taskqueue.executor import Outcome, StepOutcome

from .test_learning_prova import Real, real  # noqa: F401  (a fixture `real` é a armação da prova)

PRAZO = "Tempo da etapa esgotado (180s)."


def _eventos(real: Real, run_id: str, kind: str) -> list[str]:
    return [str(r["message"]) for r in real.db.query(
        "SELECT message FROM events WHERE run_id=? AND kind=? ORDER BY id", (run_id, kind))]


def _falhar(real: Real, chave: str) -> None:
    """A etapa `chave` devolve `failed` (prazo) sempre, sem tocar no aparelho."""
    assert real.h.state is not None
    executor = real.h.state.scheduler.executor
    original = executor.run_step

    async def run_step(**kw: Any) -> StepOutcome:
        step = kw["step"]
        assert isinstance(step, StepDTO)
        if step.key == chave:
            return StepOutcome(Outcome.failed, PRAZO)
        return await original(**kw)

    executor.run_step = run_step                       # type: ignore[method-assign]


async def test_prova_encerra_os_apps_do_plano_e_abre_o_principal_antes_da_primeira_etapa(real: Real) -> None:
    fake = real.h.fakes["android-01"]
    antes = len(fake.calls)
    controle = fake.calls.count("force_stop")           # a execução comum que ensinou o fluxo não encerrou nada
    assert controle == 0
    run = real.prova("partida-1")
    assert (await real.h.wait_run(run)).status == "completed"
    nova = fake.calls[antes:]
    assert nova.count("force_stop") == 1                # o plano do QA tem um app só: um force-stop
    # o force-stop é a PRIMEIRA chamada ao aparelho: o estado conhecido vem antes de qualquer leitura ou toque
    assert nova.index("force_stop") == 0
    decisoes = _eventos(real, run, "decision")
    assert any("ponto de partida" in d and "sem apagar dados" in d for d in decisoes), decisoes


async def test_execucao_comum_nao_parte_de_estado_conhecido(real: Real) -> None:
    fake = real.h.fakes["android-01"]
    antes = len(fake.calls)
    comum = real.h.run(["android-01"])
    assert (await real.h.wait_run(comum.id)).status == "completed"
    assert "force_stop" not in fake.calls[antes:]
    assert not any("ponto de partida" in d for d in _eventos(real, comum.id, "decision"))


async def test_o_ponto_de_partida_roda_uma_vez_por_execucao(real: Real) -> None:
    fake = real.h.fakes["android-01"]
    run = real.prova("partida-2")
    assert (await real.h.wait_run(run)).status == "completed"
    s = real.h.state
    assert s is not None
    n = fake.calls.count("force_stop")
    obj = s.repo.objective_row(f"{run}:android-01")
    await s.scheduler._partir_da_prova(obj, s.repo.run_row(run), s.devices.get("android-01"))   # noqa: SLF001
    assert fake.calls.count("force_stop") == n          # a segunda chamada não encerra nada


async def test_prova_nao_replaneja_e_diz_por_que_parou(real: Real) -> None:
    _falhar(real, "compose_message")
    run = real.prova("partida-3")
    detalhe = await real.h.wait_run(run)
    assert detalhe.status != "completed"
    versoes = real.db.scalar("SELECT COUNT(*) FROM plan_versions WHERE objective_id=?", (f"{run}:android-01",))
    assert versoes == 1                                  # nenhum plano novo
    objetivo = real.db.one("SELECT status_detail FROM objectives WHERE id=?", (f"{run}:android-01",))
    assert objetivo is not None
    texto = str(objetivo["status_detail"])
    assert "a prova não replaneja" in texto and "um plano novo não é mais o fluxo" in texto, texto
    assert not any("Recuperação automática" in d for d in _eventos(real, run, "decision"))


async def test_controle_a_execucao_comum_replaneja_na_mesma_falha(real: Real, monkeypatch: pytest.MonkeyPatch) -> None:
    """O controle positivo: sem `prova_fluxo_id`, a mesma falha de prazo revisa o plano (a prova é que não)."""
    assert real.h.state is not None
    executor = real.h.state.scheduler.executor
    original = executor.run_step
    restantes = [1]

    async def run_step(**kw: Any) -> StepOutcome:
        step = kw["step"]
        assert isinstance(step, StepDTO)
        if step.key == "compose_message" and restantes[0] > 0:
            restantes[0] -= 1
            return StepOutcome(Outcome.failed, PRAZO)
        return await original(**kw)

    monkeypatch.setattr(executor, "run_step", run_step)
    comum = real.h.run(["android-01"])
    await real.h.wait_run(comum.id, timeout=60)
    assert real.db.scalar("SELECT COUNT(*) FROM plan_versions WHERE objective_id=?", (f"{comum.id}:android-01",)) >= 2


async def test_a_reexecucao_da_validacao_do_qa_tambem_parte_de_estado_conhecido(real: Real) -> None:
    """30.43: a re-execução de receita do P4 (chave `PREFIXO_VALIDACAO`, origem `validacao_qa`) também encerra os apps
    e abre o principal antes da 1ª etapa (o 6f459c abriu o app dentro da conversa e enviou já na abertura)."""
    fake = real.h.fakes["android-01"]
    antes = len(fake.calls)
    run = real.h.run(["android-01"], key=f"{PREFIXO_VALIDACAO}lv-teste")
    assert (await real.h.wait_run(run.id)).status == "completed"
    nova = fake.calls[antes:]
    assert nova.count("force_stop") == 1 and nova.index("force_stop") == 0
    assert any("Validação do QA (re-execução): ponto de partida" in d for d in _eventos(real, run.id, "decision"))
