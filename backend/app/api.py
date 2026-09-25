"""Rotas REST + WebSocket. Ver docs/api-contract.md."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import threading
import unicodedata
from time import monotonic
from pathlib import PurePosixPath
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from .automation.appium_driver import appium_no_ar
from .automation.driver import DriverError, DriverTimeout
from .commands.outbox import PENDING as OUTBOX_PENDING
from .storage import DISK, DiskStorage, Storage, StorageError
from .commands.states import COMMAND_OPEN, COMMAND_UNSETTLED, InvalidCommandTransition
from .commands.reconciler import VERIFICAVEL_POR_ESTADO, reconciliar_incertos, verificar_comando
from .commands.store import command_dto, publicar_comando
from .db import Row, dumps, loads
from .devices.adb import AdbError, AdbTimeout
from .devices.avd import AvdError
from .devices.manager import DESEJO_DO_VERBO, ControlError, DeviceRuntime, InstanceBusy
from .devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from .devices.verbs import (PRAZO_PADRAO_S, PRAZO_POR_VERBO, SO_ADB, VERBOS_QUE_ESPERAM_O_BOOT,
                            motivo_nao_suportado, verbos_suportados)
from .models import (DistributeSpec, ServerLimitsDTO, ServerLimitsPatch, ServerLimitValues,
                     AdoptDeviceBody, ApprovalBatchBody, ApprovalDecision, AppDTO, AppInput, AppPatch, BulkBody,
                     CapabilityDTO, WorkerDeviceProposal,
                     CommandCancelBody, CommandResolveBody, CommandState, InstanceActionBody,
                     InstancePatch, InstanceState, TrainingSaveBody, TrainingStartBody, PolicyGroupCreate, PolicyGroupPatch, ProfileAccountCreate,
                     ProfileAccountPatch, ProfilePolicyPatch,
                     AppInstallBody, AppVerifyBody, CredentialUpdate, MemoryCreate, PersonaCreate, PersonaPatch,
                     PersonaPreviewBody, ProfileCreate, ProfilePatch,
                     ReleaseChannel, ReleaseImportBody, ReleaseLifecycleBody, ReleaseState, SessionStatus,
                     SignatureApprovalBody, StoreBody, WorkerEnrollBody, WorkerMaintenanceBody, WorkerRemoveBody,
                     LoginBody, ManualInput, PanelSessionInfo, ReleaseBody, ResolveBody, RunCreate)
from .planning import costs
from .security import access as acesso           # o módulo, não os nomes: `LOOPBACK_DE_TESTE` é injetado em tempo
from .security import local_secret               # de execução e um `from ... import` congelaria o valor antigo
from .security.access import avaliar, publicos_de
from .security.sessions import COOKIE, VALIDADE_S, NomeInvalido, normalizar_nome, operador_atual
from .state import AppState
from .workers.protocol import (MARCA_DE_FILA, Ack, Dispatch, Heartbeat, Hello, Progress, Refused, Result, ResultAck,
                               parse_upstream)
from .workers.portao import BLOQUEIO_S
from .workers.registry import INSCRICAO_TTL_S, WorkerError, WorkerLink
from .version import agent_version
from .planning.capabilities import load_catalog
from .planning.catalog import package_of_provider, registered
from .releases.catalog import ReleaseValidationError
from .releases.service import InstalacaoIncerta
from .social.service import SocialError
from .taskqueue.repository import CONTENT_TYPES
from .taskqueue.service import RunError
from .util import iso_in, new_command_id, new_token, now_iso

from .devices.verbs import sem_hibernacao
from .util import now, parse_iso

log = logging.getLogger("poc.api")
router = APIRouter(prefix="/api")

#: Router SÓ do canal do worker, separado do resto de propósito. É o que o listener dedicado do túnel serve
#: (`main.create_worker_app`): nele não existe rota REST nenhuma, então uma requisição que chegue pela porta do
#: túnel encontra 404 em vez da API inteira. O app principal inclui os dois, para o modo em que o worker fala
#: direto com a porta de rede do central.
worker_router = APIRouter(prefix="/api")

LIFECYCLE_ACTIONS = {"create", "start", "stop", "hibernate", "wake", "restart", "reset", "install_apk", "open_app",
                     "home", "back", "recents"}

#: Verbos que DISPUTAM o aparelho: enquanto um deles estiver aberto, o próximo é recusado com 409 `device_busy`.
#: Teclas (`home`, `back`, `recents`) ficam de fora de propósito — apertar duas teclas seguidas não é duas
#: operações concorrentes no aparelho, e é do ciclo de vida que o aceite 9 trata.
VERBOS_EXCLUSIVOS = {"create", "start", "stop", "hibernate", "wake", "restart", "reset", "install_apk", "open_app"}

#: Verbos que MEXEM no aparelho de um jeito que não se desfaz olhando. Com o inventário divergente (item 4.5),
#: qualquer um deles pode agir no aparelho errado — é exatamente o dano que o achado #47 descreve como latente.
VERBOS_DESTRUTIVOS = {"reset", "stop", "restart", "hibernate", "install_apk", "create"}


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


def quem(request: Request | None = None, informado: str | None = None) -> str:
    """Quem está pedindo, na ordem em que uma trilha de auditoria precisa que seja.

    **A sessão vence o que o cliente diz.** `requested_by` sempre foi um campo do CORPO: qualquer chamador
    escrevia ali o nome que quisesse, e era o único "quem" que o banco guardava. Com sessão, o nome vem do
    cookie — que o JavaScript da página não lê e o navegador não deixa forjar — e o campo do corpo vira o que
    sempre deveria ter sido: um rótulo de quem chama a API sem sessão (script, ferramenta, worker).

    `panel` continua existindo como último recurso, e agora quer dizer o que parecia querer: "veio do painel, e
    ninguém se identificou".
    """
    da_sessao = getattr(request.state, "operador", None) if request is not None else None
    return da_sessao or operador_atual() or (informado or "").strip() or "panel"


# ====================================================================== sessão do painel
#: Caminhos que o `main.guarda` deixa responder ANTES de haver credencial — senão a tela de login levaria 401 no
#: próprio pedido que a faria aparecer. Constante aqui, ao lado das rotas, para não divergir delas.
ROTAS_DE_SESSAO = frozenset({"/api/login", "/api/logout", "/api/session"})


def _gravar_cookie(response: Response, s: AppState, token: str) -> None:
    """`HttpOnly` (XSS não leva o segredo), `SameSite=Strict` (outro site não consegue usar a sessão nem para
    um GET), `Path=/api` (frame, evidência, avatar e o WebSocket estão todos ali) e `Secure` só quando há TLS —
    num painel servido por HTTP no loopback, `Secure` faria o navegador descartar o cookie em silêncio."""
    response.set_cookie(COOKIE, token, max_age=VALIDADE_S, httponly=True, samesite="strict",
                        secure=s.cfg.tls_ativo, path="/api")


@router.get("/session")
async def sessao_atual(request: Request) -> Any:
    """Quem está logado NESTE navegador, e o que esta origem exige para logar.

    O painel pergunta isto antes de qualquer outra coisa. Não dá para esperar um 401 para saber que falta
    login: no loopback — que é como o parque roda hoje — 401 nunca acontece, e a auditoria continuaria dizendo
    `panel` para sempre.
    """
    s = st(request)
    atual = s.sessions.atual(request.cookies.get(COOKIE))
    return PanelSessionInfo(operator=atual["operator"] if atual else None,
                       token_required=bool(getattr(request.state, "credencial_exigida", False)),
                       expires_at=atual["expires_at"] if atual else None).model_dump(mode="json")


@router.post("/login")
async def login(request: Request, response: Response, body: LoginBody) -> Any:
    """Troca "meu nome (+ o segredo, quando esta origem o exige)" por um cookie de sessão.

    O token só é cobrado de quem ainda NÃO passaria pelo portão — é `request.state.credencial_exigida`, o
    veredito que o middleware já calculou. Do loopback, onde quem chama já tem o banco e o adb na mão, cobrar
    segredo não protegeria nada e tiraria o login de quem só quer aparecer na trilha com o próprio nome.
    """
    s = st(request)
    agora = monotonic()
    exige = bool(getattr(request.state, "credencial_exigida", False))
    if exige:
        # A trava só vale para quem apresenta segredo. Fora do `if`, oito chutes vindos da rede trancariam
        # também o login do loopback — que não usa token nenhum —, e aí o ataque não rouba nada: derruba.
        if (espera := s.portao_de_login.segundos_de_espera(agora)) > 0:
            raise err(429, "too_many_attempts",
                      f"Tentativas de login demais. Espere {int(espera) + 1} s e tente de novo.")
        recebido = body.token.get_secret_value() if body.token else ""
        # `token_ok` compara em tempo constante e só aceita o esquema Bearer; reaproveitá-lo é o que impede a
        # comparação ingênua de voltar por esta porta.
        if not acesso.token_ok(f"Bearer {recebido}", s.cfg.api_token):
            s.portao_de_login.registrar_falha(agora)
            # Sem dizer o que estava errado: nome inexistente e token errado devolvem a MESMA coisa.
            raise err(401, "invalid_credentials", "Credencial inválida.")
    try:
        token, expira = s.sessions.abrir(body.operator)
    except NomeInvalido as exc:
        raise err(422, "invalid_operator", str(exc)) from None
    s.portao_de_login.registrar_acerto()
    _gravar_cookie(response, s, token)
    nome = normalizar_nome(body.operator)
    s.bus.emit("log", f"{nome} entrou no painel", level="info")
    return PanelSessionInfo(operator=nome, token_required=exige, expires_at=expira).model_dump(mode="json")


@router.post("/logout")
async def logout(request: Request, response: Response) -> Any:
    """Revoga a sessão deste navegador. Sair duas vezes não é erro — e o cookie some nas duas."""
    s = st(request)
    encerrada = s.sessions.encerrar(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict", secure=s.cfg.tls_ativo)
    return {"ended": encerrada}


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
            "workers": s.workers.dtos(),
            # Comandos em voo E os que acabaram sem desfecho. Os primeiros, porque recarregar a página no meio
            # de um `start` remoto fazia o painel esquecer que o aparelho está ocupado e reoferecer o botão — o
            # clique duplo que o aceite 9 proíbe. Os segundos, porque um `uncertain` só existia enquanto o toast
            # durava: depois de um F5 (ou no dia seguinte, que é o caso vivo) ele sumia da tela e continuava
            # aberto no banco. Um por aparelho, o mais recente — é o que o cartão mostra.
            "commands": [command_dto(r) for r in _ultimo_por_aparelho(s.commands.open_commands()
                                                                     + s.commands.unsettled())]}


def _ultimo_por_aparelho(linhas: list[Row]) -> list[Row]:
    """Um comando por aparelho: o de `created_at` mais recente. O store guarda `lastCommand[instance_id]`, então
    mandar dois do mesmo aparelho faria a hidratação depender da ordem da lista."""
    melhor: dict[str, Row] = {}
    for linha in linhas:
        atual = melhor.get(linha["instance_id"])
        if atual is None or str(atual["created_at"]) <= str(linha["created_at"]):
            melhor[linha["instance_id"]] = linha
    return list(melhor.values())


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
    """Mantido como apelido: a conta de verdade vive em `planning/costs.py`, que é a MESMA usada pelo teto."""
    return costs.price_for(prices, model)


@router.get("/apps-overview")
async def apps_overview_route(request: Request, days: int = Query(7, ge=1, le=90)) -> Any:
    """Item 12.2: um resumo por aplicativo — contas, aparelhos, execuções, custo de IA, receitas, fluxos, versões."""
    from .apps_overview import apps_overview  # noqa: PLC0415
    return apps_overview(st(request), days)


@router.get("/apps/{app_id}/overview")
async def app_overview_route(request: Request, app_id: str, days: int = Query(30, ge=1, le=180)) -> Any:
    from .apps_overview import app_detail  # noqa: PLC0415
    detalhe = app_detail(st(request), app_id, days)
    if detalhe is None:
        raise err(404, "not_found", "Aplicativo não encontrado.")
    return detalhe


# ---------------------------------------------------------------- modo treinamento (itens 13.1–13.3)
def _training_error(exc: Any) -> HTTPException:
    return err(exc.status, exc.code, exc.message)


@router.post("/instances/{instance_id}/training", status_code=201)
async def start_training(request: Request, instance_id: str, body: TrainingStartBody) -> Any:
    from .training.recorder import TrainingError  # noqa: PLC0415
    s = st(request)
    try:
        s.devices.get(instance_id)
    except KeyError as exc:
        raise err(404, "not_found", "Instância não encontrada.") from exc
    try:
        return s.training.start(instance_id, intent=body.intent, lease_id=body.lease_id, app_id=body.app_id,
                                operator=getattr(request.state, "operator", None))
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.get("/training")
async def list_training(request: Request, instance_id: str | None = None, limit: int = Query(30, ge=1, le=200)) -> Any:
    return st(request).training.list(instance_id=instance_id, limit=limit)


@router.get("/training/{session_id}")
async def get_training(request: Request, session_id: str) -> Any:
    from .training.recorder import TrainingError  # noqa: PLC0415
    try:
        return st(request).training.get(session_id)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/stop")
async def stop_training(request: Request, session_id: str) -> Any:
    from .training.recorder import TrainingError  # noqa: PLC0415
    try:
        return st(request).training.stop(session_id)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/propose")
async def propose_training(request: Request, session_id: str) -> Any:
    """A IA lê a gravação e propõe a habilidade (comando com parâmetros, etapas, descartes). Uma chamada do modelo
    do planejador; a proposta fica guardada para a pessoa revisar."""
    from .planning.provider import AIError  # noqa: PLC0415
    from .training.recorder import TrainingError  # noqa: PLC0415
    try:
        return await st(request).skills.propose(session_id)
    except TrainingError as exc:
        raise _training_error(exc) from exc
    except AIError as exc:
        raise err(502, "ai_error", f"A IA não conseguiu propor a habilidade: {exc}") from exc


@router.post("/training/{session_id}/save")
async def save_training(request: Request, session_id: str, body: TrainingSaveBody) -> Any:
    from .training.recorder import TrainingError  # noqa: PLC0415
    try:
        return await st(request).skills.save(session_id, proposal=body.proposal, profile_ids=body.profile_ids,
                                             group_ids=body.group_ids)
    except TrainingError as exc:
        raise _training_error(exc) from exc


@router.post("/training/{session_id}/discard")
async def discard_training(request: Request, session_id: str) -> Any:
    from .training.recorder import TrainingError  # noqa: PLC0415
    try:
        return st(request).training.stop(session_id, discard=True)
    except TrainingError as exc:
        raise _training_error(exc) from exc


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
    # Achado #101: por tipo de erro (recusa, orçamento, crédito, credencial…) — sem isto, saber que 7 erros de
    # verificação eram HTTP 500 do provedor exigia casar horário de `ai_calls` com o log do backend à mão.
    erros = s.db.query(
        f"SELECT COALESCE(error_kind,'error') error_kind, COUNT(*) n FROM ai_calls WHERE {where} AND ok=0"
        f" GROUP BY 1", params)
    n_obj = len(per_obj)
    # Item 7.2: quantas chamadas foram servidas por um fallback, e por quê. Antes a troca só aparecia como um
    # modelo estranho numa linha de custo — e "respondeu o fallback" era indistinguível de "estava assim".
    trocas = s.db.query(
        f"SELECT fallback, requested_model, model, COUNT(*) calls FROM ai_calls WHERE {where}"
        f" AND fallback IS NOT NULL GROUP BY fallback, requested_model, model", params)
    return {"scope": {"run_id": run_id, "days": None if run_id else days}, "groups": groups, "total_usd": round(total, 4),
            "fallbacks": [dict(r) for r in trocas],
            "spend_today_usd": costs.spent_today_usd(s.db, prices),
            "objectives_with_ai": n_obj, "calls_per_objective": round(sum(o["calls"] for o in per_obj) / n_obj, 1) if n_obj else 0,
            "usd_per_objective": round(total / n_obj, 4) if n_obj else 0,
            "steps_driven_by": {r["driven_by"]: r["n"] for r in driven},
            "errors_by_kind": {r["error_kind"]: r["n"] for r in erros},
            # Só de linhas com ALGUMA chamada OK (achado #101): um grupo 100% erro não tem custo a calcular, e
            # entrar aqui é o que fazia o pseudo-modelo '(erro)' virar um "Total parcial" que não existia — chamada
            # com erro não é chamada que faltou preço.
            "unpriced_models": sorted({r["model"] for r in rows
                                       if r["calls"] > r["errors"] and _price(prices, r["model"]) is None}),
            # Cache de prompt que não bate em DECISÃO é defeito, não escolha: toda decisão leva as 15 ferramentas
            # (prefixo ≈ 6 mil tokens, acima do mínimo de qualquer modelo). Verificação fica de fora — o system
            # sozinho (≈ 1 mil tokens) fica legitimamente abaixo do mínimo do Sonnet/Haiku. Achado de 24/09: 46
            # decisões no Sonnet sem uma leitura de cache e ninguém viu, porque o relatório só somava.
            "cache_inativo": [{"model": r["model"], "calls": r["calls"]} for r in rows
                              if r["role"] == "decide" and (r["model"] or "").startswith("claude-")
                              and r["calls"] >= 10 and not (r["cache_read"] or 0) and not (r["cache_write"] or 0)]}


@router.get("/flows")
async def list_flows(request: Request) -> Any:
    return st(request).scheduler.flows.list()


@router.get("/flows/cobertura")
async def flows_coverage(request: Request) -> Any:
    """Cada fluxo com quantas etapas já têm receita ativa para a versão promovida do app: os "caminhos mapeados"
    do parque, e o custo de IA esperado ao repetir cada um (zero / parcial / total). Só leitura."""
    from .social.capacidades import cobertura_dos_fluxos  # noqa: PLC0415

    return cobertura_dos_fluxos(st(request))


@router.get("/flows/match")
async def flows_match(request: Request, command: str = Query(..., min_length=1)) -> Any:
    """Item 7.7 ("quanto vai custar?" do Osintgram): o comando digitado casa com um fluxo conhecido? Devolve a
    cobertura e a estimativa em US$ desse fluxo, ou `null` — sem fluxo casado não há o que estimar. Mesmo
    casamento que o planejamento usa (`FlowStore.match`), então a estimativa é do plano que REALMENTE rodaria."""
    from .social.capacidades import cobertura_do_fluxo  # noqa: PLC0415

    s = st(request)
    casado = s.scheduler.flows.match(command)
    if casado is None:
        return None
    row, _ = casado
    return cobertura_do_fluxo(s, row)


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
    # A chave sai do id JÁ VALIDADO no banco, nunca do texto da URL: chave não se monta com entrada crua.
    return _servir_do_storage(s.avatares, f"avatars/{perfil.id}.jpg", "image/jpeg",
                              ausente=("sem_foto", "Este perfil não tem foto cadastrada."))


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
    del profile
    # "Sair da conta" APAGA os dados do app: é a operação mais destrutiva desta tela e era a que menos registro
    # tinha. Agora é um comando, com id, desfecho e `uncertain` quando o adb não responde.
    return {**_despachar_trabalho(s, rt, "session.logout", lambda: _do_logout(s, rt, profile_id),
                                  label="logout do Instagram", params={"profile_id": profile_id}),
            "profile_id": profile_id}


async def _do_logout(s: AppState, rt: DeviceRuntime, profile_id: str) -> None:
    # O pacote do perfil vem do REGISTRO de aplicativos (quem provê a conta), não de `cfg.file.instagram`: é a
    # mesma resposta hoje, e deixa de ser um literal do núcleo quando houver um segundo app com conta.
    package = package_of_provider("instagram") or s.cfg.file.instagram.package
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
    verbo = "session.verify" if observe_only else "session.connect"
    return {**_despachar_trabalho(
        s, rt, verbo,
        lambda: s.instagram.ensure_session(rt, profile_id, force_login=force_login, observe_only=observe_only),
        label=label, params={"profile_id": profile_id}), "profile_id": profile_id}


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
async def list_memory(request: Request, profile_id: str, subject: str | None = None, limit: int = 100,
                      app_id: str | None = None) -> Any:
    try:
        return st(request).social.list_memories(profile_id, subject=subject, limit=min(max(limit, 1), 500),
                                                app_id=app_id or None)
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


@router.get("/instagram/profiles/{profile_id}/capacidades")
async def profile_capabilities(request: Request, profile_id: str) -> Any:
    """O que esta persona já fez e quanto disso roda sem IA — fluxos concluídos com cobertura de receitas,
    etapas por origem (receita / IA), interações confirmadas por tipo. Leitura pura, sem custo de modelo."""
    from .social.capacidades import capacidades_do_perfil  # noqa: PLC0415

    s = st(request)
    try:
        s.social.get_profile(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc
    return capacidades_do_perfil(s, profile_id)


@router.get("/instagram/profiles/{profile_id}/interactions")
async def list_interactions(request: Request, profile_id: str, counterparty: str | None = None,
                            thread_key: str | None = None, limit: int = 30, app_id: str | None = None) -> Any:
    try:
        return st(request).social.list_interactions(profile_id, counterparty=counterparty, thread_key=thread_key,
                                                    limit=min(max(limit, 1), 200), app_id=app_id or None)
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


@router.get("/app-catalog")
async def app_catalog(request: Request) -> Any:
    """Os aplicativos que o registro conhece: quem tem catálogo, quem provê conta, quem exige perfil.

    É o que a interface usa para deixar de assumir um pacote por omissão — a loja e as capacidades passam a
    perguntar "qual app?" em vez de cair no Instagram.
    """
    return [{"package": c.package, "name": c.name, "label": c.label, "has_catalog": c.has_catalog,
             "session_provider": c.session_provider, "needs_profile": c.needs_profile}
            for c in registered()]


@router.get("/capabilities")
async def list_capabilities(request: Request, package: str = Query(..., min_length=1)) -> Any:
    """Catálogo do app: o que o sistema sabe fazer, com efeito, risco e política padrão de cada ação.

    O pacote é OBRIGATÓRIO: enquanto ele tinha `com.instagram.android` por omissão, qualquer chamador que
    esquecesse de dizer o app recebia o catálogo do Instagram como se fosse o do app dele.
    """
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


# ---------------------------------------------------------------- contas do perfil por app (item 12.1)
@router.get("/instagram/profiles/{profile_id}/accounts")
async def list_profile_accounts(request: Request, profile_id: str) -> Any:
    try:
        return st(request).social.list_accounts(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.post("/instagram/profiles/{profile_id}/accounts", status_code=201)
async def add_profile_account(request: Request, profile_id: str, body: ProfileAccountCreate) -> Any:
    try:
        return st(request).social.add_account(profile_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.patch("/instagram/profiles/{profile_id}/accounts/{account_id}")
async def patch_profile_account(request: Request, profile_id: str, account_id: str, body: ProfileAccountPatch) -> Any:
    try:
        return st(request).social.update_account(profile_id, account_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.delete("/instagram/profiles/{profile_id}/accounts/{account_id}", status_code=204)
async def delete_profile_account(request: Request, profile_id: str, account_id: str) -> None:
    try:
        st(request).social.delete_account(profile_id, account_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.put("/instagram/profiles/{profile_id}/accounts/{account_id}/credential")
async def put_account_credential(request: Request, profile_id: str, account_id: str, body: CredentialUpdate) -> Any:
    try:
        return st(request).social.set_account_credential(profile_id, account_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


# ---------------------------------------------------------------- grupos de acesso (migração 036)
@router.get("/instagram/policy-groups")
async def list_policy_groups(request: Request) -> Any:
    return st(request).social.list_policy_groups()


@router.get("/instagram/policy-defaults")
async def policy_defaults(request: Request) -> Any:
    """Os limites-padrão (o que vale sem grupo e sem escolha própria) — o editor de grupo parte deles."""
    from .social.policy import DEFAULT_LIMITS  # noqa: PLC0415
    return {"limits": DEFAULT_LIMITS}


@router.post("/instagram/policy-groups", status_code=201)
async def create_policy_group(request: Request, body: PolicyGroupCreate) -> Any:
    try:
        return st(request).social.create_policy_group(body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.get("/instagram/policy-groups/{group_id}")
async def get_policy_group(request: Request, group_id: str) -> Any:
    try:
        return st(request).social.get_policy_group(group_id)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.put("/instagram/policy-groups/{group_id}")
async def put_policy_group(request: Request, group_id: str, body: PolicyGroupPatch) -> Any:
    try:
        return st(request).social.update_policy_group(group_id, body)
    except SocialError as exc:
        raise _social_error(exc) from exc


@router.delete("/instagram/policy-groups/{group_id}", status_code=204)
async def delete_policy_group(request: Request, group_id: str) -> None:
    try:
        st(request).social.delete_policy_group(group_id)
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


@router.get("/releases/{release_id}/icon")
async def release_icon(request: Request, release_id: str) -> Any:
    """O ícone do launcher extraído do próprio APK. 404 quando a release não tem ícone servível.

    É o que faz o catálogo mostrar o aplicativo em vez de mostrar uma string de pacote. `immutable`: a pasta da
    release é imutável por construção (o caminho vem do hash do conjunto), então o navegador pode guardá-lo.
    """
    dados = st(request).releases.icon_bytes(release_id)
    if dados is None:
        raise err(404, "sem_icone", "Esta versão não tem ícone extraído.")
    conteudo, tipo = dados
    return Response(content=conteudo, media_type=tipo, headers={"Cache-Control": "public, max-age=86400, immutable"})


@router.get("/releases/{release_id}/targets")
async def release_targets(request: Request, release_id: str) -> Any:
    """Para onde ESTA versão pode ir, aparelho por aparelho, com o motivo de quem não pode.

    A incompatibilidade é decidida AQUI, com a mesma função que recusa a instalação (`motivo_incompativel`), e
    não reimplementada na interface: um segundo julgamento em TypeScript ficaria desatualizado no primeiro
    ajuste de regra, e a tela prometeria o que o backend recusa. Serve o diálogo "Instalar em…".
    """
    s = st(request)
    release = s.release_repo.release_row(release_id)
    if release is None:
        raise err(404, "not_found", "Release não encontrada.")
    requisitos = requisitos_de_release(release)
    package = release["package_name"]
    alvos: list[dict[str, Any]] = []
    for rt in s.devices.devices.values():
        if rt.store:
            continue                       # a loja é a FONTE do aplicativo, nunca destino — mesma regra de `distribute`
        porque = motivo_incompativel(requisitos, capacidades_de(rt), aparelho=rt.id)
        linha = s.release_repo.app_state(rt.id, package)
        alvos.append({
            "id": rt.id,
            #: Onde o aparelho está. É por isto que o diálogo agrupa por servidor: "instalar em 6 aparelhos" com
            #: 243 MB indo pelo túnel para outra máquina não é a mesma decisão que instalar nos daqui.
            "worker_id": rt.worker_id,
            "state": rt.state.value,
            "compatible": porque is None,
            "reason": porque,
            "app_state": linha["state"] if linha else None,
            "installed_release_id": linha["installed_release_id"] if linha else None,
            "installed_version_name": linha["observed_version_name"] if linha else None,
            "already": bool(linha and linha["installed_release_id"] == release_id
                            and linha["state"] in ("ready", "installed")),
        })
    return {"release_id": release_id, "package": package, "targets": sorted(alvos, key=lambda a: a["id"])}


#: Teto de um arquivo enviado pelo painel. O conjunto do Instagram passa de 240 MB somando os splits, e cada
#: arquivo vem numa requisição: 512 MB dá folga para o maior base.apk sem deixar um POST solto encher o disco.
UPLOAD_MAX_BYTES = 512 * 1024 * 1024
#: Nome de conjunto aceito na URL. O arquivo vai para `apks/inbox/<conjunto>/`, então isto é o que impede
#: `../` de virar escrita em qualquer lugar do disco.
_NOME_DE_CONJUNTO = re.compile(r"^[A-Za-z0-9._-]{1,60}$")
_NOME_DE_ARQUIVO_APK = re.compile(r"^[A-Za-z0-9._-]{1,120}\.apk$", re.IGNORECASE)


@router.post("/releases/upload", status_code=201)
async def upload_release(request: Request, filename: str = Query(..., min_length=5, max_length=120),
                         set_id: str = Query(..., min_length=1, max_length=60),
                         final: bool = False, source_reference: str | None = None) -> Any:
    """Recebe UM arquivo do conjunto e, com `final=true`, importa a pasta inteira.

    Existe porque até aqui a única entrada de APK era largar arquivo na pasta do servidor — quem abre o painel de
    outra máquina não tinha caminho nenhum. O corpo é o arquivo cru (`application/octet-stream`), não multipart:
    um conjunto de splits chega arquivo a arquivo, com o mesmo `set_id`, e só o último manda importar.

    O arquivo enviado passa exatamente pela MESMA inspeção da pasta de entrada — pacote, versão, splits,
    assinatura e ABIs saem do próprio APK, e a assinatura continua precisando de aprovação explícita. Enviar não
    instala nada.

    Um envio que nunca recebe o `final=true` (a aba fechou no meio) deixa `apks/inbox/upload-<set_id>/` no
    servidor. É de propósito: a pasta de entrada é exatamente onde um conjunto incompleto deve ficar esperando —
    "Importar da pasta" o encontra e o reprova com o motivo, em vez de o arquivo sumir em silêncio.
    """
    s = st(request)
    if not _NOME_DE_CONJUNTO.match(set_id):
        raise err(400, "set_id_invalido", "O identificador do conjunto aceita letras, números, ponto, hífen e _.")
    # O nome é validado COMO VEIO, não reduzido ao básico: aceitar `../../x.apk` e gravar `x.apk` em silêncio
    # esconderia de quem chamou que o caminho foi ignorado. `_NOME_DE_ARQUIVO_APK` não admite barra nenhuma.
    nome = filename.strip()
    if not _NOME_DE_ARQUIVO_APK.match(nome) or nome != PurePosixPath(nome).name:
        raise err(400, "nome_invalido", f"'{filename}' não é um nome de APK aceito (só .apk, sem caminho).")
    pasta = s.cfg.apk_inbox / f"upload-{set_id}"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / nome
    escrito = 0
    try:
        with open(destino, "wb") as fh:
            async for pedaco in request.stream():
                escrito += len(pedaco)
                if escrito > UPLOAD_MAX_BYTES:
                    raise err(413, "arquivo_grande",
                              f"O arquivo passou de {UPLOAD_MAX_BYTES // (1024 * 1024)} MB.")
                fh.write(pedaco)
    except HTTPException:
        destino.unlink(missing_ok=True)
        raise
    if escrito == 0:
        destino.unlink(missing_ok=True)
        raise err(400, "arquivo_vazio", "O corpo da requisição veio vazio.")
    if not final:
        return {"stored": nome, "size_bytes": escrito, "set_id": set_id, "imported": None}
    try:
        resultado = await asyncio.to_thread(
            s.releases.import_dir, pasta, source_type="upload",
            source_reference=source_reference or f"enviado pelo painel ({set_id})", expected_package=None)
    except ReleaseValidationError as exc:
        raise err(400, "import_failed", str(exc)) from exc
    finally:
        shutil.rmtree(pasta, ignore_errors=True)
    return {"stored": nome, "size_bytes": escrito, "set_id": set_id, "imported": resultado.to_dict()}


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
        verbo = "app.canary"
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
        verbo = "app.rollback"

    # Canário e rollback passam a ser COMANDOS: id acompanhável, estado honesto e `uncertain` quando o adb não
    # responde. Antes eram `202 {"accepted": true}` e o desfecho só aparecia recarregando `GET /api/app-state`.
    return {**_despachar_trabalho(s, rt, verbo, trabalho, label=rotulo,
                                  params={"release_id": release_id, "package": package},
                                  idempotency_key=body.idempotency_key),
            "release_id": release_id, "verb": body.verb}


# ---------------------------------------------------------------------- a loja como fonte do aplicativo
def _loja(s: AppState) -> DeviceRuntime:
    if not s.cfg.store_id:
        raise err(409, "no_store", "Nenhum aparelho-loja configurado (`instances.store` no config.yaml).")
    return device(s, s.cfg.store_id)


def _pacote_da_loja(s: AppState, body: StoreBody | None) -> str:
    """O pacote que a loja vai operar. Sem pacote no corpo, é erro — não "o Instagram".

    Enquanto isto caía em `cfg.file.instagram.package`, a interface nunca precisou dizer de que app falava: pelo
    painel a loja só sabia buscar o Instagram, e o caminho da loja para um segundo app nunca existiu.
    """
    pkg = body.package if body and body.package else None
    if not pkg:
        raise err(400, "package_required",
                  "Diga de que aplicativo se trata (`package`): a loja não assume um app por omissão.")
    return pkg


@router.get("/store")
async def store_status(request: Request, package: str | None = None) -> Any:
    """Loja × catálogo: o que a Play Store tem instalado lá, o que já foi catalogado e se há versão nova a buscar."""
    s = st(request)
    if not package:
        raise err(400, "package_required",
                  "Diga de que aplicativo se trata (`package`): a loja não assume um app por omissão.")
    pkg = package
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
    return {**_despachar_trabalho(
        s, rt, "store.sync", lambda: s.releases.sync_from_store(rt, pkg, s.installer),
        label="busca do aplicativo na loja", params={"package": pkg},
        idempotency_key=body.idempotency_key if body else None,
        ocupado="A loja está ocupada — se você está com o controle manual dela no painel, devolva-o e tente de "
                "novo."), "package": pkg}


@router.get("/app-state")
async def app_state(request: Request, package: str | None = None) -> Any:
    return st(request).release_repo.list_app_state(package)


@router.get("/instances")
async def list_instances(request: Request) -> Any:
    return st(request).devices.list_dtos()


def _recusar_mudanca_de_servidor(s: AppState, rt: Any, novo_worker: str | None, *, confirmado: bool) -> None:
    """Mover para outra máquina um aparelho que hospeda perfil com sessão pronta (item 4.4 / E9).

    Os dados do perfil — a sessão do Instagram — vivem na partição de dados do aparelho, no disco da máquina
    ANTIGA. Reapontar o id lógico não leva o disco junto: no aparelho da máquina nova a conta não está logada.
    Até aqui este PUT só conferia que o worker existia, e a sessão seguia `session_ready` em cache.

    Recusa com 409 a menos que a pessoa confirme. Quem confirma recebe, no mesmo movimento, a sessão invalidada.
    """
    if confirmado:
        return
    profile_id = s.social_repo.profile_id_for_instance(rt.id)
    if profile_id is None:
        return
    sessao = s.social_repo.session_row(profile_id)
    if sessao is None or sessao["status"] != SessionStatus.session_ready.value:
        return
    perfil = s.social_repo.profile_row(profile_id)
    arroba = f"@{perfil['username']}" if perfil is not None else "um perfil"
    raise err(409, "locality_change_requires_confirmation",
              f"{rt.id} hospeda {arroba}, que está com sessão pronta. Os dados dessa sessão ficam no disco de "
              f"{rt.worker_id or 'este servidor'}: movendo o aparelho para "
              f"{novo_worker or 'este servidor'}, será preciso entrar na conta de novo. Confirme para prosseguir.")


@router.put("/instances/{instance_id}")
async def update_instance(request: Request, instance_id: str, body: InstancePatch) -> Any:
    s = st(request)
    rt = device(s, instance_id)
    data = body.model_dump(exclude_unset=True)
    # Confirmação é decisão de quem chamou, nunca coluna: sai do dicionário antes de virar `UPDATE`.
    confirmado = bool(data.pop("confirm_locality_change", False))
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
        if novo != rt.worker_id:
            _recusar_mudanca_de_servidor(s, rt, novo, confirmado=confirmado)
    if data:
        s.db.execute(f"UPDATE instances SET {', '.join(f'{k}=?' for k in data)} WHERE id=?", (*data.values(), instance_id))
    if "worker_id" in data:
        # O vínculo vale JÁ: sem isto, amarrar um aparelho exigia reiniciar o backend para o runtime reler a
        # coluna — e as capacidades do worker só apareceriam depois disso.
        anterior = rt.worker_id
        rt.worker_id = data["worker_id"]
        rt.worker_verbs = s.workers.verbs_de(rt.worker_id) if rt.worker_id else None
        # O aparelho mudou de máquina: o disco onde a sessão do perfil foi gravada ficou para trás. O vínculo
        # continua apontando para onde os dados VIVEM (é o que a localidade significa) — o que deixa de valer é a
        # afirmação "este perfil está logado neste aparelho".
        s.devices.on_session_invalidated(instance_id, "o aparelho passou a ser hospedado por outra máquina; a "
                                                      "sessão gravada no disco anterior não está aqui")
        # O aparelho saiu do mapa de um túnel e entrou no de outro: os dois arquivos são reescritos, senão o
        # túnel antigo seguiria encaminhando uma porta que não serve mais a ninguém.
        for wid in {anterior, data["worker_id"]}:
            if wid:
                s.devices.escrever_mapa_do_tunel(wid)
    if data.get("app_id"):
        # Vincular um app a um aparelho é dizer "ele opera este app". A versão promovida daquele app é o estado
        # desejado do parque, então ela passa a valer aqui também — sem exigir um "Distribuir" de novo, que
        # instalaria o app em todos os aparelhos, inclusive nos que são só de QA.
        s.aplicar_versao_promovida(rt)
    s.devices.publish(rt, f"{instance_id}: configuração atualizada")
    return s.devices.dto(rt)


def _release_pronta_para(s: AppState, rt: DeviceRuntime, release_id: str) -> Row:
    """Confere que ESTA versão pode ir para ESTE aparelho, ou levanta o 404/409 com o motivo.

    Um lugar só, porque há dois chamadores: a rota de instalação por destino e o verbo `install_apk` do painel.
    Enquanto o verbo tinha caminho próprio (`adb install` do `apps.apk_path`), o mesmo pacote podia entrar por um
    caminho com assinatura aprovada, canário e estado observado e por outro sem nada disso — e o painel continuava
    dizendo `verifying` para um aparelho que já tinha outra versão instalada por fora (#83).
    """
    release = s.release_repo.release_row(release_id)
    if release is None:
        raise err(404, "not_found", "Release não encontrada.")
    if release["status"] != ReleaseState.installable.value:
        raise err(409, "release_not_installable",
                  f"A release está em '{release['status']}' e não pode ser instalada."
                  + (f" {release['detail']}" if release["detail"] else ""))
    if (porque := motivo_incompativel(requisitos_de_release(release), capacidades_de(rt), aparelho=rt.id)):
        # A limitação é explicada ANTES de agendar, que é a regra do pedido — e não no meio, como
        # `INSTALL_FAILED_NO_MATCHING_ABIS` num 202 que já tinha dito "aceito".
        raise err(409, "app_incompativel", f"{porque}.")
    if release["channel"] == ReleaseChannel.quarantined.value:
        # A quarentena é o outro eixo: o arquivo está íntegro, a VERSÃO é que já falhou a prova. A recusa tem de
        # acontecer aqui, junto da de status, senão o 202 esconderia o motivo de quem chamou.
        raise err(409, "release_quarantined",
                  "Esta versão está em quarentena porque já falhou a prova num aparelho."
                  + (f" {release['channel_detail']}" if release["channel_detail"] else "")
                  + " Para tentar de novo, coloque-a em canário de propósito.")
    return release


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
    release = _release_pronta_para(s, rt, body.release_id)
    return {**_despachar_trabalho(
        s, rt, "app.install", lambda: s.releases.install_on(rt, body.release_id, s.installer),
        label="instalação de APK", params={"release_id": body.release_id, "package": release["package_name"]},
        idempotency_key=body.idempotency_key), "release_id": body.release_id}


@router.post("/instances/{instance_id}/app/verify", status_code=202)
async def verify_app_on(request: Request, instance_id: str, body: AppVerifyBody) -> Any:
    """Relê do aparelho a versão instalada e registra divergência, se houver."""
    s = st(request)
    rt = device(s, instance_id)
    if rt.state != InstanceState.online:
        raise err(409, "not_online", "O aparelho precisa estar online para verificar o app.")
    return {**_despachar_trabalho(
        s, rt, "app.verify", lambda: s.releases.verify_on(rt, body.package, s.installer),
        label="verificação do app", params={"package": body.package},
        idempotency_key=body.idempotency_key), "package": body.package}


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


async def _instalar_versao_promovida(s: AppState, rt: DeviceRuntime, app: Row) -> None:
    """O verbo `install_apk` do painel, agora dentro da camada de releases.

    Antes ele era `adb install` do `apps.apk_path`: sem hash conferido, sem assinatura aprovada, sem canário, sem
    gravar `device_app_state` e sem prova de abertura. Dois caminhos para o mesmo pacote, um deles invisível para
    a camada que o painel exibe (#83). Agora há um só: a versão PROMOVIDA do pacote, com as mesmas recusas da
    instalação por destino. Sem release promovida, o verbo recusa e diz o que fazer — nunca cai no caminho velho.
    """
    promovida = s.releases.promoted_release(app["package"])
    if promovida is None:
        raise err(409, "sem_versao_promovida",
                  f"{app['name']}: nenhuma versão de {app['package']} foi promovida ainda. Importe o APK em "
                  "Aplicativos, coloque-o em prova num aparelho e promova-o — instalar por fora da camada de "
                  "releases deixaria este aparelho com uma versão que o painel não sabe descrever.")
    _release_pronta_para(s, rt, promovida.id)
    rt.app_versions.clear()
    await s.releases.install_on(rt, promovida.id, s.installer)


def _app_for(s: AppState, rt: DeviceRuntime, app_id: str | None) -> Row:
    chosen = app_id or s.db.scalar("SELECT app_id FROM instances WHERE id=?", (rt.id,))
    row = s.db.one("SELECT * FROM apps WHERE id=?", (chosen,)) if chosen else None
    if row is None:
        raise err(400, "no_app", f"{rt.id}: nenhum app associado. Associe um app em Configuração → Instâncias e contas.")
    return row


def _publish_command(s: AppState, row: Row) -> None:
    """Todo estado de comando vai para a interface. A regra mora em `commands/store.py` porque quem fecha um
    comando não é só este handler: a reconciliação por sonda e a decisão humana publicam pelo mesmo caminho."""
    publicar_comando(s.bus, row)


# ====================================================================== app e sessão como comandos
#: Verbos do pipeline de APLICATIVO e de SESSÃO. Ficam fora de `LIFECYCLE_ACTIONS` de propósito: não entram por
#: `/instances/{id}/actions/{verbo}` (não são ciclo de vida do aparelho) mas vivem na MESMA tabela `commands`,
#: com a mesma máquina de estados e a mesma reconciliação de reinício.
#:
#: Existem porque o E1 transformou em entidade só a ação de instância: instalar, provar (canário), voltar de
#: versão, distribuir, verificar, buscar da loja e conectar/verificar/sair continuavam devolvendo
#: `202 {"accepted": true}` sem id — não havia como distinguir criado/enviado/iniciado/concluído/falhou/
#: desconhecido, nem estado `uncertain` para o timeout que não prova nada.
APP_COMMAND_VERBS = {"app.install", "app.verify", "app.canary", "app.rollback", "app.distribute", "store.sync",
                     "session.connect", "session.verify", "session.logout"}


def _abrir_comando_de_app(s: AppState, instance_id: str, verb: str, *, params: dict[str, Any] | None = None,
                          idempotency_key: str | None = None,
                          requested_by: str | None = None) -> tuple[Row, bool]:
    rt = s.devices.devices.get(instance_id)
    chave = idempotency_key or f"{instance_id}:{verb}:{new_token()}"
    return s.commands.create(command_id=new_command_id(), instance_id=instance_id, verb=verb,
                             idempotency_key=chave, requested_by=requested_by or quem(),
                             host_worker_id=(rt.worker_id if rt is not None else None) or s.cfg.owner_id,
                             params=params)


def _despachar_trabalho(s: AppState, rt: DeviceRuntime, verb: str, factory: Callable[[], Any], *, label: str,
                        params: dict[str, Any] | None = None, idempotency_key: str | None = None,
                        ocupado: str | None = None, requested_by: str | None = None,
                        recusar_ocupado: bool = True) -> dict[str, Any]:
    """Abre um comando, despacha o trabalho pela fila do aparelho e faz o desfecho REAL fechar o comando.

    Estados: `created` → `dispatched` (a tarefa foi agendada aqui) → `running` (o trabalho começou) →
    `succeeded` / `failed` / `uncertain`. `rejected` quando o aparelho está ocupado — e aí nada foi tocado.

    `uncertain` vem de `InstalacaoIncerta`: timeout do adb não prova que a operação falhou, e chamar isso de
    falha era o que criava o estado pegajoso que só saía reinstalando. Quem cai aqui deixa o app em `verifying`
    sem operação pendente, e a releitura automática resolve.

    Síncrona de propósito: entre `run_device_job` e o carimbo de `dispatched` não pode haver `await`, senão a
    tarefa já estaria tentando ir para `running` antes de o comando sair de `created`.
    """
    row, repetido = _abrir_comando_de_app(s, rt.id, verb, params=params, idempotency_key=idempotency_key,
                                          requested_by=requested_by)
    if repetido:
        # Mesma chave: devolve o comando ORIGINAL. Reenviar não instala duas vezes.
        return {"accepted": True, "command_id": row["id"], "state": row["state"], "deduplicated": True,
                "instance_id": rt.id}
    command_id = str(row["id"])

    async def envolvido() -> Any:
        try:
            _publish_command(s, s.commands.transition(command_id, CommandState.running))
        except Exception:  # noqa: BLE001 - marcar o início nunca pode impedir o trabalho de acontecer
            log.exception("comando %s: falha ao marcar início", command_id)
        try:
            resultado = await factory()
        except InstalacaoIncerta as exc:
            _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=str(exc)))
            raise
        except Exception as exc:  # noqa: BLE001 - o desfecho negativo é registrado e repropagado
            _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(exc)))
            raise
        corpo = resultado if isinstance(resultado, dict) else None
        _publish_command(s, s.commands.transition(command_id, CommandState.succeeded,
                                                  result={"outcome": corpo} if corpo else None))
        return resultado

    if not s.scheduler.run_device_job(rt, envolvido, label=label):
        motivo = ocupado or "O aparelho está ocupado; tente novamente em instantes."
        _publish_command(s, s.commands.transition(command_id, CommandState.rejected, reason=motivo))
        if not recusar_ocupado:
            # Quem distribui para o parque INTEIRO não pode falhar por um aparelho ocupado: ali "ocupado" é
            # pendência, não recusa do pedido. O comando fica registrado como `rejected` naquele aparelho.
            return {"accepted": False, "command_id": command_id, "state": CommandState.rejected.value,
                    "deduplicated": False, "instance_id": rt.id, "reason": motivo}
        raise err(409, "device_busy", motivo, command_id=command_id)
    _publish_command(s, s.commands.transition(command_id, CommandState.dispatched))
    return {"accepted": True, "command_id": command_id, "state": CommandState.dispatched.value,
            "deduplicated": False, "instance_id": rt.id}


def _cancelamento_pedido(s: AppState, command_id: str) -> bool:
    """Alguém pediu o cancelamento DESTE comando enquanto ele corria? Lido do banco, e não de memória, porque
    quem pede (a rota HTTP) e quem executa (a tarefa) são dois caminhos que só se encontram no estado."""
    linha = s.commands.get(command_id)
    return linha is not None and linha["state"] == CommandState.cancel_requested.value


def _fechar_cancelado(s: AppState, command_id: str, motivo: str) -> None:
    """Confirma o cancelamento. Só é chamado onde se pode AFIRMAR que o efeito não aconteceu — `cancelled` é
    cancelamento confirmado (migrations/013_commands.sql), nunca "desisti de esperar"."""
    try:
        _publish_command(s, s.commands.transition(command_id, CommandState.cancelled, reason=motivo))
    except (InvalidCommandTransition, KeyError):
        # O desfecho real chegou primeiro: ele vale, e o pedido de cancelamento simplesmente perdeu a corrida.
        log.info("comando %s já tinha desfecho quando o cancelamento foi confirmar", command_id)
        return
    # A entrega deixou de ser devida. Sem isto, um comando cancelado ANTES de o transporte aceitá-lo (broker
    # fora do ar, ou queda entre aceitar e publicar) continuaria pendente e o dreno de partida o ressuscitaria,
    # executando no aparelho um verbo que já foi confirmado como cancelado.
    s.outbox.discard(command_id)


def _para_worker(rt: DeviceRuntime, action: str) -> bool:
    """O verbo vai para um worker? Uma pergunta, uma resposta, usada pelo handler HTTP e pelo executor — antes
    cada um decidia por conta própria e as marcas de tempo dependiam de quem chegasse primeiro.

    Depois do `LocalWorker`, o central TAMBÉM é um worker: os aparelhos desta máquina têm `worker_id =
    OWNER_ID`, e o ciclo de vida deles sai pelo mesmo despacho do agente remoto. O que continua fora é o verbo de
    ADB puro (`SO_ADB`), que sempre sai daqui pelo túnel, more o aparelho onde morar.
    """
    return bool(rt.worker_id and rt.worker_verbs and action in rt.worker_verbs and action not in SO_ADB)


async def _do_action_no_worker(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody,
                               command_id: str) -> None:
    """Despacha o verbo para o agente da outra máquina e traduz o desfecho dele em estado de comando.

    A cerca (`fence`) vai no despacho e volta no resultado: worker que perdeu a autorização e voltou do limbo tem
    o resultado recusado, em vez de sobrescrever o presente.

    Marcas de tempo de verdade: `dispatched` (com o `worker_id`) é gravado no instante em que o comando SAI pelo
    socket — não dentro da requisição HTTP que só agendou a tarefa. `acked` e `running` chegam do próprio worker
    (`Ack` e o primeiro `Progress`), tratados em `_tratar_mensagem_do_worker`.
    """
    linha = s.commands.get(command_id)
    if linha is None or linha["state"] != CommandState.created.value:
        if linha is not None and linha["state"] == CommandState.cancel_requested.value:
            # Cancelado entre o agendamento e o envio: o worker nunca soube deste comando, e é exatamente isso
            # que `cancelled` significa. Nada saiu pelo socket.
            _fechar_cancelado(s, command_id, "cancelado antes do envio; nada foi enviado ao worker")
            return
        # Recusado/cancelado entre o agendamento e aqui: não se despacha o que já tem desfecho. Com a rota
        # decidida uma única vez, chegar aqui com outro estado é sinal de caminho não previsto — logue.
        log.warning("comando %s não foi despachado ao worker: estado %s", command_id,
                    linha["state"] if linha else "inexistente")
        return
    cerca = int(linha["fence"])
    # Banco restaurado (K-004): o agente guarda no diário a maior cerca que já executou e a declara no `hello`.
    # Despachar abaixo dela seria recusado como ordem vencida, então a cerca sobe antes de sair daqui.
    if cerca <= (piso := s.workers.piso_de_cerca(rt.worker_id, rt.id)):
        cerca = s.commands.elevar_cerca(command_id, piso)
    prazo = PRAZO_POR_VERBO.get(action, PRAZO_PADRAO_S)
    # A DECISÃO é do central, não da máquina do worker, e é gravada ANTES do despacho: quem manda parar um
    # aparelho remoto quer que ele continue parado mesmo se o backend reiniciar, e quem manda ligar autoriza o
    # monitor a readotá-lo. Sem isto, `desired_state` ficava nulo em todo aparelho de worker e o "Parar" remoto
    # era desfeito pela readoção automática ≤30 s depois.
    if (desejo := DESEJO_DO_VERBO.get(action)) is not None:
        s.devices.set_desired_state(rt, desejo)
    msg = Dispatch(command_id=command_id, fence=cerca, verb=action, instance_id=rt.id, serial=rt.serial,
                   params={k: v for k, v in (body.model_dump() or {}).items() if v is not None
                           and k not in ("idempotency_key",)},
                   timeout_s=prazo)

    def marcar_despachado() -> None:
        _publish_command(s, s.commands.transition(command_id, CommandState.dispatched, worker_id=rt.worker_id))
        # Cinto: se o ACK já tiver chegado (transporte que entrega a resposta dentro do próprio `send`), ele
        # encontraria o comando ainda em `created` e seria descartado. Aqui ele é recuperado do registro.
        link = s.workers.live.get(rt.worker_id or "")
        if link is not None and command_id in link.confirmados:
            _mudar_estado_do_worker(s, command_id, rt.worker_id or "", CommandState.acked,
                                    de={CommandState.dispatched})

    # O mesmo cadeado que serializa o ciclo de vida local passa a valer no caminho do worker: sem ele, o
    # rodízio/IA podia mexer no aparelho remoto no meio de um `reset` da outra máquina.
    #
    # Para o worker LOCAL ele não é tomado aqui: quem executa é o `DeviceManager`, e ele já se serializa neste
    # mesmo cadeado dentro de `create`/`_boot`/`stop_instance`. Tomá-lo aqui seria esperar, de dentro do
    # despacho, por um cadeado que só o próprio despacho pode soltar — um impasse, não uma proteção.
    local = rt.worker_id == s.cfg.owner_id
    cadeado: Any = contextlib.nullcontext() if local else rt.op_lock
    try:
        async with cadeado:
            # Última conferência ANTES do envio, DENTRO do cadeado: esperar o cadeado pode levar minutos (o
            # comando anterior daquele aparelho), e um cancelamento pedido nessa espera não pode terminar em
            # despacho assim mesmo. Daqui até o `send` não há suspensão, então a conferência vale.
            if _cancelamento_pedido(s, command_id):
                _fechar_cancelado(s, command_id, "cancelado antes do envio; nada foi enviado ao worker")
                return
            resultado = await s.workers.dispatch(rt.worker_id or "", msg, marcar_despachado)
        alvo = {"succeeded": CommandState.succeeded, "failed": CommandState.failed,
                "uncertain": CommandState.uncertain, "cancelled": CommandState.cancelled}[resultado.outcome]
        motivo, dados = resultado.reason, resultado.data
    except WorkerError as exc:
        # Falha ao ENVIAR é o único caso em que se pode afirmar que nada aconteceu no aparelho.
        alvo, motivo, dados = CommandState.failed, exc.message, None
    except Exception as exc:  # noqa: BLE001 - quebrou no meio do despacho: não se sabe se o worker agiu
        log.exception("despacho de %s para o worker %s", command_id, rt.worker_id)
        alvo, motivo, dados = CommandState.uncertain, f"erro no despacho ao worker: {exc}", None
    if alvo is CommandState.cancelled and DESEJO_DO_VERBO.get(action) == InstanceState.online.value:
        # A DECISÃO foi gravada antes do despacho ("eu quero este aparelho no ar"). Um `start` cancelado não pode
        # deixá-la de pé: o monitor readotaria em ≤30 s o aparelho que alguém acabou de mandar não subir.
        s.devices.set_desired_state(rt, InstanceState.stopped.value)
    # O efeito no CENTRAL vem antes de publicar o desfecho: quem lê o `command.updated` (painel) e quem lê o
    # estado do aparelho no mesmo instante precisam ver a mesma coisa. Falhar aqui não muda o desfecho do
    # comando — o agente já agiu, e mentir sobre isso seria pior do que um estado desatualizado.
    #
    # Só o caminho REMOTO precisa disso: `aplicar_desfecho_remoto` traduz o que outra máquina fez em estado
    # daqui. O worker local É o `DeviceManager` — o efeito já aconteceu nele, e repeti-lo soltaria do painel um
    # aparelho que acabou de ser adotado.
    try:
        if not local:
            await s.devices.aplicar_desfecho_remoto(rt, action, alvo.value, dados)
    except Exception:  # noqa: BLE001
        log.exception("efeitos no central do comando %s (%s em %s)", command_id, action, rt.id)
    try:
        _publish_command(s, s.commands.transition(command_id, alvo, reason=motivo, result=dados,
                                                  worker_id=rt.worker_id))
    except Exception:  # noqa: BLE001 - comando já encerrado por outra via (resultado tardio) não é erro
        log.warning("comando %s já tinha desfecho ao voltar do worker", command_id)
    # Só agora: readotar fala com o aparelho pelo túnel e pode demorar. O comando já está fechado, e o aparelho
    # já está destrancado para o próximo pedido — a readoção acontece por trás, como faria o monitor.
    try:
        if not local:
            await s.devices.readotar_depois_do_worker(rt, action, alvo.value)
    except Exception:  # noqa: BLE001 - readoção é observação: falhar aqui não muda o desfecho do comando
        log.exception("readoção de %s depois do comando %s", rt.id, command_id)


async def _do_action(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody, command_id: str,
                     remoto: bool) -> None:
    """Executa a ação dirigindo os estados do comando.

    O que mudou: antes toda exceção era engolida e virava um evento `log` que o frontend nem tratava — a ação
    recusada no fundo era indistinguível de sucesso. Agora cada desfecho é gravado no comando, e `uncertain` é
    reservado para o caso honesto: timeout, em que o efeito pode ter acontecido e ninguém sabe.

    `remoto` vem DECIDIDO de quem gravou a marca de entrega, e não é reavaliado aqui de propósito: entre o
    handler e esta tarefa o worker pode cair (`bind_worker(..., None)` zera `rt.worker_verbs`), e as duas metades
    discordarem deixaria o comando preso — para sempre, agora que comando aberto tranca o aparelho. Se o worker
    sumiu, o despacho falha com `worker_offline` e o comando vira `failed`, que é a resposta verdadeira.

    **Um ramo só para o CICLO DE VIDA.** Criar, ligar, acordar, parar, hibernar, reiniciar e resetar saem sempre
    por `_do_action_no_worker` — para o agente da outra máquina ou para o `LocalWorker` deste servidor
    (`workers/local.py`). Não há mais duas implementações do mesmo verbo com semânticas diferentes: um despacho,
    uma cerca, um prazo, um mapa de desfechos.

    O que continua saindo DAQUI é o verbo de ADB puro (`verbs.SO_ADB`: instalar APK, abrir app, teclas), e isso
    vale igualmente para aparelho local e remoto — aquele caminho passa pelo túnel e está provado em campo, e
    mandar o catálogo de APK para cada máquina seria trocar um problema resolvido por um novo.
    """
    d = s.devices
    # A ordem importa: NADA de carimbar `running` antes de saber quem executa. No caminho do worker, `running`
    # significa "quem hospeda o aparelho começou a agir" e só ele pode dizer isso.
    if remoto:
        await _do_action_no_worker(s, rt, action, body, command_id)
        return
    if action not in SO_ADB:
        # Ciclo de vida que não achou worker. Não há mais uma segunda implementação a chamar: ou o worker local
        # não subiu, ou o do aparelho caiu entre a entrega e esta tarefa. A resposta verdadeira é dizer isso —
        # o aparelho ficou intacto, e `failed` é o desfecho que não manda ninguém desconfiar do estado dele.
        _publish_command(s, s.commands.transition(
            command_id, CommandState.failed,
            reason=f"nenhum worker está disponível para executar '{action}' em {rt.id}; "
                   "confira a Infraestrutura"))
        return
    try:
        _publish_command(s, s.commands.transition(command_id, CommandState.running))
    except InvalidCommandTransition:
        # O único caminho previsto até aqui: cancelamento pedido entre a entrega e o começo da execução. O verbo
        # não chegou a rodar, então o aparelho ficou intacto — e é isso que fica registrado.
        if _cancelamento_pedido(s, command_id):
            _fechar_cancelado(s, command_id, "cancelado antes de começar; nada foi executado neste aparelho")
        else:
            log.warning("comando %s não pôde entrar em running", command_id)
        return
    except Exception:  # noqa: BLE001 - falha ao registrar não deixa a tarefa agir às escondidas
        log.exception("comando %s não pôde entrar em running", command_id)
        return
    try:
        if action == "install_apk":
            await _instalar_versao_promovida(s, rt, _app_for(s, rt, body.app_id))
        elif action == "open_app":
            abriu, detalhe = await d.open_app(rt, _app_for(s, rt, body.app_id))
            if not abriu:
                # O `am start` volta positivo mesmo quando o app cai na abertura. Sem a janela em foco não há
                # prova de que abriu — e "não sei" é `uncertain`, não `succeeded`.
                _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=detalhe))
                return
        else:
            await d.quick_key(rt, action)
    except InstalacaoIncerta as exc:
        # Mesma regra do caminho por release: timeout do adb não prova que a instalação falhou, e gravar `failed`
        # aqui recriaria o estado pegajoso que só saía reinstalando por cima.
        _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=str(exc)))
        return
    except (DriverTimeout, AdbTimeout) as exc:
        # Não sabemos se o aparelho obedeceu: o comando não é repetido sozinho, e quem olhar vê "incerto".
        # `AdbTimeout` entra aqui ANTES de `AdbError` de propósito: prazo estourado num `adb install`/`am start`
        # é efeito possível (o `pm install` continua no aparelho), e gravar `failed` fazia o usuário reinstalar
        # por cima — ou concluir que falhou o que funcionou. Em remoto sob carga isso já aconteceu em campo.
        _publish_command(s, s.commands.transition(command_id, CommandState.uncertain, reason=str(exc)))
        return
    except HTTPException as exc:
        motivo = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(motivo)))
        return
    except (InstanceBusy, ValueError, AdbError, AvdError, DriverError) as exc:
        # `AvdError` entra aqui porque `create` agora deixa a falha subir: AVD que não foi criado não vira
        # `succeeded` com o aparelho em `absent`.
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(exc)))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("ação %s em %s", action, rt.id)
        _publish_command(s, s.commands.transition(command_id, CommandState.failed, reason=str(exc)))
        return
    # `start`/`hibernate` e a espera pelo boot saíram daqui: eles são ciclo de vida, e ciclo de vida agora tem um
    # executor só (`workers/local.py` para os aparelhos desta máquina, o agente para os das outras). O que sobra
    # neste caminho é ADB puro, que termina quando o comando de ADB volta.
    _publish_command(s, s.commands.transition(command_id, CommandState.succeeded))


def s_android_hibernation(rt: DeviceRuntime) -> bool:
    return bool(rt.cfg.instance_android(rt.id).hibernation)


def _hiberna_o_hospedeiro(s: AppState, rt: DeviceRuntime) -> tuple[bool, str]:
    """Quem decide se hibernar é possível é a máquina que HOSPEDA o aparelho. Devolve `(pode, por que não)`.

    Para o aparelho de OUTRA máquina vale a declaração do agente no `Hello`: consultar o `config.yaml` deste
    servidor fazia o painel oferecer "Hibernar" para uma máquina que sobe tudo a frio, e o `wake` seguinte seria
    um boot a frio disfarçado.

    Para o aparelho DESTA máquina vale a configuração por instância. O `LocalWorker` também declara hibernação no
    registro, mas lá ela é um `bool` por máquina, e aqui `android.hibernation` aceita sobreposição por aparelho —
    então a resposta fina continua vindo da configuração, que é onde ela é fina.
    """
    if rt.worker_id and rt.worker_id != s.cfg.owner_id:
        return s.workers.hiberna(rt.worker_id), ("o worker que hospeda este aparelho não salva snapshot "
                                                 "(android.hibernation desligado na máquina dele)")
    return s_android_hibernation(rt), "hibernação desligada na configuração (android.hibernation)"


def _precheck(s: AppState, rt: DeviceRuntime, action: str, body: InstanceActionBody,
              command_id: str | None = None) -> tuple[str, str] | None:
    """`None` quando pode seguir; senão `(código, motivo)`. O código vira o `code` do 409 — `device_busy` precisa
    ser distinguível de `rejected` por quem chama a API."""
    if action not in LIFECYCLE_ACTIONS:
        return "rejected", "ação desconhecida"
    if action == "reset" and not body.confirm:
        return "rejected", "o reset apaga dados e sessão do aparelho; envie confirm=true"
    # Inventário divergente (item 4.5; achado #47): as fontes discordam sobre QUAL aparelho está por trás deste
    # id. Verbo destrutivo aqui apagaria o aparelho errado em silêncio — era o dano latente do "três lugares sem
    # conferência". Os verbos de leitura e de tela seguem: quem diagnostica precisa deles.
    if action in VERBOS_DESTRUTIVOS and rt.inventory_state == "divergent":
        return "rejected", (f"o inventário deste aparelho está divergente ({rt.inventory_detail}); resolva o "
                            f"vínculo antes de '{action}'")
    # UM APARELHO, UMA OPERAÇÃO. Vale para os dois caminhos e para os dois clientes (painel e API): enquanto
    # houver comando aberto naquele aparelho, o próximo é recusado ANTES de tocar em qualquer coisa. Sem isto,
    # dois `start`/`reset` concorrentes chegavam juntos ao agente remoto e se intercalavam. O comando recém-criado
    # está ele mesmo aberto, por isso ele é excluído da consulta.
    if action in VERBOS_EXCLUSIVOS and \
            (aberto := s.commands.open_for_instance(rt.id, exclude=command_id, verbs=VERBOS_EXCLUSIVOS)) is not None:
        return "device_busy", (f"{rt.id} já tem o comando '{aberto['verb']}' em andamento "
                               f"({aberto['id']}, {aberto['state']}); espere o desfecho")
    # Manutenção do worker: comando de painel para um aparelho hospedado por worker em manutenção é recusado antes
    # de qualquer outra checagem — a pessoa que ligou a manutenção espera que nada novo seja despachado. Só a
    # manutenção intercepta aqui; "não conectado"/"não inscrito" seguem para a checagem de capacidade abaixo, que
    # já tem mensagem própria (verbos declarados pelo worker via `rt.worker_verbs`).
    if rt.worker_id and (porque := s.workers.motivo_manutencao(rt.worker_id)) is not None:
        return "rejected", porque
    # Capacidade primeiro: o que o aparelho NÃO consegue fazer é recusado com a explicação, antes de agendar.
    # Cobre a loja e o aparelho de outra máquina no mesmo lugar, para ação única e lote.
    if (porque := motivo_nao_suportado(rt, action)) is not None:
        return "rejected", porque
    # Quem decide se hibernar é possível é a máquina que HOSPEDA o aparelho — uma pergunta só, para os dois
    # caminhos (ver `_hiberna_o_hospedeiro`).
    if action == "hibernate" and not (resposta := _hiberna_o_hospedeiro(s, rt))[0]:
        return "rejected", resposta[1]
    # Acordar é subir A PARTIR do snapshot. Sem snapshot não existe o que acordar: o que aconteceria é um boot a
    # frio com nome de "Acordar" — recusa explicada, e "Iniciar" continua ali para quem quer ligar a frio.
    if action == "wake" and not rt.snapshot_valid:
        return "rejected", "não há snapshot salvo deste aparelho; use 'Iniciar' para ligar a frio"
    if action in ("stop", "hibernate", "restart", "reset", "install_apk", "open_app", "home", "back", "recents") \
            and rt.control.value == "ai":
        return "device_busy", "a IA está executando neste aparelho; pause/cancele a execução ou assuma o controle"
    if action in ("install_apk", "open_app", "home", "back", "recents") and rt.state != InstanceState.online:
        return "rejected", "a instância precisa estar online"
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
        recusa = _precheck(s, rt, body.action, por_aparelho, row["id"])
        if recusa:
            codigo, why = recusa
            _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=why))
            rejected.append({"id": iid, "reason": why, "command_id": row["id"], "code": codigo})
            continue
        estado, remoto = _marcar_entregue(s, rt, body.action, row["id"], por_aparelho)
        del remoto                       # a rota já foi decidida e gravada no outbox; quem a relê é `_despachar`
        await _despachar(s, row["id"])
        accepted.append(iid)
        comandos[-1]["state"] = estado
    # `accepted` continua sendo lista de ids (contrato antigo, intacto); `commands` é o acréscimo rastreável.
    return {"accepted": accepted, "rejected": rejected, "commands": comandos}


async def _despachar(s: AppState, command_id: str) -> None:
    """Publica a ordem do outbox no transporte e só então a marca como saída (item 5.6).

    A ordem das duas coisas é o item inteiro. Marcar antes de publicar tornaria a queda entre as duas linhas
    uma perda silenciosa — comando `sent` que nunca saiu —, que é exatamente o defeito do achado #30 num lugar
    novo. Publicando primeiro, a mesma queda deixa a linha `pending` e o dreno de partida publica de novo: ao
    menos uma vez, com a recusa de reentrega ficando por conta da máquina de estados do comando (aqui) e do
    diário do agente (lá).
    """
    if command_id in s.outbox.publicando:
        return                                  # este processo já está publicando esta ordem
    s.outbox.publicando.add(command_id)
    try:
        row = s.outbox.get(command_id)
        if row is None or row["state"] != OUTBOX_PENDING:
            return                              # já saiu, ou a entrega deixou de ser devida
        envelope = {"command_id": command_id, "instance_id": row["instance_id"], "worker_id": row["worker_id"],
                    "verb": row["verb"], **s.outbox.payload_of(row)}
        try:
            await s.transport.publish(envelope)
        except Exception as exc:                # noqa: BLE001 - transporte fora do ar não é comando perdido
            # A linha CONTINUA pendente, e é isso que salva o comando: o laço de repetição publica de novo. Não
            # transforme isto em erro do painel — quem clicou já teve o pedido aceito e gravado.
            log.warning("comando %s: o transporte não aceitou a publicação (%s); segue na fila", command_id, exc)
            return
        s.outbox.mark_sent(command_id)
    finally:
        s.outbox.publicando.discard(command_id)


async def executar_envelope(s: AppState, envelope: dict[str, Any]) -> None:
    """Ponta consumidora do transporte: transforma o envelope de volta em execução.

    É o MESMO caminho do despacho de sempre (`_do_action`), e é de propósito: o transporte troca por onde a
    ordem viaja, nunca o que ela faz nem quem fecha o comando.
    """
    command_id = str(envelope.get("command_id") or "")
    instance_id = str(envelope.get("instance_id") or "")
    verb = str(envelope.get("verb") or "")
    rt = s.devices.devices.get(instance_id)
    if rt is None:
        # Aparelho que saiu da configuração entre aceitar e entregar. Dizer isso é verdadeiro e fecha o comando;
        # deixá-lo aberto trancaria o aparelho (que nem existe) para sempre.
        log.warning("comando %s: %s não está neste backend; nada foi executado", command_id, instance_id)
        if (linha := s.commands.get(command_id)) is not None and CommandState(linha["state"]) in COMMAND_OPEN:
            _publish_command(s, s.commands.transition(
                command_id, CommandState.failed,
                reason=f"{instance_id} não está neste backend; nada foi executado"))
        return
    body = InstanceActionBody(**(envelope.get("body") or {}))
    await _do_action(s, rt, verb, body, command_id, bool(envelope.get("remoto")))


def _marcar_entregue(s: AppState, rt: DeviceRuntime, action: str, command_id: str,
                     body: InstanceActionBody) -> tuple[str, bool]:
    """Carimba `dispatched` SÓ quando a entrega já aconteceu. Devolve `(estado, vai_para_o_worker)`.

    O segundo valor existe para a rota ser decidida UMA vez: quem grava a marca e quem executa precisam
    concordar, senão uma queda do worker entre os dois deixaria o comando sem ninguém para fechá-lo.

    No caminho local, entregar é agendar a tarefa que vai executar aqui mesmo — a marca vale. No caminho do
    worker, entregar é o comando SAIR pelo socket, e isso ainda não aconteceu: o comando continua `created` até
    `_do_action_no_worker` conseguir enviar. Era este o carimbo mentiroso do achado #7 (`dispatched_at` gravado
    dentro da requisição HTTP, antes de qualquer envio) — e é ele que torna `created` → `failed` na reconciliação
    uma afirmação verdadeira: o que nunca saiu não tocou no aparelho.

    **A linha do outbox é gravada aqui, na MESMA transação** (item 5.6). Aqui, e não em `CommandStore.create`,
    porque é aqui que o comando passou no pré-voo e a entrega passou a ser devida: o que é recusado no pré-voo
    nunca chega a ter entrega pendente, e portanto o dreno de partida não o ressuscita.
    """
    remoto = _para_worker(rt, action)
    # O corpo COMO FOI ACEITO vai para o outbox: `commands.params` guarda só o `app_id`, e `confirm` — a
    # autorização humana que separa um `reset` pedido de um `reset` acidental — se perderia num reenvio.
    payload = {"body": body.model_dump(mode="json"), "remoto": remoto}
    with s.db.tx():
        s.outbox.enqueue(command_id=command_id, instance_id=rt.id, verb=action,
                         worker_id=(rt.worker_id if remoto else None), payload=payload)
        if remoto:
            return CommandState.created.value, True
        row = s.commands.transition(command_id, CommandState.dispatched)
    _publish_command(s, row)
    return CommandState.dispatched.value, False


def _abrir_comando(s: AppState, instance_id: str, action: str, params: InstanceActionBody,
                   requested_by: str | None = None) -> tuple[Row, bool]:
    chave = params.idempotency_key or f"{instance_id}:{action}:{new_token()}"
    rt = s.devices.devices.get(instance_id)
    # ONDE o aparelho morava quando o comando foi aberto. Vale para TODO verbo, inclusive os de ADB puro, que saem
    # daqui pelo túnel e por isso nunca carimbam `worker_id` — `GET /api/commands` mostrava `worker_id=None` num
    # `open_app` que aconteceu na outra máquina, e o histórico não tinha como dizer onde.
    return s.commands.create(command_id=new_command_id(), instance_id=instance_id, verb=action,
                             idempotency_key=chave, requested_by=requested_by or quem(),
                             host_worker_id=(rt.worker_id if rt is not None else None) or s.cfg.owner_id,
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
    recusa = _precheck(s, rt, action, params, row["id"])
    if recusa:
        codigo, why = recusa
        # A recusa fica no histórico do aparelho com o motivo, em vez de virar um evento que ninguém mostra.
        _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=why))
        raise err(409, codigo, f"{instance_id}: {why}.", command_id=row["id"])
    if action in ("install_apk", "open_app"):
        try:
            _app_for(s, rt, params.app_id)      # valida antes de despachar
        except HTTPException as exc:
            motivo = exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail)
            _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=str(motivo)))
            raise
    estado, _ = _marcar_entregue(s, rt, action, row["id"], params)
    await _despachar(s, row["id"])
    return {"command_id": row["id"], "state": estado, "deduplicated": False}


def pedir_ciclo_de_vida(s: AppState, instance_id: str, verb: str, motivo: str, *, requested_by: str,
                        nivel: str = "info") -> str | None:
    """O CENTRAL pede um verbo de ciclo de vida por conta própria, como se uma pessoa tivesse clicado. Devolve o
    id do comando aberto, ou `None` quando não há o que fazer.

    Um caminho só para os dois pedidos automáticos que existem — a remediação (`restart`) e o rodízio
    (`start`/`wake`/`stop`/`hibernate` num aparelho de outra máquina). Passa pelo mesmo `_precheck` e pelo mesmo
    despacho do painel de propósito: comando aberto no aparelho, worker em manutenção, verbo não suportado e IA
    no controle recusam aqui exatamente como recusariam lá — e a recusa fica no histórico do aparelho com o
    motivo, em vez de sumir num log.
    """
    rt = s.devices.devices.get(instance_id)
    if rt is None or verb not in (rt.worker_verbs or []):
        # Sem worker que saiba executar o verbo neste aparelho não existe pedido automático: dizer isso no
        # cartão é mais honesto do que abrir um comando que ninguém pode executar.
        return None
    params = InstanceActionBody(confirm=True)
    row, repetido = _abrir_comando(s, instance_id, verb, params, requested_by=requested_by)
    if repetido:
        return str(row["id"])
    if (recusa := _precheck(s, rt, verb, params, row["id"])) is not None:
        _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=recusa[1]))
        return None
    s.bus.emit("log", f"{instance_id}: '{verb}' pedido automaticamente — {motivo}", level=nivel,
               instance_id=instance_id, data={"command_id": row["id"]})
    _marcar_entregue(s, rt, verb, row["id"], params)
    # Chamador SÍNCRONO (gancho do gerenciador de aparelhos): a publicação vira tarefa. Perder essa tarefa não
    # perde mais o comando — a linha do outbox já está gravada, e o dreno de partida a publica.
    asyncio.create_task(_despachar(s, row["id"]))
    return str(row["id"])


#: Estados declarados por um agente que descrevem um aparelho que NÃO está no ar. `unknown` fica de fora de
#: propósito: "não sei" não autoriza ligar nada.
FORA_DO_AR = {"stopped", "absent"}


def reconciliar_estado_desejado(s: AppState, worker_id: str, devices: list[Any]) -> list[str]:
    """Worker (re)conectou: os aparelhos dele que estavam para ficar no ar voltam a subir. Devolve os ids pedidos.

    Achados #137 e #38, o buraco do aceite 7 do lado de lá. Reiniciar o notebook do parque deixava os seis
    aparelhos fora: o agente voltava, declarava tudo `stopped` — e ninguém fazia nada com isso. O `desired_state`
    já existia e já era gravado para aparelho de worker (`set_desired_state` no `start`/`stop` remoto); o que
    faltava era alguém CONFRONTAR o que o worker declara com o que o central quer.

    Central-side de propósito, e não "o agente religa o que ele mesmo tinha ligado": quem é dono do estado
    desejado e da cerca é o central. Um agente que religasse por conta própria ligaria aparelho que uma pessoa
    tinha acabado de parar daqui, e sem cerca nenhuma na decisão.

    Uma vez por CONEXÃO (ver `_reconciliar_uma_vez`), nunca a cada batida: repetido de 10 em 10 s, isto viraria
    um pedido novo enquanto o aparelho ainda estivesse subindo. Todas as outras recusas (comando aberto, worker
    em manutenção, verbo não suportado, IA no controle) já são de `pedir_ciclo_de_vida`, e ficam no histórico do
    aparelho com o motivo.
    """
    pedidos: list[str] = []
    for d in devices:
        instance_id = getattr(d, "instance_id", None)
        if not instance_id or getattr(d, "state", None) not in FORA_DO_AR:
            continue
        rt = s.devices.devices.get(instance_id)
        if rt is None or rt.worker_id != worker_id:
            continue
        if rt.desired_state != InstanceState.online.value:
            continue                     # ninguém pediu este aparelho no ar: ligá-lo seria decisão nossa
        if pedir_ciclo_de_vida(s, instance_id, "start", f"o worker {worker_id} voltou com o aparelho "
                               f"{getattr(d, 'state', '?')} e o estado desejado é online",
                               requested_by="system", nivel="warn") is not None:
            pedidos.append(instance_id)
    return pedidos


def _reconciliar_uma_vez(s: AppState, worker_id: str, link: WorkerLink, devices: list[Any]) -> None:
    """Dispara a reconciliação na PRIMEIRA batida que traz estado de verdade, e só nela.

    A marca vive no `link`, que morre com o socket: worker que reconecta ganha uma reconciliação nova, e worker
    que só está batendo não ganha nenhuma. `unknown` não conta — é o que o agente manda antes de a sondagem
    dele terminar, e agir sobre "não sei" ligaria aparelho que já está no ar.
    """
    if link.reconciliado or not any(getattr(d, "state", "unknown") != "unknown" for d in devices):
        return
    link.reconciliado = True
    if (voltando := reconciliar_estado_desejado(s, worker_id, devices)):
        s.bus.emit("log", f"Worker {worker_id}: religando {len(voltando)} aparelho(s) pelo estado desejado "
                          f"({', '.join(voltando)}).", level="warn")


#: A escada de reparo automático: quantos `restart` antes de `reset`, e quanto esperar depois de esgotar.
DEGRAUS_DE_RESTART = 2
RETENTATIVA_APOS_ESCADA_S = 6 * 3600
JANELA_DA_ESCADA_H = 24.0


def remediar(s: AppState, instance_id: str, motivo: str) -> str | None:
    """O aparelho degradou com `desired_state=online`: decide o DEGRAU e abre um comando RASTREÁVEL.

    Degraus, contados no histórico de comandos (`requested_by='system'`, 24 h): 1º e 2º `restart`; 3º `reset`
    (apaga os dados do AVD e sobe limpo — decisão do dono em 23/09, para todo aparelho que declare o verbo);
    esgotada a escada, o aparelho ganha `attention` "precisa de gente" e volta a ser tentado (`restart`) a cada
    6 h — nunca fica esquecido. Cada degrau vira evento `instance.remediation`, para o painel e o relatório de uso
    não confundirem reparo com comando manual.

    Antes: 2 restarts em memória e "a decisão é de uma pessoa" — para sempre, e zerado num reinício do backend.
    """
    rt = s.devices.devices.get(instance_id)
    if rt is None:
        return None
    historico = s.commands.remediacoes_recentes(instance_id, janela_h=JANELA_DA_ESCADA_H)
    restarts = sum(1 for c in historico if c["verb"] == "restart")
    resets = sum(1 for c in historico if c["verb"] == "reset")
    pode_resetar = "reset" in (rt.worker_verbs or []) and not rt.store
    degrau = len(historico) + 1
    if restarts < DEGRAUS_DE_RESTART:
        verbo = "restart"
    elif pode_resetar and resets == 0:
        verbo = "reset"
    else:
        ultimo = historico[-1] if historico else None
        quando = parse_iso(ultimo["created_at"]) if ultimo else None
        if quando is not None and (now() - quando).total_seconds() < RETENTATIVA_APOS_ESCADA_S:
            s.devices.marcar_atencao(rt, f"Precisa de gente: {len(historico)} reparo(s) automático(s) em 24 h não "
                                         f"resolveram ({motivo}). Nova tentativa automática em até 6 h.")
            s.devices.adiar_reparo(rt, RETENTATIVA_APOS_ESCADA_S)
            return None
        verbo = "restart"                # nova rodada depois do prazo
    cid = pedir_ciclo_de_vida(s, instance_id, verbo, motivo, requested_by="system", nivel="warn")
    if cid is None:
        return None
    s.bus.emit("instance.remediation", f"{instance_id}: reparo automático, {degrau}º degrau ({verbo}) — {motivo}",
               level="warn", instance_id=instance_id,
               data={"degrau": degrau, "verb": verbo, "command_id": cid, "motivo": motivo})
    return cid


def remediar_reiniciando(s: AppState, instance_id: str, motivo: str) -> str | None:
    """Nome antigo do gancho `on_remediation_needed`; a decisão do degrau é de `remediar`."""
    return remediar(s, instance_id, motivo)


@router.get("/commands/{command_id}")
async def get_command(request: Request, command_id: str) -> Any:
    row = st(request).commands.get(command_id)
    if row is None:
        raise err(404, "not_found", f"Comando {command_id} não existe.")
    return command_dto(row)


@router.get("/commands")
async def list_commands(request: Request, instance_id: str | None = None, unsettled: bool = False,
                        limit: int = Query(50, ge=1, le=200)) -> Any:
    """`unsettled=true` devolve só os comandos que terminaram sem desfecho conhecido — a fila de quem ainda
    espera uma resposta (da sonda ou de uma pessoa). É o que o painel precisa para eles pararem de sumir."""
    s = st(request)
    linhas = s.commands.unsettled(limit) if unsettled else s.commands.recent(instance_id, limit)
    if unsettled and instance_id:
        linhas = [r for r in linhas if r["instance_id"] == instance_id]
    return [command_dto(r) for r in linhas]


@router.post("/commands/{command_id}/verify")
async def verify_command(request: Request, command_id: str) -> Any:
    """"Verificar agora": pergunta ao estado real se aquele comando incerto deu certo.

    Para os verbos de ciclo de vida o desfecho é observável (`start` promete o aparelho no ar, `stop` promete o
    contrário), e ver o estado prometido é prova de sucesso. Não ver NÃO é prova de fracasso — então o comando
    que a sonda não fecha volta como está, esperando a decisão de alguém. Sempre 200: "continua incerto" é
    resposta legítima, e não erro.
    """
    s = st(request)
    row = s.commands.get(command_id)
    if row is None:
        raise err(404, "not_found", f"Comando {command_id} não existe.")
    novo = verificar_comando(s, row)
    mudou = novo["state"] != row["state"]
    if mudou:
        _publish_command(s, novo)
    return {"command": command_dto(novo).model_dump(mode="json"), "changed": mudou,
            "verifiable": row["verb"] in VERIFICAVEL_POR_ESTADO}


async def _entregar_cancelamento(s: AppState, row: Row) -> tuple[bool, str]:
    """Leva o pedido a QUEM ESTÁ EXECUTANDO, nos dois caminhos. Devolve `(interrompeu algo, o que dizer)`.

    O estado já mudou antes desta função: entregar é o segundo passo, e falhar aqui não desfaz o pedido. O
    comando fica em `cancel_requested` até o desfecho de verdade chegar — pedir não é ter cancelado.
    """
    command_id, verbo = row["id"], row["verb"]
    rt = s.devices.devices.get(row["instance_id"])
    # `worker_id` só é carimbado no envio: um cancelamento que chega ANTES disso ainda precisa achar o agente,
    # por isso o dono do aparelho também vale. Cancel de id desconhecido é ignorado pelo agente, sem efeito.
    worker_id = row["worker_id"] or (rt.worker_id if rt is not None else None)
    remoto = bool(row["worker_id"]) or (rt is not None and _para_worker(rt, verbo))
    if remoto and worker_id:
        try:
            if await s.workers.cancel(worker_id, command_id):
                if worker_id == s.cfg.owner_id:
                    # O worker local recebe o pedido pelo mesmo contrato, e o ponto seguro de cancelamento desta
                    # máquina continua sendo `rt.tasks["boot"]` — cancelar só a espera deixaria o emulador
                    # subindo às escondidas depois de o painel dizer "cancelado".
                    return True, ("o pedido foi entregue ao executor deste servidor: um boot em andamento nesta "
                                  "máquina é interrompido, e o desfecho continua vindo de quem executa")
                return True, "o pedido foi enviado ao worker; o desfecho continua vindo dele"
        except Exception:  # noqa: BLE001 - canal caindo no meio do envio não desfaz o pedido registrado
            log.exception("envio do cancelamento de %s ao worker %s", command_id, worker_id)
        return False, ("o worker não está conectado: o pedido fica registrado e o comando só fecha quando o "
                       "desfecho chegar")
    if rt is not None and verbo in VERBOS_QUE_ESPERAM_O_BOOT:
        tarefa = rt.tasks.get("boot")
        if tarefa is not None and not tarefa.done():
            # O boot roda em tarefa própria (`devices/manager.py`), e é ELA que precisa parar — cancelar a
            # tarefa do comando só abandonaria a espera, deixando o emulador subindo às escondidas.
            tarefa.cancel()
            return True, "o boot em andamento nesta máquina foi interrompido"
    return False, ("este verbo não tem ponto seguro de cancelamento: o pedido fica registrado e o comando fecha "
                   "como cancelado se ainda não tiver começado a agir; senão vale o desfecho real")


@router.post("/commands/{command_id}/cancel")
async def cancel_command(request: Request, command_id: str, body: CommandCancelBody | None = None) -> Any:
    """Pedir o cancelamento de um comando ABERTO — a ponta que faltava do que a máquina de estados já previa.

    `cancel_requested` não encerra nada: ele diz "quero que pare" e o desfecho continua sendo de quem executa.
    Por isso a resposta é sempre 200 com o comando como está, mais o que foi possível fazer: um `start` remoto de
    540 s é interrompido no agente, um boot local é interrompido aqui, e um verbo sem ponto seguro apenas fica
    registrado — mentir sobre isso seria pior do que a espera.

    Repetir o pedido é seguro: o estado não muda de novo e o sinal é reenviado, que é o que alguém faz quando o
    worker acabou de reconectar.
    """
    s = st(request)
    row = s.commands.get(command_id)
    if row is None:
        raise err(404, "not_found", f"Comando {command_id} não existe.")
    if CommandState(row["state"]) not in COMMAND_OPEN:
        raise err(409, "not_open", f"O comando {command_id} está em '{row['state']}': só um comando aberto pode "
                                   "ser cancelado.")
    autor = quem(request, body.requested_by if body else None)
    if CommandState(row["state"]) is not CommandState.cancel_requested:
        motivo = f"cancelamento pedido por {autor}" + (f": {body.note}" if body and body.note else "")
        try:
            row = s.commands.transition(command_id, CommandState.cancel_requested, reason=motivo)
        except InvalidCommandTransition as exc:
            # O desfecho chegou entre a leitura e a escrita: o comando já fechou sozinho, e não há o que cancelar.
            atual = s.commands.get(command_id)
            raise err(409, "not_open", f"O comando {command_id} fechou antes do cancelamento "
                                       f"('{atual['state'] if atual else '?'}').") from exc
        _publish_command(s, row)
    entregue, detalhe = await _entregar_cancelamento(s, row)
    s.bus.emit("log", f"{row['instance_id']}: cancelamento do comando {command_id} ({row['verb']}) pedido por "
                      f"{autor} — {detalhe}", level="warn", instance_id=row["instance_id"])
    atual = s.commands.get(command_id) or row
    return {"command": command_dto(atual).model_dump(mode="json"), "delivered": entregue, "detail": detalhe}


@router.post("/commands/{command_id}/resolve")
async def resolve_command(request: Request, command_id: str, body: CommandResolveBody) -> Any:
    """A decisão humana que tira um comando de `uncertain` — a outra porta de saída, para o que nenhuma sonda
    prova (o `reset` apagou os dados? o APK entrou?).

    Só `uncertain` é resolvível: comando terminal já tem desfecho, e reabrir seria apagar história. Quem
    resolveu e por quê ficam gravados no comando, porque "alguém decidiu" sem dizer quem é o mesmo tipo de
    afirmação vaga que esta fase inteira existe para eliminar.
    """
    s = st(request)
    row = s.commands.get(command_id)
    if row is None:
        raise err(404, "not_found", f"Comando {command_id} não existe.")
    if CommandState(row["state"]) not in COMMAND_UNSETTLED:
        raise err(409, "not_unsettled", f"O comando {command_id} está em '{row['state']}': só um comando "
                                        "'uncertain' é resolvido à mão.")
    alvo = {"succeeded": CommandState.succeeded, "failed": CommandState.failed,
            "cancelled": CommandState.cancelled}[body.outcome]
    autor = quem(request, body.requested_by)
    motivo = f"resolvido à mão por {autor}" + (f": {body.note}" if body.note else "")
    anterior = loads(row["result"], {}) if row["result"] else {}
    dados = {**(anterior or {}), "resolved_by": autor, "resolved_at": now_iso(), "resolution": body.outcome,
             "note": body.note, "previous_reason": row["reason"]}
    novo = s.commands.transition(command_id, alvo, reason=motivo, result=dados)
    _publish_command(s, novo)
    s.bus.emit("log", f"{novo['instance_id']}: o comando {command_id} ({novo['verb']}) era incerto e foi "
                      f"marcado como '{body.outcome}' por {autor}.", level="warn",
               instance_id=novo["instance_id"])
    return command_dto(novo)


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
    # `details` carrega o que o painel precisa para OFERECER a saída — no pré-voo, a lista por aparelho e quais
    # seguem aptos. Sem isso a recusa seria só uma frase, e "seguir só com os aptos" não teria como existir.
    return err(exc.status, exc.code, exc.message, **exc.details)


@router.post("/runs")
async def create_run(request: Request, body: RunCreate) -> Any:
    try:
        return st(request).runs.create(body)
    except RunError as exc:
        raise _run_error(exc) from exc


@router.get("/runs")
async def list_runs(request: Request, limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
                    instance_id: str | None = None, worker_id: str | None = None) -> Any:
    """A lista de execuções, paginada e filtrável por ONDE rodou.

    Sem paginação, o painel pedia 50 e as execuções mais antigas simplesmente sumiam — não havia como chegar
    nelas por nenhum caminho. Os filtros vêm da mesma fotografia do objetivo (migração 022): "o que rodou naquele
    servidor" e "o que rodou naquele aparelho" passam a ser perguntas que a tela sabe fazer.

    O filtro por aparelho também olha `runs.instance_ids` porque uma execução em `planning` ainda não tem
    objetivo materializado — e some-la da lista seria esconder justamente a que está acontecendo agora.
    """
    s = st(request)
    where, params = [], []
    if instance_id:
        where.append("(EXISTS (SELECT 1 FROM objectives o WHERE o.run_id=r.id AND o.instance_id=?)"
                     " OR r.instance_ids LIKE ?)")
        params += [instance_id, f'%"{instance_id}"%']
    if worker_id:
        where.append("EXISTS (SELECT 1 FROM objectives o WHERE o.run_id=r.id AND o.worker_id=?)")
        params.append(worker_id)
    sql = "SELECT r.* FROM runs r" + (" WHERE " + " AND ".join(where) if where else "")
    total = s.db.scalar("SELECT COUNT(*) FROM (" + sql + ") x", tuple(params)) or 0
    rows = s.db.query(sql + " ORDER BY r.created_at DESC LIMIT ? OFFSET ?", tuple(params) + (limit, offset))
    return {"runs": [s.repo.run_summary(r) for r in rows], "total": int(total), "limit": limit, "offset": offset}


@router.get("/runs/distribution")
async def preview_distribution(request: Request, count: int = Query(..., ge=1, le=64),
                               app_id: str = Query(..., min_length=1, max_length=80)) -> Any:
    """Quais aparelhos uma execução distribuída pegaria AGORA, por servidor — sem criar nada."""
    return st(request).runs.previa_de_distribuicao(DistributeSpec(count=count, app_id=app_id))


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


def _armazem_de(s: AppState, onde: str) -> Storage | None:
    """O back-end daquela LINHA. `disk` sempre existe (é a pasta local); os outros, só se forem o configurado."""
    if onde == s.storage.name:
        return s.storage
    if onde == DISK:
        return DiskStorage(s.cfg.evidence_dir)
    return None


def _servir_do_storage(armazem: Storage, chave: str, media_type: str,
                       *, ausente: tuple[str, str]) -> Any:
    """Serve um artefato PELA INTERFACE de storage, e não pelo disco deste processo (item 5.7).

    Três caminhos, nesta ordem, porque cada um é o barato do seu back-end:

    1. arquivo local → `FileResponse`, como sempre foi (envio por partes, `Range`, tudo de graça);
    2. URL pré-assinada → redireciona, e os bytes nem passam pelo backend;
    3. streaming pela interface — o que sobra quando o cliente do bucket não assina URL.
    """
    try:
        if (local := armazem.local_path(chave)) is not None:
            return FileResponse(local, media_type=media_type)
        if (link := armazem.url(chave)) is not None:
            return RedirectResponse(link, status_code=307)
        if (corpo := armazem.stream(chave)) is not None:
            return StreamingResponse(corpo, media_type=media_type)
    except StorageError as exc:
        log.warning("chave de storage recusada (%s): %s", chave, exc)
    raise err(404, ausente[0], ausente[1])


@router.get("/evidence/{evidence_id}")
async def evidence(request: Request, evidence_id: int) -> Any:
    s = st(request)
    r = s.db.one("SELECT * FROM evidence WHERE id=?", (evidence_id,))
    if r is None or not r["path"] or r["redacted"]:
        raise err(404, "not_found", "Evidência não disponível.")
    # A linha pode ter sido gravada por OUTRA réplica. Com `storage='s3'` o arquivo é de todos e a leitura
    # funciona aqui; em disco, ele está na máquina de quem gravou, e dizer isso é mais útil do que um 404 mudo
    # que faz o operador procurar defeito na retenção.
    onde = (r["storage"] if "storage" in r.keys() else None) or DISK
    dono = r["stored_by"] if "stored_by" in r.keys() else None
    if onde == DISK and dono and dono != s.cfg.owner_id:
        raise err(404, "em_outro_servidor",
                  f"A evidência está no disco de '{dono}', não neste servidor. Para que ela seja legível por "
                  "qualquer réplica, configure EVIDENCE_STORAGE=s3 (ver docs/evidencias.md).")
    # Serve pelo back-end da LINHA, não pelo que este processo tem configurado agora. Num parque que migrou
    # para S3 no meio do caminho, as linhas antigas continuam em disco: mandá-las para o bucket devolveria uma
    # URL pré-assinada de um objeto que não existe — um 307 para um 404. É para isto que a coluna existe.
    armazem = _armazem_de(s, onde)
    if armazem is None:
        raise err(404, "storage_nao_configurado",
                  f"Esta evidência está em '{onde}', que não está configurado neste backend. "
                  "Veja EVIDENCE_STORAGE em docs/evidencias.md.")
    ext = str(r["path"]).rsplit(".", 1)[-1].lower()
    return _servir_do_storage(armazem, str(r["path"]),
                              CONTENT_TYPES.get(ext, "text/plain"),
                              ausente=("not_found", "Arquivo de evidência ausente (retenção)."))


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
    # O cookie de sessão é a ÚNICA credencial que este endpoint pode receber de um navegador: a API `WebSocket`
    # não deixa a página definir cabeçalho, então `Authorization` aqui só existe para cliente de linha de
    # comando. Era por isso que o painel de outra máquina não tinha como abrir o fluxo de eventos.
    operador = s.sessions.operador_de(websocket.cookies.get(COOKIE))
    recusa = avaliar(par=websocket.client.host if websocket.client else None,
                     host=websocket.headers.get("host"), authorization=websocket.headers.get("authorization"),
                     publicos=publicos_de(s.cfg), token=s.cfg.api_token, sessao_valida=operador is not None)
    if recusa is not None:
        # 4401/4403 e não um `close()` mudo: o painel distingue "faça login" de "este nome não é aceito" pelo
        # código, e sem ele a reconexão automática ficaria tentando para sempre contra uma porta fechada.
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
            # Único fechamento server-side além da fila cheia — e era mudo: o painel mostrava "Reconectando" sem
            # que o log dissesse por quê. Agora cada um nomeia a causa.
            log.warning("painel %s: ressincronização — %d eventos desde o cursor %d (janela é 5000)",
                        websocket.client, s.bus.count_since(last), last)
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
                    log.warning("painel %s: fila de eventos estourou (o cliente não consumiu a tempo); "
                                "ressincronização e fechamento", websocket.client)
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


# ====================================================================== limites por servidor
def _limites_dos_servidores(s: Any) -> list[ServerLimitsDTO]:
    """Uma linha por máquina: o que ela declara, o que o dono decidiu, o que vale e a carga de agora.

    ESTE servidor não passa pelo `worker.yaml`: vagas e boots dele são as configurações vivas
    (`max_online_devices`, `boot_parallelism`), semeadas do `config.yaml`; o piso de RAM dele é a guarda do boot
    local (`android.min_free_ram_mb_after_boot`), que não se edita pelo painel.
    """
    lim = s.settings.get()
    base = s.cfg.file.limits
    host = s.cfg.owner_id
    fotos = s.scheduler.servidores()
    trabalhando = s.scheduler.trabalhando_por_servidor()
    por_servidor: dict[str, list[Any]] = {}
    for rt in s.devices.devices.values():
        if not rt.store:
            por_servidor.setdefault(s.scheduler.servidor_de(rt), []).append(rt)
    ids = [host] + sorted(k for k in set(fotos) | {r["id"] for r in s.db.query("SELECT id FROM workers")} if k != host)
    saida: list[ServerLimitsDTO] = []
    for wid in ids:
        linha = s.db.one("SELECT * FROM workers WHERE id=?", (wid,))
        res = loads(linha["resources"], {}) if linha is not None else {}
        cap = s.workers.capacidade(wid)
        decidido = s.workers.limites_definidos(wid)
        aparelhos = por_servidor.get(wid, [])
        if wid == host:
            declarado = ServerLimitValues(max_slots=base.max_online_devices, boot_parallelism=base.boot_parallelism,
                                          min_free_ram_mb=int(s.cfg.file.android.min_free_ram_mb_after_boot))
            decisao = ServerLimitValues(
                max_slots=lim.max_online_devices if lim.max_online_devices != base.max_online_devices else None,
                boot_parallelism=lim.boot_parallelism if lim.boot_parallelism != base.boot_parallelism else None,
                max_working=decidido.get("max_working"))
            efetivo = ServerLimitValues(max_slots=lim.max_online_devices, boot_parallelism=lim.boot_parallelism,
                                        max_working=decidido.get("max_working"),
                                        min_free_ram_mb=declarado.min_free_ram_mb)
            travado = {"min_free_ram_mb": "Guarda do boot deste servidor: `android.min_free_ram_mb_after_boot` "
                                          "no config.yaml."}
            nome = linha["name"] if linha is not None else f"{wid} (este servidor)"
        else:
            d = s.workers.limites_declarados(wid)
            declarado = ServerLimitValues(**d)
            decisao = ServerLimitValues(**decidido)
            efetivo = ServerLimitValues(
                max_slots=decidido.get("max_slots") or d.get("max_slots"),
                boot_parallelism=decidido.get("boot_parallelism") or d.get("boot_parallelism"),
                max_working=decidido.get("max_working"),
                min_free_ram_mb=decidido.get("min_free_ram_mb", d.get("min_free_ram_mb")))
            travado = {}
            nome = linha["name"] if linha is not None else wid
        saida.append(ServerLimitsDTO(
            worker_id=wid, name=nome, is_host=wid == host,
            connected=True if wid == host else bool(cap is not None and cap.connected),
            maintenance=bool(cap is not None and cap.maintenance), declared=declarado, decided=decisao,
            effective=efetivo, locked=travado, devices=len(aparelhos),
            online=sum(1 for rt in aparelhos if rt.state == InstanceState.online),
            working=trabalhando.get(wid, 0), cpu_percent=res.get("cpu_percent"), cpu_count=res.get("cpu_count"),
            ram_free_mb=res.get("ram_free_mb"), ram_total_mb=res.get("ram_total_mb")))
    return saida


@router.get("/servers/limits")
async def list_server_limits(request: Request) -> Any:
    return _limites_dos_servidores(st(request))


@router.put("/servers/{worker_id}/limits")
async def put_server_limits(request: Request, worker_id: str, body: ServerLimitsPatch) -> Any:
    """Muda os limites de UMA máquina. Campo enviado como `null` volta ao valor da máquina."""
    s = st(request)
    patch = {k: getattr(body, k) for k in body.model_fields_set}
    if not patch:
        raise err(400, "empty_patch", "Nada a mudar.")
    host = s.cfg.owner_id
    if worker_id != host and s.db.one("SELECT id FROM workers WHERE id=?", (worker_id,)) is None:
        raise err(404, "not_found", f"Servidor {worker_id} não existe.")
    if worker_id == host:
        if "min_free_ram_mb" in patch:
            raise err(400, "locked_limit", "O piso de RAM deste servidor é a guarda do boot local "
                                           "(`android.min_free_ram_mb_after_boot` no config.yaml).")
        base = s.cfg.file.limits
        vivos: dict[str, Any] = {}
        if "max_slots" in patch:
            vivos["max_online_devices"] = patch["max_slots"] or base.max_online_devices
        if "boot_parallelism" in patch:
            vivos["boot_parallelism"] = patch["boot_parallelism"] or base.boot_parallelism
        if vivos:
            valor = s.settings.update(vivos)
            s.bus.emit("settings.updated", "Limites atualizados", data={"settings": valor.model_dump()})
        if "max_working" in patch:
            s.workers.definir_limites(worker_id, {"max_working": patch["max_working"]}, por="painel")
    else:
        try:
            s.workers.definir_limites(worker_id, patch, por="painel")
        except WorkerError as exc:
            raise err(400, exc.code, exc.message) from exc
        # Aplica na hora no agente conectado; desconectado, recebe na próxima conexão (primeira batida).
        await s.workers.enviar_limites(worker_id)
    s.scheduler.wake()
    return next(x for x in _limites_dos_servidores(s) if x.worker_id == worker_id)


@router.get("/workers/{worker_id}")
async def get_worker(request: Request, worker_id: str) -> Any:
    s = st(request)
    row = s.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
    if row is None:
        raise err(404, "not_found", f"Worker {worker_id} não existe.")
    return s.workers.dto(row)


@router.get("/workers/devices/unbound")
async def unbound_worker_devices(request: Request) -> Any:
    """Aparelhos que os workers ANUNCIAM e que ainda não são instância deste parque (item 4.5).

    O inventário já chegava no `hello` (`devices[].serial`, `.adb_port`) e era descartado: o central só usava o
    que estivesse em `instances.external`. Aqui ele vira a lista do painel — é o primeiro passo de "conectar
    servidores novos e executar os mesmos comandos" sem editar `config.yaml`.
    """
    s = st(request)
    conhecidos = {rt.id for rt in s.devices.devices.values()}
    propostas: list[WorkerDeviceProposal] = []
    for w in s.workers.dtos():
        for d in w.devices:
            if d.instance_id and d.instance_id in conhecidos:
                continue
            propostas.append(WorkerDeviceProposal(worker_id=w.id, worker_name=w.name, serial=d.serial,
                                                  avd_name=d.avd_name, state=d.state, adb_port=d.adb_port))
    return propostas


@router.post("/workers/{worker_id}/devices/adopt", status_code=201)
async def adopt_worker_device(request: Request, worker_id: str, body: AdoptDeviceBody) -> Any:
    """Transforma um aparelho anunciado em instância AGORA — sem editar YAML, sem reiniciar o backend.

    A porta local do túnel é alocada por ESTE servidor (é quem conhece as portas em uso) e gravada junto com a
    porta de ADB do lado do worker. O mapa do túnel é reescrito no arquivo que `worker-tunnel.ps1 -MapaArquivo`
    relê a cada volta do laço: acrescentar aparelho deixa de exigir reinstalar a tarefa agendada.
    """
    s = st(request)
    linha = s.db.one("SELECT id, devices FROM workers WHERE id=?", (worker_id,))
    if linha is None:
        raise err(404, "not_found", f"Worker {worker_id} não existe.")
    declarado = next((d for d in (loads(linha["devices"]) or []) if d.get("serial") == body.serial), None)
    if declarado is None:
        raise err(404, "unknown_device", f"O worker {worker_id} não anunciou o aparelho '{body.serial}'. "
                                         "Ele declara o inventário no `hello` e na batida.")
    if not declarado.get("adb_port"):
        raise err(409, "no_adb_port", f"O aparelho '{body.serial}' foi anunciado sem porta de ADB: sem ela o "
                                      "túnel não tem para onde encaminhar. Atualize o agente do worker.")
    if declarado.get("instance_id") and declarado["instance_id"] in s.devices.devices:
        raise err(409, "already_bound", f"O aparelho '{body.serial}' já é a instância "
                                        f"{declarado['instance_id']}.")
    # O id que o AGENTE já usa vence o id inventado aqui. Ele resolve o despacho pelo `instance_id` do
    # `worker.yaml` dele (`worker/agent.py`: "este worker não hospeda X"); criar a instância com outro nome faria
    # todo comando falhar do outro lado e a conferência cruzada acusar divergência na batida seguinte.
    iid = body.instance_id or declarado.get("instance_id") or None
    try:
        rt = s.devices.adotar_aparelho(worker_id, serial=body.serial, adb_port=int(declarado["adb_port"]),
                                       instance_id=iid, avd_name=declarado.get("avd_name"))
    except ValueError as exc:
        raise err(409, "rejected", str(exc)) from exc
    # O aparelho novo já nasce com o ciclo de vida que aquele worker declarou saber executar.
    declarados = s.workers.verbs_de(worker_id)
    s.devices.bind_worker(worker_id, None if declarados is None
                          else sem_hibernacao(declarados, s.workers.hiberna(worker_id)))
    arquivo = s.devices.escrever_mapa_do_tunel(worker_id)
    return {"instance": s.devices.dto(rt), "tunnel_map_file": str(arquivo) if arquivo else None,
            "tunnel_map": s.devices.mapa_do_tunel(worker_id)}


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

    async def fechar() -> None:
        # 4409 = "conflito": outra conexão deste mesmo worker assumiu o canal. O agente duplicado para em vez de
        # ficar batendo por um socket órfão que o central já não usa para despachar.
        with contextlib.suppress(Exception):
            await websocket.close(code=4409)

    link = s.workers.attach(worker_id, send, fechar)
    # O aparelho daquele worker passa a aceitar o ciclo de vida que o agente declarou — menos `hibernate`/`wake`
    # quando a máquina dele não salva snapshot: é `Hello.hibernation` que sabe, e é `supported_verbs` que o painel lê.
    s.devices.bind_worker(worker_id, sem_hibernacao(hello.verbs, hello.hibernation))
    # E as CAPACIDADES declaradas no mesmo `hello`: o que o aparelho é, não só que verbo aceita.
    s.devices.capacidades_do_worker(worker_id, hello.devices)
    # E as três fontes de inventário (config/banco, `instances.worker_id` e o `worker.yaml` de lá) passam a ser
    # CONFRONTADAS: os dados já trafegavam no `hello` e eram descartados (achado #47).
    s.devices.conferir_inventario(worker_id, hello.devices)
    # `appium: local` passa a VALER: o aparelho daquele worker é dirigido pelo Appium da máquina dele, com o
    # udid de lá. Até aqui a declaração era aceita, aparecia na tela e não mudava nada.
    #
    # Antes de trocar, CONFERE: aceitar a declaração e descobrir no meio da primeira tarefa que não há Appium do
    # outro lado seria trocar um caminho provado (o central, pelo túnel) por um silêncio. Não responder não
    # derruba o worker — ele segue trabalhando pelo Appium daqui, e o motivo fica visível na Infraestrutura.
    modo, url = hello.appium_mode, hello.appium_url
    if modo == "local" and not await asyncio.to_thread(appium_no_ar, url or ""):
        s.workers.marcar_detalhe(worker_id, f"Appium local declarado em {url} não respondeu: este servidor "
                                            "continua dirigindo os aparelhos deste worker")
        modo, url = "central", None
    s.devices.bind_worker_appium(worker_id, appium_mode=modo, appium_url=url, devices=hello.devices)
    _anunciar_inflight(s, worker_id, hello.inflight)
    esperados = {r["id"]: r["avd_name"] for r in
                 s.db.query("SELECT id, avd_name FROM instances WHERE worker_id=?", (worker_id,))}
    bem_vindo = s.workers.welcome(esperados).model_dump()
    if credencial:
        # Só aqui, e uma única vez: a credencial em claro não é guardada nem repetida.
        bem_vindo["credential"] = credencial
    await websocket.send_json(bem_vindo)
    s.bus.emit("log", f"Worker {hello.name} conectou ({hello.os}, agente {hello.agent_version}, "
                      f"{hello.max_slots} vaga(s), Appium {hello.appium_mode}).")
    if s.workers.dto(s.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))).agent_outdated:
        s.bus.emit("log", f"Worker {hello.name}: o agente roda {hello.agent_version} e este servidor roda "
                          f"{agent_version()}. Atualize o agente (scripts/worker-install.ps1 ou "
                          "worker-install.sh) — código diferente dos dois lados é defeito que só aparece "
                          "no meio de uma execução.", level="warn")
    # A reconciliação do estado desejado NÃO cabe aqui: o `hello` declara todo aparelho como `unknown` (o agente
    # não sonda antes do handshake, de propósito). Ela acontece na primeira batida com estado de verdade, em
    # `_reconciliar_uma_vez`.
    try:
        while True:
            bruto = await websocket.receive_json()
            # Este socket ainda é o canal vivo deste worker? Se uma conexão nova já assumiu, o handler velho sai
            # em vez de continuar tratando mensagem de um canal que o central já não usa.
            if s.workers.live.get(worker_id) is not link:
                break
            try:
                msg = parse_upstream(bruto)
            except ValueError as exc:
                log.warning("worker %s: %s", worker_id, exc)
                continue
            await _tratar_mensagem_do_worker(s, worker_id, link, msg)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("canal do worker %s", worker_id)
    finally:
        # `link`: só desmonta se ESTE socket ainda for o canal vivo. Um socket que morreu TARDE (rede direta,
        # notebook suspenso, agente duplicado) apagava o link NOVO, marcava os comandos dele como incertos e
        # tirava os verbos do aparelho — o painel mostrava o worker online e todo despacho recusava.
        if s.workers.detach(worker_id, "conexão encerrada", link):
            # Sem agente do outro lado, o aparelho volta a aceitar só o que o transporte alcança.
            s.devices.bind_worker(worker_id, None)
            # E volta a ser dirigido pelo Appium DESTE servidor: o da outra máquina foi embora com ela.
            s.devices.bind_worker_appium(worker_id, appium_mode="central", appium_url=None, devices=[])
            s.bus.emit("log", f"Worker {hello.name} desconectou.", level="warn")


def _anunciar_inflight(s: AppState, worker_id: str, inflight: list[str]) -> None:
    """O agente reconectou dizendo o que AINDA está executando. Queda de canal não cancela trabalho, então um
    comando que o central marcou `uncertain` pode estar vivo do outro lado — e quem olha o painel precisa saber
    disso antes de decidir repetir. O estado não muda (`uncertain` só sai por desfecho de verdade)."""
    for command_id in inflight[:50]:
        try:
            row = s.commands.get(command_id)
            if row is None or row["worker_id"] not in (None, worker_id):
                continue
            dto = command_dto(row)
            s.bus.emit("command.updated",
                       f"{dto.instance_id}: {dto.verb} — o worker reconectou e ainda está executando este comando",
                       level="warn", instance_id=dto.instance_id,
                       data={"command": dto.model_dump(), "inflight": True})
        except Exception:  # noqa: BLE001 - aviso nunca derruba o canal
            log.exception("inflight do comando %s", command_id)


async def _tratar_mensagem_do_worker(s: AppState, worker_id: str, link: WorkerLink,
                                     msg: Heartbeat | Ack | Progress | Result | Hello) -> None:
    """Uma mensagem do worker, traduzida em estado persistido. Nada aqui pode escapar: exceção neste ponto cairia
    no `except` de fora e derrubaria o canal do worker por causa de um erro de banco."""
    try:
        if isinstance(msg, Heartbeat):
            s.workers.on_heartbeat(worker_id, msg, link)
            # A batida traz também o que o agente DECLARA sobre cada aparelho (imagem, nível de API, ABIs, GMS).
            # É o que permite ao pré-voo recusar com explicação antes de agendar, em vez de descobrir no meio.
            s.devices.capacidades_do_worker(worker_id, msg.devices)
            s.devices.conferir_inventario(worker_id, msg.devices)
            # A batida traz o estado de cada aparelho daquela máquina: é o instante em que chega informação nova
            # capaz de fechar um comando incerto. Era exatamente o caso vivo — `start` incerto por prazo de boot
            # com o aparelho relatado `running` na batida seguinte, e o comando ficando incerto para sempre.
            reconciliar_incertos(s)
            # E é também o instante em que dá para saber o que aquela máquina PERDEU num reboot. No `hello` não
            # dava: `agent._declarados()` manda tudo como `unknown` de propósito (sondar seis aparelhos custa
            # ~28 s e o handshake morreria antes). Então a reconciliação espera a primeira batida que traz
            # estado de verdade, e acontece UMA vez por conexão — na batida seguinte o aparelho está `booting` e
            # um segundo pedido só disputaria com o primeiro.
            _reconciliar_uma_vez(s, worker_id, link, msg.devices)
        elif isinstance(msg, Ack):
            # O ACK deixa de morrer num `set` em memória: "o worker RECEBEU" vira estado no banco, com hora. É o
            # que separa, numa queda, "não sabemos se chegou" de "chegou e não sabemos o efeito".
            s.workers.on_ack(worker_id, msg.command_id)
            _mudar_estado_do_worker(s, msg.command_id, worker_id, CommandState.acked,
                                    de={CommandState.dispatched})
        elif isinstance(msg, Progress):
            _progresso_do_worker(s, msg.command_id, worker_id, msg.message)
        elif isinstance(msg, Result):
            await _desfecho_do_worker(s, worker_id, link, msg)
        elif isinstance(msg, Hello):
            # Re-declaração: o worker mudou de inventário ou de capacidade sem reconectar. Vale como batida,
            # e não repete autenticação — quem já está dentro do canal não se reautentica a cada mensagem.
            s.workers.on_heartbeat(worker_id, Heartbeat(devices=msg.devices, resources=msg.resources), link)
            s.devices.capacidades_do_worker(worker_id, msg.devices)
            s.devices.conferir_inventario(worker_id, msg.devices)
    except Exception:  # noqa: BLE001 - erro ao registrar não pode custar a conexão do worker
        log.exception("mensagem %s do worker %s", type(msg).__name__, worker_id)


def _mudar_estado_do_worker(s: AppState, command_id: str, worker_id: str, alvo: CommandState,
                            de: set[CommandState]) -> Row | None:
    """Transição pedida pelo worker, só a partir dos estados em que ela faz sentido. Fora deles não é erro: é
    mensagem fora de ordem, ou comando já encerrado por outra via."""
    row = s.commands.get(command_id)
    if row is None or row["worker_id"] not in (None, worker_id) or CommandState(row["state"]) not in de:
        return None
    try:
        novo = s.commands.transition(command_id, alvo, worker_id=worker_id)
    except InvalidCommandTransition:
        return None
    _publish_command(s, novo)
    return novo


def _progresso_do_worker(s: AppState, command_id: str, worker_id: str, mensagem: str) -> None:
    """Primeiro progresso do worker = `running`. Antes de existir isto, `running` era gravado no central ANTES de
    o comando sair, e `started_at` ficava a menos de 1 ms de `dispatched_at` em todos os comandos reais.

    E o progresso deixa de ser um evento `log` solto: vai como `command.updated`, com a instância e o comando,
    que é o que a interface sabe mostrar.
    """
    # Esperar vaga na fila de boot do worker NÃO é executar: o comando continua `dispatched`/`acked` (carimbar
    # `running` aqui seria o mesmo carimbo falso que saiu do despacho), e o prazo é empurrado — porque contar a
    # espera na fila como tempo de boot transformava a proteção contra ANR em "resultado incerto".
    na_fila = MARCA_DE_FILA in mensagem
    if na_fila:
        linha = s.commands.get(command_id)
        verbo = linha["verb"] if linha is not None else ""
        s.workers.adiar(worker_id, command_id, PRAZO_POR_VERBO.get(verbo, PRAZO_PADRAO_S))
    row = None if na_fila else _mudar_estado_do_worker(s, command_id, worker_id, CommandState.running,
                                                       de={CommandState.dispatched, CommandState.acked})
    row = row or s.commands.get(command_id)
    if row is None:
        s.bus.emit("log", f"{command_id}: {mensagem}")
        return
    dto = command_dto(row)
    s.bus.emit("command.updated", f"{dto.instance_id}: {dto.verb} — {mensagem}", level="info",
               instance_id=dto.instance_id, data={"command": dto.model_dump(), "progress": mensagem})


#: Desfecho do worker → estado de comando. Um mapa, não um `if` espalhado por dois arquivos.
DESFECHO = {"succeeded": CommandState.succeeded, "failed": CommandState.failed,
            "uncertain": CommandState.uncertain, "cancelled": CommandState.cancelled}


async def _desfecho_do_worker(s: AppState, worker_id: str, link: WorkerLink, msg: Result) -> None:
    """O resultado do worker, inclusive o TARDIO.

    Enquanto o comando está em voo, quem trata é `on_result` (o futuro que `dispatch` espera). Se o canal caiu no
    meio, o central já marcou `uncertain` e não havia mais ninguém esperando: o resultado que o agente produziu
    depois era descartado, e "a rede piscou" continuava significando "a ação falhou". Agora o comando é
    procurado no BANCO e a transição `uncertain → succeeded/failed` — que a tabela de estados sempre permitiu —
    é aplicada, conferindo cerca e worker.

    O `result_ack` sai SEMPRE que a mensagem foi tratada, mesmo quando não mudou nada (comando já terminal, cerca
    velha): senão o agente reenviaria o mesmo resultado para sempre.
    """
    tratado = s.workers.on_result(worker_id, msg, fence=msg.fence)
    if not tratado:
        tratado = _resultado_tardio(s, worker_id, msg)
    with contextlib.suppress(Exception):
        await link.send(ResultAck(command_id=msg.command_id).model_dump())


def _resultado_tardio(s: AppState, worker_id: str, msg: Result) -> bool:
    row = s.commands.get(msg.command_id)
    if row is None:
        return False
    if row["worker_id"] != worker_id:
        log.warning("resultado tardio de %s para comando de %s: recusado", worker_id, row["worker_id"])
        return False
    if msg.fence is None or int(row["fence"]) != int(msg.fence):
        log.warning("resultado tardio de %s com cerca %s (esperada %s): recusado", worker_id, msg.fence,
                    row["fence"])
        return False
    alvo = DESFECHO.get(msg.outcome)
    if alvo is None or CommandState(row["state"]) is alvo:
        return True                     # nada a fazer, mas a mensagem foi tratada: confirme e deixe o agente em paz
    try:
        motivo = msg.reason or "desfecho recebido do worker depois da reconexão"
        novo = s.commands.transition(msg.command_id, alvo, reason=motivo, result=msg.data, worker_id=worker_id)
    except InvalidCommandTransition:
        log.info("resultado tardio de %s: comando %s já estava em %s", worker_id, msg.command_id, row["state"])
        return True
    _publish_command(s, novo)
    s.bus.emit("log", f"{novo['instance_id']}: o worker reconectou e entregou o desfecho de {msg.command_id} "
                      f"({alvo.value}).")
    return True
