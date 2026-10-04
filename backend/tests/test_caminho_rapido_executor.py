"""Caminho rápido 1 (LT-1 e LT-2): atalhos do executor que PULAM O ATOR e nunca a prova.

- LT-1: a pós-condição já vale na tela lida na entrada da volta -> sai do laço para o `_verify` sem chamar o ator
  (etapa determinística, sem efeito, tela não sensível); etapa julgada sem efeito e sem nível de entrega -> o juiz
  confere a tela na entrada e só um "não" chama o ator. Com efeito (`side_effect`) NÃO há atalho (UI otimista).
- LT-2: `expect_done=true` em etapa julgada vai direto ao `_verify`; "não" volta ao ator NA MESMA tentativa, com o
  motivo no histórico (nunca retry/failed). O veredito "sim" é reusado no fim do laço: uma verificação, não duas.
- `steps.driven_by='sem_ator'` na etapa que fecha sem o ator decidir nada.

Nível de prova: `simulated` (Harness da porta 5640, aparelho falso e provedor simulado; o veredito do juiz é programado
no provedor falso). Nada disto prova a latência no ambiente real.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import Postcondition
from app.planning.provider import Usage, Verdict
from app.taskqueue import executor as executor_mod
from app.util import norm_text

from .conftest import Harness


@pytest.fixture(autouse=True)
def _pular_o_tempo(request: pytest.FixtureRequest) -> None:
    """T2: o tempo das ferramentas do aparelho falso é PULADO (relógio virtual), não esperado. Só nos testes com o
    harness; o que eles provam (ordem dos fatos, contagens, desfechos) é o mesmo."""
    if "harness" in request.fixturenames:
        request.getfixturevalue("harness").pular_o_tempo()

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user")


def _chamadas(h: Harness, papel: str, etapa: str) -> int:
    return sum(1 for c in h.ai.calls if c["role"] == papel and c.get("step") == etapa)


def _etapas(h: Harness, run_id: str) -> dict[str, Any]:
    return {r["key"]: r for r in h.state.db.query(                         # type: ignore[union-attr]
        "SELECT key, driven_by, attempts, status FROM steps WHERE run_id=?", (run_id,))}


def _campo_da_mensagem(req: Any) -> str:
    achados = req.screen.tree.find(resource_id="message_input")
    return norm_text(achados[0].text) if achados else ""


def _julgar_a_mensagem(h: Harness, *, marcar_expect_done: bool = False, nega_primeiras: int = 0,
                       sempre_sim: bool = False, historicos: list[list[str]] | None = None) -> list[str]:
    """`compose_message` vira etapa JULGADA sem efeito e sem nível (a do plano simulado é por texto). O juiz falso diz
    "sim" quando o campo de mensagem já tem o texto; `nega_primeiras` faz as N primeiras conferências dizerem "não" de
    qualquer jeito (o juiz errou/ainda não viu). Devolve a lista de motivos que ele deu."""
    h.cfg.file.ai.recipes = "replay"                  # com receitas desligadas `driven_by` nem é gravado (legado: nulo)
    inner = h.ai.inner
    plano0, decide0, verify0 = inner.plan, inner.decide, inner.verify
    vistos: list[str] = []

    async def plan(req: Any) -> Any:
        plano, uso = await plano0(req)
        for s in plano.steps:
            if s.key == "compose_message":
                s.postcondition = Postcondition(kind="model_judged", value="campo preenchido",
                                                description="O campo de mensagem contém o texto pedido.")
        return plano, uso

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "compose_message" and historicos is not None:
            historicos.append(list(req.history))
        decisao, uso = await decide0(req)
        if marcar_expect_done and req.ctx.step_key == "compose_message" and decisao.tool == "type_text":
            decisao.args["expect_done"] = True
        return decisao, uso

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "compose_message":
            return await verify0(req)
        if len(vistos) < nega_primeiras:
            vistos.append("não")
            return Verdict(satisfied="no", evidence="[simulado] o juiz ainda não viu o texto"), Usage()
        pronto = sempre_sim or _campo_da_mensagem(req) == norm_text(req.ctx.parameters.get("message", ""))
        vistos.append("sim" if pronto else "não")
        return Verdict(satisfied="yes" if pronto else "no",
                       evidence="[simulado] o campo mostra o texto" if pronto else "[simulado] campo vazio"), Usage()

    inner.plan, inner.decide, inner.verify = plan, decide, verify
    return vistos


# ------------------------------------------------------------------ LT-1
async def test_lt1_etapa_deterministica_ja_no_estado_final_nao_chama_o_ator(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    etapas = _etapas(harness, run.id)
    # a tela inicial do app JÁ mostra "Conta: …" quando a etapa começa: nenhum decide, e a trilha diz quem conduziu
    assert _chamadas(harness, "decide", "confirm_account") == 0
    assert etapas["confirm_account"]["status"] == "succeeded"
    assert etapas["confirm_account"]["driven_by"] == "sem_ator"
    assert len(harness.fakes["android-01"].messages) == 1               # e o resto do plano seguiu normalmente


async def test_lt1_etapa_com_efeito_nao_tem_atalho(harness: Harness) -> None:
    """A mesma etapa (a pós-condição já vale na entrada), agora marcada com efeito: UI otimista mostra o "feito" antes de
    ele valer, então o ator decide e a trilha NÃO diz `sem_ator`."""
    harness.cfg.file.ai.recipes = "replay"
    plano0 = harness.ai.inner.plan

    async def plan(req: Any) -> Any:
        plano, uso = await plano0(req)
        for s in plano.steps:
            if s.key == "confirm_account":
                s.side_effect = True
        return plano, uso

    harness.ai.inner.plan = plan
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert _chamadas(harness, "decide", "confirm_account") >= 1
    assert _etapas(harness, run.id)["confirm_account"]["driven_by"] != "sem_ator"


async def test_lt1_etapa_julgada_aprovada_pelo_juiz_na_entrada_tem_zero_decide_e_um_verify(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    # o caminho do handoff: sem prova local no catálogo, o juiz barato confere a tela já na entrada
    monkeypatch.setattr(executor_mod, "ENTRADA_JULGADA_SO_COM_PROVA_LOCAL", False)
    _julgar_a_mensagem(harness, sempre_sim=True)     # o juiz vê a etapa pronta já na entrada (a tela é a final)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert _chamadas(harness, "decide", "compose_message") == 0
    assert _chamadas(harness, "verify", "compose_message") == 1         # o veredito da entrada é reusado no fim do laço
    etapa = _etapas(harness, run.id)["compose_message"]
    assert etapa["status"] == "succeeded" and etapa["driven_by"] == "sem_ator"


async def test_lt1_etapa_julgada_com_veredito_negativo_na_entrada_chama_o_ator(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "ENTRADA_JULGADA_SO_COM_PROVA_LOCAL", False)
    vistos = _julgar_a_mensagem(harness)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert vistos[0] == "não"                                            # campo vazio na entrada: sem atalho
    assert _chamadas(harness, "decide", "compose_message") >= 1          # o ator digitou e concluiu, como sempre
    etapa = _etapas(harness, run.id)["compose_message"]
    assert etapa["status"] == "succeeded" and etapa["driven_by"] == "ai" and etapa["attempts"] == 1


async def test_lt1_entrada_de_etapa_julgada_sem_prova_local_nao_paga_modelo(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O padrão: sem prova local no catálogo a entrada NÃO chama o juiz (a tela de entrada quase nunca é a final, e o
    aceite do LT-1 é `verify` por etapa não subir): o ator decide como sempre e a etapa paga UM julgamento, o do fim."""
    vistos = _julgar_a_mensagem(harness)
    antes_do_ator: list[str] = []
    verify0 = executor_mod.StepExecutor._verify

    async def espiao(self: Any, rt: Any, step: Any, *a: Any, **kw: Any) -> Any:
        if kw.get("uma_rodada") and step.key == "compose_message":
            antes_do_ator.append(step.key)
        return await verify0(self, rt, step, *a, **kw)

    monkeypatch.setattr(executor_mod.StepExecutor, "_verify", espiao)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert antes_do_ator == []          # nem a releitura da árvore da conferência de entrada foi paga
    assert _chamadas(harness, "verify", "compose_message") == 1 and vistos == ["sim"]
    assert _chamadas(harness, "decide", "compose_message") >= 1


