"""Item 12.1: o perfil é uma identidade com contas em vários apps; conhecimento por app; comando que atravessa apps."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.models import (MemoryCreate, Plan, PlannerInfo, PlanStep, Postcondition, ProfileAccountCreate,
                        ProfileAccountPatch, ProfileCreate, RunCreate)
from app.planning.parsing import plan_from_json
from app.planning.provider import AppContext
from app.social.service import SocialError

from .conftest import Harness
from .test_capabilities import build, perfil

SENHA_OUTLOOK = "outlook-Segredo-42!"


def _outlook(db) -> None:
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
               "('outlook','Outlook','com.microsoft.office.outlook','.MainActivity',0)")


def test_perfil_nasce_com_a_conta_do_instagram_e_ganha_outras(tmp_path: Path) -> None:
    svc, repo, _, db = build(tmp_path)
    if not db.scalar("SELECT id FROM apps WHERE package='com.instagram.android'"):
        db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
                   "('instagram','Instagram','com.instagram.android','.MainActivity',0)")
    _outlook(db)
    pid = perfil(svc)
    contas = svc.list_accounts(pid)
    assert [c.app_id for c in contas] == ["instagram"]
    assert contas[0].automated_login and contas[0].handle == "mariana.costa91182" and contas[0].credential_configured

    # Senha de conta só com o consentimento da pessoa (ADR-040): sem ele, 409 e nenhuma conta criada.
    with pytest.raises(SocialError) as sem:
        svc.add_account(pid, ProfileAccountCreate(app_id="outlook", handle="mariana@exemplo.com",
                                                  password=SecretStr(SENHA_OUTLOOK)))
    assert sem.value.code == "consentimento_de_credencial" and [c.app_id for c in svc.list_accounts(pid)] == ["instagram"]
    conta = svc.add_account(pid, ProfileAccountCreate(app_id="outlook", handle="mariana@exemplo.com",
                                                      password=SecretStr(SENHA_OUTLOOK), consent=True))
    assert conta.app_name == "Outlook" and not conta.automated_login and conta.credential_configured
    assert conta.session_status == "unknown"
    # a senha vai para o cofre; o banco só guarda a referência
    linha = db.one("SELECT * FROM account_credentials WHERE account_id=?", (conta.id,))
    assert linha is not None and SENHA_OUTLOOK not in json.dumps(dict(linha))
    assert SENHA_OUTLOOK not in json.dumps([c.model_dump() for c in svc.list_accounts(pid)])

    # a pessoa entrou pelo Foco e marca a sessão; no Instagram isso é do provedor
    assert svc.update_account(pid, conta.id, ProfileAccountPatch(session_status="session_ready")).session_status == "session_ready"
    with pytest.raises(SocialError):
        svc.update_account(pid, contas[0].id, ProfileAccountPatch(session_status="session_ready"))
    # uma conta por app; app inexistente; a do Instagram é a âncora
    for ruim in (ProfileAccountCreate(app_id="outlook"), ProfileAccountCreate(app_id="nao-existe")):
        with pytest.raises(SocialError):
            svc.add_account(pid, ruim)
    with pytest.raises(SocialError):
        svc.delete_account(pid, contas[0].id)
    svc.delete_account(pid, conta.id)
    assert [c.app_id for c in svc.list_accounts(pid)] == ["instagram"]
    assert db.one("SELECT * FROM account_credentials WHERE account_id=?", (conta.id,)) is None


def test_conhecimento_por_app_e_frota_coordenada_por_app(tmp_path: Path) -> None:
    svc, repo, _, db = build(tmp_path)
    _outlook(db)
    a, b = perfil(svc), perfil(svc, "outro.perfil", "android-01")
    svc.add_memory(a, MemoryCreate(subject="@ana", content="Prefere e-mail de manhã", app_id="outlook"))
    svc.add_memory(a, MemoryCreate(subject="@ana", content="Mora em Araraquara"))           # fato geral
    assert [m.content for m in svc.list_memories(a, app_id="outlook")] == ["Prefere e-mail de manhã"]
    assert len(svc.list_memories(a)) == 2
    with pytest.raises(SocialError):
        svc.add_memory(a, MemoryCreate(subject="@ana", content="x", app_id="nao-existe"))
    ctx = svc.context(a, recall_hint="ana e-mail manhã")
    assert "no app outlook" in ctx.rendered

    # o mesmo @alvo em apps diferentes não é o mesmo alvo para a coordenação da frota
    svc.record_interaction(b, type="followed", direction="outbound", status="confirmed", counterparty="@nasa",
                           app_id="outlook")
    agora = "2000-01-01T00:00:00Z"
    n_outlook, _ = repo.fleet_targeting("@nasa", agora, types=("followed",), statuses=("confirmed",),
                                        exclude_profile_id=a, app_id="outlook")
    n_insta, _ = repo.fleet_targeting("@nasa", agora, types=("followed",), statuses=("confirmed",),
                                      exclude_profile_id=a, app_id="instagram")
    assert (n_outlook, n_insta) == (1, 0)
    assert [i.app_id for i in svc.list_interactions(b, app_id="outlook")] == ["outlook"]


def test_planejador_pode_declarar_o_app_de_cada_etapa() -> None:
    apps = [AppContext("qa-messenger", "QA", "com.pocqa.messenger", None, None, None),
            AppContext("instagram", "Instagram", "com.instagram.android", None, None, None)]
    req = SimpleNamespace(apps=apps)
    etapa = {"title": "t", "goal": "g", "depends_on": [], "side_effect": False, "commit_guard": [],
             "precondition": None, "timeout_s": 60, "max_attempts": 2, "for_each": None,
             "postcondition": {"kind": "text_visible", "value": "x", "description": "d", "required_delivery_level": None}}
    bruto = json.dumps({"summary": "s", "app_id": "qa-messenger", "parameters": [], "success_criteria": [], "missing": [],
                        "steps": [{**etapa, "key": "ler_codigo", "app_id": "qa-messenger"},
                                  {**etapa, "key": "usar_codigo", "app_id": "instagram"},
                                  {**etapa, "key": "fim", "app_id": None},
                                  {**etapa, "key": "outro", "app_id": "tiktok"}]})
    plano = plan_from_json(bruto, req, provider="p", model="m", max_steps=10)       # type: ignore[arg-type]
    # igual ao do plano vira None (planos de um app só ficam como antes); app desconhecido vira pergunta
    assert [s.app_id for s in plano.steps] == [None, "instagram", None, None]
    assert any("tiktok" in m.question for m in plano.missing)


async def test_etapas_em_apps_diferentes_resolvem_o_proprio_app(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    st = await h.boot()
    try:
        run = st.runs.create(RunCreate(command="ler no QA e usar no Instagram", instance_ids=["android-01"],
                                       idempotency_key="multiapp-1", mode="plan"))
        await h.wait_run(run.id, statuses=("planned", "completed", "completed_with_issues", "failed"))
        post = Postcondition(kind="text_visible", value="x", description="d")
        plano = Plan(summary="dois apps", app_id="qa-messenger", app_package="com.pocqa.messenger",
                     steps=[PlanStep(key="ler", title="Ler o código", goal="g", postcondition=post),
                            PlanStep(key="usar", title="Usar no Instagram", goal="g", postcondition=post,
                                     app_id="instagram")],
                     planner=PlannerInfo(provider="t", model="t", simulated=True))
        st.db.execute("DELETE FROM steps WHERE run_id=?", (run.id,))
        st.db.execute("DELETE FROM objectives WHERE run_id=?", (run.id,))
        st.repo.save_plan(run.id, plano)
        st.repo.materialize(run.id, plano, [{"instance_id": "android-01"}])
        assert json.loads(st.repo.run_row(run.id)["app_ids"]) == ["qa-messenger", "instagram"]
        assert st.repo.run_summary(st.repo.run_row(run.id)).app_ids == ["qa-messenger", "instagram"]
        linhas = {r["key"]: r["app_id"] for r in st.db.query("SELECT key, app_id FROM steps WHERE run_id=?", (run.id,))}
        assert linhas == {"ler": None, "usar": "instagram"}

        rt = st.devices.get("android-01")
        obj = st.repo.objective_row(f"{run.id}:android-01")
        sched = st.scheduler
        # as portas valem para CADA app das etapas que faltam, na ordem
        assert sched._pacotes_do_objetivo(obj, rt) == ["com.pocqa.messenger", "com.instagram.android"]  # noqa: SLF001
        assert sched._app_context(st.repo.run_row(run.id), rt, "instagram")[0].package == "com.instagram.android"  # noqa: SLF001
        assert sched._app_context(st.repo.run_row(run.id), rt)[0].package == "com.pocqa.messenger"  # noqa: SLF001
        passo = st.repo.step_dto(st.repo.step_row(f"{run.id}:android-01:v1:usar"))
        assert passo.app_id == "instagram"
        # com a etapa do QA concluída, só o Instagram segura o item
        st.db.execute("UPDATE steps SET status='succeeded' WHERE id=?", (f"{run.id}:android-01:v1:ler",))
        assert sched._pacotes_do_objetivo(obj, rt) == ["com.instagram.android"]  # noqa: SLF001

        # a visão por app enxerga a execução nos DOIS apps, e a página de cada um a lista
        from app.apps_overview import app_detail, apps_overview
        resumo = {a["app_id"]: a for a in apps_overview(st, 7)}
        assert resumo["qa-messenger"]["runs"] == 1 and resumo["instagram"]["runs"] == 1
        detalhe = app_detail(st, "instagram", 30)
        assert detalhe is not None and [r["id"] for r in detalhe["runs"]] == [run.id]
        assert app_detail(st, "nao-existe") is None
    finally:
        await st.stop()


async def test_rotas_http_de_contas_e_da_visao_por_app(tmp_path: Path) -> None:
    from .test_perfil_bloqueado_e_capacidades import _cliente
    h = Harness(tmp_path, 1)
    st = await h.boot()
    try:
        pid = st.social.create_profile(ProfileCreate(username="rota.multiapp", instance_id="android-01")).id
        async with _cliente(h) as c:
            contas = (await c.get(f"/api/instagram/profiles/{pid}/accounts")).json()
            assert [x["app_id"] for x in contas] == ["instagram"]
            r = await c.post(f"/api/instagram/profiles/{pid}/accounts", json={"app_id": "qa-messenger", "handle": "qa-user-01"})
            assert r.status_code == 201, r.text
            conta = r.json()
            r = await c.patch(f"/api/instagram/profiles/{pid}/accounts/{conta['id']}", json={"session_status": "session_ready"})
            assert r.status_code == 200 and r.json()["session_status"] == "session_ready"
            assert (await c.post(f"/api/instagram/profiles/{pid}/accounts", json={"app_id": "qa-messenger"})).status_code == 409
            r = await c.put(f"/api/instagram/profiles/{pid}/accounts/{conta['id']}/credential",
                            json={"password": SENHA_OUTLOOK, "login_identifier": "qa-user-01"})
            assert r.status_code == 409 and SENHA_OUTLOOK not in r.text          # sem consentimento, não guarda
            r = await c.put(f"/api/instagram/profiles/{pid}/accounts/{conta['id']}/credential",
                            json={"password": SENHA_OUTLOOK, "login_identifier": "qa-user-01", "consent": True})
            assert r.status_code == 200, r.text
            assert r.json()["credential_configured"] and SENHA_OUTLOOK not in r.text
            assert (await c.get(f"/api/instagram/profiles/{pid}/memory?app_id=qa-messenger")).json() == []
            assert (await c.get(f"/api/instagram/profiles/{pid}/interactions?app_id=qa-messenger")).json() == []
            resumo = {a["app_id"]: a for a in (await c.get("/api/apps-overview?days=7")).json()}
            assert resumo["qa-messenger"]["accounts"] == 1 and resumo["qa-messenger"]["accounts_ready"] == 1
            assert (await c.get("/api/apps/qa-messenger/overview")).json()["accounts"][0]["username"] == "rota.multiapp"
            assert (await c.get("/api/apps/nao-existe/overview")).status_code == 404
            assert (await c.delete(f"/api/instagram/profiles/{pid}/accounts/{conta['id']}")).status_code == 204
    finally:
        await st.stop()
