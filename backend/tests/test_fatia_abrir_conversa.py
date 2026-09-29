"""Fase G (G3): a fatia vertical "abrir uma conversa no Instagram" pelo caminho novo, de ponta a ponta.

comando → `SkillRegistry` → compilador → IR → `Plan` → `materialize` → executor de sempre (estratégia registrada por
tentativa) → VERIFY pelo `CapabilityProvider` (prova local) → evidência; e a COMPOSIÇÃO com `ig.ler_conversa`.

As skills são as fixtures do design (§12.4 e §12.5), publicadas pelo repositório como o domínio exige (P4): a
`ig.abrir_conversa` com os DOIS casos do próprio documento observados no FakeInstagram pela mesma prova que a execução
usa (`simulated` basta para caso não-`device`), validada pelo sistema; a `ig.ler_conversa`, sem casos, validada à
mão pelo dono, com o motivo registrado na transição — a via que P4 prevê para promover sem prova real.

Nível de prova: `simulated` — Harness (porta base 5640) + `FakeInstagram` + `AtorDoInstagram` envolto no
`CountingProvider`. A prova `real`, numa conta do Instagram, fica `not_run` até autorização do dono.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio
import yaml

from app.automation.hierarchy import parse_hierarchy
from app.models import Plan
from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.domain.verification import Observation, StepView, VerifyOutcome
from app.modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from app.modules.skills.domain.lifecycle import SYSTEM_ACTOR, SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.validation import CaseKind, Outcome, Proof
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from app.state import AppState

from .conftest import CountingProvider, Harness
from .fake_instagram import CONVERSAS, PKG, AtorDoInstagram, FakeInstagram
from .fake_skills import PLANO_CURTIR

FIXTURES = Path(__file__).parent / "fixtures" / "dsl" / "v1alpha1" / "validos"
ABRIR, LER = "ig.abrir_conversa", "ig.ler_conversa"
DONO = "painel:flavio"
IID = "android-01"
ABRA = "abra a conversa com @ana no instagram"
LEIA = "leia a conversa com @ana no instagram"


def carregar(nome: str) -> dict[str, Any]:
    dado = yaml.safe_load((FIXTURES / f"{nome}.yaml").read_text(encoding="utf-8"))
    assert isinstance(dado, dict)
    return dado


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    """Um aparelho do Instagram, logado e no feed; receitas em `replay`, habilidades ligadas. `ai.flows` também
    ligado: é o que prova que execução de skill não vira fluxo (sem a guarda, o `_learn_flow` aprenderia um)."""
    h = Harness(tmp_path, 1, factory=lambda rt: FakeInstagram(account="eu.teste", screen="feed"))
    h.ai = CountingProvider(AtorDoInstagram())
    h.cfg.file.ai.recipes = "replay"
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


def aparelho(h: Harness) -> FakeInstagram:
    fake = h.fakes[IID]
    assert isinstance(fake, FakeInstagram)
    return fake


def no_feed(h: Harness) -> None:
    """Cada execução parte do feed. Nada reinicia o app entre execuções (só a recuperação o faz)."""
    fake = aparelho(h)
    fake.screen, fake.thread_with, fake.search_query = "feed", None, ""


def _tela(**campos: Any) -> Any:
    """A árvore de uma tela do FakeInstagram, pelo mesmo parser que o aparelho usa."""
    return parse_hierarchy(FakeInstagram(account="eu.teste", **campos).page_source())


async def _observar_casos(s: AppState, ref: SkillRef) -> None:
    """Os casos do documento (`spec.validation.cases`) entram na tabela e são OBSERVADOS no FakeInstagram pela porta
    que a execução usa (`CatalogCapabilityProvider.verify`): conversa aberta prova; caixa de entrada com a linha do
    usuário não prova (`not_proved` do documento = a prova local não afirma; quem julgaria é o verificador)."""
    provider = CatalogCapabilityProvider(CatalogCapabilityRegistry(s._pacote_do_app_id))  # noqa: SLF001
    for caso in carregar(ABRIR)["spec"]["validation"]["cases"]:
        s.skill_repo.add_case(ABRIR, f"{ABRIR}:{caso['name']}", name=caso["name"], kind=CaseKind(caso["kind"]),
                              parameters=caso["parameters"], preconditions=caso.get("preconditions"),
                              expected=caso["expected"], by=DONO)
        usuario = str(caso["parameters"]["username"])
        conversas = {usuario.lstrip("@"): ["oi"]}
        tela = (_tela(screen="inbox", threads=conversas) if caso["kind"] == "negative"
                else _tela(screen="thread", threads=conversas, thread_with=usuario.lstrip("@")))
        veredito = await provider.verify(StepView(node_id="abrir_conversa", capability=CapabilityRef(PKG, "OPEN_THREAD"),
                                                  bindings=(("username", usuario),)), Observation(tela, PKG))
        esperado_prova = caso["expected"]["outcome"] == "succeeded"
        ok = (veredito.outcome is VerifyOutcome.proved) == esperado_prova
        s.skill_repo.record_result(ref, f"{ABRIR}:{caso['name']}", proof=Proof.SIMULATED,
                                   outcome=Outcome.PASSED if ok else Outcome.FAILED,
                                   detail=f"FakeInstagram, CatalogCapabilityProvider.verify: {veredito.detail}",
                                   by=DONO)


async def publicar_abrir(s: AppState) -> SkillRef:
    repo = s.skill_repo
    v = repo.create_draft(ABRIR, carregar(ABRIR), source=Provenance(SourceKind.MANUAL), by=DONO)
    repo.transition(v.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    await _observar_casos(s, v.ref)
    assert repo.transition(v.ref, SkillState.VALIDATED, by=SYSTEM_ACTOR, reason="").state is SkillState.VALIDATED
    repo.transition(v.ref, SkillState.PUBLISHED, by=DONO, reason="fatia G")
    return v.ref


def publicar_ler(s: AppState) -> SkillRef:
    """Sem casos de validação no documento: P4 manda a validação manual do dono, com motivo registrado."""
    repo = s.skill_repo
    v = repo.create_draft(LER, carregar(LER), source=Provenance(SourceKind.MANUAL), by=DONO)
    repo.transition(v.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    repo.transition(v.ref, SkillState.VALIDATED, by=DONO, manual=True,
                    reason="composta sem caso próprio; a filha tem os casos observados (teste da fatia G)")
    repo.transition(v.ref, SkillState.PUBLISHED, by=DONO, reason="fatia G")
    return v.ref


def etapas(s: AppState, run_id: str) -> dict[str, Any]:
    return {r["key"]: r for r in s.db.query("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run_id,))}


def tentativas(s: AppState, step_id: str) -> list[Any]:
    return s.db.query("SELECT * FROM attempts WHERE step_id=? ORDER BY number", (step_id,))


async def executar(h: Harness, comando: str) -> Any:
    run = h.run([IID], command=comando)
    detalhe = await h.wait_run(run.id, timeout=60)
    assert detalhe.status == "completed", (detalhe.status, detalhe.status_detail)
    return estado(h).repo.run_row(run.id)


# ==================================================================== ig.abrir_conversa
async def test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha(parque: Harness) -> None:
    s, ai = estado(parque), parque.ai
    ref = await publicar_abrir(s)
    assert [r.outcome for r in s.skill_repo.results(ref)] == [Outcome.PASSED, Outcome.PASSED]

    # ---------- 1ª execução: a skill resolve, o compilador planeja, a IA age e a receita é aprendida
    run = await executar(parque, ABRA)
    assert ai.count("plan") == 0
    plano = Plan.model_validate_json(run["plan"])
    assert (plano.planner.provider, plano.planner.model) == ("skill", "skill:ig.abrir_conversa@1")
    assert plano.parameters == {"username": "@ana"}
    # trilha da 045 na execução: skill, versão, hash do conteúdo; nada de fluxo
    assert (run["skill_id"], run["skill_version"], run["flow_id"]) == (ABRIR, 1, None)
    assert run["skill_hash"] == s.skill_repo.get(ref).content_hash
    passos = etapas(s, run["id"])
    assert list(passos) == ["abrir_inbox", "abrir_conversa"]
    for chave, passo in passos.items():
        assert (passo["skill_id"], passo["skill_version"], passo["node_id"], passo["strategy"]) == (
            ABRIR, 1, chave, "recipe>ai_actor")
        [tentativa] = tentativas(s, passo["id"])
        assert (tentativa["strategy"], tentativa["recipe_id"]) == ("ai_actor", None)
        assert passo["driven_by"] == "ai"
        # custo por tentativa: toda decisão e todo julgamento desta etapa apontam a tentativa que os pagou
        chamadas = s.db.query("SELECT role, attempt_id FROM ai_calls WHERE step_id=?", (passo["id"],))
        assert chamadas and {c["attempt_id"] for c in chamadas} == {tentativa["id"]}
    assert s.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE run_id=? AND attempt_id IS NULL", (run["id"],)) == 0
    # OPEN_THREAD comprovado pela árvore local (compositor + usuário), sem chamar o verificador
    assert ai.count("verify", step="abrir_conversa") == 0
    assert "árvore local" in (passos["abrir_conversa"]["status_detail"] or "")
    assert ai.count("verify", step="abrir_inbox") == 1              # OPEN_INBOX não tem prova local: o modelo julga
    evidencias = s.db.query("SELECT note FROM evidence WHERE step_id=?", (passos["abrir_conversa"]["id"],))
    assert any("comprovada" in (e["note"] or "") for e in evidencias)
    assert (aparelho(parque).screen, aparelho(parque).thread_with) == ("thread", "ana")
    receitas = {r["step_key"]: r for r in s.db.query("SELECT * FROM recipes WHERE app_package=? AND status='active'",
                                                     (PKG,))}
    assert set(receitas) == {"abrir_inbox", "abrir_conversa"}
    # execução de skill não vira fluxo, mesmo com `ai.flows` ligado (senão o comando viveria nos dois backends)
    assert s.db.scalar("SELECT COUNT(*) FROM flows") == 0

    # ---------- 2ª execução: as receitas reproduzem, zero decisão de IA
    no_feed(parque)
    decisoes_antes = ai.count("decide")
    run2 = await executar(parque, ABRA)
    assert ai.count("decide") == decisoes_antes and ai.count("plan") == 0
    for chave, passo in etapas(s, run2["id"]).items():
        [tentativa] = tentativas(s, passo["id"])
        assert (tentativa["strategy"], tentativa["recipe_id"]) == ("recipe", receitas[chave]["id"]), chave
        assert passo["driven_by"] == "recipe"
    assert ai.count("verify", step="abrir_conversa") == 0
    assert (aparelho(parque).screen, aparelho(parque).thread_with) == ("thread", "ana")


# ==================================================================== composição: ig.ler_conversa
async def test_composta_le_a_conversa_que_a_filha_abriu(parque: Harness) -> None:
    s, ai = estado(parque), parque.ai
    await publicar_abrir(s)
    publicar_ler(s)
    transicoes = s.skill_repo.history(SkillRef(LER, 1))
    assert any(t.to_state is SkillState.VALIDATED and (t.reason or "").startswith("validação manual")
               and t.decided_by == DONO for t in transicoes)

    run = await executar(parque, LEIA)
    assert ai.count("plan") == 0
    plano = Plan.model_validate_json(run["plan"])
    assert plano.planner.model == "skill:ig.ler_conversa@1" and plano.parameters == {"contato": "@ana"}
    assert (run["skill_id"], run["skill_version"]) == (LER, 1)          # a raiz
    passos = etapas(s, run["id"])
    assert list(passos) == ["abrir_abrir_inbox", "abrir_abrir_conversa", "ler"]
    assert {k: p["skill_id"] for k, p in passos.items()} == {                  # a origem de cada nó
        "abrir_abrir_inbox": ABRIR, "abrir_abrir_conversa": ABRIR, "ler": LER}
    assert {k: p["node_id"] for k, p in passos.items()} == {k: k for k in passos}
    # o nó de leitura depende do nó de abrir (a composição SEMPRE emite a aresta)
    assert json.loads(passos["ler"]["depends_on"]) == ["abrir_abrir_conversa"]
    assert json.loads(passos["abrir_abrir_conversa"]["depends_on"]) == ["abrir_abrir_inbox"]
    # a conversa aberta é a do contato certo, e o alvo da leitura sai da etapa de que ela depende
    assert json.loads(passos["abrir_abrir_conversa"]["bindings"]) == {"username": "@ana"}
    assert ai.count("verify", step="abrir_abrir_conversa") == 0          # prova local também na composta
    obj = s.repo.objective_row(f"{run['id']}:{IID}")
    ler = s.repo.step_dto(passos["ler"])
    assert s._alvo_da_conversa(obj, ler) == "@ana"  # noqa: SLF001
    assert ler.result is not None and ler.result.items == CONVERSAS["ana"]


async def test_filha_desabilitada_poe_a_composta_em_needs_input_sem_plano(parque: Harness) -> None:
    """`disabled` é a parada de emergência e vale para quem compõe: a execução não planeja por fora nem pela metade."""
    s, ai = estado(parque), parque.ai
    filha = await publicar_abrir(s)
    publicar_ler(s)
    s.skill_repo.transition(filha, SkillState.DISABLED, by=DONO, reason="parada de emergência")
    run = parque.run([IID], command=LEIA)
    detalhe = await parque.wait_run(run.id, statuses=("needs_input", "failed", "completed"), timeout=30)
    assert detalhe.status == "needs_input" and "E_SKILL_NOT_FOUND" in (detalhe.status_detail or "")
    assert ai.count("plan") == 0
    assert s.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run.id,)) == 0
    linha = s.repo.run_row(run.id)
    assert (linha["skill_id"], linha["skill_version"], linha["plan"]) == (LER, 1, None)


# ==================================================================== regressão e legado
async def test_com_as_habilidades_desligadas_o_comando_vai_ao_planejador(parque: Harness) -> None:
    """P1: publicar não liga nada sozinho. Desligado, é o caminho de hoje — planejador, sem trilha de skill."""
    s, ai = estado(parque), parque.ai
    parque.cfg.file.skills.enabled = False
    parque.cfg.file.ai.flows = False
    await publicar_abrir(s)
    run = await executar(parque, ABRA)
    assert ai.count("plan") == 1
    assert Plan.model_validate_json(run["plan"]).planner.provider == "ator-do-instagram"
    assert (run["skill_id"], run["skill_version"], run["skill_hash"], run["flow_id"]) == (None, None, None, None)
    passos = etapas(s, run["id"])
    assert all(p["skill_id"] is None and p["node_id"] is None and p["strategy"] is None for p in passos.values())
    # a estratégia EXERCIDA vale para toda tentativa, com ou sem skill
    assert all(tentativas(s, p["id"])[0]["strategy"] == "ai_actor" for p in passos.values())


async def test_fluxo_legado_grava_a_trilha_de_sempre_e_o_hash(parque: Harness) -> None:
    """Sem skill publicada: a 1ª execução planeja e vira fluxo (como sempre); a 2ª reaproveita o fluxo pelo registro
    — `flow_id` e `flows.used` como antes, `skill_id` nulo, e o `skill_hash` calculado do conteúdo do fluxo.

    Receitas desligadas AQUI de propósito: o teste é sobre a trilha do fluxo. A receita de `OPEN_THREAD` gravava o
    texto da linha ("ana", sem arroba) como literal e, reproduzida para "@bia", abria a conversa errada — corrigido
    em `eb9ba02` e coberto por `test_recipes.py::test_seletor_com_username_sem_arroba_vira_parametro_...`."""
    s, ai = estado(parque), parque.ai
    parque.cfg.file.ai.recipes = "off"
    run1 = await executar(parque, ABRA)
    [fluxo] = s.db.query("SELECT * FROM flows")
    assert fluxo["source_run_id"] == run1["id"] and ai.count("plan") == 1
    no_feed(parque)
    run2 = await executar(parque, "abra a conversa com @bia no instagram")
    assert ai.count("plan") == 1
    assert (run2["flow_id"], run2["skill_id"], run2["skill_version"]) == (fluxo["id"], None, None)
    assert run2["skill_hash"] == LegacyFlowAdapter(s.db).get(SkillRef.legacy(fluxo["id"])).content_hash
    assert Plan.model_validate_json(run2["plan"]).planner.model == f"fluxo:{fluxo['id']}"
    assert s.db.scalar("SELECT uses FROM flows WHERE id=?", (fluxo["id"],)) == 1
    assert aparelho(parque).thread_with == "bia"


# ==================================================================== guardas das rotas de fluxos
def cliente(h: Harness) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_rotas_de_fluxo_respeitam_a_skill(parque: Harness) -> None:
    s = estado(parque)
    await publicar_abrir(s)
    async with cliente(parque) as c:
        corpo = (await c.get("/api/flows/match", params={"command": ABRA})).json()
        assert corpo["flow_id"] == corpo["skill_ref"] == "ig.abrir_conversa@1" and corpo["steps_total"] == 2
        # fluxo adotado com a skill publicada não se religa por fora
        s.db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
                     " VALUES ('curtir','Curtir','curtir {perfil}','curtir {perfil}',?,'instagram','active',"
                     "'2026-09-27T00:00:00Z')", (json.dumps(PLANO_CURTIR),))
        s.skill_repo.adopt_flow("curtir", skill_id="ig.curtir", by=DONO)
        r = await c.put("/api/flows/curtir", json={"status": "active"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "flow_adopted"
        assert s.db.scalar("SELECT status FROM flows WHERE id='curtir'") == "disabled"
        assert (await c.put("/api/flows/curtir", json={"status": "disabled"})).status_code == 200
        # nem se apaga: é o caminho de volta da adoção
        r = await c.delete("/api/flows/curtir")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "flow_adopted"
        assert s.db.scalar("SELECT COUNT(*) FROM flows WHERE id='curtir'") == 1


# ==================================================================== correção pela visão da execução (plano 22.7)
async def test_etapa_da_skill_se_corrige_no_ensino_da_mesma_versao_pelas_rotas_do_painel(parque: Harness) -> None:
    """O contrato de que o painel depende, pelas rotas e com um plano COMPILADO da skill (não um `origin` escrito à
    mão): `GET /api/runs/{id}` traz `origin` em `plan_versions[].steps` (é por ela que a etapa ganha "Corrigir esta
    etapa"); o ensino abre com `base_version` (versão inexistente = 404); a lista de abertos traz `base_version` (é
    por ele que o painel acha o ensino a reaproveitar); e a correção entra com a execução e a LINHA de `steps`."""
    s = estado(parque)
    ref = await publicar_abrir(s)
    run = parque.run([IID], command=ABRA, mode="plan")
    await parque.wait_run(run.id, statuses=("planned",))
    assert Plan.model_validate_json(s.repo.run_row(run.id)["plan"]).planner.model == "skill:ig.abrir_conversa@1"
    etapa = etapas(s, run.id)["abrir_conversa"]["id"]
    s.db.execute("UPDATE steps SET status='failed' WHERE id=?", (etapa,))
    instrucao = f"Corrigir a habilidade {ABRIR} (versão {ref.version})."
    async with cliente(parque) as c:
        detalhe = (await c.get(f"/api/runs/{run.id}")).json()
        [versao] = detalhe["plan_versions"]
        assert versao["objective_id"] == f"{run.id}:{IID}"
        assert {p["key"]: p["origin"] for p in versao["steps"]} == {
            chave: {"skill_id": ABRIR, "skill_version": 1, "node_id": chave, "strategies": ["recipe", "ai_actor"]}
            for chave in ("abrir_inbox", "abrir_conversa")}
        assert all(p["origin"]["skill_id"] == ABRIR for p in detalhe["plan"]["steps"])
        linha = next(x for x in detalhe["steps"] if x["id"] == etapa)
        assert (linha["key"], linha["status"], linha["plan_version"]) == ("abrir_conversa", "failed", 1)

        r = await c.post("/api/teaching-sessions", json={"instruction": instrucao, "skill_id": ABRIR, "base_version": 2})
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found", r.text
        r = await c.post("/api/teaching-sessions", json={"instruction": instrucao, "skill_id": ABRIR, "base_version": 1})
        assert r.status_code == 201, r.text
        ensino = r.json()
        assert (ensino["skill_id"], ensino["base_version"], ensino["app_id"]) == (ABRIR, 1, "instagram")
        abertos = (await c.get("/api/teaching-sessions", params={"status": "open", "limit": 200})).json()
        assert [(a["id"], a["skill_id"], a["base_version"]) for a in abertos] == [(ensino["id"], ABRIR, 1)]

        r = await c.post(f"/api/teaching-sessions/{ensino['id']}/corrections",
                         json={"body": "devia abrir a conversa da @ana, não a primeira da lista", "run_id": run.id,
                               "step_id": etapa})
    assert r.status_code == 200, r.text
    [turno] = [t for t in r.json()["turns"] if t["kind"] == "correction"]
    assert turno["target"] == {"run_id": run.id, "step_id": etapa}
    assert r.json()["source"] == "correction"