# ------------------------------------------------------------------ LT-2
async def test_lt2_expect_done_em_etapa_julgada_vai_direto_ao_juiz(harness: Harness) -> None:
    vistos = _julgar_a_mensagem(harness, marcar_expect_done=True)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    # 1 decide (a digitação, com expect_done) e 1 verify: a conferência depois da ação ("sim"), REUSADA no fim do laço
    # — sem step_done do ator (o 2º decide de antes) e sem segunda chamada ao juiz.
    assert _chamadas(harness, "decide", "compose_message") == 1
    assert _chamadas(harness, "verify", "compose_message") == 1
    assert vistos == ["sim"]
    etapa = _etapas(harness, run.id)["compose_message"]
    assert etapa["status"] == "succeeded" and etapa["attempts"] == 1
    assert etapa["driven_by"] == "ai"                                    # o ator agiu: LT-2 não é `sem_ator`
    assert len(harness.fakes["android-01"].messages) == 1


async def test_lt2_veredito_negativo_volta_ao_ator_na_mesma_tentativa_com_o_motivo_no_historico(harness: Harness) -> None:
    historicos: list[list[str]] = []
    # a 1ª conferência (depois da digitação com expect_done) diz "não": o ator decide de novo na mesma tentativa
    vistos = _julgar_a_mensagem(harness, marcar_expect_done=True, nega_primeiras=1, historicos=historicos)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert vistos == ["não", "sim"]
    assert _chamadas(harness, "decide", "compose_message") >= 2
    # o 2º decide já vê o veredito do juiz no histórico
    assert any("NÃO está comprovada" in linha and "depois desta ação (expect_done)" in linha
               for linha in historicos[1]), historicos[1]
    etapa = _etapas(harness, run.id)["compose_message"]
    assert etapa["status"] == "succeeded" and etapa["attempts"] == 1     # nunca uma tentativa nova por causa do "não"


