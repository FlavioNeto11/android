"""Rotas REST + WebSocket. Ver docs/api-contract.md."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import threading
import time
from time import monotonic
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from .automation.appium_driver import appium_no_ar
from .automation.driver import DriverError
from .contexto import contexto_do_aparelho
from .storage import DISK, DiskStorage, Storage, StorageError
from .commands.states import COMMAND_OPEN, COMMAND_UNSETTLED, InvalidCommandTransition
from .commands.reconciler import VERIFICAVEL_POR_ESTADO, verificar_comando
from .commands.store import command_dto
# O despacho de comandos mora em `commands/despacho.py` (não é HTTP, e o `state` precisa dele sem importar a API).
# Os nomes seguem acessíveis por aqui: as rotas os usam, e os testes os importam de `app.api`.
from .commands.despacho import (LIFECYCLE_ACTIONS, DespachoRecusado, _abrir_comando, _anunciar_inflight, _app_for,
                                _despachar, _despachar_trabalho, _do_action, _entregar_cancelamento,
                                _fechar_cancelado, _marcar_entregue, _precheck, _publish_command,
                                _reconciliar_uma_vez, _release_pronta_para, _tratar_mensagem_do_worker,
                                executar_envelope, pedir_ciclo_de_vida, reconciliar_estado_desejado, remediar,
                                remediar_reiniciando)
from .db import Row, loads
from .devices.adb import AdbError
from .devices import conectividade
from .devices.manager import ControlError, DeviceRuntime
from .devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from .devices.proxy import ProxyApplyBody, ProxyInput  # modelos da loja de apps fora de models.py (menos conflito)
from .devices.verbs import PRAZO_POR_VERBO, verbos_suportados  # noqa: F401 - os testes ajustam o prazo por aqui
from .models import (DistributeSpec, ServerLimitsDTO, ServerLimitsPatch, ServerLimitValues,
                     AdoptDeviceBody, ApprovalBatchBody, ApprovalDecision, AppInput, AppPatch, BulkBody,
                     CapabilityDTO, WorkerDeviceProposal,
                     CommandCancelBody, CommandResolveBody, CommandState, InstanceActionBody,
                     InstancePatch, InstanceState, TrainingSaveBody, TrainingStartBody, PolicyGroupCreate, PolicyGroupPatch, ProfileAccountCreate,
                     ProfileAccountPatch, ProfilePolicyPatch,
                     AppInstallBody, AppVerifyBody, CredentialUpdate, MemoryCreate, PersonaCreate, PersonaPatch,
                     PersonaPreviewBody, ProfileCreate, ProfilePatch,
                     ReleaseChannel, ReleaseImportBody, ReleaseLifecycleBody, SessionStatus,
                     SignatureApprovalBody, StoreBody, WorkerEnrollBody, WorkerMaintenanceBody, WorkerRemoveBody,
                     LoginBody, ManualInput, PanelSessionInfo, ReleaseBody, ResolveBody, RunCreate)
from .metricas import metricas
from .contracts.skills.resolve import SkillResolveRequest
from .modules.skills.domain.document import JsonObject
from .modules.skills.domain.lifecycle import ContentTampered
from .planning import costs
from .security import access as acesso           # o módulo, não os nomes: `LOOPBACK_DE_TESTE` é injetado em tempo
from .security import local_secret               # de execução e um `from ... import` congelaria o valor antigo
from .security.access import avaliar, publicos_de
from .security.sessions import COOKIE, VALIDADE_S, NomeInvalido, normalizar_nome, operador_atual
from .state import AppState
from .workers.captura import ErroDeMidia
from .workers.protocol import EnvioDeMidia, Hello, Refused, parse_upstream
from .workers.portao import BLOQUEIO_S
from .workers.registry import INSCRICAO_TTL_S, WorkerError
from .version import agent_version
from .planning.capabilities import load_catalog
from .planning.catalog import registered
from .releases.catalog import ReleaseValidationError
from .social.service import SocialError
from .taskqueue.repository import CONTENT_TYPES
from .taskqueue.service import RunError
from .util import iso_in, new_token, now_iso
from .vitrine import _apps_changed, app_dto, apps_list, convergir_o_parque, vitrine

from .devices.verbs import sem_hibernacao

log = logging.getLogger("poc.api")
router = APIRouter(prefix="/api")

#: Router SÓ do canal do worker, separado do resto de propósito. É o que o listener dedicado do túnel serve
#: (`main.create_worker_app`): nele não existe rota REST nenhuma, então uma requisição que chegue pela porta do
#: túnel encontra 404 em vez da API inteira. O app principal inclui os dois, para o modo em que o worker fala
#: direto com a porta de rede do central.
worker_router = APIRouter(prefix="/api")


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


async def recusa_do_despacho(request: Request, exc: DespachoRecusado) -> Response:
    """Tradução de `DespachoRecusado` na borda HTTP (registrada em `main.create_app`).

    O despacho saiu deste arquivo e deixou de levantar `HTTPException` (o FastAPI mora só aqui e em `main`). A
    resposta continua a mesma: monta o `HTTPException` que `err()` montava e entrega ao tratador padrão do FastAPI,
    que é quem respondia quando a exceção era HTTP — mesmo status, mesmo corpo `{"detail": {...}}`.
    """
    return await http_exception_handler(request, HTTPException(status_code=exc.status, detail=exc.detail))


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


@router.get("/desempenho")
async def desempenho(request: Request, janelas: int = Query(24, ge=0, le=672),
                     dias: int = Query(0, ge=0, le=90)) -> Any:
    """Métricas agregadas de desempenho (contrato C5, adendo v0.20): o acumulado DESTE processo desde a partida e
    as últimas `janelas` gravadas em `measurements` (15 min cada; todas as réplicas, com `owner`). Só lê — nada
    aqui toca aparelho ou provedor. Distribuição sem amostra devolve `None` no percentil: desconhecido, não zero.

    `dias` > 0 acrescenta `historico`: `desempenho.resumo` dos últimos `dias` a partir das tabelas que já existem
    (objetivos, etapas, ações, `ai_calls`, comandos, boot), com p50/p95/n por entidade. 0 (padrão) devolve o de
    antes — a consulta varre a janela inteira e não precisa pesar em quem só quer o acumulado."""
    s = st(request)
    linhas = s.db.query("SELECT ts, data FROM measurements WHERE kind='metricas' ORDER BY id DESC LIMIT ?",
                        (janelas,)) if janelas else []
    out: dict[str, Any] = {"processo": {**metricas.snapshot(), "owner": s.cfg.owner_id},
                           "janelas": [{"ts": r["ts"], **loads(r["data"], {})} for r in linhas]}
    if dias:
        from . import desempenho as historico  # noqa: PLC0415
        # Em thread: com 90 dias são dezenas de milhares de linhas de `ai_calls`, e o laço de eventos serve o painel.
        out["historico"] = await asyncio.to_thread(historico.resumo, s.db, desde_iso=iso_in(-dias * 86400),
                                                   precos=s.cfg.file.ai.prices)
    return out


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
    """Item 7.7 ("quanto vai custar?" do Osintgram): o comando digitado casa com uma habilidade ou um fluxo
    conhecido? Devolve a cobertura e a estimativa em US$ do plano, ou `null` — sem nada casado não há o que estimar.

    Fase G (decisão P2): a MESMA resolução que o planejamento usa (`skill_planner`: habilidade publicada atrás de
    `skills.enabled`, depois fluxo ativo atrás de `ai.flows`), então a estimativa é do plano que REALMENTE rodaria.
    Mudança visível: antes a rota ignorava `ai.flows` e estimava um fluxo que a execução nunca usaria. Para
    habilidade, `flow_id` traz a versão (`ig.abrir_conversa@1`) e `skill_ref` diz que não é fluxo."""
    from .social.capacidades import cobertura_do_fluxo  # noqa: PLC0415

    s = st(request)
    casado = s.skill_planner.for_command(command, None)
    if casado is None or casado.plan is None:
        return None
    if casado.legacy_flow_id is not None:
        row = s.db.one("SELECT * FROM flows WHERE id=?", (casado.legacy_flow_id,))
        return cobertura_do_fluxo(s, row) if row is not None else None
    modelo = {"id": str(casado.ref), "app_id": casado.plan.app_id, "plan": casado.plan.model_dump_json()}
    return {**cobertura_do_fluxo(s, modelo), "skill_ref": str(casado.ref)}


@router.post("/skills/resolve", response_model=None)
async def skills_resolve(request: Request, body: SkillResolveRequest) -> JsonObject:
    """Fase I: a RESOLVE sozinha — que habilidade a frase pede, com que valores tipados, ou que pergunta falta.

    SEM criar execução, sem planejador e sem IA: é a mesma cadeia que `_plan` usa (`skill_planner.resolve_intent`),
    com as etapas por IA no provedor nulo (`not_run` na trilha). Atrás de `skills.enabled`: desligado, 409 — a
    resolução por habilidade não existe (404, como as rotas do ensino v2). A resposta sempre traz `gated_by_config`, os interruptores lidos NESTA
    chamada (`ai.flows` decide se o fluxo legado entra), porque a mesma frase resolve diferente com eles mudados.
    Aparelhos/perfis no corpo conferem o escopo como no planejamento; sem eles, qualquer escopo casa (prévia)."""
    s = st(request)
    portas: JsonObject = {"skills.enabled": s.cfg.file.skills.enabled, "ai.flows": s.cfg.file.ai.flows}
    if not s.cfg.file.skills.enabled:
        # 404 e não 409: o mesmo código e status das rotas de `modules/skills/presentation` com o flag desligado
        # (a rota não existe nesta instalação) — o painel trata `skills_disabled` de um jeito só.
        raise err(404, "skills_disabled", "As habilidades estão desligadas (skills.enabled: false): não há resolução "
                                          "por habilidade para prever.", gated_by_config=portas)
    perfis: list[str | None] | None = None
    if body.instance_ids or body.profile_ids:
        perfis = list(body.profile_ids)
        for iid in body.instance_ids:
            if s.db.one("SELECT id FROM instances WHERE id=?", (iid,)) is None:
                raise err(404, "not_found", f"Instância {iid} não existe.")
            perfis.append(s.social.profile_of(iid))
    try:
        resolvido = s.skill_planner.resolve_intent(body.command, perfis)
    except ContentTampered as exc:
        # Versão publicada alterada por fora do repositório entre as candidatas: recusa explícita, nunca 500 nem
        # "resolveu outra coisa em silêncio" (a execução recusa do mesmo jeito, em `needs_input`).
        raise err(409, exc.code, str(exc), gated_by_config=portas) from exc
    return {**resolvido.as_dict(), "gated_by_config": portas}


@router.put("/flows/{flow_id}")
async def update_flow(request: Request, flow_id: str, patch: dict[str, Any]) -> Any:
    s = st(request)
    if s.db.one("SELECT id FROM flows WHERE id=?", (flow_id,)) is None:
        raise err(404, "not_found", "Fluxo não encontrado.")
    if patch.get("status") not in ("active", "disabled"):
        raise err(400, "invalid", "status deve ser 'active' ou 'disabled'.")
    # Fase G (guarda apontada pela fase D): fluxo ADOTADO por uma habilidade publicada não se religa por aqui — o
    # mesmo comando ficaria vivo nos dois backends. Voltar ao fluxo é desfazer a adoção, que desabilita a versão
    # na mesma transação.
    # Fase J: nem por outra habilidade publicada com o MESMO comando (critério da fase: nenhum fluxo ativo e skill
    # publicada com o mesmo comando). A conferência e a escrita numa transação: no SQLite, a publicação concorrente
    # espera (BEGIN IMMEDIATE).
    with s.db.tx():
        if patch["status"] == "active" and (adotante := s.skill_repo.published_adopter(flow_id)) is not None:
            raise err(409, "flow_adopted", f"O fluxo foi adotado pela habilidade {adotante.ref}, que está publicada: "
                                           "religá-lo deixaria o mesmo comando vivo nos dois lugares. Desfaça a adoção "
                                           "para voltar ao fluxo.")
        chave = s.db.scalar("SELECT match_key FROM flows WHERE id=?", (flow_id,))
        if patch["status"] == "active" and (outra := s.skill_repo.published_with_command(chave)) is not None:
            raise err(409, "command_published", f"A habilidade {outra.ref} está publicada com o mesmo comando: "
                                                "religar o fluxo deixaria o comando vivo nos dois lugares. Desabilite "
                                                "a habilidade antes.")
        s.db.execute("UPDATE flows SET status=? WHERE id=?", (patch["status"], flow_id))
    return next(f for f in s.scheduler.flows.list() if f["id"] == flow_id)


@router.delete("/flows/{flow_id}", status_code=204)
async def delete_flow(request: Request, flow_id: str) -> Response:
    s = st(request)
    # Fluxo adotado por uma habilidade é o caminho de volta da adoção (`release_flow` o religa): apagá-lo deixaria
    # a habilidade sem ter para onde desfazer. Desligar continua possível; apagar, só depois de desfazer.
    if (dona := s.skill_repo.adopter_id(flow_id)) is not None:
        raise err(409, "flow_adopted", f"O fluxo foi adotado pela habilidade {dona}: apagá-lo tiraria o caminho de "
                                       "volta da adoção. Desfaça a adoção antes de apagar.")
    s.db.execute("DELETE FROM flows WHERE id=?", (flow_id,))
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
    if s.apps.id_do_pacote(body.package) is not None:
        # Loja de apps: o pacote é a identidade que as versões, o estado por aparelho e a vitrine usam. Dois
        # cadastros do mesmo pacote dividiriam as contagens em dois cartões que falam do mesmo aplicativo.
        raise err(409, "package_exists", f"O pacote {body.package} já está cadastrado.")
    app_id = s.apps.criar(name=body.name, package=body.package, activity=body.activity or None,
                          apk_path=body.apk_path or None, nav_hints=body.nav_hints or None,
                          known_selectors=body.known_selectors, category=body.category)
    _apps_changed(s)
    return app_dto(s.apps.obter(app_id), s)


@router.put("/apps/{app_id}")
async def update_app(request: Request, app_id: str, body: AppPatch) -> Any:
    s = st(request)
    if s.apps.obter(app_id) is None:
        raise err(404, "not_found", "App não encontrado.")
    data = body.model_dump(exclude_unset=True)
    if data.get("package") and s.apps.id_do_pacote(data["package"], exceto=app_id) is not None:
        # Mesma regra do cadastro: dois apps com o mesmo pacote dividiriam a vitrine em dois cartões do mesmo app.
        raise err(409, "package_exists", f"O pacote {data['package']} já está cadastrado em outro app.")
    _validate_apk(s, data.get("apk_path"))
    # Texto vazio vindo do formulário quer dizer "sem valor" (o repositório grava o que recebe).
    for k in ("activity", "apk_path", "nav_hints"):
        if k in data and not data[k]:
            data[k] = None
    s.apps.atualizar(app_id, data)
    _apps_changed(s)
    return app_dto(s.apps.obter(app_id), s)


@router.delete("/apps/{app_id}", status_code=204)
async def delete_app(request: Request, app_id: str) -> Response:
    s = st(request)
    row = s.apps.obter(app_id)
    if row is None:
        raise err(404, "not_found", "App não encontrado.")
    if row["builtin"]:
        raise err(409, "builtin", "O app de QA embutido não pode ser removido.")
    s.apps.remover(app_id)
    _apps_changed(s)
    for rt in s.devices.devices.values():
        s.devices.publish(rt)
    return Response(status_code=204)


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
    return await _start_session_job(request, profile_id, force_login=False, label="autenticação do Instagram")


@router.post("/instagram/profiles/{profile_id}/verify", status_code=202)
async def verify_profile(request: Request, profile_id: str) -> Any:
    """Relê do aparelho qual conta está aberta. Não digita senha: só observa.

    `observe_only` faz a promessa valer: num aparelho deslogado, para na tela de login em vez de autenticar.
    """
    return await _start_session_job(request, profile_id, force_login=False, observe_only=True,
                                    label="verificação da conta")


@router.post("/instagram/profiles/{profile_id}/logout", status_code=202)
async def logout_profile(request: Request, profile_id: str) -> Any:
    """Encerra a sessão no aparelho apagando os dados do app — é o jeito determinístico de sair.

    Apaga também cache e preferências do Instagram naquele aparelho; por isso é uma ação explícita, nunca efeito
    colateral de outra operação.
    """
    s = st(request)
    rt, profile = _profile_device(s, profile_id)
    _recusa_pelo_portao(profile, "logout")
    # "Sair da conta" APAGA os dados do app: é a operação mais destrutiva desta tela e era a que menos registro
    # tinha. Agora é um comando, com id, desfecho e `uncertain` quando o adb não responde.
    return {**_despachar_trabalho(s, rt, "session.logout", lambda: _do_logout(s, rt, profile_id),
                                  label="logout do Instagram", params={"profile_id": profile_id}),
            "profile_id": profile_id}


async def _do_logout(s: AppState, rt: DeviceRuntime, profile_id: str) -> None:
    # O pacote do perfil vem do REGISTRO de aplicativos (quem provê a conta), resolvido uma vez na composição
    # (`social_repo.app_package`) — o mesmo que a porta de sessão e "Conectar" usam.
    package = s.social_repo.app_package
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


def _recusa_pelo_portao(profile: Any, acao: str) -> None:
    """Recusa pela MESMA regra que decide o botão (`social/sessao_gate.py`), com o código da fase.

    Sem isto, "Conectar" num aparelho sem o Instagram virava um 202 e uma tentativa de abrir um app que não existe.
    """
    acoes = profile.session_actions
    if acoes is None:
        return
    portao = getattr(acoes, acao)
    if not portao.allowed:
        codigo = {"app_missing": "app_not_installed", "app_unknown": "app_not_verified",
                  "app_installing": "app_busy", "authenticating": "session_busy"}.get(acoes.phase, "not_allowed")
        raise err(409, codigo, portao.reason or acoes.detail)


async def _exigir_internet(s: Any, rt: Any) -> None:
    """Conectar precisa de internet DENTRO do aparelho. `online` não prova isso (android-06, 25/09/2026: online,
    sem DNS, e o login virava "An unexpected error occurred"). Resultado velho ou desconhecido → sonda agora; e o
    que não se confirma recusa — incerteza não vira tentativa de login numa conta real."""
    info = rt.connectivity
    if info.state == "unknown" or time.monotonic() - rt.connectivity_mono > conectividade.VALIDADE_S:
        info = await s.devices.conferir_conectividade(rt)
    if info.state != "healthy":
        raise err(409, "device_no_internet", info.detail)


async def _start_session_job(request: Request, profile_id: str, *, force_login: bool, label: str,
                             observe_only: bool = False) -> Any:
    s = st(request)
    rt, profile = _profile_device(s, profile_id)
    if not profile.credential.configured and not observe_only:
        raise err(409, "no_credential", "Cadastre a senha deste perfil antes de conectar.")
    _recusa_pelo_portao(profile, "verify" if observe_only else "connect")
    if rt.state not in (InstanceState.online, InstanceState.booting, InstanceState.stopped,
                        InstanceState.hibernated, InstanceState.absent):
        raise err(409, "device_unavailable", f"O aparelho está em '{rt.state.value}'.")
    if rt.state != InstanceState.online:
        s.devices.request_start(rt, "conectar perfil do Instagram")
        raise err(409, "device_starting", "O aparelho está sendo ligado; tente novamente em instantes.")
    if not observe_only:
        await _exigir_internet(s, rt)
    verbo = "session.verify" if observe_only else "session.connect"
    provedor = s.provedor_do_perfil()
    if provedor is None:
        raise err(409, "no_session_provider", "Nenhum aplicativo registrado provê a sessão deste perfil.")
    return {**_despachar_trabalho(
        s, rt, verbo,
        lambda: provedor.ensure_session(rt, profile_id, force_login=force_login, observe_only=observe_only),
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


@router.get("/app-store")
async def app_store(request: Request) -> Any:
    """A vitrine da loja de apps: por app, ícone, versão promovida, aparelhos por versão e o que pede atenção."""
    return vitrine(st(request))


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
#: `.xapk`/`.apks`/`.apkm` desde a loja de apps (26/09): é o formato em que o dono costuma ter o arquivo. O
#: contêiner é extraído e passa pela mesma inspeção de um `.apk`.
_NOME_DE_ARQUIVO_APK = re.compile(r"^[A-Za-z0-9._-]{1,120}\.(apk|apks|xapk|apkm)$", re.IGNORECASE)


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
        raise err(400, "nome_invalido",
                  f"'{filename}' não é um nome de APK aceito (.apk, .apks, .xapk ou .apkm, sem caminho).")
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
        # Promover e quarentenar mudam a versão que "Instalar <app> <versão>" mostra: a lista de apps é republicada.
        if body.verb == "promote":
            feito = s.releases.promote(release_id, note=body.note)
            _apps_changed(s)
            # ADR-026 ("todos devem ficar atualizados sempre"): cada aparelho que TEM o app passa a perseguir a
            # promovida — o ligado e livre instala já, o ocupado na varredura, o desligado quando ligar. Não liga
            # ninguém. `devices` diz, aparelho por aparelho, o que vai acontecer; `target_release_id` é a versão que
            # o parque persegue (promover uma versão MENOR que a promovida não muda o alvo).
            try:
                convergencia = convergir_o_parque(s, release["package_name"])
            except Exception as exc:  # noqa: BLE001 - a promoção JÁ valeu no banco: um 500 aqui faria quem chamou
                # repetir e levar 409 ("só promove quem está em canário"). A varredura de 60 s e o "entrou no ar"
                # adotam a promovida do mesmo jeito; a resposta diz que a convergência imediata não aconteceu.
                log.exception("convergência do parque depois de promover %s", release_id)
                convergencia = {"target_release_id": None, "devices": [],
                                "convergence_error": f"a convergência imediata falhou ({exc}); a varredura de 60 s "
                                                     "e a entrada no ar entregam a versão do mesmo jeito"}
            return {"accepted": True, "release": feito, **convergencia}
        if body.verb == "quarantine":
            feito = s.releases.quarantine(release_id, reason=body.note)
            _apps_changed(s)
            return {"accepted": True, "release": feito}
        if body.verb == "distribute":
            # Sem alvo, vale para o parque inteiro. A resposta diz, aparelho por aparelho, se a instalação começou já
            # ou ficou pendente para quando ele entrar em serviço. `dry_run` é a prévia: nada é gravado.
            devices = s.distribute(release_id, eager=body.eager, instance_ids=body.instance_ids, count=body.count,
                                   dry_run=body.dry_run)
            return {"accepted": not body.dry_run, "dry_run": body.dry_run, "eager": body.eager, "devices": devices}
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

        async def trabalho() -> Any:
            resultado = await s.releases.rollback(rt, package, s.installer, preserve=preserve, note=body.note)
            # ADR-026: a versão de onde este aparelho saiu virou "substituída" para o parque inteiro. Quem está nela
            # (ou a esperava) volta para a promovida anterior já, em vez de esperar a varredura de 60 s.
            try:
                convergir_o_parque(s, package)
            except Exception:  # noqa: BLE001 - a volta deste aparelho já aconteceu; a varredura cobre o resto
                log.exception("convergência do parque depois da volta de %s em %s", package, rt.id)
            return resultado

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


# ---------------------------------------------------------------------- proxy do aparelho (loja de apps, 26/09)
def _proxy_error(exc: Any) -> HTTPException:
    return err(exc.status, exc.code, exc.message)


@router.get("/proxies")
async def list_proxies(request: Request) -> Any:
    from .devices.proxy import listar  # noqa: PLC0415
    return listar(st(request))


@router.post("/proxies", status_code=201)
async def create_proxy(request: Request, body: ProxyInput) -> Any:
    from .devices.proxy import criar  # noqa: PLC0415
    return criar(st(request), body, quem(request))


@router.delete("/proxies/{proxy_id}", status_code=204)
async def delete_proxy(request: Request, proxy_id: str) -> Response:
    from .devices.proxy import ProxyError, remover  # noqa: PLC0415
    try:
        remover(st(request), proxy_id)
    except ProxyError as exc:
        raise _proxy_error(exc) from exc
    return Response(status_code=204)


@router.post("/proxies/apply")
async def apply_proxy(request: Request, body: ProxyApplyBody) -> Any:
    """Pede um proxy (ou nenhum, com `proxy_id` nulo) para os aparelhos. `dry_run` = prévia, nada é gravado."""
    from .devices.proxy import ProxyError, aplicar  # noqa: PLC0415
    try:
        devices = aplicar(st(request), body)
    except ProxyError as exc:
        raise _proxy_error(exc) from exc
    return {"accepted": not body.dry_run, "dry_run": body.dry_run, "devices": devices}


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


@router.get("/instances/{instance_id}/operational-context")
async def instance_context(request: Request, instance_id: str) -> Any:
    """Servidor → aparelho → tela → apps → perfil/conta → sessão, cada camada com a sua fonte. Só leitura."""
    s = st(request)
    device(s, instance_id)                                   # 404 com a frase de sempre
    return contexto_do_aparelho(s, instance_id)


@router.get("/instagram/profiles/{profile_id}/operational-context")
async def profile_context(request: Request, profile_id: str) -> Any:
    """O MESMO contexto, chegando pelo perfil: do André Carvalho ao aparelho dele sem trocar de tela."""
    s = st(request)
    try:
        perfil = s.social.get_profile(profile_id)
    except SocialError as exc:
        raise _social_error(exc) from exc
    if not perfil.instance_id:
        raise err(409, "no_binding", "Este perfil não está vinculado a nenhum aparelho.")
    device(s, perfil.instance_id)
    return contexto_do_aparelho(s, perfil.instance_id)


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
    alvo: dict[str, Any] = {}
    if action in ("install_apk", "open_app"):
        try:
            app = _app_for(s, rt, params.app_id)      # valida antes de despachar
            if action == "install_apk":
                # O QUE será instalado é decidido e dito ANTES do 202: sem versão promovida a recusa acontece aqui,
                # e não depois de um "Instalação solicitada" que já parecia aceito.
                promovida = s.releases.promoted_release(app["package"])
                if promovida is None:
                    # A mesma exceção de `_app_for`, para a recusa ser gravada no comando por um `except` só.
                    raise DespachoRecusado(
                        409, "sem_versao_promovida",
                        f"{app['name']}: nenhuma versão de {app['package']} foi promovida ainda. Importe o APK "
                        "em Aplicativos, coloque-o em prova num aparelho e promova-o.", command_id=row["id"])
                alvo = {"install_target": {"app_id": app["id"], "app_name": app["name"], "package": app["package"],
                                           "release_id": promovida.id, "version_name": promovida.version_name,
                                           "version_code": promovida.version_code,
                                           # Instalar é pela camada de releases (ADB a partir do catálogo), nunca
                                           # pela Play Store do aparelho: ela não precisa estar aberta.
                                           "mechanism": "release_catalog_adb"}}
                s.bus.emit("log", f"{rt.id}: instalar {app['name']} {promovida.version_name} "
                                  f"({promovida.version_code}, versão promovida)", instance_id=rt.id)
        except DespachoRecusado as exc:
            _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=exc.message))
            raise                                         # a borda HTTP traduz (`recusa_do_despacho`)
    estado, _ = _marcar_entregue(s, rt, action, row["id"], params)
    await _despachar(s, row["id"])
    return {"command_id": row["id"], "state": estado, "deduplicated": False, **alvo}


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
    if f.sensitive:
        # Contrato C4: a prévia nunca mostra tela sensível. O marcador existe (tamanho, id para o controle manual),
        # mas não tem imagem — e a anterior já saiu do ar quando ele foi publicado.
        raise err(404, "sensitive_screen", "A tela atual deste aparelho é sensível: a prévia não a mostra.")
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
    """Cria a execução. Com `mode=plan`, a resposta leva também `plan_report`: o relatório dos recursos declarados
    (design §14.2) — o que está certo, o que diverge, o que seria feito e o que só uma pessoa resolve —, lido sem
    aplicar nada. Aditivo: o resumo de sempre continua igual, campo a campo."""
    runs = st(request).runs
    try:
        resumo = runs.create(body)
    except RunError as exc:
        raise _run_error(exc) from exc
    if body.mode != "plan":
        return resumo
    try:
        relatorio: dict[str, object] = runs.relatorio_de_recursos(resumo.id)
    except Exception as exc:  # noqa: BLE001 - a execução já existe: o relatório ao lado não pode virar um 500
        log.exception("relatório de recursos da execução %s", resumo.id)
        relatorio = {"source": "error", "detail": f"o relatório dos recursos não pôde ser montado: {exc}"}
    return {**jsonable_encoder(resumo), "plan_report": relatorio}


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
            # Interesse em prévia (contrato C2). Até mandar o primeiro `watch`, a conexão é painel ANTIGO: vale como
            # grade em todos os aparelhos, que é o comportamento de antes. Registrado aqui (e não antes) para que o
            # `finally` sempre o solte: desconexão, fila estourada no `pump` ou mensagem inválida.
            conexao = f"ws-{new_token()}"
            s.devices.interesse_legado(conexao)
            try:
                while True:
                    msg = await websocket.receive_json()
                    kind = msg.get("type") if isinstance(msg, dict) else None
                    if kind == "watch":
                        s.devices.registrar_interesse(conexao, msg.get("grid"), msg.get("focus"), msg.get("ttl_s"))
                    elif kind == "focus":            # legado: vira foco com TTL de 15 s
                        iid = msg.get("instance_id")
                        s.devices.set_focus(iid if isinstance(iid, str) else None)
                    elif kind == "ping":
                        await websocket.send_json({"type": "pong"})
            finally:
                s.devices.soltar_interesse(conexao)

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
    no_portao = True
    try:
        if not await _host_do_worker_permitido(s, websocket, ip):
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
        # O portão conta HANDSHAKE, não sessão: autenticado, o socket sai dele. Segurar a vaga a sessão inteira
        # (o que havia) deixava três das quatro vagas do IP para o resto — e pelo túnel todo worker chega como
        # 127.0.0.1, então o canal de mídia (`/api/worker/midia`, uma conexão por imagem) de seis aparelhos
        # observando juntos recebia 4429 sem nada ter falhado.
        portao.sair(ip)
        no_portao = False
        worker_id = hello.worker_id
        await _worker_canal(s, websocket, hello, credencial)
    finally:
        if no_portao:
            portao.sair(ip)


async def _host_do_worker_permitido(s: AppState, websocket: WebSocket, ip: str) -> bool:
    """`Host` contra loopback + `public_hosts`, a mesma defesa de DNS rebinding do resto da API — para os DOIS
    sockets do worker (comando e mídia). Recusa fecha antes do `accept()` e vira evento persistido."""
    nome = acesso.host_de(websocket.headers.get("host"))
    if nome in acesso.LOOPBACK or nome in acesso.LOOPBACK_DE_TESTE or nome in publicos_de(s.cfg):
        return True
    s.bus.emit("worker.refused", f"Conexão de worker recusada: host '{nome}' não está em "
                                 f"server.public_hosts (origem {ip}).", level="warn",
               data={"reason": "forbidden_host", "ip": ip})
    await websocket.close(code=4403)
    return False


#: Teto da primeira mensagem do canal de mídia (`request_id` + token): dezenas de bytes na prática.
MIDIA_ENVIO_MAX_BYTES = 1024


@worker_router.websocket("/worker/midia")
async def worker_midia(websocket: WebSocket) -> None:
    """Canal de MÍDIA do worker (`observe_local`): uma conexão por imagem, separada do WebSocket de comando.

    Separada de propósito: uma imagem de centenas de KB no socket de comando ficaria na frente da batida, do `ack`
    e do desfecho — e é a ausência de batida que marca o worker como indisponível. WebSocket, e não um POST, para
    não abrir exceção de credencial no middleware HTTP da porta principal: o socket confere tudo sozinho.

    Mesmas conferências do canal de comando antes do `accept()` (portão por IP, `Host`), depois:

    1. **O envio** (texto, até `MIDIA_ENVIO_MAX_BYTES`, no prazo do `hello`): `request_id` + token de uso único que
       o central emitiu no `observe_image`. O token só existe como hash, vale uma vez, até o prazo do pedido, e
       para o canal de comando que pediu (`workers/captura.py`). Token errado conta como credencial errada no
       portão.
    2. **O corpo** (binário, no que resta do prazo do pedido, até o teto do pedido): conferido parte a parte
       (`desempacotar_midia`). Corpo inválido falha o pedido na hora.

    Resposta `{"ok": true}` ou `{"ok": false, "code": ...}` e fecha. O portão é liberado assim que o token confere:
    dali em diante a conexão está autenticada e não pode ocupar vaga de handshake enquanto o corpo sobe.
    """
    s: AppState = websocket.app.state.poc
    ip = websocket.client.host if websocket.client else "?"
    portao = s.workers.portao
    if not portao.entrar(ip):
        await websocket.close(code=4429)
        return
    no_portao = True
    try:
        if not await _host_do_worker_permitido(s, websocket, ip):
            return
        await websocket.accept()
        try:
            bruto = await asyncio.wait_for(websocket.receive_text(), timeout=HELLO_TIMEOUT_S)
            if len(bruto.encode("utf-8", "surrogatepass")) > MIDIA_ENVIO_MAX_BYTES:
                raise ValueError("envio grande demais")
            envio = EnvioDeMidia.model_validate(loads(bruto))
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, TypeError, KeyError):
            await websocket.close(code=4400)
            return
        captura = s.workers.captura
        try:
            pedido = captura.autorizar(envio)
        except ErroDeMidia as exc:
            if exc.code == "bad_token":
                portao.falhou(ip)
            await _recusar_midia(websocket, exc)
            return
        portao.sair(ip)
        no_portao = False
        try:
            restante = max(0.0, pedido.fim - asyncio.get_running_loop().time())
            mensagem = await asyncio.wait_for(websocket.receive(), timeout=restante)
            if mensagem.get("type") == "websocket.disconnect":
                # O token já foi gasto: sem isto, quem pediu esperaria o prazo inteiro por um corpo que não vem.
                captura.abandonar(pedido, "o worker fechou o canal de mídia antes de mandar a imagem")
                return
            corpo = mensagem.get("bytes")
            if mensagem.get("type") != "websocket.receive" or not isinstance(corpo, (bytes, bytearray)):
                raise ErroDeMidia("bad_media", 4400, "o corpo da imagem tem de ser binário")
            captura.receber(pedido, bytes(corpo))
        except asyncio.TimeoutError:
            captura.abandonar(pedido, "o corpo da imagem não chegou no prazo do pedido")
            await _recusar_midia(websocket, ErroDeMidia("expired", 4410, "o corpo não chegou no prazo do pedido"))
            return
        except ErroDeMidia as exc:
            # Toda recusa DEPOIS de o token conferir falha o pedido na hora — inclusive a que não passa por
            # `receber` (corpo em texto). Sem isto, quem pediu esperava o prazo inteiro com o executor do aparelho
            # preso. `abandonar` não faz nada se `receber` já tiver falhado o pedido.
            captura.abandonar(pedido, f"imagem recusada no canal de mídia: {exc}")
            await _recusar_midia(websocket, exc)
            return
        with contextlib.suppress(Exception):
            await websocket.send_json({"ok": True})
            await websocket.close(code=1000)
    except WebSocketDisconnect:
        pass
    finally:
        if no_portao:
            portao.sair(ip)


async def _recusar_midia(websocket: WebSocket, exc: ErroDeMidia) -> None:
    log.info("envio de mídia recusado (%s): %s", exc.code, exc)
    with contextlib.suppress(Exception):
        await websocket.send_json({"ok": False, "code": exc.code})
        await websocket.close(code=exc.close)


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
    # Captura na origem (`observe_local`): ligada sempre; USADA só enquanto o canal vivo tiver a feature aceita
    # (`DeviceManager._captura_remota` pergunta a cada captura). Agente antigo: nada muda, ADB pelo túnel.
    s.devices.bind_worker_captura(worker_id, s.workers.captura)
    _anunciar_inflight(s, worker_id, hello.inflight)
    esperados = {r["id"]: r["avd_name"] for r in
                 s.db.query("SELECT id, avd_name FROM instances WHERE worker_id=?", (worker_id,))}
    # C7: sai no `welcome` o que `attach` negociou para ESTE link — o que o agente anunciou e este central usa.
    bem_vindo = s.workers.welcome(esperados, sorted(link.features_aceitas)).model_dump()
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
            s.devices.bind_worker_captura(worker_id, None)
            s.bus.emit("log", f"Worker {hello.name} desconectou.", level="warn")
