"""Rotas REST + WebSocket. Ver docs/api-contract.md."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import threading
import unicodedata
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .automation.driver import DriverError
from .db import dumps, loads
from .devices.adb import AdbError
from .devices.manager import ControlError, DeviceRuntime, InstanceBusy
from .models import (ApprovalDecision, AppDTO, AppInput, AppPatch, BulkBody, CapabilityDTO, InstanceActionBody,
                     InstancePatch, InstanceState, ProfilePolicyPatch,
                     AppInstallBody, AppVerifyBody, CredentialUpdate, MemoryCreate, PersonaCreate, PersonaPatch,
                     PersonaPreviewBody, ProfileCreate, ProfilePatch,
                     ReleaseChannel, ReleaseImportBody, ReleaseLifecycleBody, ReleaseState, SessionStatus,
                     SignatureApprovalBody, StoreBody,
                     ManualInput, ReleaseBody, ResolveBody, RunCreate)
from .state import AppState
from .planning.capabilities import load_catalog
from .releases.catalog import ReleaseValidationError
from .social.service import SocialError
from .taskqueue.service import RunError
from .util import now_iso

log = logging.getLogger("poc.api")
router = APIRouter(prefix="/api")

LIFECYCLE_ACTIONS = {"create", "start", "stop", "hibernate", "wake", "restart", "reset", "install_apk", "open_app",
                     "home", "back", "recents"}


def st(request: Request) -> AppState:
    return request.app.state.poc


def err(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def device(state: AppState, instance_id: str) -> DeviceRuntime:
    try:
        return state.devices.get(instance_id)
    except KeyError:
        raise err(404, "not_found", f"Instância {instance_id} não existe.") from None


def _recusa_loja_como_alvo(rt: DeviceRuntime) -> None:
    """A loja é a FONTE do aplicativo, nunca o destino: nela o app vem da Play Store, não do nosso catálogo."""
    if rt.store:
        raise err(409, "store_instance", f"{rt.id} é a loja (Play Store): nela o aplicativo vem da própria loja. "
                                         "Instale, prove e reverta releases nos aparelhos do parque.")


def app_dto(r: sqlite3.Row) -> AppDTO:
    return AppDTO(id=r["id"], name=r["name"], package=r["package"], activity=r["activity"], apk_path=r["apk_path"],
                  nav_hints=r["nav_hints"], known_selectors=loads(r["known_selectors"]), builtin=bool(r["builtin"]))


def apps_list(state: AppState) -> list[AppDTO]:
    return [app_dto(r) for r in state.db.query("SELECT * FROM apps ORDER BY builtin DESC, name")]


# ====================================================================== sistema
@router.get("/health")
async def health(request: Request) -> Any:
    return st(request).health()


@router.get("/snapshot")
async def snapshot(request: Request) -> Any:
    s = st(request)
    runs = s.db.query("SELECT * FROM runs ORDER BY created_at DESC LIMIT 20")
    return {"last_event_id": s.bus.last_id(), "server_time": now_iso(), "health": s.health(),
            "metrics": s.devices.last_metrics, "instances": s.devices.list_dtos(), "apps": apps_list(s),
            "runs": [s.repo.run_summary(r) for r in runs], "settings": s.settings.get()}


@router.get("/diagnostics")
async def diagnostics(request: Request, refresh: int = 0) -> Any:
    return await st(request).diagnostics(refresh=bool(refresh))


@router.get("/metrics")
async def metrics(request: Request) -> Any:
    m = st(request).devices.last_metrics
    if m is None:
        raise err(503, "not_ready", "Métricas ainda não coletadas.")
    return m


@router.get("/settings")
async def get_settings(request: Request) -> Any:
    return st(request).settings.get()


@router.put("/settings")
async def put_settings(request: Request, patch: dict[str, Any]) -> Any:
    s = st(request)
    unknown = set(patch) - set(s.settings.get().model_dump())
    if unknown:
        raise err(400, "unknown_setting", f"Limite(s) desconhecido(s): {', '.join(sorted(unknown))}")
    try:
        value = s.settings.update(patch)
    except ValueError as exc:
        raise err(400, "invalid_setting", str(exc).splitlines()[0] + ": " + "; ".join(str(exc).splitlines()[1:3])) from exc
    s.bus.emit("settings.updated", "Limites atualizados", data={"settings": value.model_dump()})
    s.scheduler.wake()
    return value


@router.get("/ai")
async def ai_status(request: Request) -> Any:
    return st(request).provider.status()


@router.post("/admin/shutdown", status_code=202)
async def shutdown(request: Request, stop_emulators: bool = False) -> Any:
    """Encerramento gracioso (usado por scripts/stop.ps1): fecha sessões, para o Appium iniciado por nós e,
    se pedido, os emuladores que ESTE projeto iniciou. Aceita apenas chamadas locais."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise err(403, "forbidden", "Apenas chamadas locais.")
    s = st(request)
    server = getattr(request.app.state, "server", None)

    async def _go() -> None:
        if stop_emulators:
            await asyncio.gather(*(s.devices.stop_instance(rt, force=True) for rt in s.devices.devices.values()
                                   if rt.pid), return_exceptions=True)
        if server is not None:
            server.should_exit = True
            # rede de segurança: se o encerramento gracioso travar, o processo sai assim mesmo (nunca dois backends)
            watchdog = threading.Timer(60, os._exit, (0,))
            watchdog.daemon = True
            watchdog.start()

    asyncio.create_task(_go())
    return {"accepted": True, "stop_emulators": stop_emulators}


# ====================================================================== custo de IA, fluxos e receitas
def _price(prices: dict[str, list[float]], model: str) -> list[float] | None:
    return next((v for k, v in prices.items() if model.startswith(k)), None)


