"""Rotas REST + WebSocket. Ver docs/api-contract.md."""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import unicodedata
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .automation.driver import DriverError
from .db import dumps, loads
from .devices.adb import AdbError
from .devices.manager import ControlError, DeviceRuntime, InstanceBusy
from .models import (AppDTO, AppInput, AppPatch, BulkBody, InstanceActionBody, InstancePatch, InstanceState,
                     ManualInput, ReleaseBody, ResolveBody, RunCreate)
from .state import AppState
from .taskqueue.service import RunError
from .util import now_iso

log = logging.getLogger("poc.api")
router = APIRouter(prefix="/api")

LIFECYCLE_ACTIONS = {"create", "start", "stop", "restart", "reset", "install_apk", "open_app", "home", "back", "recents"}


def st(request: Request) -> AppState:
    return request.app.state.poc


def err(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def device(state: AppState, instance_id: str) -> DeviceRuntime:
    try:
        return state.devices.get(instance_id)
    except KeyError:
        raise err(404, "not_found", f"Instância {instance_id} não existe.") from None


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

    asyncio.create_task(_go())
    return {"accepted": True, "stop_emulators": stop_emulators}


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
        elif action == "start":
            await d.start_instance(rt)
        elif action == "stop":
            await d.stop_instance(rt)
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


def _precheck(rt: DeviceRuntime, action: str, body: InstanceActionBody) -> str | None:
    if action not in LIFECYCLE_ACTIONS:
        return "ação desconhecida"
    if action == "reset" and not body.confirm:
        return "o reset apaga dados e sessão do aparelho; envie confirm=true"
    if action in ("stop", "restart", "reset", "install_apk", "open_app", "home", "back", "recents") and rt.control.value == "ai":
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
