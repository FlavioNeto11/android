"""31.113 F2: a linha da etapa guarda o marcador da persona; o executor resolve o valor em memória, num ponto só.

A materialização deixa `{perfil_*}`/`{conta_*_usuario}` como marcador no texto que descreve e confere a etapa (título,
objetivo, pré e pós-condição, guardas). `StepExecutor.run_step` resolve com a persona do objetivo
(`dado_da_persona.resolver_persona`): o ator, a receita, a conferência da tela (`text_visible`) e o juiz recebem o
valor; a linha, o `plan_versions` e o evento `step.updated` ficam com o marcador. Desde a F3, os `bindings` também
guardam o marcador (a porta os resolve pelo leitor único, `Repository.bindings_da_etapa`). O dado que sumiu da persona depois da
materialização não vai cru ao aparelho: a etapa espera a pessoa com o nome do campo.

Os quatro casos pedidos pela Android (06/10 02:03Z): (i) o ator recebe o valor; (ii) a `text_visible` confere o
valor; (iii) linha, `plan_versions` e `step.updated` com o marcador; (iv) duas personas no mesmo plano.

Nível de prova: `simulated` (provedor por regras, aparelhos falsos, valores sintéticos).
"""
from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from app.models import ProfileCreate
from app.taskqueue.dado_da_persona import resolver_persona, sem_valor_na_etapa

from .conftest import Harness

MARCA_GOAL = "Pesquisar {perfil_nome_exibicao} na busca"
MARCA_POST = "{perfil_nome_exibicao}"
PESSOAS = {"android-01": ("pessoa.um", "Zelda Primeira"), "android-02": ("pessoa.dois", "Odete Segunda")}


def _planejar_com_marcador(inner: Any, bindings: dict[str, str] | None = None) -> None:
    """O planejador simulado devolve o plano de sempre com o marcador no objetivo e na pós-condição da 1ª etapa (e nos
    `bindings`, quando dados)."""
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        s = p.steps[0]
        post = s.postcondition.model_copy(update={"kind": "text_visible", "value": MARCA_POST})
        primeira = s.model_copy(update={"goal": MARCA_GOAL, "postcondition": post,
                                        **({"bindings": {**s.bindings, **bindings}} if bindings else {})})
        return p.model_copy(update={"steps": [primeira, *p.steps[1:]]}), u

    inner.plan = plan


def _espiar(h: Harness) -> tuple[dict[str, list[Any]], dict[str, list[str]]]:
    """Guarda a etapa que o `_run_step` (o ator) recebe e o valor que a conferência da tela usa, por aparelho."""
    ex = h.state.scheduler.executor                                          # type: ignore[union-attr]
    recebidas: dict[str, list[Any]] = {}
    conferidas: dict[str, list[str]] = {}
    run0, holds0 = ex._run_step, ex._postcondition_holds                    # noqa: SLF001

    async def run_step(**kw: Any) -> Any:
        recebidas.setdefault(kw["rt"].id, []).append(kw["step"])
        return await run0(**kw)

    def holds(step: Any, *a: Any, **kw: Any) -> bool:
        conferidas.setdefault(step.instance_id, []).append(step.postcondition.value)
        return bool(holds0(step, *a, **kw))

    ex._run_step, ex._postcondition_holds = run_step, holds                 # noqa: SLF001
    return recebidas, conferidas


def _personas(h: Harness) -> None:
    for iid, (usuario, exibicao) in PESSOAS.items():
        h.state.social.create_profile(ProfileCreate(username=usuario, instance_id=iid,  # type: ignore[union-attr]
                                                    display_name=exibicao))


async def _executar(h: Harness, recebidas: dict[str, list[Any]] | None = None,
                   bindings: dict[str, str] | None = None) -> str:
    """Roda até a 1ª tentativa de cada aparelho ter conferido a tela (a tela falsa nunca mostra o nome, então a
    execução não termina sozinha) e cancela."""
    _personas(h)
    _planejar_com_marcador(h.ai.inner, bindings)
    run = h.run(list(PESSOAS))
    db = h.state.db                                                          # type: ignore[union-attr]
    await h.wait(lambda: db.scalar("SELECT COUNT(DISTINCT s.instance_id) FROM attempts t JOIN steps s ON"
                                   " s.id=t.step_id WHERE s.run_id=? AND t.status<>'running'", (run.id,)) == len(PESSOAS),
                 timeout=60, what="a 1ª tentativa fechada nos dois aparelhos")
    h.state.runs.cancel(run.id)                                              # type: ignore[union-attr]
    return str(run.id)