@router.get("/usage")
async def usage(request: Request, run_id: str | None = None, days: int = Query(7, ge=1, le=365)) -> Any:
    """Custo de IA por função e modelo (uma linha por chamada em `ai_calls`), com US$ pelos preços de `ai.prices`."""
    s = st(request)
    prices = s.cfg.file.ai.prices
    where, params = ("run_id=?", (run_id,)) if run_id else ("ts >= datetime('now', ?)", (f"-{days} days",))
    rows = s.db.query(
        f"SELECT role, model, tier, COUNT(*) calls, SUM(input_tokens) fresh, SUM(cache_read) cache_read,"
        f" SUM(cache_write) cache_write, SUM(output_tokens) output, SUM(with_image) with_image, SUM(1-ok) errors,"
        f" AVG(ms) avg_ms FROM ai_calls WHERE {where} GROUP BY role, model, tier ORDER BY role, model", params)
    groups, total = [], 0.0
    for r in rows:
        p = _price(prices, r["model"])
        usd = None if p is None else round((r["fresh"] * p[0] + r["cache_read"] * p[1] + r["cache_write"] * p[2]
                                            + r["output"] * p[3]) / 1_000_000, 4)
        total += usd or 0.0
        groups.append({**dict(r), "avg_ms": round(r["avg_ms"] or 0), "usd": usd})
    per_obj = s.db.query(
        f"SELECT run_id, objective_id, COUNT(*) calls FROM ai_calls WHERE {where} AND objective_id IS NOT NULL"
        f" GROUP BY run_id, objective_id", params)
    driven = s.db.query(
        "SELECT COALESCE(driven_by,'ai') driven_by, COUNT(*) n FROM steps WHERE status='succeeded'"
        + (" AND run_id=?" if run_id else " AND finished_at >= datetime('now', ?)") + " GROUP BY 1", params)
    n_obj = len(per_obj)
    return {"scope": {"run_id": run_id, "days": None if run_id else days}, "groups": groups, "total_usd": round(total, 4),
            "objectives_with_ai": n_obj, "calls_per_objective": round(sum(o["calls"] for o in per_obj) / n_obj, 1) if n_obj else 0,
            "usd_per_objective": round(total / n_obj, 4) if n_obj else 0,
            "steps_driven_by": {r["driven_by"]: r["n"] for r in driven},
            "unpriced_models": sorted({r["model"] for r in rows if _price(prices, r["model"]) is None})}


@router.get("/flows")
async def list_flows(request: Request) -> Any:
    return st(request).scheduler.flows.list()


@router.put("/flows/{flow_id}")
async def update_flow(request: Request, flow_id: str, patch: dict[str, Any]) -> Any:
    s = st(request)
    if s.db.one("SELECT id FROM flows WHERE id=?", (flow_id,)) is None:
        raise err(404, "not_found", "Fluxo não encontrado.")
    if patch.get("status") not in ("active", "disabled"):
        raise err(400, "invalid", "status deve ser 'active' ou 'disabled'.")
    s.db.execute("UPDATE flows SET status=? WHERE id=?", (patch["status"], flow_id))
    return next(f for f in s.scheduler.flows.list() if f["id"] == flow_id)


@router.delete("/flows/{flow_id}", status_code=204)
async def delete_flow(request: Request, flow_id: str) -> Response:
    st(request).db.execute("DELETE FROM flows WHERE id=?", (flow_id,))
    return Response(status_code=204)


@router.get("/recipes")
async def list_recipes(request: Request) -> Any:
    rows = st(request).db.query("SELECT * FROM recipes ORDER BY app_package, step_key, version DESC")
    return [{**{k: r[k] for k in r.keys() if k != "actions"}, "actions": loads(r["actions"], [])} for r in rows]


@router.put("/recipes/{recipe_id}")
async def update_recipe(request: Request, recipe_id: int, patch: dict[str, Any]) -> Any:
    s = st(request)
    if patch.get("status") not in ("active", "quarantined"):
        raise err(400, "invalid", "status deve ser 'active' ou 'quarantined'.")
    s.db.execute("UPDATE recipes SET status=?, consecutive_fail=0 WHERE id=?", (patch["status"], recipe_id))
    return {"id": recipe_id, "status": patch["status"]}


@router.delete("/recipes/{recipe_id}", status_code=204)
async def delete_recipe(request: Request, recipe_id: int) -> Response:
    st(request).db.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))
    return Response(status_code=204)


# ====================================================================== apps
@router.get("/apps")
async def list_apps(request: Request) -> Any:
    return apps_list(st(request))


def _validate_apk(state: AppState, apk_path: str | None) -> None:
    if apk_path:
        try:
            state.devices.resolve_apk(apk_path)
        except ValueError as exc:
            raise err(400, "invalid_apk_path", str(exc)) from exc


@router.post("/apps")
async def create_app(request: Request, body: AppInput) -> Any:
    s = st(request)
    _validate_apk(s, body.apk_path)
    plain = unicodedata.normalize("NFKD", body.name).encode("ascii", "ignore").decode()   # "Configurações" → "Configuracoes"
    base = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "app"
    app_id, n = base, 2
    while s.db.one("SELECT id FROM apps WHERE id=?", (app_id,)):
        app_id, n = f"{base}-{n}", n + 1
    s.db.execute("INSERT INTO apps(id, name, package, activity, apk_path, nav_hints, known_selectors, builtin) VALUES (?,?,?,?,?,?,?,0)",
                 (app_id, body.name, body.package, body.activity or None, body.apk_path or None, body.nav_hints or None,
                  dumps(body.known_selectors) if body.known_selectors else None))
    _apps_changed(s)
    return app_dto(s.db.one("SELECT * FROM apps WHERE id=?", (app_id,)))


