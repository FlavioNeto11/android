"""Rotas REST + WebSocket. Ver docs/api-contract.md."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import threading
import time
from datetime import timedelta
from time import monotonic
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from pydantic import BaseModel, ConfigDict, Field

from .automation.appium_driver import appium_no_ar
from .storage import DISK, DiskStorage, Storage
from .commands.store import command_dto
# O despacho de comandos mora em `commands/despacho.py` (não é HTTP, e o `state` precisa dele sem importar a API).
# Os nomes seguem acessíveis por aqui: as rotas os usam, e os testes os importam de `app.api`.
from .commands.despacho import (DespachoRecusado, _anunciar_inflight, _despachar_trabalho, _do_action,
                                _fechar_cancelado, _reconciliar_uma_vez, _tratar_mensagem_do_worker,
                                executar_envelope, reconciliar_estado_desejado, remediar, remediar_reiniciando)
from .db import Row, loads
from .training.recorder import TrainingError
from .devices.adb import AdbError
from .devices.manager import DeviceRuntime
from .modules.fleet.presentation.comum import quem
from .modules.identity.presentation.comum import servir_do_storage as _servir_do_storage
from .modules.identity.presentation.comum import social_error as _social_error
from .modules.applications.presentation.comum import device
from .devices.verbs import PRAZO_POR_VERBO, verbos_suportados
from .models import (RUN_TERMINAL, RunStatus, DistributeSpec, Plan, ApprovalBatchBody, ApprovalDecision,
                     CapabilityDTO, InstanceState, TrainingSaveBody, TrainingStartBody, StoreBody, LoginBody,
                     PanelSessionInfo, ResolveBody, RunCreate, RunTargetsPreview, RunTargetsResolveBody)
from .metricas import metricas
from .contracts.skills.resolve import SkillResolveRequest
from .modules.learning.domain.vocabulario import LivroKind
from .modules.learning.infrastructure.segredo import TriagemDeCredencial
from .modules.learning.presentation.livro import mudar_status_legado
from .modules.skills.domain.document import JsonObject
from .modules.skills.domain.lifecycle import ContentTampered
from .modules.skills.presentation.schemas import EscopoDoFluxoBody, TrainingDeFalhaBody, TrainingStopBody, TrainingUndoBody
from .planning import conciliacao, costs, saldos
from .porta_do_plano import (AprovarPlanoBody, PortaIndisponivel, PreviaDoItemBody, aprovar_plano, previa_da_porta,
                             previa_do_item, renovar_plano)
from .security import access as acesso           # o módulo, não os nomes: `LOOPBACK_DE_TESTE` é injetado em tempo
from .security import local_secret               # de execução e um `from ... import` congelaria o valor antigo
from .security.access import avaliar, publicos_de
from .security.sessions import COOKIE, VALIDADE_S, NomeInvalido, normalizar_nome
from .shared.costuras import autor_do_gesto
from .state import AppState
from .workers.captura import ErroDeMidia
from .workers.protocol import EnvioDeMidia, Hello, Refused, parse_upstream
from .workers.portao import BLOQUEIO_S
from .workers.registry import WorkerError, motivo_do_conflito
from .version import agent_version, codigo_do_agente
from .planning.capabilities import load_catalog
from .social.excecoes import ExcecaoEmUso, ExcecaoInvalida
from .social.service import SocialError
from .taskqueue import observabilidade
from .taskqueue.flows import id_do_fluxo
from .taskqueue.repository import CONTENT_TYPES
from .models import RunSummary
from .taskqueue.assistente import RunSuccessorBody
from .taskqueue.orquestrador import Orquestrador, RunTargetsSuggestBody, RunTargetsSuggestion
from .util import iso_in, new_token, now, now_iso, parse_iso, to_iso
from .vitrine import apps_list

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


async def recusa_do_despacho(request: Request, exc: DespachoRecusado) -> Response:
    """Tradução de `DespachoRecusado` na borda HTTP (registrada em `main.create_app`).

    O despacho saiu deste arquivo e deixou de levantar `HTTPException` (o FastAPI mora só aqui e em `main`). A
    resposta continua a mesma: monta o `HTTPException` que `err()` montava e entrega ao tratador padrão do FastAPI,
    que é quem respondia quando a exceção era HTTP — mesmo status, mesmo corpo `{"detail": {...}}`.
    """
    return await http_exception_handler(request, HTTPException(status_code=exc.status, detail=exc.detail))


#: A triagem de credencial do voto do D2 (`registrar_sinal(recusar_nota=True)`), para a nota livre que uma pessoa
#: escreve numa rota daqui e que entraria crua no banco e no evento (segredo nunca em evento).
_TRIAGEM_DE_NOTA = TriagemDeCredencial()

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
    # 29.56: a trava é por cliente (`security.access.cliente_de`); o middleware já calculou a chave.
    cliente = str(getattr(request.state, "cliente", ""))
    if exige:
        # A trava só vale para quem apresenta segredo. Fora do `if`, oito chutes vindos da rede trancariam
        # também o login do loopback — que não usa token nenhum —, e aí o ataque não rouba nada: derruba.
        if (espera := s.portao_de_login.segundos_de_espera(agora, cliente)) > 0:
            raise HTTPException(status_code=429, headers={"Retry-After": str(int(espera) + 1)},
                                detail={"code": "too_many_attempts", "retry_after_s": int(espera) + 1,
                                        "message": f"Tentativas de login demais. Espere {int(espera) + 1} s e tente "
                                                   "de novo."})
        recebido = body.token.get_secret_value() if body.token else ""
        # `token_ok` compara em tempo constante e só aceita o esquema Bearer; reaproveitá-lo é o que impede a
        # comparação ingênua de voltar por esta porta.
        if not acesso.token_ok(f"Bearer {recebido}", s.cfg.api_token):
            s.portao_de_login.registrar_falha(agora, cliente)
            # Sem dizer o que estava errado: nome inexistente e token errado devolvem a MESMA coisa.
            raise err(401, "invalid_credentials", "Credencial inválida.")
    try:
        token, expira = s.sessions.abrir(body.operator)
    except NomeInvalido as exc:
        raise err(422, "invalid_operator", str(exc)) from None
    s.portao_de_login.registrar_acerto(cliente)
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


# O snapshot traz as 20 execuções mais recentes E TODAS as que não terminaram, exceto `planned`: as em andamento e as
# que esperam resposta (`needs_input`). O painel conta as duas sobre o que o snapshot entrega (topo e chip "Em
# andamento"; caixa de Pendências, chip "aguardando você" e selo do menu), e uma execução de semanas atrás ficava fora
# das 20 mais recentes: o contador abria num número de janela e mudava depois de visitar Execuções (RF-05 e decisão D1
# da revisão de UX). `planned` é um plano pronto para inspeção, que ninguém mandou executar: não é "em andamento"
# (decisão D2) e nenhum contador a lê, então só vem se estiver entre as 20 recentes. Custo: uma linha por execução
# parada em `needs_input` ou em andamento (27 + 0 no parque real em 30/09).
# `awaiting_person` (29.93) não é terminal, mas só vem por `AGUARDANDO_NO_SNAPSHOT_D` dias depois do fim do trabalho
# automático (`finished_at`); mais velha, só se estiver entre as 20 recentes. Com o vencimento do 31.50 desligado nada a
# fecha, e sem o corte o snapshot cresceria sem limite. A caixa de Pendências não depende disto: ela conta só
# `needs_input` (ADR-062, D1), e o objetivo esperando segue no detalhe da execução e no chip "Pede atenção".
AGUARDANDO_NO_SNAPSHOT_D = 7
_STATUS_DO_SNAPSHOT = tuple(sorted(str(x.value) for x in RunStatus
                                   if x not in RUN_TERMINAL and x not in (RunStatus.planned, RunStatus.awaiting_person)))
_SQL_RUNS_DO_SNAPSHOT = (
    "SELECT * FROM runs WHERE status IN (" + ",".join(f"'{x}'" for x in _STATUS_DO_SNAPSHOT) + ")"
    f" OR (status='{RunStatus.awaiting_person.value}' AND finished_at >= ?)"
    " OR id IN (SELECT id FROM runs ORDER BY created_at DESC LIMIT 20) ORDER BY created_at DESC")


@router.get("/snapshot")
async def snapshot(request: Request) -> Any:
    s = st(request)
    runs = s.db.query(_SQL_RUNS_DO_SNAPSHOT, (to_iso(now() - timedelta(days=AGUARDANDO_NO_SNAPSHOT_D)),))
    return {"last_event_id": s.bus.last_id(), "server_time": now_iso(), "health": s.health(),
            "metrics": s.devices.last_metrics, "instances": s.devices.list_dtos(), "apps": apps_list(s),
            "runs": s.repo.run_summaries(runs), "settings": s.settings.get(),
            "workers": s.workers.dtos(),
            # Pedidos persistentes (28.9): por estado, avisos e os que esperam uma pessoa, completo e sem janela. Cada
            # `aguardando_pessoa` é UM item da caixa de Pendências, com a aprovação e a `needs_input` dele agrupadas.
            "pedidos": s.pedidos_api.snapshot(),
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
    if "max_online_devices" in patch:
        s.workers.publicar(s.cfg.owner_id)
    s.scheduler.wake()
    return value


@router.get("/ai")
async def ai_status(request: Request) -> Any:
    return st(request).ai_status()


class LeituraDeSaldo(BaseModel):
    """Uma leitura do saldo no console do provedor (ADR-051). `observed_at` vazio = agora."""
    balance: float = Field(ge=-100_000, le=1_000_000)
    source: Literal["manual", "console"] = "manual"
    observed_at: str | None = None
    currency: Literal["USD", "BRL"] | None = None
    units_per_usd: float | None = Field(None, gt=0, le=1000)
    note: str | None = Field(None, max_length=300)


class RegraDeSaldo(BaseModel):
    """Limites da conta, na moeda dela. `null` desliga o aviso/bloqueio."""
    currency: Literal["USD", "BRL"] | None = None
    units_per_usd: float | None = Field(None, gt=0, le=1000)
    warn_below: float | None = None
    block_below: float | None = None


class RecargaDeSaldo(BaseModel):
    """Compra de crédito no console do provedor (ADR-051): soma ao saldo estimado de agora."""
    amount: float = Field(gt=0, le=100_000)
    currency: Literal["USD", "BRL"] | None = None
    note: str | None = Field(None, max_length=300)


def _saldos_dto(s: AppState) -> dict[str, object]:
    contas = [c.as_dict() for c in s.saldos_de_ia()]
    return {"accounts": contas, "blocked": [c["account"] for c in contas if c["state"] in ("blocked", "exhausted")],
            "estimated": True,
            "note": "Livro-caixa: saldo = última âncora (leitura, recarga ou fechamento diário) menos o consumo desde "
                    "ela. Anthropic e OpenAI pelo relatório oficial de uso do provedor (a Anthropic de hora em hora); "
                    "o Gemini pelo consumo medido em cada chamada (usageMetadata), porque a chave é só da plataforma. "
                    "A TypeSafe (Jev) nasce sem âncora e sem leitura automática: o dono registra a recarga. "
                    "Recarga: registre em Configuração › IA."}


@router.get("/ai/balances")
async def ai_balances(request: Request, refresh: bool = False) -> dict[str, object]:
    """Saldo estimado das contas de IA (Anthropic, OpenAI, Gemini), com limites e o que cada uma paga. Com chave de
    administrador no `.env`, concilia pelo relatório de custo do provedor (cache de 15 min; `refresh=1` força)."""
    s = st(request)
    if refresh or conciliacao.precisa_atualizar(s.cfg):
        await conciliacao.atualizar(s.db, s.cfg, forcar=refresh)
    return await asyncio.to_thread(_saldos_dto, s)


@router.post("/ai/balances/{account}", status_code=201)
async def ai_balance_reading(request: Request, account: str, body: LeituraDeSaldo) -> dict[str, object]:
    s = st(request)
    if account not in saldos.CONTAS:
        raise err(404, "unknown_account", f"Conta de IA desconhecida: {account}")
    observado = None
    if body.observed_at:
        try:
            lido = parse_iso(body.observed_at)
        except ValueError:
            lido = None
        if lido is None or lido.tzinfo is None:
            raise err(400, "invalid_observed_at", "observed_at deve ser ISO-8601 com fuso (ex.: 2026-09-28T15:00:00Z).")
        observado = to_iso(lido)
    try:
        saldos.registrar_leitura(s.db, account, body.balance, source=body.source, observed_at=observado,
                                 currency=body.currency, units_per_usd=body.units_per_usd, note=body.note)
    except ValueError as exc:
        raise err(400, "invalid_reading", str(exc)) from exc
    # Concilia JÁ: a primeira conciliação grava a linha de base da leitura, e quanto mais perto do registro, mais
    # exata (o gasto de fora feito antes da leitura não sai duas vezes).
    await conciliacao.atualizar(s.db, s.cfg, forcar=True)
    dto = _saldos_dto(s)
    s.bus.emit("ai.balances.updated", f"Saldo de IA registrado: {account}", data=dto)
    return dto


@router.post("/ai/balances/{account}/recharge", status_code=201)
async def ai_balance_recharge(request: Request, account: str, body: RecargaDeSaldo) -> dict[str, object]:
    """Recarga: concilia antes (o saldo de agora tem de estar em dia), grava a âncora nova e concilia de novo para
    gravar a linha de base dela."""
    s = st(request)
    if account not in saldos.CONTAS:
        raise err(404, "unknown_account", f"Conta de IA desconhecida: {account}")
    await conciliacao.atualizar(s.db, s.cfg, forcar=True)
    try:
        saldos.registrar_recarga(s.db, s.cfg, account, body.amount, currency=body.currency, note=body.note)
    except LookupError as exc:
        raise err(409, "no_initial_balance", str(exc)) from exc
    except ValueError as exc:
        raise err(400, "invalid_recharge", str(exc)) from exc
    await conciliacao.atualizar(s.db, s.cfg, forcar=True)
    dto = _saldos_dto(s)
    s.bus.emit("ai.balances.updated", f"Recarga registrada: {account}", data=dto)
    return dto


@router.put("/ai/balances/{account}")
async def ai_balance_rule(request: Request, account: str, body: RegraDeSaldo) -> dict[str, object]:
    s = st(request)
    if account not in saldos.CONTAS:
        raise err(404, "unknown_account", f"Conta de IA desconhecida: {account}")
    saldos.ajustar_regra(s.db, account, **body.model_dump(exclude_unset=True))
    dto = _saldos_dto(s)
    s.bus.emit("ai.balances.updated", f"Limites de saldo de IA ajustados: {account}", data=dto)
    return dto


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


@router.get("/desempenho")
async def desempenho(request: Request, janelas: int = Query(24, ge=0, le=672),
                     dias: int = Query(0, ge=0, le=90), irq_horas: int = Query(0, ge=0, le=336),
                     irq_aparelho: str | None = Query(None, max_length=64)) -> Any:
    """Métricas agregadas de desempenho (contrato C5, adendo v0.20): o acumulado DESTE processo desde a partida e
    as últimas `janelas` gravadas em `measurements` (15 min cada; todas as réplicas, com `owner`). Só lê — nada
    aqui toca aparelho ou provedor. Distribuição sem amostra devolve `None` no percentil: desconhecido, não zero.

    `dias` > 0 acrescenta `historico`: `desempenho.resumo` dos últimos `dias` a partir das tabelas que já existem
    (objetivos, etapas, ações, `ai_calls`, comandos, boot), com p50/p95/n por entidade. 0 (padrão) devolve o de
    antes — a consulta varre a janela inteira e não precisa pesar em quem só quer o acumulado.

    `irq_horas` > 0 acrescenta `interrupcoes` (`desempenho.interrupcoes`): a fração de CPU do convidado em irq +
    softirq gravada a cada sonda de saúde nas últimas `irq_horas`, por aparelho (série, último valor e p50/p95),
    ou só de `irq_aparelho`. Mesmo padrão de `dias`: sem pedir, nada muda na resposta."""
    s = st(request)
    linhas = s.db.query("SELECT ts, data FROM measurements WHERE kind='metricas' ORDER BY id DESC LIMIT ?",
                        (janelas,)) if janelas else []
    out: dict[str, Any] = {"processo": {**metricas.snapshot(), "owner": s.cfg.owner_id},
                           "janelas": [{"ts": r["ts"], **loads(r["data"], {})} for r in linhas]}
    if not (dias or irq_horas):
        return out
    from . import desempenho as historico  # noqa: PLC0415
    if dias:
        # Em thread: com 90 dias são dezenas de milhares de linhas de `ai_calls`, e o laço de eventos serve o painel.
        out["historico"] = await asyncio.to_thread(historico.resumo, s.db, desde_iso=iso_in(-dias * 86400),
                                                   precos=s.cfg.file.ai.prices)
    if irq_horas:
        # Em thread pelo mesmo motivo: `measurements` não tem índice, e são duas linhas por minuto por aparelho.
        out["interrupcoes"] = await asyncio.to_thread(historico.interrupcoes, s.db,
                                                      desde_iso=iso_in(-irq_horas * 3600), aparelho=irq_aparelho)
    return out


@router.get("/usage")
async def usage(request: Request, run_id: str | None = None, days: int = Query(7, ge=1, le=365)) -> Any:
    """Custo de IA por função e modelo (uma linha por chamada em `ai_calls`), com US$ pelos preços de `ai.prices`."""
    s = st(request)
    prices = s.cfg.file.ai.prices
    # `datetime('now', ?)` é aritmética de data do SQLite. As colunas guardam ISO-8601, que ordena
    # lexicograficamente, então o corte calculado em Python compara igual nos dois dialetos.
    where, params = ("run_id=?", (run_id,)) if run_id else ("ts >= ?", (iso_in(-days * 86400),))
    # I1 da validação do deploy 7: o custo DECLARADO na linha (`usd`: a imagem da persona, que não tem preço por token)
    # entra no total como entra nas peças por conta (`saldos.gasto_usd_por_conta`) e por origem (`costs.usd_por`). Antes
    # o total só fazia tokens × preço, e as peças somavam US$ 0,27 a mais (as 5 imagens da semana) sem a tela dizer por quê.
    rows = s.db.query(
        f"SELECT role, model, tier, COUNT(*) calls, SUM(input_tokens) fresh, SUM(cache_read) cache_read,"
        f" SUM(cache_write) cache_write, SUM(output_tokens) output, SUM(with_image) with_image, SUM(1-ok) errors,"
        f" AVG(ms) avg_ms, SUM(CASE WHEN usd IS NULL THEN 0 ELSE 1 END) declaradas, SUM(COALESCE(usd, 0)) usd_declarado,"
        f" SUM(CASE WHEN usd IS NULL THEN input_tokens ELSE 0 END) p_fresh,"
        f" SUM(CASE WHEN usd IS NULL THEN cache_read ELSE 0 END) p_cache_read,"
        f" SUM(CASE WHEN usd IS NULL THEN cache_write ELSE 0 END) p_cache_write,"
        f" SUM(CASE WHEN usd IS NULL THEN COALESCE(cache_write_1h, 0) ELSE 0 END) p_cache_write_1h,"
        f" SUM(CASE WHEN usd IS NULL THEN output_tokens ELSE 0 END) p_output"
        f" FROM ai_calls WHERE {where} GROUP BY role, model, tier ORDER BY role, model", params)
    groups, total = [], 0.0
    for r in rows:
        p = _price(prices, r["model"])
        # Sem preço por token, o grupo só tem custo se toda chamada OK declarou o dela; senão fica sem preço (total parcial).
        por_token = (0.0 if r["declaradas"] >= r["calls"] - r["errors"] else None) if p is None else (
            r["p_fresh"] * p[0] + r["p_cache_read"] * p[1] + r["p_cache_write"] * p[2] + r["p_output"] * p[3]) / 1_000_000
        if por_token is not None and p is not None:   # 31.31: a gravação de 1 h custa 2x a entrada
            por_token += costs.extra_1h(prices, r["model"], r["p_cache_write_1h"])
        usd = None if por_token is None else round(por_token + float(r["usd_declarado"] or 0), 4)
        total += usd or 0.0
        linha = {k: r[k] for k in ("role", "model", "tier", "calls", "fresh", "cache_read", "cache_write", "output",
                                   "with_image", "errors")}
        groups.append({**linha, "avg_ms": round(r["avg_ms"] or 0), "usd": usd})
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
    # Custo por CONTA de IA (ADR-051): de qual saldo o dinheiro saiu — Anthropic, OpenAI ou Google (Gemini). Mesma
    # regra de conta do saldo (provedor → tipo e host; linha antiga → prefixo do modelo).
    por_conta = {c: round(v, 4) for c, v in (saldos.gasto_usd_por_conta(s.db, s.cfg, run_id=run_id) if run_id else
                                             saldos.gasto_usd_por_conta(s.db, s.cfg, iso_in(-days * 86400))).items()}
    return {"scope": {"run_id": run_id, "days": None if run_id else days}, "groups": groups, "total_usd": round(total, 4),
            "by_account": por_conta,
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
                                       if r["calls"] > r["errors"] and _price(prices, r["model"]) is None
                                       and r["declaradas"] < r["calls"] - r["errors"]}),
            # Cache de prompt que não bate em DECISÃO é defeito, não escolha: toda decisão leva as 15 ferramentas
            # (prefixo ≈ 6 mil tokens, acima do mínimo de qualquer modelo). Verificação fica de fora — o system
            # sozinho (≈ 1 mil tokens) fica legitimamente abaixo do mínimo do Sonnet/Haiku. Achado de 24/09: 46
            # decisões no Sonnet sem uma leitura de cache e ninguém viu, porque o relatório só somava.
            "cache_inativo": [{"model": r["model"], "calls": r["calls"]} for r in rows
                              if r["role"] == "decide" and (r["model"] or "").startswith("claude-")
                              and r["calls"] >= 10 and not (r["cache_read"] or 0) and not (r["cache_write"] or 0)],
            # RA-10 (migração 080): custo por origem, rejulgamento (com a discordância por app), cascata do bloqueio,
            # motivos do modelo forte e da imagem, e as etapas com decisão de IA ainda sem `driven_by`. Só chaves NOVAS:
            # as de cima não mudam de sentido.
            **observabilidade.grupos(s.db, prices, run_id=run_id,
                                     desde=None if run_id else iso_in(-days * 86400))}


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
            # Todas as personas do aparelho (N:N): o escopo da habilidade casa com qualquer uma delas; sem
            # nenhuma, o aparelho entra como "sem perfil", que é o escopo do caminho antigo.
            perfis.extend(s.social.profiles_of(iid) or [None])
    try:
        resolvido = s.skill_planner.resolve_intent(s.runs.sem_destinos(body.command), perfis)
    except ContentTampered as exc:
        # Versão publicada alterada por fora do repositório entre as candidatas: recusa explícita, nunca 500 nem
        # "resolveu outra coisa em silêncio" (a execução recusa do mesmo jeito, em `needs_input`).
        raise err(409, exc.code, str(exc), gated_by_config=portas) from exc
    return {**resolvido.as_dict(), "gated_by_config": portas}


@router.get("/recipes")
async def list_recipes(request: Request) -> Any:
    rows = st(request).db.query("SELECT * FROM recipes ORDER BY app_package, step_key, version DESC")
    return [{**{k: r[k] for k in r.keys() if k != "actions"}, "actions": loads(r["actions"], [])} for r in rows]


@router.put("/recipes/{recipe_id}")
async def update_recipe(request: Request, recipe_id: int, patch: dict[str, Any]) -> Any:
    s = st(request)
    if patch.get("status") not in ("active", "quarantined"):
        raise err(400, "invalid", "status deve ser 'active' ou 'quarantined'.")
    # ADR-054 (D1): pelo livro — trilha com a pessoa, veto do caminho que ela pôs em quarentena, nunca duas ativas na
    # mesma chave, e a substituída não volta. Receita inexistente é 404 (antes, 200 sem tocar nada).
    with s.db.tx():
        mudar_status_legado(request, LivroKind.RECEITA, str(recipe_id), patch["status"],
                            reason="reativada na lista de receitas do painel" if patch["status"] == "active"
                            else "posta em quarentena na lista de receitas do painel")
        # O que a rota sempre fez, e não é status: a pessoa que mexe na receita zera a sequência de falhas (e, desde o
        # 30.80, a de "não se aplicou").
        s.db.execute("UPDATE recipes SET consecutive_fail=0, nao_aplicavel_seguidas=0 WHERE id=?", (recipe_id,))
    return {"id": recipe_id, "status": patch["status"]}


@router.delete("/recipes/{recipe_id}", status_code=204)
async def delete_recipe(request: Request, recipe_id: int) -> Response:
    st(request).db.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))
    return Response(status_code=204)


# ====================================================================== apps
# ====================================================================== instâncias
# ====================================================================== perfis do Instagram
# ---------------------------------------------------------------- credencial e sessão POR CONTA (ADR-040)
# ====================================================================== persona, memória e histórico
# ---------------------------------------------------------------- imagens da persona (048)
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


# ---------------------------------------------------------------- exceções de política (item 30.65)
class ExcecaoDePoliticaCreate(BaseModel):
    """Exceção de uso único à regra de uma conta por alvo (ADR-055): um perfil, um alvo, uma ação, até 72 h. Ela não
    libera sozinha: a etapa casada passa por aprovação em Pendências."""
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=80)
    alvo: str = Field(min_length=1, max_length=120)
    capability: str = Field(min_length=1, max_length=60)
    motivo: str = Field(min_length=1, max_length=500)
    autorizacao: str = Field(min_length=1, max_length=300)
    expira_em: str = Field(min_length=1, max_length=40)


@router.post("/politica/excecoes", status_code=201)
async def criar_excecao_de_politica(request: Request, body: ExcecaoDePoliticaCreate) -> dict[str, object]:
    # Motivo e autorização são texto livre que vai ao banco e ao evento `politica.excecao_criada`: a triagem de nota
    # recusa antes de qualquer escrita (segredo nunca em evento).
    for campo, valor in (("motivo", body.motivo), ("autorizacao", body.autorizacao)):
        if _TRIAGEM_DE_NOTA.recusa(valor.strip()):
            raise err(409, "note_looks_secret", f"O campo {campo} tem formato ou assunto de credencial e nada foi "
                                                "gravado. Reescreva sem o segredo; hora com segundos (19:02:26Z) cai na "
                                                "mesma regra, escreva 19:02 UTC.")
    try:
        criada = st(request).excecoes.criar(profile_id=body.profile_id, alvo=body.alvo, capability=body.capability,
                                            motivo=body.motivo, autorizacao=body.autorizacao, autor=quem(request),
                                            expira_em=body.expira_em,
                                            autor_com_sessao=bool(getattr(request.state, "operador", None)))
    except ExcecaoInvalida as exc:
        raise err(422, "excecao_invalida", str(exc)) from exc
    return {"excecao": criada.to_dict()}


@router.post("/politica/excecoes/{excecao_id}/revogar")
async def revogar_excecao_de_politica(request: Request, excecao_id: str) -> dict[str, object]:
    """Encerra a exceção ainda em aberto (livre ou presa a uma etapa que espera o cartão). 404 se não existe; 409
    `excecao_em_uso` se o executor já a reservou (o efeito pode ter saído); 409 `excecao_encerrada` se já terminou."""
    excecoes = st(request).excecoes
    if excecoes.obter(excecao_id) is None:
        raise err(404, "not_found", f"exceção {excecao_id} não existe")
    try:
        revogada = excecoes.revogar(excecao_id, por=quem(request))
    except ExcecaoEmUso as exc:
        # A reserva do executor ganhou: o gesto já pode ter acontecido. Nunca grava "revogada" por cima de "em uso".
        raise err(409, "excecao_em_uso", str(exc)) from exc
    except ExcecaoInvalida as exc:
        raise err(409, "excecao_encerrada", str(exc)) from exc
    # O cartão pendente da etapa presa não fica órfão em Pendências e no Telegram; a etapa que já passou da porta é
    # parada no commit pelo executor (a reserva, `_reservar_excecao`, falha na revogada).
    st(request).approval_service.expirar_da_etapa(revogada.step_id, motivo=f"exceção {revogada.id} revogada")
    return {"excecao": revogada.to_dict()}


@router.get("/politica/excecoes")
async def listar_excecoes_de_politica(request: Request, profile_id: str | None = None) -> dict[str, object]:
    return {"excecoes": [e.to_dict() for e in st(request).excecoes.listar(profile_id=profile_id)]}


# ---------------------------------------------------------------- contas do perfil por app (item 12.1)
# ---------------------------------------------------------------- grupos de acesso (migração 036)
@router.get("/approvals")
async def list_approvals(request: Request, status: str | None = "pending", profile_id: str | None = None,
                         run_id: str | None = None, limit: int = 50) -> Any:
    """`run_id` junta os textos de uma execução — um por perfil — para serem lidos e decididos de uma vez. 31.113 F3:
    o pedido guarda o marcador da persona; a tela recebe o valor de agora (`na_tela`), sem gravar."""
    servico = st(request).approval_service
    return [servico.na_tela(a) for a in servico.list(status=status or None, profile_id=profile_id, run_id=run_id,
                                                     limit=min(max(limit, 1), 200))]


@router.post("/approvals/decide")
async def decide_approvals(request: Request, body: ApprovalBatchBody) -> Any:
    """Decide várias aprovações. Cada uma é independente: uma recusada não impede as demais, e a resposta diz quais."""
    servico = st(request).approval_service
    feito = servico.decide_many(body.decisions)
    return {**feito, "decided": [servico.na_tela(a) for a in feito["decided"]]}


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(request: Request, approval_id: str, body: ApprovalDecision) -> Any:
    """Aprovar, editar ou rejeitar. Nenhum dos três marca a etapa como concluída: eles decidem o que VAI acontecer."""
    try:
        servico = st(request).approval_service
        return servico.na_tela(servico.decide(approval_id, body.verb, content=body.content, note=body.note))
    except SocialError as exc:
        raise _social_error(exc) from exc


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
# ====================================================================== provisionamento (migração 050)
# ---------------------------------------------------------------------- controle manual
# ====================================================================== execuções
def _armazem_de(s: AppState, onde: str) -> Storage | None:
    """O back-end daquela LINHA. `disk` sempre existe (é a pasta local); os outros, só se forem o configurado."""
    if onde == s.storage.name:
        return s.storage
    if onde == DISK:
        return DiskStorage(s.cfg.evidence_dir)
    return None


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
    # 29.54 (ADR-073): com o listener dedicado ligado (`worker_port != 0`), o canal do worker NÃO atende pela porta do
    # painel por nome público — pelo túnel da Cloudflare todo par é 127.0.0.1, então um `hello` errado vindo da
    # internet bloquearia o worker legítimo por 60 s. A regra do ingress (`^/api/worker/` → 404) é a primeira barreira;
    # esta é a segunda. Com `worker_port: 0` o canal é da porta principal e o nome público segue valendo.
    so_loopback = (bool(int(s.cfg.file.server.worker_port or 0))
                   and not getattr(websocket.app.state, "canal_dedicado", False))
    publico_vale = nome in publicos_de(s.cfg) and not so_loopback
    if nome in acesso.LOOPBACK or nome in acesso.LOOPBACK_DE_TESTE or publico_vale:
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
        # 4409 = "conflito": outra conexão deste mesmo worker assumiu o canal. O agente deslocado cancela o que tinha
        # em voo e cede o canal por um tempo (29.76). Se ESTA conexão roda código diferente do central e a nova roda
        # o do central (`codigo_do_agente` já guarda o `hello` da nova), é a cópia velha de uma atualização: o motivo
        # diz isso, e o agente velho não volta.
        motivo = motivo_do_conflito(codigo_do_agente(), hello.agent_code, s.workers.codigo_do_agente.get(worker_id))
        with contextlib.suppress(Exception):
            await websocket.close(code=4409, reason=motivo)

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
        s.bus.emit("log", f"Worker {hello.name}: o código do agente difere do deste servidor (agente "
                          f"{hello.agent_version}, servidor {agent_version()}). Atualize o agente (scripts/worker-install.ps1 ou "
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
