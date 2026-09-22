"""Rotas REST + WebSocket. Ver docs/api-contract.md."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import unicodedata
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .automation.driver import DriverError, DriverTimeout
from .commands.store import command_dto
from .db import Row, dumps, loads
from .devices.adb import AdbError
from .devices.manager import ControlError, DeviceRuntime, InstanceBusy
from .devices.verbs import SO_ADB, motivo_nao_suportado, verbos_suportados
from .models import (ApprovalBatchBody, ApprovalDecision, AppDTO, AppInput, AppPatch, BulkBody, CapabilityDTO,
                     CommandState, InstanceActionBody,
                     InstancePatch, InstanceState, ProfilePolicyPatch,
                     AppInstallBody, AppVerifyBody, CredentialUpdate, MemoryCreate, PersonaCreate, PersonaPatch,
                     PersonaPreviewBody, ProfileCreate, ProfilePatch,
                     ReleaseChannel, ReleaseImportBody, ReleaseLifecycleBody, ReleaseState, SessionStatus,
                     SignatureApprovalBody, StoreBody, WorkerEnrollBody, WorkerMaintenanceBody, WorkerRemoveBody,
                     ManualInput, ReleaseBody, ResolveBody, RunCreate)
from .security import access as acesso           # o módulo, não os nomes: `LOOPBACK_DE_TESTE` é injetado em tempo
from .security import local_secret               # de execução e um `from ... import` congelaria o valor antigo
from .security.access import avaliar, publicos_de
from .state import AppState
from .workers.protocol import Ack, Dispatch, Heartbeat, Hello, Progress, Refused, Result, parse_upstream
from .workers.portao import BLOQUEIO_S
from .workers.registry import INSCRICAO_TTL_S, WorkerError
from .planning.capabilities import load_catalog
from .releases.catalog import ReleaseValidationError
from .social.service import SocialError
from .taskqueue.service import RunError
from .util import iso_in, new_command_id, new_token, now_iso

log = logging.getLogger("poc.api")
router = APIRouter(prefix="/api")

#: Router SÓ do canal do worker, separado do resto de propósito. É o que o listener dedicado do túnel serve
#: (`main.create_worker_app`): nele não existe rota REST nenhuma, então uma requisição que chegue pela porta do
#: túnel encontra 404 em vez da API inteira. O app principal inclui os dois, para o modo em que o worker fala
#: direto com a porta de rede do central.
worker_router = APIRouter(prefix="/api")

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


def app_dto(r: Row) -> AppDTO:
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
            "runs": [s.repo.run_summary(r) for r in runs], "settings": s.settings.get(),
            "workers": s.workers.dtos()}


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
    return st(request).ai_status()


@router.post("/admin/shutdown", status_code=202)
async def shutdown(request: Request, stop_emulators: bool = False) -> Any:
    """Encerramento gracioso (usado por scripts/stop.ps1): fecha sessões, para o Appium iniciado por nós e,
    se pedido, os emuladores que ESTE projeto iniciou.

    Duas trancas, porque a primeira sozinha deixou de valer. O par `127.0.0.1` significava "esta máquina" até o
    túnel SSH reverso existir: toda conexão que chega pelo `-R` tem par de loopback **de verdade**, então qualquer
    processo da máquina do worker derrubava o central sem credencial. A segunda tranca é o segredo local de
    `data/shutdown.token`, que prova acesso ao disco desta máquina — ver `security/local_secret.py`.
    """
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise err(403, "forbidden", "Apenas chamadas locais.")
    s = st(request)
    if not local_secret.confere(s.cfg.data_dir, request.headers.get(local_secret.CABECALHO)):
        # Registrado porque uma tentativa de desligar o parque é coisa que o operador tem de ver — e o segredo
        # recebido NÃO entra no evento.
        s.bus.emit("log", "Pedido de encerramento recusado: segredo local ausente ou inválido.", level="warn")
        raise err(403, "forbidden", "Encerramento exige o segredo local de data/shutdown.token.")
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
    # `datetime('now', ?)` é aritmética de data do SQLite. As colunas guardam ISO-8601, que ordena
    # lexicograficamente, então o corte calculado em Python compara igual nos dois dialetos.
    where, params = ("run_id=?", (run_id,)) if run_id else ("ts >= ?", (iso_in(-days * 86400),))
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
        + (" AND run_id=?" if run_id else " AND finished_at >= ?") + " GROUP BY 1", params)
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


@router.get("/instagram/profiles/{profile_id}/avatar")
async def profile_avatar(request: Request, profile_id: str) -> Any:
    """Foto do perfil. 404 quando não há — o portal cai nas iniciais sozinho, sem precisar de campo no DTO."""
    s = st(request)
    try:
        perfil = s.social.get_profile(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc
    # O nome do arquivo sai do id JÁ VALIDADO no banco, nunca do texto da URL: caminho não se monta com entrada crua.
    caminho = s.cfg.data_dir / "avatars" / f"{perfil.id}.jpg"
    if not caminho.is_file():
        raise err(404, "sem_foto", "Este perfil não tem foto cadastrada.")
    return FileResponse(caminho, media_type="image/jpeg")


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
    """Relê do aparelho qual conta está aberta. Não digita senha: só observa.

    `observe_only` faz a promessa valer: num aparelho deslogado, para na tela de login em vez de autenticar.
    """
    return _start_session_job(request, profile_id, force_login=False, observe_only=True,
                              label="verificação da conta")


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


def _start_session_job(request: Request, profile_id: str, *, force_login: bool, label: str,
                       observe_only: bool = False) -> Any:
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
        rt, lambda: s.instagram.ensure_session(rt, profile_id, force_login=force_login,
                                               observe_only=observe_only), label=label)
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
                          bindings=list(c.bindings), optional_bindings=list(c.optional_bindings))
            for c in catalog.offered]


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
                         run_id: str | None = None, limit: int = 50) -> Any:
    """`run_id` junta os textos de uma execução — um por perfil — para serem lidos e decididos de uma vez."""
    return st(request).approval_service.list(status=status or None, profile_id=profile_id, run_id=run_id,
                                             limit=min(max(limit, 1), 200))


@router.post("/approvals/decide")
async def decide_approvals(request: Request, body: ApprovalBatchBody) -> Any:
    """Decide várias aprovações. Cada uma é independente: uma recusada não impede as demais, e a resposta diz quais."""
    return st(request).approval_service.decide_many(body.decisions)


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
    if "worker_id" in data:
        novo = (data["worker_id"] or "").strip() or None
        if novo and s.db.one("SELECT id FROM workers WHERE id=?", (novo,)) is None:
            raise err(400, "unknown_worker", f"Worker '{novo}' não está inscrito. Inscreva-o antes de amarrar "
                                             "um aparelho a ele.")
        data["worker_id"] = novo
    if data:
        s.db.execute(f"UPDATE instances SET {', '.join(f'{k}=?' for k in data)} WHERE id=?", (*data.values(), instance_id))
    if "worker_id" in data:
        # O vínculo vale JÁ: sem isto, amarrar um aparelho exigia reiniciar o backend para o runtime reler a
        # coluna — e as capacidades do worker só apareceriam depois disso.
        rt.worker_id = data["worker_id"]
        rt.worker_verbs = s.workers.verbs_de(rt.worker_id) if rt.worker_id else None
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


def _app_for(s: AppState, rt: DeviceRuntime, app_id: str | None) -> Row:
    chosen = app_id or s.db.scalar("SELECT app_id FROM instances WHERE id=?", (rt.id,))
    row = s.db.one("SELECT * FROM apps WHERE id=?", (chosen,)) if chosen else None
    if row is None:
        raise err(400, "no_app", f"{rt.id}: nenhum app associado. Associe um app em Configuração → Instâncias e contas.")
    return row


def _publish_command(s: AppState, row: Row) -> None:
    """Todo estado de comando vai para a interface. Sem isto o desfecho existiria só no banco."""
    dto = command_dto(row)
    nivel = {CommandState.succeeded: "info", CommandState.uncertain: "warn"}.get(dto.state, "info")
    if dto.state in (CommandState.failed, CommandState.rejected):
        nivel = "error"
    s.bus.emit("command.updated", f"{dto.instance_id}: {dto.verb} — {dto.state.value}"
               + (f" ({dto.reason})" if dto.reason else ""),
               level=nivel, instance_id=dto.instance_id, data={"command": dto.model_dump()})


async def _do_action_no_worker(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody,
                               command_id: str) -> None:
    """Despacha o verbo para o agente da outra máquina e traduz o desfecho dele em estado de comando.

    A cerca (`fence`) vai no despacho e volta no resultado: worker que perdeu a autorização e voltou do limbo tem
    o resultado recusado, em vez de sobrescrever o presente.
    """
    linha = s.commands.get(command_id)
    cerca = int(linha["fence"]) if linha is not None else 0
    prazo = {"start": 540.0, "wake": 180.0, "restart": 600.0, "reset": 600.0, "create": 240.0,
             "hibernate": 400.0}.get(action, 300.0)
    msg = Dispatch(command_id=command_id, fence=cerca, verb=action, instance_id=rt.id, serial=rt.serial,
                   params={k: v for k, v in (body.model_dump() or {}).items() if v is not None
                           and k not in ("idempotency_key",)},
                   timeout_s=prazo)
    try:
        resultado = await s.workers.dispatch(rt.worker_id or "", msg)
        alvo = {"succeeded": CommandState.succeeded, "failed": CommandState.failed,
                "uncertain": CommandState.uncertain, "cancelled": CommandState.cancelled}[resultado.outcome]
        motivo, dados = resultado.reason, resultado.data
    except WorkerError as exc:
        # Falha ao ENVIAR é o único caso em que se pode afirmar que nada aconteceu no aparelho.
        alvo, motivo, dados = CommandState.failed, exc.message, None
    except Exception as exc:  # noqa: BLE001 - quebrou no meio do despacho: não se sabe se o worker agiu
        log.exception("despacho de %s para o worker %s", command_id, rt.worker_id)
        alvo, motivo, dados = CommandState.uncertain, f"erro no despacho ao worker: {exc}", None
    try:
        _publish_command(s, s.commands.transition(command_id, alvo, reason=motivo, result=dados,
                                                  worker_id=rt.worker_id))
    except Exception:  # noqa: BLE001 - comando já encerrado por outra via (cancelamento) não é erro
        log.warning("comando %s já tinha desfecho ao voltar do worker", command_id)


async def _do_action(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody, command_id: str) -> None:
    """Executa a ação dirigindo os estados do comando.

    O que mudou: antes toda exceção era engolida e virava um evento `log` que o frontend nem tratava — a ação
    recusada no fundo era indistinguível de sucesso. Agora cada desfecho é gravado no comando, e `uncertain` é
    reservado para o caso honesto: timeout, em que o efeito pode ter acontecido e ninguém sabe.
    """
    d = s.devices
    try:
        _publish_command(s, s.commands.transition(command_id, CommandState.running))
    except Exception:  # noqa: BLE001 - comando cancelado antes de começar não impede nada
        log.exception("comando %s não pôde entrar em running", command_id)
        return
    # Aparelho que vive em OUTRA máquina: o ciclo de vida vai para o agente dela. Os verbos de ADB continuam saindo
    # daqui pelo túnel, porque aquele caminho está provado e não exige o catálogo de APK do outro lado.
    if rt.worker_id and rt.worker_verbs and action in rt.worker_verbs and action not in SO_ADB:
        await _do_action_no_worker(s, rt, action, body, command_id)
        return
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
    except DriverTimeout as exc:
        # Não sabemos se o aparelho obedeceu: o comando não é repetido sozinho, e quem olhar vê "incerto".
        _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=str(exc)))
        return
    except HTTPException as exc:
        motivo = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(motivo)))
        return
    except (InstanceBusy, ValueError, AdbError, DriverError) as exc:
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(exc)))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("ação %s em %s", action, rt.id)
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(exc)))
        return
    _publish_command(s, s.commands.transition(command_id, CommandState.succeeded))


def s_android_hibernation(rt: DeviceRuntime) -> bool:
    return bool(rt.cfg.instance_android(rt.id).hibernation)


def _precheck(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody) -> str | None:
    if action not in LIFECYCLE_ACTIONS:
        return "ação desconhecida"
    if action == "reset" and not body.confirm:
        return "o reset apaga dados e sessão do aparelho; envie confirm=true"
    # Manutenção do worker: comando de painel para um aparelho hospedado por worker em manutenção é recusado antes
    # de qualquer outra checagem — a pessoa que ligou a manutenção espera que nada novo seja despachado. Só a
    # manutenção intercepta aqui; "não conectado"/"não inscrito" seguem para a checagem de capacidade abaixo, que
    # já tem mensagem própria (verbos declarados pelo worker via `rt.worker_verbs`).
    if rt.worker_id and (porque := s.workers.motivo_manutencao(rt.worker_id)) is not None:
        return porque
    # Capacidade primeiro: o que o aparelho NÃO consegue fazer é recusado com a explicação, antes de agendar.
    # Cobre a loja e o aparelho de outra máquina no mesmo lugar, para ação única e lote.
    if (porque := motivo_nao_suportado(rt, action)) is not None:
        return porque
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
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []
    comandos: list[dict[str, Any]] = []
    params = body.params or InstanceActionBody()
    if body.action not in LIFECYCLE_ACTIONS:
        raise err(400, "rejected", "ação desconhecida.")
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
        # A chave do lote inclui o aparelho: um comando por aparelho, e reenviar o lote não duplica nenhum deles.
        por_aparelho = InstanceActionBody(
            confirm=params.confirm, app_id=params.app_id,
            idempotency_key=f"{params.idempotency_key}:{iid}" if params.idempotency_key else None)
        row, repetido = _abrir_comando(s, iid, body.action, por_aparelho)
        comandos.append({"id": iid, "command_id": row["id"], "deduplicated": repetido})
        if repetido:
            accepted.append(iid)
            continue
        why = _precheck(s, rt, body.action, por_aparelho)
        if why:
            _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=why))
            rejected.append({"id": iid, "reason": why, "command_id": row["id"]})
            continue
        _publish_command(s, s.commands.transition(row["id"], CommandState.dispatched))
        asyncio.create_task(_do_action(s, rt, body.action, por_aparelho, row["id"]))
        accepted.append(iid)
    # `accepted` continua sendo lista de ids (contrato antigo, intacto); `commands` é o acréscimo rastreável.
    return {"accepted": accepted, "rejected": rejected, "commands": comandos}


def _abrir_comando(s: AppState, instance_id: str, action: str, params: InstanceActionBody) -> tuple[Row, bool]:
    chave = params.idempotency_key or f"{instance_id}:{action}:{new_token()}"
    return s.commands.create(command_id=new_command_id(), instance_id=instance_id, verb=action,
                             idempotency_key=chave, requested_by="panel",
                             params={"app_id": params.app_id} if params.app_id else None)


@router.post("/instances/{instance_id}/actions/{action}", status_code=202)
async def instance_action(request: Request, instance_id: str, action: str, body: InstanceActionBody | None = None) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    params = body or InstanceActionBody()
    # Verbo inexistente não merece registro: não é tentativa de operar o aparelho, é chamada malformada.
    if action not in LIFECYCLE_ACTIONS:
        raise err(400, "rejected", f"{instance_id}: ação desconhecida.")
    row, repetido = _abrir_comando(s, instance_id, action, params)
    if repetido:
        # Mesma chave: devolve o comando original. Reenviar não age duas vezes.
        return {"command_id": row["id"], "state": row["state"], "deduplicated": True}
    why = _precheck(s, rt, action, params)
    if why:
        # A recusa fica no histórico do aparelho com o motivo, em vez de virar um evento que ninguém mostra.
        _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=why))
        raise err(409, "rejected", f"{instance_id}: {why}.", command_id=row["id"])
    if action in ("install_apk", "open_app"):
        try:
            _app_for(s, rt, params.app_id)      # valida antes de despachar
        except HTTPException as exc:
            motivo = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
            _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=str(motivo)))
            raise
    _publish_command(s, s.commands.transition(row["id"], CommandState.dispatched))
    asyncio.create_task(_do_action(s, rt, action, params, row["id"]))
    return {"command_id": row["id"], "state": CommandState.dispatched.value, "deduplicated": False}


@router.get("/commands/{command_id}")
async def get_command(request: Request, command_id: str) -> Any:
    row = st(request).commands.get(command_id)
    if row is None:
        raise err(404, "not_found", f"Comando {command_id} não existe.")
    return command_dto(row)


@router.get("/commands")
async def list_commands(request: Request, instance_id: str | None = None,
                        limit: int = Query(50, ge=1, le=200)) -> Any:
    return [command_dto(r) for r in st(request).commands.recent(instance_id, limit)]


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
    """Fluxo de eventos do painel, com repetição de histórico.

    **Confere o acesso aqui, e não no middleware, porque middleware não vale para WebSocket**: o
    `BaseHTTPMiddleware` do Starlette devolve o controle sem olhar quando o scope não é `http`. Enquanto o backend
    só atendia `127.0.0.1` isso era inofensivo; com `server.host` num endereço de rede, sem esta conferência o
    histórico inteiro — estados de aparelho, execuções, desfecho de comando — ficaria legível para quem estivesse na
    rede. A recusa é ANTES do `accept()`, então o cliente recebe a negativa no próprio handshake HTTP.
    """
    s: AppState = websocket.app.state.poc
    recusa = avaliar(par=websocket.client.host if websocket.client else None,
                     host=websocket.headers.get("host"), authorization=websocket.headers.get("authorization"),
                     publicos=publicos_de(s.cfg), token=s.cfg.api_token)
    if recusa is not None:
        await websocket.close(code=4401 if recusa == "unauthorized" else 4403)
        return
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


# ====================================================================== workers
@router.get("/workers")
async def list_workers(request: Request) -> Any:
    return st(request).workers.dtos()


@router.get("/workers/{worker_id}")
async def get_worker(request: Request, worker_id: str) -> Any:
    s = st(request)
    row = s.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
    if row is None:
        raise err(404, "not_found", f"Worker {worker_id} não existe.")
    return s.workers.dto(row)


@router.post("/workers/enroll", status_code=201)
async def enroll_worker(request: Request, body: WorkerEnrollBody | None = None) -> Any:
    """Gera um token de inscrição de USO ÚNICO e prazo curto.

    O token em claro aparece nesta resposta e nunca mais: só o hash é guardado. É o que responde "ao configurado
    aqui, o servidor principal tem acesso" sem ninguém digitar credencial permanente numa máquina nova.
    """
    s = st(request)
    token = s.workers.criar_inscricao((body or WorkerEnrollBody()).label)
    s.bus.emit("log", "Token de inscrição de worker gerado (uso único, validade de 1 h).")
    return {"enrollment_token": token, "expires_in_s": int(INSCRICAO_TTL_S)}


@router.post("/workers/{worker_id}/maintenance")
async def worker_maintenance(request: Request, worker_id: str, body: WorkerMaintenanceBody) -> Any:
    """Manutenção suspende NOVAS atribuições e não derruba o que já está em voo."""
    s = st(request)
    try:
        row = s.workers.set_maintenance(worker_id, body.on)
    except WorkerError as exc:
        raise err(404 if exc.code == "not_found" else 409, exc.code, exc.message) from exc
    return s.workers.dto(row)


@router.delete("/workers/{worker_id}", status_code=200)
async def remove_worker(request: Request, worker_id: str, body: WorkerRemoveBody | None = None) -> Any:
    """Fecha o procedimento que `docs/worker.md` já prometia: sem isto, um worker sem credencial ficava trancado
    do lado de fora para sempre — não dava para reinscrever o mesmo id nem apagar o registro sem editar o banco."""
    s = st(request)
    try:
        s.workers.remove(worker_id, force=(body or WorkerRemoveBody()).force)
    except WorkerError as exc:
        raise err(404 if exc.code == "not_found" else 409, exc.code, exc.message) from exc
    # O banco já desamarrou (UPDATE instances SET worker_id=NULL); o runtime em memória precisa do mesmo —
    # senão o painel segue mostrando o aparelho preso a um worker que não existe mais até reiniciar o backend.
    for rt in s.devices.devices.values():
        if rt.worker_id == worker_id:
            rt.worker_id = None
            rt.worker_verbs = None
            s.devices.publish(rt, f"{rt.id}: worker '{worker_id}' foi removido; aparelho ficou sem dono")
    s.bus.emit("worker.removed", f"Worker {worker_id} removido do painel.", data={"worker_id": worker_id})
    return {"ok": True, "worker_id": worker_id}


@router.post("/workers/{worker_id}/rotate-credential")
async def rotate_worker_credential(request: Request, worker_id: str) -> Any:
    """Máquina comprometida: a credencial velha para de servir NA HORA, e esta resposta traz a nova uma única vez."""
    s = st(request)
    try:
        token = s.workers.rotate_credential(worker_id)
    except WorkerError as exc:
        raise err(404 if exc.code == "not_found" else 409, exc.code, exc.message) from exc
    s.bus.emit("log", f"Credencial do worker {worker_id} rotacionada no painel.", level="warn")
    return {"credential": token}


#: Prazo do `hello`. Era 30 s, e 30 s de socket segurado por quem não provou nada é o custo que a negação de
#: serviço comprava. O agente monta o `hello` sem sondar aparelho (`worker/agent.py`), então o que sobra é rede:
#: 10 s é folgado para isso e corta o custo do abuso por três.
HELLO_TIMEOUT_S = 10.0

#: Teto da PRIMEIRA mensagem — a única que chega sem credencial. O `ws_max_size` do uvicorn (16 MiB por padrão)
#: vale para a conexão inteira e não dá para baixar só neste canal sem baixar também o do painel, então o teto do
#: que ainda não foi autenticado é aplicado aqui. Um `hello` real com 64 aparelhos não passa de alguns KB.
HELLO_MAX_BYTES = 32 * 1024


@worker_router.websocket("/worker/ws")
async def worker_ws(websocket: WebSocket) -> None:
    """Canal do worker. **É o worker que liga para cá** — atravessa NAT sem abrir porta na casa de ninguém.

    Autenticação na primeira mensagem: token de inscrição (uma vez, e volta a credencial permanente) ou a
    credencial. Nada de segredo em query string, que acabaria em log de proxy.

    Mora num router PRÓPRIO (`worker_router`) porque é a única rota que o listener dedicado do túnel serve —
    `main.create_worker_app`. Enquanto ela morava junto com o resto, apontar o `-R` para a porta do backend
    entregava a API inteira, sem credencial, a qualquer processo da máquina do worker.

    Quatro conferências ANTES de `accept()`, na ordem do mais barato para o mais caro:

    1. **IP bloqueado ou com handshakes demais pendentes** → fecha sem aceitar. Ver `workers/portao.py`.
    2. **`Host`** contra loopback + `public_hosts`, a mesma defesa de DNS rebinding do resto da API. Note que
       loopback CONTINUA valendo aqui: pelo túnel o agente chega com `Host: 127.0.0.1:18000`, e é assim mesmo.
    3. **Tamanho e prazo do `hello`**, porque é o único byte que entra sem credencial.
    4. **A credencial**, e a recusa vira evento persistido com o worker declarado e o IP.
    """
    s: AppState = websocket.app.state.poc
    ip = websocket.client.host if websocket.client else "?"
    portao = s.workers.portao
    if not portao.entrar(ip):
        # Antes do accept: o cliente recebe a negativa no próprio handshake HTTP e não custa um socket aberto.
        await websocket.close(code=4429)
        return
    try:
        nome = acesso.host_de(websocket.headers.get("host"))
        if nome not in acesso.LOOPBACK and nome not in acesso.LOOPBACK_DE_TESTE and nome not in publicos_de(s.cfg):
            s.bus.emit("worker.refused", f"Conexão de worker recusada: host '{nome}' não está em "
                                         f"server.public_hosts (origem {ip}).", level="warn",
                       data={"reason": "forbidden_host", "ip": ip})
            await websocket.close(code=4403)
            return
        await websocket.accept()
        worker_id: str | None = None
        try:
            bruto_hello = await asyncio.wait_for(websocket.receive_text(), timeout=HELLO_TIMEOUT_S)
            if len(bruto_hello.encode("utf-8", "surrogatepass")) > HELLO_MAX_BYTES:
                raise ValueError("hello grande demais")
            primeira = loads(bruto_hello)
            if not isinstance(primeira, dict):
                raise ValueError("hello não é um objeto")
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, TypeError):
            await websocket.close(code=4400)
            return
        try:
            hello = Hello.model_validate(primeira.get("hello") or {})
            credencial = s.workers.autenticar(hello, token=primeira.get("token"),
                                              enrollment=primeira.get("enrollment_token"))
        except WorkerError as exc:
            # O que faltava: a tentativa recusada não deixava rastro nenhum: nem log, nem evento. Quem tentasse se
            # passar por um worker passava despercebido. `worker.refused` é persistido como qualquer evento, com o
            # id DECLARADO (não confirmado — é o que o cliente disse ser) e o IP. Nada do segredo recebido entra.
            declarado = (primeira.get("hello") or {}).get("worker_id") if isinstance(primeira.get("hello"), dict) else None
            bloqueou = portao.falhou(ip) if exc.code in ("bad_credential", "not_enrolled") else False
            s.bus.emit("worker.refused",
                       f"Conexão de worker recusada ({exc.code}): id declarado '{declarado or '?'}', origem {ip}."
                       + (f" Novas tentativas deste IP serão recusadas por {int(BLOQUEIO_S)} s." if bloqueou else ""),
                       level="warn", data={"reason": exc.code, "ip": ip, "worker_id": declarado})
            await websocket.send_json(Refused(code=exc.code, message=exc.message).model_dump())
            await websocket.close(code=4401)
            return
        except Exception as exc:  # noqa: BLE001 - `hello` malformado é recusa explicada, não socket fechado calado
            await websocket.send_json(Refused(code="bad_hello", message=f"hello inválido: {exc}").model_dump())
            await websocket.close(code=4400)
            return

        portao.perdoou(ip)
        worker_id = hello.worker_id
        await _worker_canal(s, websocket, hello, credencial)
    finally:
        portao.sair(ip)


async def _worker_canal(s: AppState, websocket: WebSocket, hello: Hello, credencial: str) -> None:
    """O canal já autenticado. Separado do handshake para o portão acima caber numa tela."""
    worker_id = hello.worker_id

    async def send(payload: dict[str, Any]) -> None:
        await websocket.send_json(payload)

    link = s.workers.attach(worker_id, send)
    # O aparelho daquele worker passa a aceitar o ciclo de vida que o agente declarou.
    s.devices.bind_worker(worker_id, hello.verbs)
    esperados = {r["id"]: r["avd_name"] for r in
                 s.db.query("SELECT id, avd_name FROM instances WHERE worker_id=?", (worker_id,))}
    bem_vindo = s.workers.welcome(esperados).model_dump()
    if credencial:
        # Só aqui, e uma única vez: a credencial em claro não é guardada nem repetida.
        bem_vindo["credential"] = credencial
    await websocket.send_json(bem_vindo)
    s.bus.emit("log", f"Worker {hello.name} conectou ({hello.os}, agente {hello.agent_version}, "
                      f"{hello.max_slots} vaga(s), Appium {hello.appium_mode}).")
    try:
        while True:
            bruto = await websocket.receive_json()
            try:
                msg = parse_upstream(bruto)
            except ValueError as exc:
                log.warning("worker %s: %s", worker_id, exc)
                continue
            if isinstance(msg, Heartbeat):
                s.workers.on_heartbeat(worker_id, msg)
            elif isinstance(msg, Ack):
                s.workers.on_ack(worker_id, msg.command_id)
            elif isinstance(msg, Progress):
                s.bus.emit("log", f"{msg.command_id}: {msg.message}")
            elif isinstance(msg, Result):
                s.workers.on_result(worker_id, msg, fence=bruto.get("fence"))
            elif isinstance(msg, Hello):
                # Re-declaração: o worker mudou de inventário ou de capacidade sem reconectar. Vale como batida,
                # e não repete autenticação — quem já está dentro do canal não se reautentica a cada mensagem.
                s.workers.on_heartbeat(worker_id, Heartbeat(devices=msg.devices, resources=msg.resources))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("canal do worker %s", worker_id)
    finally:
        s.workers.detach(worker_id, "conexão encerrada")
        # Sem agente do outro lado, o aparelho volta a aceitar só o que o transporte alcança.
        s.devices.bind_worker(worker_id, None)
        s.bus.emit("log", f"Worker {hello.name} desconectou.", level="warn")