async def test_lt2_nunca_em_etapa_com_efeito(harness: Harness) -> None:
    """Revisão (03/10): em etapa COM efeito, o `expect_done` de uma ação que não é o commit não vai ao juiz. O juiz que diz
    "sim" cedo (o texto digitado no campo lido como já publicado) fecharia a etapa como sucesso sem o efeito; com a
    trava, o ator segue e só o caminho de sempre (commit ou `step_done`, depois a verificação) fecha a etapa."""
    _julgar_a_mensagem(harness, marcar_expect_done=True, sempre_sim=True)
    plano0 = harness.ai.inner.plan

    async def plan(req: Any) -> Any:
        plano, uso = await plano0(req)
        for s in plano.steps:
            if s.key == "compose_message":
                s.side_effect = True
        return plano, uso

    harness.ai.inner.plan = plan
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    # sem a trava seria 1 decide (a digitação com expect_done) e a etapa fechava pelo juiz; com ela o ator decide de novo
    assert _chamadas(harness, "decide", "compose_message") >= 2
    assert _etapas(harness, run.id)["compose_message"]["driven_by"] != "sem_ator"


async def test_sem_ator_tambem_e_gravado_com_as_receitas_desligadas(harness: Harness) -> None:
    """Com `ai.recipes: off` as outras etapas ficam com `driven_by` nulo (legado); a que fechou sem o ator ainda diz
    `sem_ator` — o atalho não depende das receitas."""
    harness.cfg.file.ai.recipes = "off"
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    etapas = _etapas(harness, run.id)
    assert etapas["confirm_account"]["driven_by"] == "sem_ator"
    assert etapas["send_message"]["driven_by"] != "sem_ator"
