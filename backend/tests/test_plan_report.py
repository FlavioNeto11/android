"""`PlanReport` dos recursos (design §14.2, fase H, primeira parte): puro, determinístico, sem aplicar nada.

O que cada teste protege, em uma frase:

* de ponta a ponta (simulado): a skill `ig.abrir_conversa` compilada da fixture, lida por aparelho pelos quatro
  providers sobre um banco de teste, vira um relatório com o que está certo, o que diverge, as ações, os riscos e o
  que exige pessoa — e ler e montar não mudam o banco;
* a segunda passada, depois de convergir, não tem ação nenhuma;
* a mesma entrada em outra ordem dá o mesmo relatório e o mesmo hash;
* o que ninguém leu é `not_read`, sem ação e listado como não comprovado — nunca "certo";
* recurso pedido duas vezes com estados diferentes, ou lido duas vezes, é recusado.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.db import Database
from app.modules.applications.infrastructure.app_installation import AppInstallationProvider
from app.modules.applications.infrastructure.app_repository import AppRepository
from app.modules.execution.domain.plan_report import KIND_ORDER, PlanReport, build_plan_report, specs_of
from app.modules.fleet.infrastructure.device_state import DeviceStateProvider
from app.modules.identity.infrastructure.account_session import AccountBindingProvider, AppSessionProvider
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.infrastructure.lowering import SkillPlanCompiler
from app.planning.catalog import session_provider_of
from app.planning.catalog.instagram import PACKAGE as IG
from app.shared.resources import (NOT_READ, ActionPurpose, DriftStatus, ObservedState, ResourceKind, ResourceSpec,
                                  Target)
from app.util import to_iso

from .conftest import make_config

AGORA = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "dsl" / "v1alpha1" / "validos" / "ig.abrir_conversa.yaml"
TABELAS = ("instances", "apps", "app_releases", "device_app_state", "instagram_profiles", "device_profile_bindings",
           "profile_accounts", "account_sessions", "account_credentials")


@dataclass
class RuntimeFalso:
    state: str = "online"
    state_detail: str | None = None
    readiness_phase: str = "ready"
    store: bool = False
    external: bool = False
    worker_verbs: list[str] | None = None
    snapshot_valid: bool = False


@pytest.fixture
def banco(tmp_path: Path) -> Database:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    return db


def _parque(db: Database) -> None:
    """android-01 pronto para a skill; android-02 com o aparelho parado, o app atrasado e a conta deslogada."""
    AppRepository(db).criar(app_id="instagram", name="Instagram", package=IG)
    for rid, code in (("r-440", 440), ("r-447", 447)):
        db.execute("INSERT INTO app_releases(id, package_name, version_name, version_code, artifact_type,"
                   " signature_sha256, catalog_dir, source_type, imported_at, status, channel, channel_at)"
                   " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (rid, IG, f"{code}.0", code, "apk", "ab" * 32, f"apks/{rid}", "upload", to_iso(AGORA),
                    "installable", "promoted", to_iso(AGORA)))
    for idx, (iid, pid, nome, instalada, sessao) in enumerate(
            (("android-01", "p-lucas", "lucas", "r-447", "session_ready"),
             ("android-02", "p-bruno", "bruno", "r-440", "auth_required")), 1):
        db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port,"
                   " chromedriver_port, app_id) VALUES (?,?,?,?,?,?,?,?)",
                   (iid, idx, iid, 5640 + 2 * idx, 8300 + idx, 9300 + idx, 9600 + idx, "instagram"))
        db.execute("INSERT INTO device_app_state(instance_id, package_name, state, installed_release_id,"
                   " desired_release_id, observed_version_code) VALUES (?,?,?,?,?,?)",
                   (iid, IG, "ready", instalada, instalada, 447 if instalada == "r-447" else 440))
        db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                   (pid, nome, "active", to_iso(AGORA), to_iso(AGORA)))
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at) VALUES (?,?,1,?)",
                   (pid, iid, to_iso(AGORA)))
        # A conta do Instagram do perfil (049): a sessão é dela NAQUELE aparelho, e a credencial é dela, consentida.
        db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, created_at, updated_at)"
                   " VALUES (?,?,?,?,?,?,?)", (f"acc-{pid}", pid, "instagram", nome, "active", to_iso(AGORA),
                                              to_iso(AGORA)))
        db.execute("INSERT INTO account_sessions(account_id, instance_id, status, observed_handle, verified_at,"
                   " updated_at) VALUES (?,?,?,?,?,?)", (f"acc-{pid}", iid, sessao, nome, to_iso(AGORA), to_iso(AGORA)))
        # Nome de referência no cofre, não um segredo; o relatório nunca o mostra (conferido abaixo).
        db.execute("INSERT INTO account_credentials(account_id, login_identifier, secret_ref, key_id, status,"
                   " created_at, updated_at, consent_at, consent_by) VALUES (?,?,?,?,?,?,?,?,?)",
                   (f"acc-{pid}", f"login-de-{nome}", f"ref-de-{nome}", "chave-de-teste", "active", to_iso(AGORA),
                    to_iso(AGORA), to_iso(AGORA), "teste"))


class Leitores:
    """A orquestração que a camada de aplicação vai fazer: um provider por tipo, uma leitura por (recurso, alvo)."""

    def __init__(self, db: Database, runtimes: dict[str, RuntimeFalso]) -> None:
        sessoes = AppSessionProvider(db, tem_provedor_de_sessao=lambda pkg: session_provider_of(pkg) == "instagram",
                                     session_max_age_s=3600, unknown_retry_cap=3, agora=lambda: AGORA)
        self.por_tipo: dict[ResourceKind, Any] = {
            ResourceKind.device_state: DeviceStateProvider(db, runtimes),
            ResourceKind.app_installation: AppInstallationProvider(db),
            ResourceKind.account_binding: AccountBindingProvider(db),
            ResourceKind.app_session: sessoes,
        }

    def ler(self, specs: tuple[ResourceSpec, ...], alvos: list[Target]) -> list[ObservedState]:
        return [self.por_tipo[s.ref.kind].read_current_state(s.ref, t) for t in alvos for s in specs]


def _skill() -> tuple[tuple[ResourceSpec, ...], SkillRef, str]:
    r = SkillPlanCompiler({"instagram": IG}.get).compilar(yaml.safe_load(FIXTURE.read_text(encoding="utf-8")),
                                                          version=1, parameters={"username": "@ana.teste"})
    assert r.graph is not None
    return specs_of(r.graph), SkillRef(r.graph.skill_id, r.graph.skill_version), r.graph.content_hash()


def _foto(db: Database) -> dict[str, list[tuple[object, ...]]]:
    return {t: sorted((tuple(r.values()) for r in db.query(f"SELECT * FROM {t}")), key=repr) for t in TABELAS}


def _linhas(rel: PlanReport) -> list[tuple[str, str, str, str, list[tuple[str, str | None]]]]:
    return [(ln.drift.target.instance_id, ln.drift.ref.kind.value, ln.drift.status.value, str(ln.drift.code),
             [(a.purpose.value, a.verb) for a in ln.actions]) for ln in rel.lines]


def test_relatorio_da_skill_abrir_conversa_sobre_o_parque(banco: Database) -> None:
    _parque(banco)
    specs, ref, hash_da_skill = _skill()
    assert [s.ref.kind for s in specs] == [ResourceKind.device_state, ResourceKind.app_installation,
                                           ResourceKind.app_session]
    runtimes = {"android-01": RuntimeFalso(), "android-02": RuntimeFalso(state="stopped", readiness_phase="not_running")}
    alvos = [Target("android-02"), Target("android-01")]
    antes = _foto(banco)
    rel = build_plan_report(specs, alvos, Leitores(banco, runtimes).ler(specs, alvos), skill=ref,
                            skill_hash=hash_da_skill)

    assert _linhas(rel) == [
        ("android-01", "device.state", "in_sync", "ready", []),
        ("android-01", "app.installation", "in_sync", "current", []),
        ("android-01", "app.session", "in_sync", "ready", []),
        ("android-02", "device.state", "diverged", "stopped", [("converge", "start")]),
        ("android-02", "app.installation", "diverged", "outdated", [("converge", "app.install")]),
        # `on_missing: ask` na fixture: a conta deslogada é de pessoa, mesmo com credencial no cofre.
        ("android-02", "app.session", "diverged", "logged_out", [("ask", None)]),
    ]
    assert len(rel.ok) == 3 and len(rel.drifts) == 3 and not rel.ready_to_run and rel.blockers == ()
    assert [(a.target.instance_id, a.verb) for a in rel.actions] == [
        ("android-02", "start"), ("android-02", "app.install"), ("android-02", None)]
    assert [a.ref.kind for a in rel.human_interventions] == [ResourceKind.app_session]
    assert rel.risks[0].startswith("app.install em android-02: instalar mexe no disco")
    assert rel.risks[1].startswith("start em android-02: ligar ocupa uma vaga do rodízio")

    canonico = rel.canonical()
    assert canonico["skill"] == {"id": "ig.abrir_conversa", "version": 1, "content_hash": hash_da_skill}
    assert canonico["summary"] == {"in_sync": 3, "diverged": 3, "pending": 0, "held": 0, "unknown": 0,
                                   "blocked": 0, "unsupported": 0, "actions": 3, "human_interventions": 1,
                                   "blockers": 0, "ready_to_run": False}
    texto = json.dumps(canonico, ensure_ascii=False)
    for nome in ("lucas", "bruno"):
        assert f"ref-de-{nome}" not in texto and f"login-de-{nome}" not in texto   # credencial: só "tem/não tem"
    assert _foto(banco) == antes                                                      # ler e montar não gravam


def test_segunda_passada_depois_de_convergir_nao_tem_acao(banco: Database) -> None:
    _parque(banco)
    specs, ref, _ = _skill()
    alvos = [Target("android-01"), Target("android-02")]
    runtimes = {"android-01": RuntimeFalso(), "android-02": RuntimeFalso()}
    # O que o apply faria (e ainda não existe): o aparelho no ar, a promovida instalada e a conta entrada.
    banco.execute("UPDATE device_app_state SET installed_release_id='r-447', desired_release_id='r-447',"
                  " observed_version_code=447 WHERE instance_id='android-02'")
    banco.execute("UPDATE account_sessions SET status='session_ready' WHERE account_id='acc-p-bruno'")
    leitores = Leitores(banco, runtimes)
    primeira = build_plan_report(specs, alvos, leitores.ler(specs, alvos), skill=ref)
    segunda = build_plan_report(specs, alvos, leitores.ler(specs, alvos), skill=ref)
    assert primeira.actions == () and primeira.ready_to_run and len(primeira.ok) == 6
    assert segunda.content_hash() == primeira.content_hash()


def test_mesma_entrada_em_outra_ordem_da_o_mesmo_relatorio(banco: Database) -> None:
    _parque(banco)
    specs, ref, h = _skill()
    alvos = [Target("android-01"), Target("android-02")]
    runtimes = {"android-01": RuntimeFalso(), "android-02": RuntimeFalso(state="hibernated")}
    lidos = Leitores(banco, runtimes).ler(specs, alvos)
    base = build_plan_report(specs, alvos, lidos, skill=ref, skill_hash=h)
    sorteio = random.Random(27)
    for _ in range(5):
        s, a, o = list(specs), list(alvos), list(lidos)
        sorteio.shuffle(s), sorteio.shuffle(a), sorteio.shuffle(o)
        outro = build_plan_report(s + s[:1], a + a[:1], o, skill=ref, skill_hash=h)   # repetido idêntico conta 1
        assert outro == base and outro.content_hash() == base.content_hash()
    ordem = [ln.drift.ref.kind for ln in base.lines if ln.drift.target.instance_id == "android-01"]
    assert ordem == sorted(ordem, key=KIND_ORDER.index)


def test_par_nao_lido_e_desconhecido_sem_acao_e_listado_como_risco() -> None:
    spec = ResourceSpec.of("app.session", "instagram", {"session": "ready"}, "apply")
    rel = build_plan_report([spec], [Target("android-07", "p-lucas")], [])
    (linha,) = rel.lines
    assert (linha.drift.status, linha.drift.code, linha.actions) == (DriftStatus.unknown, NOT_READ, ())
    assert rel.ok == () and not rel.ready_to_run
    assert rel.risks == ("android-07 · app.session(instagram): não comprovado — app.session(instagram) não foi lido "
                         "em android-07",)
    assert rel.canonical()["resources"][0]["observed"] is None  # type: ignore[index,call-overload]


def test_recurso_em_conflito_ou_lido_duas_vezes_e_recusado(banco: Database) -> None:
    online = ResourceSpec.of("device.state", None, "online")
    with pytest.raises(ValueError, match="dois estados desejados"):
        build_plan_report([online, ResourceSpec.of("device.state", None, "stopped")], [Target("android-01")], [])
    _parque(banco)
    lido = Leitores(banco, {"android-01": RuntimeFalso()}).ler((online,), [Target("android-01")])
    with pytest.raises(ValueError, match="duas leituras"):
        build_plan_report([online], [Target("android-01")], [*lido, *Leitores(
            banco, {"android-01": RuntimeFalso()}).ler((online,), [Target("android-01")])])


def test_bloqueio_e_retido_aparecem_como_pessoa_e_como_aviso(banco: Database) -> None:
    _parque(banco)
    banco.execute("UPDATE device_app_state SET state='install_failed', detail='adb caiu' WHERE instance_id='android-02'")
    banco.execute("UPDATE instances SET desired_state='stopped' WHERE id='android-01'")
    specs = (ResourceSpec.of("device.state", None, "online"),
             ResourceSpec.of("app.installation", "instagram", {"release": "promoted"}))
    alvos = [Target("android-01"), Target("android-02")]
    runtimes = {"android-01": RuntimeFalso(state="stopped"), "android-02": RuntimeFalso()}
    rel = build_plan_report(specs, alvos, Leitores(banco, runtimes).ler(specs, alvos))
    assert [(ln.drift.target.instance_id, str(ln.drift.code)) for ln in rel.blockers] == [
        ("android-02", "delivery_failed")]
    assert any("alguém mandou parar" in r for r in rel.risks)     # o rodízio liga mesmo assim: vira aviso
    assert [(a.target.instance_id, a.purpose) for a in rel.human_interventions] == [
        ("android-02", ActionPurpose.ask)]