async def test_o_ator_recebe_o_valor_de_cada_persona_e_a_tela_e_conferida_com_ele(harness: Harness) -> None:
    """(i), (ii) e (iv): no mesmo plano, cada aparelho recebe o nome da SUA persona no objetivo e confere a tela com ele."""
    recebidas, conferidas = _espiar(harness)
    await _executar(harness, recebidas)
    for iid, (_u, exibicao) in PESSOAS.items():
        primeira = recebidas[iid][0]
        assert primeira.goal == f"Pesquisar {exibicao} na busca", iid
        assert "{perfil_" not in primeira.goal and primeira.postcondition.value == exibicao
        assert exibicao in conferidas.get(iid, []), (iid, conferidas)
    assert recebidas["android-01"][0].goal != recebidas["android-02"][0].goal


async def test_a_linha_o_plan_versions_e_o_step_updated_ficam_com_o_marcador(harness: Harness) -> None:
    """(iii) e (iv): o que fica guarda o marcador nos dois aparelhos, e nenhum nome em claro."""
    run_id = await _executar(harness)
    db = harness.state.db                                                    # type: ignore[union-attr]
    linhas = db.query("SELECT instance_id, goal, postcondition FROM steps WHERE run_id=? AND key=("
                      "SELECT key FROM steps WHERE run_id=? ORDER BY seq LIMIT 1)", (run_id, run_id))
    assert {r["instance_id"] for r in linhas} == set(PESSOAS)
    assert all(r["goal"] == MARCA_GOAL and json.loads(r["postcondition"])["value"] == MARCA_POST for r in linhas)
    versoes = " ".join(str(r["steps"]) for r in db.query(
        "SELECT pv.steps FROM plan_versions pv JOIN objectives o ON o.id=pv.objective_id WHERE o.run_id=?", (run_id,)))
    eventos = " ".join(str(r["data"]) for r in db.query(
        "SELECT data FROM events WHERE run_id=? AND kind='step.updated'", (run_id,)))
    assert MARCA_POST in versoes and MARCA_POST in eventos
    for _u, exibicao in PESSOAS.values():
        for nome in (exibicao, *exibicao.split()):
            assert nome not in versoes and nome not in eventos, nome
            assert all(nome not in str(r["goal"]) + str(r["postcondition"]) for r in linhas), nome


def test_resolver_so_troca_o_marcador_da_persona_e_aponta_o_que_sumiu(harness: Harness) -> None:
    """O parâmetro do comando (`{perfil_alvo}`, que não é da persona) fica; o dado que a persona não tem mais sobra, e
    `sem_valor_na_etapa` o aponta só pelo nome."""
    from app.models import Postcondition, StepDTO, StepStatus

    passo = StepDTO.model_construct(
        id="s", run_id="r", objective_id="o", instance_id="android-01", plan_version=1, seq=1, key="k",
        title="ver {perfil_nome}", goal="abrir {perfil_alvo} de {perfil_sobrenome}", depends_on=[], side_effect=False,
        commit_guard=[], precondition=None, band_guard=[], bindings={},
        postcondition=Postcondition(kind="text_visible", value="{perfil_nome}", description="d"),
        status=StepStatus.ready)
    etapa = resolver_persona(passo, {"perfil_nome": "Zelda"})
    assert etapa.title == "ver Zelda" and etapa.postcondition.value == "Zelda"
    assert etapa.goal == "abrir {perfil_alvo} de {perfil_sobrenome}"
    assert sem_valor_na_etapa(etapa) == ["perfil_sobrenome"]
    assert resolver_persona(passo, {}) is passo