@router.put("/apps/{app_id}")
async def update_app(request: Request, app_id: str, body: AppPatch) -> Any:
    s = st(request)
    if s.db.one("SELECT id FROM apps WHERE id=?", (app_id,)) is None:
        raise err(404, "not_found", "App não encontrado.")
    data = body.model_dump(exclude_unset=True)
    _validate_apk(s, data.get("apk_path"))
    if "known_selectors" in data:
        data["known_selectors"] = dumps(data["known_selectors"]) if data["known_selectors"] else None
    for k in ("activity", "apk_path", "nav_hints"):
        if k in data and not data[k]:
            data[k] = None
    if data:
        s.db.execute(f"UPDATE apps SET {', '.join(f'{k}=?' for k in data)} WHERE id=?", (*data.values(), app_id))
    _apps_changed(s)
    return app_dto(s.db.one("SELECT * FROM apps WHERE id=?", (app_id,)))


@router.delete("/apps/{app_id}", status_code=204)
async def delete_app(request: Request, app_id: str) -> Response:
    s = st(request)
    row = s.db.one("SELECT * FROM apps WHERE id=?", (app_id,))
    if row is None:
        raise err(404, "not_found", "App não encontrado.")
    if row["builtin"]:
        raise err(409, "builtin", "O app de QA embutido não pode ser removido.")
    s.db.execute("DELETE FROM apps WHERE id=?", (app_id,))
    _apps_changed(s)
    for rt in s.devices.devices.values():
        s.devices.publish(rt)
    return Response(status_code=204)


def _apps_changed(s: AppState) -> None:
    s.bus.emit("apps.updated", "Apps atualizados", data={"apps": [a.model_dump() for a in apps_list(s)]})


# ====================================================================== instâncias
# ====================================================================== perfis do Instagram
def _social_error(exc: SocialError) -> HTTPException:
    return err(exc.status, exc.code, exc.message)


@router.get("/instagram/profiles")
async def list_profiles(request: Request) -> Any:
    return st(request).social.list_profiles()


@router.post("/instagram/profiles", status_code=201)
async def create_profile(request: Request, body: ProfileCreate) -> Any:
    """Cadastro pelo portal. A senha entra aqui e vai direto para o cofre: nenhuma rota a devolve."""
    try:
        return st(request).social.create_profile(body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}")
