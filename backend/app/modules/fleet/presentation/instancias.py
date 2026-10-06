"""Os aparelhos (`/api/instances*`): listar, criar (provisionar), aposentar, mudar, instalar e verificar um aplicativo, o contexto
operacional, os pacotes, o lote, as ações, o quadro e a hierarquia da tela, a conta travada, a pausa do reparo, o controle manual
(pegar, soltar e a entrada) e o que cada um dessas rotas usa só aqui (a partida encadeada depois de criar, a recusa de provisionamento,
a recusa de mudar de servidor). Saíram de `api.py` no 15.15 F4 (corte 7, F4g) sem mudar caminho, método, corpo nem resposta.

Ficam em `api.py` as rotas de `/commands*` e `/profiles/{id}/context` (que dividiam o mesmo bloco) e `/instances/{id}/personas` (das
personas). Montado em `main.py` no MESMO lugar em que `api.router` entra; `/instances/bulk` (literal) vem ANTES de
`/instances/{instance_id}/...` como estava. Este módulo não importa `app.api` (ciclo): o estado é `request.app.state.poc`
(`AppState` só em `TYPE_CHECKING`).
"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response

from app.automation.driver import DriverError
from app.commands.despacho import (LIFECYCLE_ACTIONS, DespachoRecusado, _abrir_comando, _app_for, _despachar,
                                   _despachar_trabalho, _marcar_entregue, _precheck, _publish_command,
                                   _release_pronta_para, abrir_e_despachar, pedir_ciclo_de_vida)
from app.commands.states import COMMAND_OPEN
from app.contexto import contexto_do_aparelho
from app.devices.adb import AdbError
from app.devices.avd import AvdError
from app.devices.manager import ControlError, DeviceRuntime
from app.devices.verbs import prazo_de
from app.metricas import metricas
from app.models import (AppInstallBody, AppVerifyBody, BulkBody, CommandState, InstanceActionBody, InstancePatch,
                        InstanceProvisionBody, InstanceState, ManualInput, ReleaseBody, RepairPauseBody, RepairPauseInfo,
                        ResolverQuarentenaBody, SessionStatus)
from app.modules.applications.presentation.comum import device, recusa_loja_como_alvo
from app.shared.costuras import autor_do_gesto
from app.modules.fleet.presentation.comum import quem
from app.modules.fleet.presentation.schemas import TakeControlBody
from app.util import now_iso

if TYPE_CHECKING:
    from app.state import AppState

log = logging.getLogger("poc.api")
router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/instances", response_model=None)
async def list_instances(request: Request) -> object:
    return _st(request).devices.list_dtos()


def _recusa_de_provisionamento(status: int, code: str, message: str, **extra: object) -> HTTPException:
    """A recusa contada: cada motivo é um rótulo de conjunto fechado (os códigos 409/400/404 desta rota)."""
    metricas.contar("provisionamento.pedido", resultado="recusado", motivo=code)
    return _err(status, code, message, **extra)


def _aparelhos_do_servidor(s: AppState, worker_id: str) -> int:
    """Quantos aparelhos EXISTEM naquela máquina — a mesma conta que a tela Limites mostra em `devices` (a loja fora),
    para o número do cartão e a recusa por teto contarem a mesma história."""
    return sum(1 for rt in s.devices.devices.values() if not rt.store and s.scheduler.servidor_de(rt) == worker_id)


#: Tarefas de partida encadeada em voo (referência forte: uma tarefa sem dono pode ser recolhida no meio).
_PARTIDAS_ENCADEADAS: set[asyncio.Task[None]] = set()


async def _partida_depois_do_create(s: AppState, instance_id: str, command_id: str, requested_by: str) -> None:
    """`start: true` no provisionamento: liga SÓ depois de o `create` fechar `succeeded`.

    Encadear é mais honesto do que abrir os dois de uma vez: o `start` seria recusado no pré-voo (`device_busy`,
    o `create` ainda em voo) ou, pior, faria o boot criar o AVD por conta própria e o `create` fechar em cima de um
    AVD que já existia. A partida é pedida por `pedir_ciclo_de_vida`, o mesmo caminho do rodízio: cerca, pré-voo e
    outbox de sempre; a recusa fica no histórico do aparelho. A espera é limitada ao prazo do `create` mais folga.
    NÃO sobrevive a um reinício do backend entre o `create` e o `start`: nesse caso o aparelho fica criado e
    `stopped`, e a pessoa liga pelo painel — é o que a resposta (`start: "after_create"`) promete, nada além.
    """
    limite = monotonic() + prazo_de("create") + 30
    try:
        while True:
            linha = s.commands.get(command_id)
            if linha is None:
                return
            estado = CommandState(linha["state"])
            if estado not in COMMAND_OPEN:
                break
            if monotonic() > limite:
                s.bus.emit("log", f"{instance_id}: o 'create' não fechou dentro do prazo; a partida encadeada foi "
                                  "cancelada — ligue pelo painel quando o AVD existir", level="warn",
                           instance_id=instance_id, data={"command_id": command_id})
                return
            await asyncio.sleep(0.25)
        if estado != CommandState.succeeded:
            s.bus.emit("log", f"{instance_id}: o 'create' fechou '{estado.value}'; a partida encadeada não foi "
                              "pedida", level="warn", instance_id=instance_id, data={"command_id": command_id})
            return
        pedido = pedir_ciclo_de_vida(s, instance_id, "start", "partida pedida junto com o provisionamento",
                                     requested_by=requested_by)
        if pedido is None:
            s.bus.emit("log", f"{instance_id}: a partida encadeada foi recusada no pré-voo (veja o histórico do "
                              "aparelho)", level="warn", instance_id=instance_id)
    except Exception:  # noqa: BLE001 - a partida encadeada é conveniência: nunca derruba nada nem fica sem registro
        log.exception("%s: falha na partida encadeada ao provisionamento", instance_id)


@router.post("/instances", status_code=202)
async def provision_instance(request: Request, body: InstanceProvisionBody,
                             response: Response) -> dict[str, object]:
    """Um aparelho NOVO neste servidor, pela plataforma (migração 050; proposta §8.3 da onda D).

    Até aqui nenhuma origem criava aparelho novo: o YAML (`instances.count`) exige reinício, a adoção exige um
    worker anunciando, e o verbo `create` só cria o AVD de instância já declarada. Aqui a INSTÂNCIA nasce (linha
    `dynamic` + runtime vivo, como na adoção) e o AVD vem pelo `create` de sempre — cerca, outbox, desfecho
    `succeeded|failed|uncertain` em `GET /api/commands/{id}`. 202 porque o AVD leva tempo; 201 quando `create:
    false` (só a instância).

    Só o hospedeiro: provisionar num worker remoto exige verbo novo no protocolo e inventário mutável no agente
    (ADR próprio, outra rodada) — 409 `provisionamento_remoto_indisponivel`. Nunca vira loja: a loja é a do YAML.
    """
    s = _st(request)
    host = s.cfg.owner_id
    quem_pediu = quem(request)
    alvo = (body.worker_id or "").strip() or host
    if alvo != host:
        if s.db.one("SELECT id FROM workers WHERE id=?", (alvo,)) is None:
            raise _recusa_de_provisionamento(404, "not_found", f"Servidor {alvo} não existe.")
        raise _recusa_de_provisionamento(
            409, "provisionamento_remoto_indisponivel",
            f"Provisionar em {alvo} ainda não é possível: o agente do worker só conhece o inventário do "
            "`worker.yaml` dele, e criar aparelho lá exige um verbo novo no protocolo e inventário mutável no "
            "agente (ADR próprio). Provisione neste servidor, ou adote um aparelho que o worker anuncie "
            "(`POST /api/workers/{id}/devices/adopt`).")
    if body.idempotency_key and not body.create:
        # A chave deduplica pelo comando `create`; sem comando não há o que deduplicar, e aceitar a chave calada
        # prometeria uma proteção que não existe.
        raise _recusa_de_provisionamento(400, "idempotency_key_sem_comando",
                                         "`idempotency_key` só vale com `create: true` — é a chave do comando.")
    if body.start and not body.create:
        # Mesma razão: a partida é encadeada ao `create`; sem ele, aceitar `start` seria deixar cair um pedido.
        raise _recusa_de_provisionamento(400, "start_sem_create",
                                         "`start: true` só vale com `create: true` — a partida vem depois do AVD.")
    if body.idempotency_key:
        anterior = s.db.one("SELECT id, instance_id, state FROM commands WHERE idempotency_key=? AND verb='create'",
                            (body.idempotency_key,))
        if anterior is not None and anterior["instance_id"] in s.devices.devices:
            # Mesma chave: a instância e o comando originais. Reenviar não cria outro aparelho.
            rt = s.devices.devices[anterior["instance_id"]]
            return {"instance": s.devices.dto(rt), "instance_id": rt.id, "command_id": anterior["id"],
                    "command_state": anterior["state"], "deduplicated": True, "start": "not_requested"}
        if anterior is not None:
            # A chave é de um aparelho que já saiu do parque (aposentado): seguir criaria uma instância nova cujo
            # `create` o banco deduplicaria para o comando ANTIGO — resposta coerente só recusando.
            raise _recusa_de_provisionamento(
                409, "chave_ja_usada", f"`idempotency_key` já foi usada pelo provisionamento de "
                                       f"{anterior['instance_id']}, que não está mais no parque; use outra chave.",
                instance_id=anterior["instance_id"])
    if not s.cfg.hospeda_aparelhos:
        raise _recusa_de_provisionamento(409, "servidor_nao_hospeda",
                                         "Este backend não hospeda aparelhos (ROLE=api): provisione no que hospeda.")
    if body.app_id and s.db.one("SELECT id FROM apps WHERE id=?", (body.app_id,)) is None:
        raise _recusa_de_provisionamento(400, "unknown_app", "App não cadastrado.")
    teto = s.workers.limites_definidos(host).get("max_devices")
    existentes = _aparelhos_do_servidor(s, host)
    if teto is not None and existentes >= teto:
        raise _recusa_de_provisionamento(
            409, "teto_de_aparelhos",
            f"Este servidor já tem {existentes} aparelho(s) e o teto decidido é {teto} (`max_devices`). Suba o "
            "teto em Limites ou aposente um aparelho antes de criar outro.", devices=existentes, max_devices=teto)
    cap = s.workers.capacidade(host)
    livre = cap.disk_free_gb if cap is not None else None
    piso = float(s.cfg.file.provisioning.min_free_disk_gb)
    if livre is None:
        raise _recusa_de_provisionamento(
            409, "disco_desconhecido", "Este servidor ainda não mediu o disco livre (a batida do worker local não "
                                       "trouxe `disk_free_gb`); sem medição não se cria AVD.")
    if livre < piso:
        raise _recusa_de_provisionamento(
            409, "disco_insuficiente",
            f"Este servidor tem {livre:.1f} GB livres e o mínimo para criar mais um AVD é {piso:.0f} GB "
            "(`provisioning.min_free_disk_gb`).", disk_free_gb=livre, min_free_disk_gb=piso)
    try:
        sobreposicao = s.devices.conferir_sobreposicao_android(body.sobreposicao_android())
    except ValueError as exc:
        raise _recusa_de_provisionamento(400, "sobreposicao_invalida", str(exc)) from exc
    try:
        rt = s.devices.provisionar(app_id=body.app_id, android_overrides=sobreposicao,
                                   worker_verbs=s.workers.verbs_de(host), requested_by=quem_pediu)
    except ValueError as exc:
        raise _recusa_de_provisionamento(409, "conflito_de_provisionamento", str(exc)) from exc
    metricas.contar("provisionamento.pedido", resultado="aceito")
    if body.app_id:
        # Como no `PUT`: vincular um app é dizer "ele opera este app", e a versão promovida é o estado desejado.
        s.aplicar_versao_promovida(rt)
    saida: dict[str, object] = {"instance": s.devices.dto(rt), "instance_id": rt.id, "command_id": None,
                                "command_state": None, "deduplicated": False, "start": "not_requested"}
    if not body.create:
        response.status_code = 201
        return saida
    row, estado, _ = await abrir_e_despachar(s, rt, "create", InstanceActionBody(idempotency_key=body.idempotency_key),
                                             requested_by=quem_pediu)
    saida.update(command_id=row["id"], command_state=estado)
    if body.start:
        tarefa = asyncio.create_task(_partida_depois_do_create(s, rt.id, str(row["id"]), quem_pediu),
                                     name=f"partida-encadeada-{rt.id}")
        _PARTIDAS_ENCADEADAS.add(tarefa)
        tarefa.add_done_callback(_PARTIDAS_ENCADEADAS.discard)
        saida["start"] = "after_create"
    return saida


#: Estados de objetivo que ainda vão tocar no aparelho. `uncertain` fica de fora: terminou, ninguém sabe o efeito, e
#: aposentar o aparelho não muda isso; quem decide o desfecho continua podendo.
_OBJETIVO_ABERTO = ("pending", "running", "waiting_user")


@router.delete("/instances/{instance_id}")
async def retire_instance(request: Request, instance_id: str) -> dict[str, object]:
    """Aposenta uma instância provisionada pela plataforma (migração 050): apaga o AVD e tira o aparelho do parque.

    Só `origin='dynamic'`: a instância do YAML sai editando `instances.count` (409 `instancia_da_configuracao`).
    Recusa enquanto houver objetivo aberto, vínculo ativo de perfil (os dados da sessão vivem no AVD que seria
    apagado), comando em voo ou trabalho em curso — e com o emulador no ar. A linha fica, com `retired_at`; o id e
    as portas não são reaproveitados.
    """
    s = _st(request)
    rt = device(s, instance_id)

    def recusa(code: str, message: str, **extra: object) -> HTTPException:
        metricas.contar("provisionamento.aposentadoria", resultado="recusada", motivo=code)
        return _err(409, code, message, **extra)

    if rt.external:
        # Adotado de um worker também é `dynamic`, mas o AVD dele mora na OUTRA máquina: apagar aqui pelo nome
        # atingiria um AVD local homônimo ou nada, e o túnel continuaria encaminhando a porta da linha aposentada.
        # Desfazer a adoção fica com a rodada do provisionamento remoto.
        raise recusa("aparelho_de_worker",
                     f"{instance_id} é um aparelho adotado do worker {rt.worker_id}: o AVD dele vive naquela máquina. "
                     "Aposentar aparelho de worker fica para o provisionamento remoto.")
    if rt.origin != "dynamic":
        raise recusa("instancia_da_configuracao",
                     f"{instance_id} vem do config.yaml (`instances.count`); aposentar por aqui só vale para "
                     "instância criada pela plataforma ou adotada. Reduza `count` e reinicie para removê-la.")
    marcadores = ",".join("?" for _ in _OBJETIVO_ABERTO)
    aberto = s.db.one(f"SELECT id, status FROM objectives WHERE instance_id=? AND status IN ({marcadores})"
                      " ORDER BY id LIMIT 1", (instance_id, *_OBJETIVO_ABERTO))
    if aberto is not None:
        raise recusa("objetivo_aberto", f"{instance_id} tem o objetivo {aberto['id']} em '{aberto['status']}'; "
                                        "espere o desfecho ou cancele a execução antes.", objective_id=aberto["id"])
    if (perfis := [str(v["profile_id"]) for v in s.social_repo.profiles_of_instance(instance_id)]):
        raise recusa("vinculo_ativo", f"{instance_id} hospeda {'o perfil' if len(perfis) == 1 else 'os perfis'} "
                                      f"{', '.join(perfis)}: a sessão vive no AVD que seria apagado. Desvincule "
                                      "antes.", profile_id=perfis[0], profile_ids=perfis)
    if (em_voo := s.commands.open_for_instance(instance_id)) is not None:
        raise recusa("comando_em_voo", f"{instance_id} tem o comando '{em_voo['verb']}' em andamento "
                                       f"({em_voo['id']}, {em_voo['state']}); espere o desfecho.",
                     command_id=em_voo["id"])
    if instance_id in s.scheduler.workers:
        raise recusa("trabalho_em_curso", f"{instance_id} está com trabalho em execução (instalação, "
                                          "autenticação ou objetivo); espere terminar.")
    try:
        retired_at, apagado = s.devices.aposentar(rt, requested_by=quem(request))
    except ValueError as exc:
        raise recusa("aparelho_ligado", str(exc)) from exc
    except AvdError as exc:
        raise recusa("avd_nao_apagado", str(exc)) from exc
    metricas.contar("provisionamento.aposentadoria", resultado="aceita")
    return {"instance_id": instance_id, "retired_at": retired_at, "avd_removed": apagado}


def _recusar_mudanca_de_servidor(s: AppState, rt: DeviceRuntime, novo_worker: str | None, *, confirmado: bool) -> None:
    """Mover para outra máquina um aparelho que hospeda perfil com sessão pronta (item 4.4 / E9).

    Os dados do perfil — a sessão do Instagram — vivem na partição de dados do aparelho, no disco da máquina
    ANTIGA. Reapontar o id lógico não leva o disco junto: no aparelho da máquina nova a conta não está logada.
    Até aqui este PUT só conferia que o worker existia, e a sessão seguia `session_ready` em cache.

    Recusa com 409 a menos que a pessoa confirme. Quem confirma recebe, no mesmo movimento, a sessão invalidada.
    """
    if confirmado:
        return
    # Todas as personas do aparelho (vínculo N:N): basta UMA com sessão pronta NESTE aparelho para a mudança
    # precisar de confirmação — é a sessão dela que fica no disco de trás. Sessão de QUALQUER conta dela ali (item
    # 23.4): a do segundo app de login gerenciado fica no mesmo disco que a da âncora.
    com_sessao: list[str] = []
    vistas: set[str] = set()
    for vinculo in s.social_repo.profiles_of_instance(rt.id):
        profile_id = str(vinculo["profile_id"])
        if profile_id in vistas:
            continue                          # a mesma persona vinculada por dois apps é uma pessoa só na mensagem
        vistas.add(profile_id)
        if not any((sessao := s.social_repo.account_session_row(profile_id, str(conta["id"]), rt.id)) is not None
                   and sessao["status"] == SessionStatus.session_ready.value
                   for conta in s.social_repo.list_accounts(profile_id)):
            continue
        perfil = s.social_repo.profile_row(profile_id)
        com_sessao.append(f"@{perfil['username']}" if perfil is not None and perfil["username"]
                          else (perfil["display_name"] if perfil is not None else profile_id) or profile_id)
    if not com_sessao:
        return
    raise _err(409, "locality_change_requires_confirmation",
              f"{rt.id} hospeda {', '.join(com_sessao)}, com sessão pronta. Os dados dessa sessão ficam no disco de "
              f"{rt.worker_id or 'este servidor'}: movendo o aparelho para "
              f"{novo_worker or 'este servidor'}, será preciso entrar na conta de novo. Confirme para prosseguir.")


@router.put("/instances/{instance_id}", response_model=None)
async def update_instance(request: Request, instance_id: str, body: InstancePatch) -> object:
    s = _st(request)
    rt = device(s, instance_id)
    data = body.model_dump(exclude_unset=True)
    # Confirmação é decisão de quem chamou, nunca coluna: sai do dicionário antes de virar `UPDATE`.
    confirmado = bool(data.pop("confirm_locality_change", False))
    if data.get("app_id") and s.db.one("SELECT id FROM apps WHERE id=?", (data["app_id"],)) is None:
        raise _err(400, "unknown_app", "App não cadastrado.")
    if "account_label" in data:
        data["account_label"] = (data["account_label"] or "").strip() or None
    if "worker_id" in data:
        novo = (data["worker_id"] or "").strip() or None
        if novo and s.db.one("SELECT id FROM workers WHERE id=?", (novo,)) is None:
            raise _err(400, "unknown_worker", f"Worker '{novo}' não está inscrito. Inscreva-o antes de amarrar "
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
        s.devices.on_session_invalidated(  # type: ignore[attr-defined]  # o gancho é preenchido por `AppState.__init__`
            instance_id, "o aparelho passou a ser hospedado por outra máquina; a sessão gravada no disco anterior não está aqui")
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


@router.post("/instances/{instance_id}/app/install", status_code=202, response_model=None)
async def install_release_on(request: Request, instance_id: str, body: AppInstallBody) -> object:
    """Instala um conjunto do catálogo. 202 porque leva minutos: o resultado aparece em `GET /api/app-state`."""
    s = _st(request)
    rt = device(s, instance_id)
    recusa_loja_como_alvo(rt)
    if rt.state != InstanceState.online:
        raise _err(409, "not_online", "O aparelho precisa estar online para instalar.")
    # A pré-condição é conferida ANTES de aceitar: o trabalho roda em segundo plano, então uma recusa lá dentro
    # nunca chegaria a quem chamou.
    release = _release_pronta_para(s, rt, body.release_id)
    return {**_despachar_trabalho(
        s, rt, "app.install", lambda: s.releases.install_on(rt, body.release_id, s.installer),
        label="instalação de APK", params={"release_id": body.release_id, "package": release["package_name"]},
        idempotency_key=body.idempotency_key), "release_id": body.release_id}


@router.post("/instances/{instance_id}/app/verify", status_code=202, response_model=None)
async def verify_app_on(request: Request, instance_id: str, body: AppVerifyBody) -> object:
    """Relê do aparelho a versão instalada e registra divergência, se houver."""
    s = _st(request)
    rt = device(s, instance_id)
    if rt.state != InstanceState.online:
        raise _err(409, "not_online", "O aparelho precisa estar online para verificar o app.")
    return {**_despachar_trabalho(
        s, rt, "app.verify", lambda: s.releases.verify_on(rt, body.package, s.installer),
        label="verificação do app", params={"package": body.package},
        idempotency_key=body.idempotency_key), "package": body.package}


@router.get("/instances/{instance_id}/operational-context", response_model=None)
async def instance_context(request: Request, instance_id: str) -> object:
    """Servidor → aparelho → tela → apps → perfil/conta → sessão, cada camada com a sua fonte. Só leitura."""
    s = _st(request)
    device(s, instance_id)                                   # 404 com a frase de sempre
    return contexto_do_aparelho(s, instance_id)


@router.get("/instances/{instance_id}/packages", response_model=None)
async def packages(request: Request, instance_id: str) -> object:
    s = _st(request)
    rt = device(s, instance_id)
    if rt.state != InstanceState.online:
        raise _err(409, "offline", "A instância precisa estar online.")
    try:
        return {"packages": await s.devices.list_packages(rt)}
    except (DriverError, AdbError) as exc:
        raise _err(503, "adb_error", str(exc)) from exc


@router.post("/instances/bulk", status_code=202, response_model=None)
async def bulk_action(request: Request, body: BulkBody) -> object:
    s = _st(request)
    accepted: list[str] = []
    rejected: list[dict[str, object]] = []
    comandos: list[dict[str, object]] = []
    params = body.params or InstanceActionBody()
    if body.action not in LIFECYCLE_ACTIONS:
        raise _err(400, "rejected", "ação desconhecida.")
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


@router.post("/instances/{instance_id}/actions/{action}", status_code=202, response_model=None)
async def instance_action(request: Request, instance_id: str, action: str, body: InstanceActionBody | None = None) -> object:
    s = _st(request)
    rt = device(s, instance_id)
    params = body or InstanceActionBody()
    # Verbo inexistente não merece registro: não é tentativa de operar o aparelho, é chamada malformada.
    if action not in LIFECYCLE_ACTIONS:
        raise _err(400, "rejected", f"{instance_id}: ação desconhecida.")
    row, repetido = _abrir_comando(s, instance_id, action, params)
    if repetido:
        # Mesma chave: devolve o comando original. Reenviar não age duas vezes.
        return {"command_id": row["id"], "state": row["state"], "deduplicated": True}
    recusa = _precheck(s, rt, action, params, row["id"])
    if recusa:
        codigo, why = recusa
        # A recusa fica no histórico do aparelho com o motivo, em vez de virar um evento que ninguém mostra.
        _publish_command(s, s.commands.transition(row["id"], CommandState.rejected, reason=why))
        raise _err(409, codigo, f"{instance_id}: {why}.", command_id=row["id"])
    alvo: dict[str, object] = {}
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


@router.get("/instances/{instance_id}/frame", response_model=None)
async def frame(request: Request, instance_id: str, mode: str = "thumb") -> Response:
    rt = device(_st(request), instance_id)
    f = rt.frame
    if f is None:
        raise _err(404, "no_frame", "Ainda não há frame deste aparelho.")
    if f.sensitive:
        # Contrato C4: a prévia nunca mostra tela sensível. O marcador existe (tamanho, id para o controle manual),
        # mas não tem imagem — e a anterior já saiu do ar quando ele foi publicado.
        raise _err(404, "sensitive_screen", "A tela atual deste aparelho é sensível: a prévia não a mostra.")
    headers = {"X-Frame-Id": f.info.id, "X-Frame-Ts": f.info.ts, "X-Frame-Width": str(f.info.width),
               "X-Frame-Height": str(f.info.height), "X-Frame-Orientation": f.info.orientation,
               "Cache-Control": "no-store", "Access-Control-Expose-Headers": "X-Frame-Id, X-Frame-Ts, X-Frame-Width, X-Frame-Height, X-Frame-Orientation"}
    return Response(content=f.jpeg_full if mode == "full" else f.jpeg_thumb, media_type="image/jpeg", headers=headers)


@router.get("/instances/{instance_id}/hierarchy", response_model=None)
async def hierarchy(request: Request, instance_id: str) -> object:
    s = _st(request)
    rt = device(s, instance_id)
    if rt.state != InstanceState.online:
        raise _err(409, "offline", "A instância precisa estar online.")
    try:
        tree = await s.devices.hierarchy(rt)
    except DriverError as exc:
        raise _err(503, "automation_unavailable", str(exc)) from exc
    return {"ts": now_iso(), "elements": [e.to_dict() for e in tree.elements]}


@router.post("/instances/{instance_id}/locked-account/resolve")
async def resolve_locked_account(request: Request, instance_id: str, body: ResolverQuarentenaBody) -> dict[str, object]:
    """Uma pessoa resolve a QUARENTENA (ADR-055) depois de limpar o app do aparelho: o marcador aberto vira história
    (`resolved_by/resolution` = a nota), o rótulo sincroniza e sai o evento `device.locked_account` ("resolvido").
    Só banco: não toca disco nem app, e não reativa o perfil (isso é decisão de pessoa). A nota é obrigatória (422).
    Sem marcador aberto, 404 `no_locked_account` — como o `repair-pause` sem pausa."""
    s = _st(request)
    device(s, instance_id)
    nota = body.nota.strip()
    if not nota:
        raise _err(422, "nota_obrigatoria", "informe por que a quarentena foi resolvida")
    resolvidos = s.social_repo.resolver_conta_travada(instance_id, por=quem(request), nota=nota)
    if not resolvidos:
        raise _err(404, "no_locked_account", f"{instance_id} não tem marcador de conta travada aberto")
    return {"instance_id": instance_id, "resolvidos": resolvidos}


@router.put("/instances/{instance_id}/repair-pause")
async def set_repair_pause(request: Request, instance_id: str, body: RepairPauseBody) -> RepairPauseInfo | None:
    """Pausa o reparo AUTOMÁTICO deste aparelho (a escada e o reinício por saúde do central) por `ttl_s` (obrigatório,
    60 s a 3 h). Para experimento ou manutenção de UM aparelho: não afeta os outros nem a manutenção do worker; comando
    de pessoa e o `restart` da rede continuam passando. Expira sozinha; repetir o PUT renova o prazo."""
    s = _st(request)
    rt = device(s, instance_id)
    s.devices.pausar_reparo(rt, body.ttl_s, body.reason.strip(), quem(request))
    return s.devices.dto(rt).repair_pause


@router.delete("/instances/{instance_id}/repair-pause")
async def clear_repair_pause(request: Request, instance_id: str) -> dict[str, str]:
    """Encerra a pausa antes do prazo. Sem pausa em vigor, responde 404 `no_repair_pause`."""
    s = _st(request)
    rt = device(s, instance_id)
    if not s.devices.retomar_reparo(rt, quem(request)):
        raise _err(404, "no_repair_pause", f"{instance_id} não tem pausa de reparo")
    return {"status": "resumed"}


@router.post("/instances/{instance_id}/control/take", response_model=None)
async def take_control(request: Request, instance_id: str, body: TakeControlBody | None = None) -> object:
    s = _st(request)
    rt = device(s, instance_id)
    # Pedir o aparelho com a IA numa etapa é o gesto `tomou_controle` (ADR-054): leva o operador da sessão, que desde o
    # 29.143 também é o dono do lease (outra pessoa recebe 409 `controlled_by_other`, com `dono` e `desde`).
    try:
        status, lease = s.devices.request_control(rt, por=autor_do_gesto(getattr(request.state, "operador", None)), tomar=bool(body and body.tomar))
    except ControlError as exc:
        raise _err(409, exc.code, exc.message, **exc.detalhes) from exc
    return {"status": status, "lease_id": lease}


@router.post("/instances/{instance_id}/control/release", response_model=None)
async def release_control(request: Request, instance_id: str, body: ReleaseBody) -> object:
    s = _st(request)
    rt = device(s, instance_id)
    try:
        s.devices.release_control(rt, body.lease_id)
    except ControlError as exc:
        raise _err(409, exc.code, exc.message) from exc
    return {"status": "released"}


@router.post("/instances/{instance_id}/input", response_model=None)
async def manual_input(request: Request, instance_id: str, body: ManualInput) -> object:
    s = _st(request)
    rt = device(s, instance_id)
    try:
        await s.devices.manual_input(rt, body)
    except ControlError as exc:
        raise _err(409 if exc.code != "bad_input" else 400, exc.code, exc.message) from exc
    except (DriverError, AdbError) as exc:
        raise _err(503, "device_error", str(exc)) from exc
    return {"ok": True}
