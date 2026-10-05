"""Item 17.10 — cascata para ator barato: o bloqueio do tier 0 sobe ao tier 1 antes de pedir uma pessoa, e o "sim" do
verificador barato em etapa com efeito externo é conferido pelo modelo de escalonamento (como o B14/7.10 fez com o "não").

`simulated`: o provedor é o falso do harness; o que se prova é a REGRA do executor (quem decide, quantas vezes, em que tier),
não a qualidade dos modelos.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import DeliveryLevel
from app.planning.provider import Decision, Usage, Verdict

from .conftest import Harness


@pytest.fixture(autouse=True)
def _pular_o_tempo(request: pytest.FixtureRequest) -> None:
    """T2: o tempo das ferramentas do aparelho falso é PULADO (relógio virtual), não esperado. Só nos testes com o
    harness; o que eles provam (ordem dos fatos, contagens, desfechos) é o mesmo."""
    if "harness" in request.fixturenames:
        request.getfixturevalue("harness").pular_o_tempo()

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user", "awaiting_person")


def _bloqueio(kind: str, needs_user: bool) -> Decision:
    return Decision(tool="step_blocked", args={"kind": kind, "reason": "não vejo o campo de texto", "needs_user": needs_user,
                                               "rationale": "ator barato desistiu"})


def _decide_que_bloqueia_no_tier0(harness: Harness, kind: str, needs_user: bool, tiers: list[int]) -> None:
    harness.cfg.file.ai.strong_model_for_side_effect = False        # a etapa de envio decide no tier 0 (senão já nasce no 1)
    inner = harness.ai.inner
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "send_message":
            tiers.append(req.tier)
            if req.tier == 0:
                return _bloqueio(kind, needs_user), Usage()
        return await decide0(req)

    inner.decide = decide


def _revisoes_por_falta_de_informacao(harness: Harness, run_id: str) -> int:
    return int(harness.state.db.scalar(
        "SELECT COUNT(*) FROM plan_versions v JOIN objectives o ON o.id = v.objective_id"
        " WHERE o.run_id=? AND v.reason LIKE ?", (run_id, "%falta de informação%")) or 0)


@pytest.mark.parametrize("kind", ["unexpected_screen", "missing_info", "other"])
async def test_bloqueio_do_tier_0_sobe_ao_tier_1_e_a_etapa_se_resolve(harness: Harness, kind: str) -> None:
    tiers: list[int] = []
    _decide_que_bloqueia_no_tier0(harness, kind, needs_user=True, tiers=tiers)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    obj = harness.state.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))      # type: ignore[union-attr]
    assert tiers[:2] == [0, 1], tiers                      # o 1º foi o tier 0 que bloqueou; o 2º, o de escalonamento
    assert obj["status"] == "succeeded"                    # sem pedir pessoa: a etapa terminou no tier 1
    assert len(harness.fakes["android-01"].messages) == 1


async def test_se_o_tier_1_tambem_bloqueia_vale_o_caminho_de_sempre_e_sobe_uma_vez_so(harness: Harness) -> None:
    harness.cfg.file.ai.strong_model_for_side_effect = False
    inner = harness.ai.inner
    tiers: list[int] = []

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "send_message":
            tiers.append(req.tier)
            return _bloqueio("missing_info", needs_user=True), Usage()
        return await inner_decide0(req)

    inner_decide0 = inner.decide
    inner.decide = decide
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    obj = harness.state.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))      # type: ignore[union-attr]
    assert obj["status"] == "waiting_user"                 # a pessoa só entra depois do tier 1 também bloquear
    # 29.35: a falta de informação (que não é credencial) ganha UMA revisão do plano antes da pessoa; na versão revisada,
    # a mesma subida ao tier 1. Uma subida por tentativa, nem laço nem terceira consulta por tentativa.
    assert tiers == [0, 1, 0, 1], tiers
    assert _revisoes_por_falta_de_informacao(harness, run.id) == 1


@pytest.mark.parametrize("kind", ["challenge", "auth_required", "wrong_account"])
async def test_bloqueios_que_dependem_de_pessoa_nao_sobem(harness: Harness, kind: str) -> None:
    tiers: list[int] = []
    _decide_que_bloqueia_no_tier0(harness, kind, needs_user=True, tiers=tiers)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert tiers.count(1) == 0 and tiers[:1] == [0], tiers


async def test_cascata_desligada_na_configuracao_nao_sobe(harness: Harness) -> None:
    harness.cfg.file.ai.cascade_blocked_to_tier1 = False
    tiers: list[int] = []
    _decide_que_bloqueia_no_tier0(harness, "missing_info", needs_user=True, tiers=tiers)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    obj = harness.state.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))      # type: ignore[union-attr]
    assert obj["status"] == "waiting_user" and tiers == [0, 0]     # 29.35: uma revisão do plano, depois a pessoa
    assert _revisoes_por_falta_de_informacao(harness, run.id) == 1


# ---------------------------------------------------------------- "sim" barato em etapa com efeito externo
def _modelos_diferentes(harness: Harness) -> None:
    """O rejulgamento só faz sentido com verificador diferente do modelo de escalonamento (configuração real)."""
    harness.cfg.env.ai_model_verifier = "modelo-barato"
    harness.cfg.env.ai_model_escalation = "modelo-forte"


def _verify_com(harness: Harness, barato: str, forte: str, vistos: list[bool]) -> None:
    inner = harness.ai.inner
    verify0 = inner.verify

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "verify_sent":
            return await verify0(req)
        vistos.append(req.escalate)
        quem = forte if req.escalate else barato
        return Verdict(satisfied=quem, evidence=f"julgamento {'forte' if req.escalate else 'barato'}",
                       delivery_level=DeliveryLevel.sent), Usage()

    inner.verify = verify


async def test_sim_barato_em_efeito_externo_e_conferido_e_o_forte_concordando_fecha(harness: Harness) -> None:
    _modelos_diferentes(harness)
    vistos: list[bool] = []
    _verify_com(harness, barato="yes", forte="yes", vistos=vistos)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    obj = harness.state.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))      # type: ignore[union-attr]
    assert vistos[:2] == [False, True] and obj["status"] == "succeeded"


async def test_forte_discordando_do_sim_barato_nao_conta_como_prova(harness: Harness) -> None:
    _modelos_diferentes(harness)
    vistos: list[bool] = []
    # "unprovable" encerra a verificação na hora (um "no" faria o executor esperar a tela mudar até o prazo da etapa, e o
    # teste só ficaria lento); o que importa aqui é que o veredito do modelo forte, não o do barato, é o que vale.
    _verify_com(harness, barato="yes", forte="unprovable", vistos=vistos)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    obj = harness.state.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))      # type: ignore[union-attr]
    assert vistos[:2] == [False, True]
    assert obj["status"] != "succeeded"                    # "falha ou incerteza nunca contam como sucesso"
    assert len(harness.fakes["android-01"].messages) == 1  # e nada foi reenviado


async def test_mesmo_modelo_no_verificador_e_no_escalonamento_nao_rejulga(harness: Harness) -> None:
    harness.cfg.env.ai_model_verifier = harness.cfg.env.ai_model_escalation = "o-mesmo"
    vistos: list[bool] = []
    _verify_com(harness, barato="yes", forte="yes", vistos=vistos)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert True not in vistos


async def test_rejulgamento_desligado_na_configuracao(harness: Harness) -> None:
    _modelos_diferentes(harness)
    harness.cfg.file.ai.rejudge_yes_on_side_effect = False
    vistos: list[bool] = []
    _verify_com(harness, barato="yes", forte="no", vistos=vistos)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert True not in vistos