async def get_profile(request: Request, profile_id: str) -> Any:
    try:
        return st(request).social.get_profile(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.patch("/instagram/profiles/{profile_id}")
async def patch_profile(request: Request, profile_id: str, body: ProfilePatch) -> Any:
    try:
        return st(request).social.update_profile(profile_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}", status_code=204)
async def delete_profile(request: Request, profile_id: str) -> Response:
    try:
        st(request).social.delete_profile(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc
    return Response(status_code=204)


@router.put("/instagram/profiles/{profile_id}/credential")
async def put_credential(request: Request, profile_id: str, body: CredentialUpdate) -> Any:
    """Só escrita. O painel mostra apenas que existe uma credencial, nunca o valor."""
    try:
        return st(request).social.set_credential(profile_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}/credential")
async def delete_credential(request: Request, profile_id: str) -> Any:
    try:
        return st(request).social.delete_credential(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/connect", status_code=202)
async def connect_profile(request: Request, profile_id: str) -> Any:
    """Abre o Instagram no aparelho vinculado, reaproveita a sessão ou autentica, e verifica a conta.

    202 porque leva dezenas de segundos: o resultado aparece no próprio perfil (`session`).
    """
    return _start_session_job(request, profile_id, force_login=False, label="autenticação do Instagram")


@router.post("/instagram/profiles/{profile_id}/verify", status_code=202)
async def verify_profile(request: Request, profile_id: str) -> Any:
    """Relê do aparelho qual conta está aberta. Não digita senha: só observa."""
    return _start_session_job(request, profile_id, force_login=False, label="verificação da conta")


@router.post("/instagram/profiles/{profile_id}/logout", status_code=202)
async def logout_profile(request: Request, profile_id: str) -> Any:
    """Encerra a sessão no aparelho apagando os dados do app — é o jeito determinístico de sair.

    Apaga também cache e preferências do Instagram naquele aparelho; por isso é uma ação explícita, nunca efeito
    colateral de outra operação.
    """
    s = st(request)
    rt, profile = _profile_device(s, profile_id)
    started = s.scheduler.run_device_job(
        rt, lambda: _do_logout(s, rt, profile_id), label="logout do Instagram")
    if not started:
        raise err(409, "device_busy", "O aparelho está ocupado; tente novamente em instantes.")
    return {"accepted": True, "profile_id": profile_id, "instance_id": rt.id}


async def _do_logout(s: AppState, rt: DeviceRuntime, profile_id: str) -> None:
    package = s.cfg.file.instagram.package
    await rt.executor.run(rt.adb.clear_data, package, timeout=120, label="apagar dados do app")
    rt.app_versions.clear()
    s.social_repo.set_session(profile_id, status=SessionStatus.unknown, instance_id=rt.id,
                              detail="Dados do app apagados neste aparelho; é preciso entrar de novo.")
    s.bus.emit("log", f"{rt.id}: sessão do Instagram encerrada (dados do app apagados)", instance_id=rt.id)


def _profile_device(s: AppState, profile_id: str) -> tuple[DeviceRuntime, Any]:
    try:
        profile = s.social.get_profile(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc
    if not profile.instance_id:
        raise err(409, "no_binding", "Este perfil não está vinculado a nenhum aparelho.")
    rt = device(s, profile.instance_id)
    return rt, profile


def _start_session_job(request: Request, profile_id: str, *, force_login: bool, label: str) -> Any:
    s = st(request)
    rt, profile = _profile_device(s, profile_id)
    if not profile.credential.configured:
        raise err(409, "no_credential", "Cadastre a senha deste perfil antes de conectar.")
    if rt.state not in (InstanceState.online, InstanceState.booting, InstanceState.stopped,
                        InstanceState.hibernated, InstanceState.absent):
        raise err(409, "device_unavailable", f"O aparelho está em '{rt.state.value}'.")
    if rt.state != InstanceState.online:
        s.devices.request_start(rt, "conectar perfil do Instagram")
        raise err(409, "device_starting", "O aparelho está sendo ligado; tente novamente em instantes.")
    started = s.scheduler.run_device_job(
        rt, lambda: s.instagram.ensure_session(rt, profile_id, force_login=force_login), label=label)
    if not started:
        raise err(409, "device_busy", "O aparelho está ocupado; tente novamente em instantes.")
    return {"accepted": True, "profile_id": profile_id, "instance_id": rt.id}


# ====================================================================== persona, memória e histórico
@router.get("/personas")
async def list_personas(request: Request) -> Any:
    return st(request).social.list_personas()


@router.post("/personas", status_code=201)
async def create_persona(request: Request, body: PersonaCreate) -> Any:
    try:
        return st(request).social.create_persona(body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/personas/{persona_id}")
async def get_persona(request: Request, persona_id: str) -> Any:
    try:
        return st(request).social.get_persona(persona_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.patch("/personas/{persona_id}")
async def update_persona(request: Request, persona_id: str, body: PersonaPatch) -> Any:
    try:
        return st(request).social.update_persona(persona_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.delete("/personas/{persona_id}", status_code=204)
async def delete_persona(request: Request, persona_id: str) -> None:
    try:
        st(request).social.delete_persona(persona_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.post("/personas/{persona_id}/preview")
async def preview_persona(request: Request, persona_id: str, body: PersonaPreviewBody) -> Any:
    """Testar Persona: mostra como ela responderia. Não toca em aparelho, não grava interação, não publica nada."""
    try:
        return await st(request).social.preview_persona(persona_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/memory")
async def list_memory(request: Request, profile_id: str, subject: str | None = None, limit: int = 100) -> Any:
    try:
        return st(request).social.list_memories(profile_id, subject=subject, limit=min(max(limit, 1), 500))
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/memory", status_code=201)
async def add_memory(request: Request, profile_id: str, body: MemoryCreate) -> Any:
    try:
        return st(request).social.add_memory(profile_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}/memory/{memory_id}", status_code=204)
async def delete_memory(request: Request, profile_id: str, memory_id: str) -> None:
    try:
        st(request).social.delete_memory(profile_id, memory_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/interactions")
async def list_interactions(request: Request, profile_id: str, counterparty: str | None = None,
                            thread_key: str | None = None, limit: int = 30) -> Any:
    try:
        return st(request).social.list_interactions(profile_id, counterparty=counterparty, thread_key=thread_key,
                                                    limit=min(max(limit, 1), 200))
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/context")
async def social_context(request: Request, profile_id: str, counterparty: str | None = None,
                         thread_key: str | None = None, content: str | None = None) -> Any:
    """Exatamente o que o modelo veria deste perfil. Serve para conferir persona, memória — e a ausência de senha."""
    try:
        return st(request).social.context(profile_id, counterparty=counterparty, thread_key=thread_key,
                                          current_content=content)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/capabilities")
async def list_capabilities(request: Request, package: str = "com.instagram.android") -> Any:
    """Catálogo do app: o que o sistema sabe fazer, com efeito, risco e política padrão de cada ação."""
    catalog = load_catalog(package)
    if catalog is None:
        return []
    return [CapabilityDTO(key=c.key, title=c.title, side_effect=c.side_effect, risk=c.risk,
                          default_policy=c.default_policy, limit_bucket=c.limit_bucket, needs_draft=c.needs_draft,
                          bindings=list(c.bindings)) for c in catalog.offered]


@router.get("/instagram/profiles/{profile_id}/policy")
async def get_policy(request: Request, profile_id: str) -> Any:
    try:
        return st(request).social.get_policy(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.put("/instagram/profiles/{profile_id}/policy")
async def put_policy(request: Request, profile_id: str, body: ProfilePolicyPatch) -> Any:
    try:
        return st(request).social.set_policy(profile_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/auth-attempts")
async def auth_attempts(request: Request, profile_id: str, limit: int = 20) -> Any:
    """Tentativas de autenticação deste perfil: quando, em qual aparelho e com que desfecho."""
    try:
        return st(request).social.auth_attempts(profile_id, min(max(limit, 1), 100))
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/instagram/profiles/{profile_id}/runs")
async def profile_runs(request: Request, profile_id: str, limit: int = 20) -> Any:
    """Execuções que passaram por este perfil. O vínculo vem do objetivo, que guarda o dono fotografado."""
    s = st(request)
    try:
        s.social.get_profile(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc
    rows = s.db.query(
        "SELECT r.* FROM runs r WHERE EXISTS (SELECT 1 FROM objectives o WHERE o.run_id=r.id AND o.profile_id=?)"
        " ORDER BY r.created_at DESC LIMIT ?", (profile_id, min(max(limit, 1), 100)))
    return [s.repo.run_summary(r) for r in rows]


@router.get("/approvals")
async def list_approvals(request: Request, status: str | None = "pending", profile_id: str | None = None,
                         limit: int = 50) -> Any:
    return st(request).approval_service.list(status=status or None, profile_id=profile_id,
                                             limit=min(max(limit, 1), 200))


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(request: Request, approval_id: str, body: ApprovalDecision) -> Any:
    """Aprovar, editar ou rejeitar. Nenhum dos três marca a etapa como concluída: eles decidem o que VAI acontecer."""
    try:
        return st(request).approval_service.decide(approval_id, body.verb, content=body.content, note=body.note)
    except SocialError as exc:
        raise _social_error(exc) from exc


# ====================================================================== releases de aplicativo
@router.get("/releases")
async def list_releases(request: Request, package: str | None = None) -> Any:
    return st(request).releases.list_releases(package)


@router.post("/releases/import", status_code=202)
async def import_releases(request: Request, body: ReleaseImportBody | None = None) -> Any:
    """Varre a pasta de entrada, inspeciona cada conjunto com as ferramentas do SDK e cataloga os aprovados.
    O nome do arquivo não decide nada: pacote, versão, splits e assinatura vêm do próprio pacote."""
    s = st(request)
    body = body or ReleaseImportBody()
    try:
        results = await asyncio.to_thread(
            s.releases.import_inbox, source_reference=body.source_reference, expected_package=body.expected_package)
    except ReleaseValidationError as exc:
        raise err(400, "import_failed", str(exc)) from exc
    return {"imported": [r.to_dict() for r in results]}


@router.post("/releases/{release_id}/approve-signature")
async def approve_signature(request: Request, release_id: str, body: SignatureApprovalBody | None = None) -> Any:
    """Aprovação explícita do operador. Depois dela, release com assinatura diferente é bloqueada sozinha."""
    try:
        return st(request).releases.approve_signature(release_id, note=(body.note if body else None))
    except ReleaseValidationError as exc:
        raise err(404, "not_found", str(exc)) from exc


@router.post("/releases/{release_id}/lifecycle")
async def release_lifecycle(request: Request, release_id: str, body: ReleaseLifecycleBody) -> Any:
    """Canário, promoção, quarentena e rollback numa rota só, com um verbo por chamada.

    `promote` e `quarantine` são decisões de banco e respondem na hora. `canary` e `rollback` mexem no aparelho:
    são aceitos aqui, rodam pela fila do aparelho e o resultado aparece em `GET /api/app-state`.
    """
    s = st(request)
    release = s.release_repo.release_row(release_id)
    if release is None:
        raise err(404, "not_found", "Release não encontrada.")
    try:
        if body.verb == "promote":
            return {"accepted": True, "release": s.releases.promote(release_id, note=body.note)}
        if body.verb == "quarantine":
            return {"accepted": True, "release": s.releases.quarantine(release_id, reason=body.note)}
        if body.verb == "distribute":
            # Vale para o parque inteiro, por isso não pede `instance_id`. A resposta diz, aparelho por aparelho, se
            # a instalação começou já ou ficou pendente para quando ele entrar em serviço.
            return {"accepted": True, "eager": body.eager, "devices": s.distribute(release_id, eager=body.eager)}
    except ReleaseValidationError as exc:
        raise err(409, "lifecycle_refused", str(exc)) from exc

    if not body.instance_id:
        raise err(400, "instance_required", f"O verbo '{body.verb}' precisa do aparelho (`instance_id`).")
    rt = device(s, body.instance_id)
    _recusa_loja_como_alvo(rt)
    if rt.state != InstanceState.online:
        raise err(409, "not_online", "O aparelho precisa estar online.")
    package = release["package_name"]

    if body.verb == "canary":
        # A mesma regra do serviço, conferida aqui: o trabalho roda em segundo plano, então uma recusa lá dentro
        # devolveria 202 e quem chamou nunca saberia por quê.
        if release["channel"] == ReleaseChannel.promoted.value:
            raise err(409, "lifecycle_refused",
                      "Esta versão já foi promovida: instale-a normalmente. Para prová-la outra vez, coloque-a em "
                      "quarentena antes — assim a decisão de desfazer a promoção fica explícita.")
        trabalho = lambda: s.releases.start_canary(rt, release_id, s.installer)  # noqa: E731
        rotulo = "canário de APK"
    else:
        estado = s.release_repo.app_state(rt.id, package)
        if not (estado and estado["previous_release_id"]):
            raise err(409, "no_previous_release",
                      f"{rt.id} não tem versão anterior registrada para {package}; não há para onde voltar.")
        # Preservar os dados é o padrão. Reinstalar apaga a sessão, então só acontece se quem chamou disser isso
        # de propósito — a API nunca escolhe esse caminho sozinha.
        preserve = not body.confirm_reinstall
        trabalho = lambda: s.releases.rollback(rt, package, s.installer, preserve=preserve, note=body.note)  # noqa: E731
        rotulo = "rollback de APK"

    if not s.scheduler.run_device_job(rt, trabalho, label=rotulo):
        raise err(409, "device_busy", "O aparelho está ocupado; tente novamente em instantes.")
    return {"accepted": True, "instance_id": rt.id, "release_id": release_id, "verb": body.verb}


# ---------------------------------------------------------------------- a loja como fonte do aplicativo
def _loja(s: AppState) -> DeviceRuntime:
    if not s.cfg.store_id:
        raise err(409, "no_store", "Nenhum aparelho-loja configurado (`instances.store` no config.yaml).")
    return device(s, s.cfg.store_id)


def _pacote_da_loja(s: AppState, body: StoreBody | None) -> str:
    return (body.package if body and body.package else None) or s.cfg.file.instagram.package


@router.get("/store")
async def store_status(request: Request, package: str | None = None) -> Any:
    """Loja × catálogo: o que a Play Store tem instalado lá, o que já foi catalogado e se há versão nova a buscar."""
    s = st(request)
    pkg = package or s.cfg.file.instagram.package
    rt = s.devices.devices.get(s.cfg.store_id) if s.cfg.store_id else None
    return {**s.releases.store_status(s.cfg.store_id, pkg), "configured": rt is not None,
            "state": rt.state.value if rt else None}


@router.post("/store/open-listing")
async def store_open_listing(request: Request, body: StoreBody | None = None) -> Any:
    """Abre a página do app na Play Store da loja. Instalar ou atualizar é um toque do USUÁRIO — nunca daqui."""
    s = st(request)
    rt = _loja(s)
    if rt.state != InstanceState.online:
        raise err(409, "not_online", "A loja precisa estar ligada.")
    pkg = _pacote_da_loja(s, body)
    try:
        await rt.executor.run(rt.adb.open_store_listing, pkg, timeout=40, label="abrir a Play Store")
    except AdbError as exc:
        raise err(503, "device_error", str(exc)) from exc
    return {"ok": True, "instance_id": rt.id, "package": pkg}


@router.post("/store/sync", status_code=202)
async def store_sync(request: Request, body: StoreBody | None = None) -> Any:
    """Copia da loja o pacote instalado pela Play Store e o cataloga. 202: o resultado aparece em `/releases`.

    A loja precisa JÁ estar ligada. Ligar-e-esperar daqui viraria um 202 que falha em silêncio minutos depois.
    """
    s = st(request)
    rt = _loja(s)
    if rt.state != InstanceState.online:
        raise err(409, "not_online", "A loja precisa estar ligada para buscar o aplicativo.")
    pkg = _pacote_da_loja(s, body)
    if not s.scheduler.run_device_job(rt, lambda: s.releases.sync_from_store(rt, pkg, s.installer),
                                      label="busca do aplicativo na loja"):
        raise err(409, "device_busy", "A loja está ocupada — se você está com o controle manual dela no painel, "
                                      "devolva-o e tente de novo.")
    return {"accepted": True, "instance_id": rt.id, "package": pkg}


@router.get("/app-state")
async def app_state(request: Request, package: str | None = None) -> Any:
    return st(request).release_repo.list_app_state(package)


@router.get("/instances")
async def list_instances(request: Request) -> Any:
    return st(request).devices.list_dtos()


@router.put("/instances/{instance_id}")
async def update_instance(request: Request, instance_id: str, body: InstancePatch) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("app_id") and s.db.one("SELECT id FROM apps WHERE id=?", (data["app_id"],)) is None:
        raise err(400, "unknown_app", "App não cadastrado.")
    if "account_label" in data:
        data["account_label"] = (data["account_label"] or "").strip() or None
    if data:
        s.db.execute(f"UPDATE instances SET {', '.join(f'{k}=?' for k in data)} WHERE id=?", (*data.values(), instance_id))
    s.devices.publish(rt, f"{instance_id}: configuração atualizada")
    return s.devices.dto(rt)


@router.post("/instances/{instance_id}/app/install", status_code=202)
async def install_release_on(request: Request, instance_id: str, body: AppInstallBody) -> Any:
    """Instala um conjunto do catálogo. 202 porque leva minutos: o resultado aparece em `GET /api/app-state`."""
    s = st(request)
    rt = device(s, instance_id)
    _recusa_loja_como_alvo(rt)
    if rt.state != InstanceState.online:
        raise err(409, "not_online", "O aparelho precisa estar online para instalar.")
    # A pré-condição é conferida ANTES de aceitar: o trabalho roda em segundo plano, então uma recusa lá dentro
    # nunca chegaria a quem chamou.
    release = s.release_repo.release_row(body.release_id)
    if release is None:
        raise err(404, "not_found", "Release não encontrada.")
    if release["status"] != ReleaseState.installable.value:
        raise err(409, "release_not_installable",
                  f"A release está em '{release['status']}' e não pode ser instalada."
                  + (f" {release['detail']}" if release["detail"] else ""))
    if release["channel"] == ReleaseChannel.quarantined.value:
        # A quarentena é o outro eixo: o arquivo está íntegro, a VERSÃO é que já falhou a prova. A recusa tem de
        # acontecer aqui, junto da de status, senão o 202 esconderia o motivo de quem chamou.
        raise err(409, "release_quarantined",
                  "Esta versão está em quarentena porque já falhou a prova num aparelho."
                  + (f" {release['channel_detail']}" if release["channel_detail"] else "")
                  + " Para tentar de novo, coloque-a em canário de propósito.")
    started = s.scheduler.run_device_job(
        rt, lambda: s.releases.install_on(rt, body.release_id, s.installer), label="instalação de APK")
    if not started:
        raise err(409, "device_busy", "O aparelho está ocupado; tente novamente em instantes.")
    return {"accepted": True, "instance_id": instance_id, "release_id": body.release_id}


@router.post("/instances/{instance_id}/app/verify", status_code=202)
async def verify_app_on(request: Request, instance_id: str, body: AppVerifyBody) -> Any:
    """Relê do aparelho a versão instalada e registra divergência, se houver."""
    s = st(request)
    rt = device(s, instance_id)
    if rt.state != InstanceState.online:
        raise err(409, "not_online", "O aparelho precisa estar online para verificar o app.")
    started = s.scheduler.run_device_job(
        rt, lambda: s.releases.verify_on(rt, body.package, s.installer), label="verificação do app")
    if not started:
        raise err(409, "device_busy", "O aparelho está ocupado; tente novamente em instantes.")
    return {"accepted": True, "instance_id": instance_id, "package": body.package}


@router.get("/instances/{instance_id}/packages")
async def packages(request: Request, instance_id: str) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    if rt.state != InstanceState.online:
        raise err(409, "offline", "A instância precisa estar online.")
    try:
        return {"packages": await s.devices.list_packages(rt)}
    except (DriverError, AdbError) as exc:
        raise err(503, "adb_error", str(exc)) from exc


def _app_for(s: AppState, rt: DeviceRuntime, app_id: str | None) -> sqlite3.Row:
    chosen = app_id or s.db.scalar("SELECT app_id FROM instances WHERE id=?", (rt.id,))
    row = s.db.one("SELECT * FROM apps WHERE id=?", (chosen,)) if chosen else None
    if row is None:
        raise err(400, "no_app", f"{rt.id}: nenhum app associado. Associe um app em Configuração → Instâncias e contas.")
    return row


async def _do_action(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody) -> None:
    d = s.devices
    try:
        if action == "create":
            await d.create(rt)
        elif action in ("start", "wake"):
            await d.start_instance(rt)
        elif action == "stop":
            await d.stop_instance(rt)
        elif action == "hibernate":
            await d.stop_instance(rt, hibernate=True)
        elif action == "restart":
            await d.restart_instance(rt)
        elif action == "reset":
            await d.reset_instance(rt)
        elif action == "install_apk":
            await d.install_apk(rt, _app_for(s, rt, body.app_id))
        elif action == "open_app":
            await d.open_app(rt, _app_for(s, rt, body.app_id))
        else:
            await d.quick_key(rt, action)
    except HTTPException as exc:
        s.bus.emit("log", f"{rt.id}: {action} — {exc.detail.get('message') if isinstance(exc.detail, dict) else exc.detail}",
                   level="error", instance_id=rt.id)
    except (InstanceBusy, ValueError, AdbError, DriverError) as exc:
        s.bus.emit("log", f"{rt.id}: {action} não executado — {exc}", level="error", instance_id=rt.id)
    except Exception as exc:  # noqa: BLE001
        log.exception("ação %s em %s", action, rt.id)
        s.bus.emit("log", f"{rt.id}: erro em {action} — {exc}", level="error", instance_id=rt.id)


def s_android_hibernation(rt: DeviceRuntime) -> bool:
    return bool(rt.cfg.instance_android(rt.id).hibernation)


def _precheck(rt: DeviceRuntime, action: str, body: InstanceActionBody) -> str | None:
    if action not in LIFECYCLE_ACTIONS:
        return "ação desconhecida"
    if action == "reset" and not body.confirm:
        return "o reset apaga dados e sessão do aparelho; envie confirm=true"
    if rt.store and action in ("install_apk", "open_app"):
        return "este aparelho é a loja (Play Store): ele não recebe aplicativo do parque nem opera app de tarefa"
    if action == "hibernate" and not s_android_hibernation(rt):
        return "hibernação desligada na configuração (android.hibernation)"
    if action in ("stop", "hibernate", "restart", "reset", "install_apk", "open_app", "home", "back", "recents") \
            and rt.control.value == "ai":
        return "a IA está executando neste aparelho; pause/cancele a execução ou assuma o controle"
    if action in ("install_apk", "open_app", "home", "back", "recents") and rt.state != InstanceState.online:
        return "a instância precisa estar online"
    return None


@router.post("/instances/bulk", status_code=202)
async def bulk_action(request: Request, body: BulkBody) -> Any:
    s = st(request)
    accepted, rejected = [], []
    params = body.params or InstanceActionBody()
    for iid in dict.fromkeys(body.ids):
        if iid not in s.devices.devices:
            rejected.append({"id": iid, "reason": "instância desconhecida"})
            continue
        rt = s.devices.devices[iid]
        if rt.store:
            # Ação em massa é para o parque. Um `reset` em lote apagaria o login do Google da loja; ligar, desligar e
            # resetar a loja continuam possíveis, mas um a um, com quem pediu sabendo em que aparelho está mexendo.
            rejected.append({"id": iid, "reason": "é a loja (Play Store): ações em lote não se aplicam a ela"})
            continue
        why = _precheck(rt, body.action, params)
        if why:
            rejected.append({"id": iid, "reason": why})
            continue
        asyncio.create_task(_do_action(s, rt, body.action, params))
        accepted.append(iid)
    return {"accepted": accepted, "rejected": rejected}


@router.post("/instances/{instance_id}/actions/{action}", status_code=202)
async def instance_action(request: Request, instance_id: str, action: str, body: InstanceActionBody | None = None) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    params = body or InstanceActionBody()
    why = _precheck(rt, action, params)
    if why:
        raise err(409 if why != "ação desconhecida" else 400, "rejected", f"{instance_id}: {why}.")
    if action in ("install_apk", "open_app"):
        _app_for(s, rt, params.app_id)          # valida antes de aceitar
    asyncio.create_task(_do_action(s, rt, action, params))
    return {"accepted": True}


@router.get("/instances/{instance_id}/frame")
async def frame(request: Request, instance_id: str, mode: str = "thumb") -> Response:
    rt = device(st(request), instance_id)
    f = rt.frame
    if f is None:
        raise err(404, "no_frame", "Ainda não há frame deste aparelho.")
    headers = {"X-Frame-Id": f.info.id, "X-Frame-Ts": f.info.ts, "X-Frame-Width": str(f.info.width),
               "X-Frame-Height": str(f.info.height), "X-Frame-Orientation": f.info.orientation,
               "Cache-Control": "no-store", "Access-Control-Expose-Headers": "X-Frame-Id, X-Frame-Ts, X-Frame-Width, X-Frame-Height, X-Frame-Orientation"}
    return Response(content=f.jpeg_full if mode == "full" else f.jpeg_thumb, media_type="image/jpeg", headers=headers)


@router.get("/instances/{instance_id}/hierarchy")
async def hierarchy(request: Request, instance_id: str) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    if rt.state != InstanceState.online:
        raise err(409, "offline", "A instância precisa estar online.")
    try:
        tree = await s.devices.hierarchy(rt)
    except DriverError as exc:
        raise err(503, "automation_unavailable", str(exc)) from exc
    return {"ts": now_iso(), "elements": [e.to_dict() for e in tree.elements]}


# ---------------------------------------------------------------------- controle manual
@router.post("/instances/{instance_id}/control/take")
async def take_control(request: Request, instance_id: str) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    status, lease = s.devices.request_control(rt)
    return {"status": status, "lease_id": lease}


@router.post("/instances/{instance_id}/control/release")
async def release_control(request: Request, instance_id: str, body: ReleaseBody) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    try:
        s.devices.release_control(rt, body.lease_id)
    except ControlError as exc:
        raise err(409, exc.code, exc.message) from exc
    return {"status": "released"}


@router.post("/instances/{instance_id}/input")
async def manual_input(request: Request, instance_id: str, body: ManualInput) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    try:
        await s.devices.manual_input(rt, body)
    except ControlError as exc:
        raise err(409 if exc.code != "bad_input" else 400, exc.code, exc.message) from exc
    except (DriverError, AdbError) as exc:
        raise err(503, "device_error", str(exc)) from exc
    return {"ok": True}


# ====================================================================== execuções
def _run_error(exc: RunError) -> HTTPException:
    return err(exc.status, exc.code, exc.message)


@router.post("/runs")
async def create_run(request: Request, body: RunCreate) -> Any:
    try:
        return st(request).runs.create(body)
    except RunError as exc:
        raise _run_error(exc) from exc


@router.get("/runs")
async def list_runs(request: Request, limit: int = Query(20, ge=1, le=200)) -> Any:
    s = st(request)
    return [s.repo.run_summary(r) for r in s.db.query("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))]


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> Any:
    detail = st(request).repo.run_detail(run_id)
    if detail is None:
        raise err(404, "not_found", "Execução não encontrada.")
    return detail


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: str, after: int = 0, limit: int = Query(500, ge=1, le=5000)) -> Any:
    return st(request).bus.since(after, run_id=run_id, limit=limit)


@router.get("/runs/{run_id}/report")
async def run_report(request: Request, run_id: str) -> Any:
    try:
        return st(request).runs.report(run_id)
    except RunError as exc:
        raise _run_error(exc) from exc


@router.post("/runs/{run_id}/{op}")
async def run_op(request: Request, run_id: str, op: str) -> Any:
    runs = st(request).runs
    ops = {"start": runs.start, "pause": runs.pause, "resume": runs.resume, "cancel": runs.cancel,
           "retry_failed": runs.retry_failed}
    if op not in ops:
        raise err(404, "not_found", "Operação desconhecida.")
    try:
        return ops[op](run_id)
    except RunError as exc:
        raise _run_error(exc) from exc


@router.post("/runs/{run_id}/objectives/{objective_id}/resolve")
async def resolve(request: Request, run_id: str, objective_id: str, body: ResolveBody) -> Any:
    try:
        return st(request).runs.resolve(run_id, objective_id, body)
    except RunError as exc:
        raise _run_error(exc) from exc


@router.get("/evidence/{evidence_id}")
async def evidence(request: Request, evidence_id: int) -> Any:
    s = st(request)
    r = s.db.one("SELECT * FROM evidence WHERE id=?", (evidence_id,))
    if r is None or not r["path"] or r["redacted"]:
        raise err(404, "not_found", "Evidência não disponível.")
    path = (s.cfg.evidence_dir / r["path"]).resolve()
    if not path.is_relative_to(s.cfg.evidence_dir.resolve()) or not path.is_file():
        raise err(404, "not_found", "Arquivo de evidência ausente (retenção).")
    return FileResponse(path, media_type="image/jpeg" if path.suffix == ".jpg" else "text/plain")


# ====================================================================== websocket
@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    s: AppState = websocket.app.state.poc
    origin = websocket.headers.get("origin")
    if origin and origin not in s.cfg.file.server.allowed_origins:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        last = int(websocket.query_params.get("last_event_id", "0") or 0)
    except ValueError:
        last = 0
    queue = s.bus.subscribe()            # assina ANTES de reenviar o histórico para não perder eventos
    try:
        await websocket.send_json({"type": "hello", "server_time": now_iso(), "last_event_id": s.bus.last_id()})
        if s.bus.count_since(last) > 5000:
            await websocket.send_json({"type": "resync"})
            return
        for ev in s.bus.since(last):
            await websocket.send_json({"type": "event", "event": ev.model_dump(mode="json")})
            last = max(last, ev.id or 0)

        async def pump() -> None:
            nonlocal last
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    ev = None
                if not s.bus.is_subscribed(queue):     # fila estourou: o cliente precisa de um snapshot novo
                    await websocket.send_json({"type": "resync"})
                    await websocket.close()
                    return
                if ev is None:
                    continue
                if ev.id is not None:
                    if ev.id <= last:
                        continue
                    last = ev.id
                await websocket.send_json({"type": "event", "event": ev.model_dump(mode="json")})

        async def listen() -> None:
            while True:
                msg = await websocket.receive_json()
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "focus":
                    iid = msg.get("instance_id")
                    s.devices.set_focus(iid if isinstance(iid, str) else None)
                elif kind == "ping":
                    await websocket.send_json({"type": "pong"})

        tasks = [asyncio.create_task(pump()), asyncio.create_task(listen())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.debug("ws encerrado: %s", exc)
    except WebSocketDisconnect:
        pass
    finally:
        s.bus.unsubscribe(queue)