@pytest.mark.parametrize("campo", ["display_name"])
async def test_o_dado_que_sumiu_da_persona_nao_vai_cru_ao_aparelho(harness: Harness, campo: str) -> None:
    """Entre a materialização e a vez da etapa, a persona perdeu o nome de exibição: a etapa espera a pessoa com o
    nome do campo (nunca o valor), e o ator não é chamado com o molde."""
    recebidas, _ = _espiar(harness)
    _personas(harness)
    _planejar_com_marcador(harness.ai.inner)
    repo = harness.state.repo                                                # type: ignore[union-attr]
    ler = repo.variaveis_da_persona

    def variaveis(perfil: str | None) -> dict[str, str]:
        v = dict(ler(perfil))
        if sys._getframe(1).f_code.co_name == "run_step":    # noqa: SLF001 - só a leitura da vez da etapa perde o dado
            v.pop("perfil_nome_exibicao", None)
        return v

    repo.variaveis_da_persona = variaveis                                    # type: ignore[method-assign]
    run = harness.run(["android-01"])
    await harness.wait(lambda: harness.state.db.scalar(                      # type: ignore[union-attr]
        "SELECT COUNT(*) FROM steps WHERE run_id=? AND status_detail LIKE 'Dado ausente:%'", (run.id,)) > 0,
        timeout=60, what="a etapa parada por dado ausente")
    detalhe = str(harness.state.db.scalar(                                   # type: ignore[union-attr]
        "SELECT status_detail FROM steps WHERE run_id=? AND status_detail LIKE 'Dado ausente:%'", (run.id,)))
    assert "nome de exibição" in detalhe and "Zelda" not in detalhe
    assert "android-01" not in recebidas                                     # o ator nunca viu o molde


async def test_os_bindings_guardam_o_marcador_na_linha_e_o_valor_nao_sai(harness: Any) -> None:
    """F3 (06/10): os `bindings` guardam o MARCADOR na linha, como o texto da etapa (a F2 os deixava com o valor; a
    porta e a chave da aprovação os resolvem agora pelo leitor único, `Repository.bindings_da_etapa`). O que SAI leva o
    marcador: o detalhe da execução (`GET /runs/{id}`), o relatório, as versões do plano na resposta, o evento
    `step.updated` e a evidência."""
    run_id = await _executar(harness, bindings={"texto": MARCA_POST})
    st = harness.state
    assert st is not None
    linhas = st.db.query("SELECT instance_id, bindings FROM steps WHERE run_id=? AND bindings LIKE ?",
                         (run_id, '%"texto"%'))
    assert {r["instance_id"]: json.loads(r["bindings"])["texto"] for r in linhas} ==         {iid: MARCA_POST for iid in PESSOAS}                                 # na linha: o marcador (F3)
    for r in linhas:                                                          # a porta lê o valor de cada persona
        assert st.repo.bindings_da_etapa(st.db.one("SELECT * FROM steps WHERE instance_id=? AND bindings LIKE ?",
                                                   (r["instance_id"], '%"texto"%')))["texto"] == PESSOAS[r["instance_id"]][1]
    detalhe = st.repo.run_detail(run_id)
    assert detalhe is not None
    saidas = {
        "detalhe": detalhe.model_dump_json(),
        "relatorio": json.dumps(st.runs.report(run_id), default=str, ensure_ascii=False),
        "step.updated": " ".join(str(r["data"]) for r in st.db.query(
            "SELECT data FROM events WHERE run_id=? AND kind='step.updated'", (run_id,))),
        "evidencia": " ".join(str(r["note"]) for r in st.db.query("SELECT note FROM evidence WHERE run_id=?", (run_id,))),
    }
    assert MARCA_POST in saidas["detalhe"]
    for fonte, texto in saidas.items():
        for _u, exibicao in PESSOAS.values():
            for nome in (exibicao, *exibicao.split()):
                assert nome not in texto, (fonte, nome)
    marcados = [s.bindings.get("texto") for s in detalhe.steps if "texto" in s.bindings]
    assert marcados and set(marcados) == {MARCA_POST}
    assert {s.bindings.get("texto") for v in detalhe.plan_versions for s in v.steps if "texto" in s.bindings} == {MARCA_POST}
