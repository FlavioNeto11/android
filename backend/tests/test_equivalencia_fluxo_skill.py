"""Fase J: a bateria de equivalência `["legado", "novo"]` e a trilha do fluxo adotado, de ponta a ponta.

O mesmo comando, no mesmo Instagram falso, pelo fluxo legado (`flow:<id>@1`) e pela habilidade convertida dele (a v2
descompilada, publicada — o caminho novo inteiro: descompilador → compilador → `Plan` → `materialize`), dá:

- o MESMO plano: mesmas chaves, capabilities, pós-condições, argumentos e parâmetros;
- as MESMAS receitas: mesmo `step_key` e mesmo `step_hash` (a identidade de `recipes.step_template_hash`);
- a MESMA conta de IA no provedor (`plan`, `decide`, `verify`), e, na 2ª execução, receita sem decisão de IA nos dois.

As duas pontas são conferidas contra os MESMOS valores literais — o que o plano congelado do fluxo diz, e a conta
medida no legado —, então a igualdade entre elas não depende da ordem em que o pytest as roda.

O fluxo é aprendido como em produção: uma execução planejada (`AtorDoInstagram` pelo planejador por catálogo) que
termina toda comprovada vira fluxo (`_learn_flow`), com receitas DESLIGADAS nessa execução para que a bateria parta
sem receita nenhuma.

Nível de prova: `simulated` — Harness (porta base 5640) + `FakeInstagram` + `AtorDoInstagram` no `CountingProvider`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from app.models import Plan
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.infrastructure.decompiler import PlanDecompiler
from app.modules.skills.infrastructure.flow_conversion import FlowConverter
from app.social.capacidades import capacidades_do_perfil
from app.state import AppState
from app.taskqueue.recipes import para_hash, step_template_hash

from .conftest import CountingProvider, Harness
from .fake_instagram import PKG, AtorDoInstagram, FakeInstagram
from .fake_skills import perfil

DONO = "painel:flavio"
IID = "android-01"
APRENDER = "abra a conversa com @ana no instagram"
COMANDO = "abra a conversa com @bia no instagram"
#: A conta de IA de UMA execução deste comando partindo do feed, medida no caminho legado (e exigida dos dois):
#: - 1ª (sem receita): `decide` 4 — em cada etapa, tocar (Mensagens; a linha da conversa) e declarar feito;
#:   `verify` 1 — só OPEN_INBOX vai ao verificador: OPEN_THREAD se prova pela árvore local;
#: - 2ª (com as receitas): nenhuma decisão; a verificação é a mesma, com ou sem receita.
IA_1 = {"plan": 0, "decide": 4, "verify": 1}
IA_2 = {"plan": 0, "decide": 0, "verify": 1}


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1, factory=lambda rt: FakeInstagram(account="eu.teste", screen="feed"))
    h.ai = CountingProvider(AtorDoInstagram())
    h.cfg.file.ai.recipes = "off"
    h.cfg.file.ai.flows = True
    h.cfg.file.skills.enabled = True
    await h.boot()
    s = estado(h)
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id=?", (IID,))
    s.devices.get(IID).app_id = "instagram"
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def no_feed(h: Harness) -> None:
    fake = h.fakes[IID]
    assert isinstance(fake, FakeInstagram)
    fake.screen, fake.thread_with, fake.search_query = "feed", None, ""


async def executar(h: Harness, comando: str) -> Any:
    no_feed(h)
    run = h.run([IID], command=comando)
    detalhe = await h.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    return estado(h).repo.run_row(run.id)


def conta(ai: CountingProvider, antes: int) -> dict[str, int]:
    novas = ai.calls[antes:]
    return {papel: sum(1 for c in novas if c["role"] == papel) for papel in ("plan", "decide", "verify")}


def conversor(s: AppState) -> FlowConverter:
    return FlowConverter(s.skill_repo, PlanDecompiler(s.skill_planner.compiler))


async def aprender_fluxo(h: Harness) -> tuple[str, Plan]:
    """A execução planejada que vira fluxo, como em produção; receitas desligadas nela (a bateria parte do zero)."""
    s = estado(h)
    await executar(h, APRENDER)
    [fluxo] = s.db.query("SELECT * FROM flows")
    assert fluxo["status"] == "active" and fluxo["command_template"] == "abra a conversa com {username} no instagram"
    assert s.db.scalar("SELECT COUNT(*) FROM recipes") == 0
    h.cfg.file.ai.recipes = "replay"
    return fluxo["id"], Plan.model_validate_json(fluxo["plan"])


def converter_e_publicar_a_v2(s: AppState, flow_id: str) -> SkillRef:
    """Conversão (v1 = o plano do fluxo, v2 = o documento descompilado) e a v2 publicada pela via da P4 sem prova
    real: validação manual do dono, com o motivo registrado. A v1 fica `deprecated`."""
    c = conversor(s).convert(flow_id, by=DONO)
    repo = s.skill_repo
    repo.transition(c.draft.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    repo.transition(c.draft.ref, SkillState.VALIDATED, by=DONO, manual=True,
                    reason="conversão de fluxo comprovado; bateria de equivalência (teste da fase J)")
    repo.transition(c.draft.ref, SkillState.PUBLISHED, by=DONO, reason="fase J")
    assert repo.get(c.published.ref).state is SkillState.DEPRECATED
    return c.draft.ref


def pegada_do_plano(plano: Plan) -> list[tuple[Any, ...]]:
    return [(p.key, p.capability, p.depends_on, p.bindings, p.postcondition.kind, p.postcondition.value,
             p.postcondition.required_delivery_level, sorted(p.commit_guard)) for p in plano.steps]


# ================================================================== a bateria
@pytest.mark.parametrize("caminho", ["legado", "novo"])
async def test_mesmo_plano_mesmas_receitas_e_mesma_conta_de_ia(parque: Harness, caminho: str) -> None:
    s, ai = estado(parque), parque.ai
    flow_id, congelado = await aprender_fluxo(parque)
    if caminho == "novo":
        ref = converter_e_publicar_a_v2(s, flow_id)
        assert s.db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)) == "disabled"

    # ---------- 1ª execução: sem receita — a IA age, a receita é aprendida
    antes = len(ai.calls)
    run = await executar(parque, COMANDO)
    assert conta(ai, antes) == IA_1
    plano = Plan.model_validate_json(run["plan"])
    esperado = Plan.model_validate_json(congelado.model_dump_json())
    esperado.parameters = {"username": "@bia"}
    assert pegada_do_plano(plano) == pegada_do_plano(esperado)
    assert plano.parameters == {"username": "@bia"} and plano.app_id == "instagram"
    # a identidade de receita de cada etapa: a gravada em `steps` e a que o plano congelado do fluxo dá
    etapas = s.db.query("SELECT key, template_hash, capability FROM steps WHERE run_id=? ORDER BY seq", (run["id"],))
    identidade = [(p.key, step_template_hash(para_hash(p, esperado.parameters))) for p in esperado.steps]
    assert [(e["key"], e["template_hash"]) for e in etapas] == identidade
    receitas = {(r["step_key"], r["step_hash"]) for r in s.db.query(
        "SELECT step_key, step_hash FROM recipes WHERE app_package=? AND status='active'", (PKG,))}
    assert receitas == set(identidade)

    # a trilha de cada caminho
    if caminho == "legado":
        assert (run["flow_id"], run["skill_id"], run["skill_version"]) == (flow_id, None, None)
        assert plano.planner.model == f"fluxo:{flow_id}"
    else:
        assert (run["skill_id"], run["skill_version"], run["flow_id"]) == (ref.skill_id, 2, None)
        assert plano.planner.model == f"skill:{ref}"
        assert [e["capability"] for e in etapas] == ["OPEN_INBOX", "OPEN_THREAD"]

    # ---------- 2ª execução: receita, sem decisão de IA, nos dois
    antes = len(ai.calls)
    run2 = await executar(parque, COMANDO)
    assert conta(ai, antes) == IA_2
    assert {r["driven_by"] for r in s.db.query("SELECT driven_by FROM steps WHERE run_id=?", (run2["id"],))} == {"recipe"}
    fake = parque.fakes[IID]
    assert isinstance(fake, FakeInstagram) and (fake.screen, fake.thread_with) == ("thread", "bia")


async def test_receitas_aprendidas_pelo_fluxo_servem_a_habilidade_convertida(parque: Harness) -> None:
    """A garantia da conversão: o que o fluxo já aprendeu não se paga de novo. Receitas aprendidas pelo fluxo; a v2
    descompilada publicada as reproduz na primeira execução dela, sem decisão de IA."""
    s, ai = estado(parque), parque.ai
    flow_id, _ = await aprender_fluxo(parque)
    await executar(parque, COMANDO)                                            # o fluxo aprende as receitas
    ref = converter_e_publicar_a_v2(s, flow_id)
    antes = len(ai.calls)
    run = await executar(parque, "abra a conversa com @ana no instagram")
    assert conta(ai, antes) == IA_2
    assert run["skill_id"] == ref.skill_id and run["skill_version"] == 2
    tentativas = s.db.query("SELECT a.strategy, a.recipe_id FROM attempts a JOIN steps st ON st.id = a.step_id"
                            " WHERE st.run_id=?", (run["id"],))
    assert tentativas and all(t["strategy"] == "recipe" and t["recipe_id"] is not None for t in tentativas)


# ================================================================== trilha do fluxo adotado (item 3)
async def test_a_v1_adotada_grava_a_skill_e_o_fluxo_e_as_capacidades_enxergam(parque: Harness) -> None:
    """Decisão da fase J: a execução da versão que É o plano do fluxo (a v1 adotada, conteúdo `schema_version` 0)
    grava `skill_id` E `flow_id`, e conta em `flows.uses`; a da v2 (DSL) grava só a skill. As capacidades do perfil
    enxergam as duas: `flows` pelo `flow_id`, `skills` pelo `skill_id`."""
    s = estado(parque)
    flow_id, _ = await aprender_fluxo(parque)
    c = conversor(s).convert(flow_id, by=DONO)
    usos = s.db.scalar("SELECT uses FROM flows WHERE id=?", (flow_id,))

    run1 = await executar(parque, COMANDO)                                   # a v1 publicada: o plano do fluxo
    assert (run1["skill_id"], run1["skill_version"], run1["flow_id"]) == (c.published.ref.skill_id, 1, flow_id)
    assert run1["skill_hash"] == c.published.content_hash
    assert json.loads(run1["plan"])["planner"]["model"] == f"skill:{c.published.ref}"
    assert s.db.scalar("SELECT uses FROM flows WHERE id=?", (flow_id,)) == usos + 1
    assert s.db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)) == "disabled"   # continua desligado

    repo = s.skill_repo
    repo.transition(c.draft.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    repo.transition(c.draft.ref, SkillState.VALIDATED, by=DONO, manual=True, reason="teste da trilha (fase J)")
    repo.transition(c.draft.ref, SkillState.PUBLISHED, by=DONO, reason="fase J")
    run2 = await executar(parque, COMANDO)                                   # a v2: DSL, não é o plano do fluxo
    assert (run2["skill_id"], run2["skill_version"], run2["flow_id"]) == (c.draft.ref.skill_id, 2, None)
    assert s.db.scalar("SELECT uses FROM flows WHERE id=?", (flow_id,)) == usos + 1

    perfil(s.db, "p-eu")
    s.db.execute("UPDATE objectives SET profile_id='p-eu' WHERE run_id IN (?,?)", (run1["id"], run2["id"]))
    cap = capacidades_do_perfil(s, "p-eu")
    assert [(f["flow_id"], f["times"]) for f in cap["flows"]] == [(flow_id, 1)]
    [hab] = cap["skills"]
    assert (hab["skill_id"], hab["version"], hab["times"], hab["legacy_flow_id"]) == (
        c.published.ref.skill_id, 2, 2, flow_id)
    assert hab["steps_total"] == 2
