"""Fase H, segunda parte: o `PlanReport` servido em `mode=plan` e a foto `objectives.resource_plan` no `materialize`.

O que cada teste protege, em uma frase:

* `POST /api/runs` com `mode=plan` devolve o resumo de sempre MAIS o `plan_report` dos recursos que a skill declara —
  o que está certo, o que diverge, as ações, os riscos e o que é de pessoa — e não cria comando nenhum;
* montar o relatório não escreve nada no banco;
* o `materialize` grava em cada objetivo o spec declarado, resolvido para aquele aparelho (e o `mode=execute` também,
  sem `plan_report` na resposta);
* comando que nenhuma skill resolve (planejador) tem relatório sem recursos, dizendo de onde veio; e sem recurso
  declarado, `resource_plan` fica nulo.

Nível de prova: `simulated` — harness (porta base 5640), `FakeInstagram`, `AtorDoInstagram` no `CountingProvider`,
a skill `ig.abrir_conversa` da fixture do design publicada pelo repositório com validação manual do dono (P4).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio
import yaml

from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.state import AppState

from .conftest import CountingProvider, Harness
from .fake_instagram import AtorDoInstagram, FakeInstagram


@pytest.fixture(autouse=True)
def _pular_o_tempo(request: pytest.FixtureRequest) -> None:
    """T2: o tempo das ferramentas do aparelho falso é PULADO (relógio virtual), não esperado. Só nos testes com o
    harness; o que eles provam (ordem dos fatos, contagens, desfechos) é o mesmo."""
    if "harness" in request.fixturenames:
        request.getfixturevalue("harness").pular_o_tempo()

FIXTURES = Path(__file__).parent / "fixtures" / "dsl" / "v1alpha1" / "validos"
ABRIR = "ig.abrir_conversa"
DONO = "painel:flavio"
IID = "android-01"
ABRA = "abra a conversa com @ana no instagram"
#: O que montar o relatório poderia escrever: comandos e entregas (aplicar), o estado lido (aparelho, app, sessão), a
#: execução, e o que a RESOLVE repetida tocaria se tivesse efeito (uso de fluxo, registro de skills, custo de IA).
TABELAS = ("commands", "command_outbox", "instances", "device_app_state", "account_sessions",
           "device_profile_bindings", "objectives", "steps", "runs", "flows", "skill_definitions", "skill_versions",
           "ai_calls")


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1, factory=lambda rt: FakeInstagram(account="eu.teste", screen="feed"))
    h.ai = CountingProvider(AtorDoInstagram())
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


def publicar_abrir(s: AppState) -> SkillRef:
    doc = yaml.safe_load((FIXTURES / f"{ABRIR}.yaml").read_text(encoding="utf-8"))
    v = s.skill_repo.create_draft(ABRIR, doc, source=Provenance(SourceKind.MANUAL), by=DONO)
    s.skill_repo.transition(v.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    s.skill_repo.transition(v.ref, SkillState.VALIDATED, by=DONO, manual=True,
                            reason="validação manual do dono (teste da fase H)")
    s.skill_repo.transition(v.ref, SkillState.PUBLISHED, by=DONO, reason="fase H")
    return v.ref


def cliente(h: Harness) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _contar(s: AppState, tabela: str) -> int:
    return int(s.db.scalar(f"SELECT COUNT(*) FROM {tabela}") or 0)


def _foto(s: AppState, run_id: str) -> dict[str, list[str]]:
    """As tabelas inteiras e os eventos DESTA execução (os do monitor do parque seguem chegando, e não são dela)."""
    foto = {t: sorted(repr(tuple(r.values())) for r in s.db.query(f"SELECT * FROM {t}")) for t in TABELAS}
    foto["events"] = sorted(repr(tuple(r.values())) for r in s.db.query("SELECT * FROM events WHERE run_id=?",
                                                                        (run_id,)))
    return foto


async def test_mode_plan_serve_o_plan_report_sem_criar_comando(parque: Harness) -> None:
    s = estado(parque)
    ref = publicar_abrir(s)
    comandos, saidas = _contar(s, "commands"), _contar(s, "command_outbox")
    async with cliente(parque) as c:
        r = await c.post("/api/runs", json={"command": ABRA, "instance_ids": [IID], "mode": "plan",
                                            "idempotency_key": "plano-h-0001"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    # o resumo de sempre, campo a campo, e o relatório ao lado
    assert corpo["status"] == "planning" and corpo["instance_ids"] == [IID]
    rel = corpo["plan_report"]
    assert rel["source"] == "skill"
    assert rel["skill"]["id"] == ABRIR and rel["skill"]["version"] == ref.version and rel["skill"]["content_hash"]
    assert [ln["kind"] for ln in rel["resources"]] == ["device.state", "app.installation", "app.session"]
    assert {ln["instance_id"] for ln in rel["resources"]} == {IID}
    assert rel["targets"] == [{"instance_id": IID, "profile_id": None}]
    por_tipo = {ln["kind"]: ln for ln in rel["resources"]}
    # sem perfil vinculado, a sessão é de pessoa: vira intervenção humana, e nada fica "pronto" por omissão
    assert por_tipo["app.session"]["status"] == "blocked" and por_tipo["app.session"]["code"] == "no_profile"
    assert [a["purpose"] for a in por_tipo["app.session"]["actions"]] == ["ask"]
    assert rel["summary"]["human_interventions"] >= 1 and rel["summary"]["ready_to_run"] is False
    assert rel["summary"]["actions"] == sum(len(ln["actions"]) for ln in rel["resources"])
    assert isinstance(rel["risks"], list) and len(rel["content_hash"]) == 64

    run_id = corpo["id"]
    await parque.wait(lambda: s.repo.run_row(run_id)["status"] == "planned", what="o plano ficar pronto")
    # Nada foi aplicado: nenhum comando e nenhuma entrega devida, nem na resposta nem no planejamento.
    assert (_contar(s, "commands"), _contar(s, "command_outbox")) == (comandos, saidas)
    assert parque.ai.count("plan") == 0                    # a skill casou: nenhum planejador

    # O relatório é leitura: montá-lo de novo não muda nada, e dá o mesmo conteúdo.
    antes = _foto(s, run_id)
    de_novo = s.runs.relatorio_de_recursos(run_id)
    assert _foto(s, run_id) == antes
    assert de_novo["resources"] == rel["resources"] and de_novo["source"] == "skill"

    # A foto do `materialize`: o spec declarado, resolvido para este aparelho.
    objetivo = s.db.one("SELECT resource_plan FROM objectives WHERE run_id=? AND instance_id=?", (run_id, IID))
    foto = json.loads(objetivo["resource_plan"])
    assert foto["instance_id"] == IID and foto["profile_id"] is None
    assert foto["skill"] == {"id": ABRIR, "version": ref.version, "content_hash": rel["skill"]["content_hash"]}
    assert foto["resources"] == [
        {"kind": "device.state", "target": None, "desired": "online", "on_missing": "wait"},
        {"kind": "app.installation", "target": "instagram", "desired": {"release": "promoted"}, "on_missing": "wait"},
        {"kind": "app.session", "target": "instagram",
         "desired": {"account": "bound_profile", "session": "ready"}, "on_missing": "ask"},
    ]
    assert foto["resolved_at"]


async def test_mode_plan_pelo_planejador_diz_que_nada_foi_declarado(parque: Harness) -> None:
    s = estado(parque)
    async with cliente(parque) as c:
        r = await c.post("/api/runs", json={"command": "curta a última foto do feed", "instance_ids": [IID],
                                            "mode": "plan", "idempotency_key": "plano-h-0002"})
    assert r.status_code == 200, r.text
    rel = r.json()["plan_report"]
    assert rel["source"] == "planner" and rel["skill"] is None and rel["resources"] == []
    run_id = r.json()["id"]
    await parque.wait(lambda: s.repo.run_row(run_id)["status"] not in ("planning",), what="o planejamento terminar")
    # sem recurso declarado, nenhuma foto
    assert all(o["resource_plan"] is None
               for o in s.db.query("SELECT resource_plan FROM objectives WHERE run_id=?", (run_id,)))


async def test_mode_execute_nao_traz_relatorio_mas_grava_a_foto(parque: Harness) -> None:
    s = estado(parque)
    publicar_abrir(s)
    async with cliente(parque) as c:
        r = await c.post("/api/runs", json={"command": ABRA, "instance_ids": [IID], "mode": "execute",
                                            "idempotency_key": "plano-h-0003"})
    assert r.status_code == 200, r.text
    assert "plan_report" not in r.json()
    run_id = r.json()["id"]
    await parque.wait(lambda: s.db.scalar("SELECT resource_plan FROM objectives WHERE run_id=?", (run_id,))
                      is not None, what="o materialize gravar a foto")
    foto: dict[str, Any] = json.loads(s.db.scalar("SELECT resource_plan FROM objectives WHERE run_id=?", (run_id,)))
    assert [x["kind"] for x in foto["resources"]] == ["device.state", "app.installation", "app.session"]
    await parque.wait_run(run_id, timeout=60)             # sem trabalho em segundo plano depois do teste


async def test_relatorio_que_falha_nao_derruba_a_execucao_criada(parque: Harness, monkeypatch: Any) -> None:
    """A execução já nasceu quando o relatório é montado: a falha dele vira `source: error`, nunca um 500."""
    s = estado(parque)

    def quebra(run_id: str) -> dict[str, object]:
        raise RuntimeError("leitura indisponível")

    monkeypatch.setattr(s.runs, "relatorio_de_recursos", quebra)
    async with cliente(parque) as c:
        r = await c.post("/api/runs", json={"command": ABRA, "instance_ids": [IID], "mode": "plan",
                                            "idempotency_key": "plano-h-0004"})
    assert r.status_code == 200, r.text
    assert r.json()["plan_report"]["source"] == "error" and "leitura indisponível" in r.json()["plan_report"]["detail"]
    assert s.repo.run_row(r.json()["id"]) is not None
    await parque.wait(lambda: s.repo.run_row(r.json()["id"])["status"] != "planning", what="o planejamento terminar")
