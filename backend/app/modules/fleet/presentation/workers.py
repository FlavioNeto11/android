"""Os workers e os limites por servidor (`/api/workers*` e `/api/servers/*/limits`): listar, ler, adotar aparelho, cadastrar,
manutenção, remover e girar a credencial; e os limites de cada máquina. Saíram de `api.py` no 15.15 F4 (corte 5, F4e) sem
mudar caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra. `/workers/devices/unbound` (literal de três segmentos) não
é engolida por `/workers/{worker_id}` (um segmento). Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`). A rotação de credencial devolve o token novo UMA vez, na resposta;
nem ele nem o corpo de `enroll` vão para log ou evento (o evento diz só o id do worker).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import loads
from app.devices.manager import DeviceRuntime
from app.devices.verbs import sem_hibernacao
from app.models import (AdoptDeviceBody, InstanceState, ServerLimitsDTO, ServerLimitsPatch, ServerLimitValues,
                        WorkerDeviceProposal, WorkerEnrollBody, WorkerMaintenanceBody, WorkerRemoveBody)
from app.security.access import host_de, publicos_de
from app.workers.comando_remoto import ErroDeComando
from app.workers.comando_remoto import dto as dto_do_comando
from app.workers.registry import INSCRICAO_TTL_S, WorkerError

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/workers", response_model=None)
async def list_workers(request: Request) -> object:
    return _st(request).workers.dtos()


# ====================================================================== limites por servidor
def _limites_dos_servidores(s: AppState) -> list[ServerLimitsDTO]:
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
    por_servidor: dict[str, list[DeviceRuntime]] = {}
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
                max_working=decidido.get("max_working"), max_devices=decidido.get("max_devices"))
            # 29.84: as vagas que valem vêm da regra única (`vagas_que_valem`, pelo `capacidade`), a do agendador.
            efetivo = ServerLimitValues(max_slots=cap.max_slots if cap is not None else lim.max_online_devices,
                                        boot_parallelism=lim.boot_parallelism,
                                        max_working=decidido.get("max_working"),
                                        min_free_ram_mb=declarado.min_free_ram_mb,
                                        max_devices=decidido.get("max_devices"))
            # 29.142: a frase é para a pessoa (a chave crua com crases aparecia no painel); a chave fica no comentário:
            # `android.min_free_ram_mb_after_boot` no config.yaml.
            travado = {"min_free_ram_mb": "Depois de ligar mais um aparelho, este servidor tem de manter pelo menos isto "
                                          "livre. Muda só na configuração da instalação, com reinício."}
            nome = linha["name"] if linha is not None else f"{wid} (este servidor)"
        else:
            d = s.workers.limites_declarados(wid)
            declarado = ServerLimitValues(**d)
            decisao = ServerLimitValues(**decidido)
            efetivo = ServerLimitValues(
                max_slots=cap.max_slots if cap is not None else (decidido.get("max_slots") or d.get("max_slots")),
                boot_parallelism=decidido.get("boot_parallelism") or d.get("boot_parallelism"),
                max_working=decidido.get("max_working"),
                min_free_ram_mb=decidido.get("min_free_ram_mb", d.get("min_free_ram_mb")),
                # Só decisão: nenhuma máquina declara teto de aparelhos, e sem decisão não há teto.
                max_devices=decidido.get("max_devices"))
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


@router.get("/servers/limits", response_model=None)
async def list_server_limits(request: Request) -> object:
    return _limites_dos_servidores(_st(request))


@router.put("/servers/{worker_id}/limits", response_model=None)
async def put_server_limits(request: Request, worker_id: str, body: ServerLimitsPatch) -> object:
    """Muda os limites de UMA máquina. Campo enviado como `null` volta ao valor da máquina."""
    s = _st(request)
    patch = {k: getattr(body, k) for k in body.model_fields_set}
    if not patch:
        raise _err(400, "empty_patch", "Nada a mudar.")
    host = s.cfg.owner_id
    if worker_id != host and s.db.one("SELECT id FROM workers WHERE id=?", (worker_id,)) is None:
        raise _err(404, "not_found", f"Servidor {worker_id} não existe.")
    if worker_id == host:
        if "min_free_ram_mb" in patch:
            # 29.142: frase para a pessoa; a chave é `android.min_free_ram_mb_after_boot` no config.yaml.
            raise _err(400, "locked_limit", "A RAM livre depois de ligar um aparelho neste servidor muda só na "
                                           "configuração da instalação, com reinício.")
        base = s.cfg.file.limits
        vivos: dict[str, object] = {}
        if "max_slots" in patch:
            vivos["max_online_devices"] = patch["max_slots"] or base.max_online_devices
        if "boot_parallelism" in patch:
            vivos["boot_parallelism"] = patch["boot_parallelism"] or base.boot_parallelism
        if vivos:
            valor = s.settings.update(vivos)
            s.bus.emit("settings.updated", "Limites atualizados", data={"settings": valor.model_dump()})
            if "max_online_devices" in vivos:
                s.workers.publicar(s.cfg.owner_id)
        # Os dois tetos que não são configuração viva deste servidor moram em `worker_limits`, como nos workers:
        # "trabalhando ao mesmo tempo" (agendador) e "aparelhos existentes" (provisionamento, migração 050).
        no_banco: dict[str, int | None] = {k: patch[k] for k in ("max_working", "max_devices") if k in patch}
        if no_banco:
            s.workers.definir_limites(worker_id, no_banco, por="painel")
    else:
        try:
            s.workers.definir_limites(worker_id, patch, por="painel")
        except WorkerError as exc:
            raise _err(400, exc.code, exc.message) from exc
        # Aplica na hora no agente conectado; desconectado, recebe na próxima conexão (primeira batida).
        await s.workers.enviar_limites(worker_id)
        # As vagas efetivas estão no `WorkerDTO` (29.82): sem o evento, o painel seguia com as antigas até a próxima
        # mudança observável daquela máquina.
        s.workers.publicar(worker_id)
    s.scheduler.wake()
    return next(x for x in _limites_dos_servidores(s) if x.worker_id == worker_id)


@router.get("/workers/{worker_id}", response_model=None)
async def get_worker(request: Request, worker_id: str) -> object:
    s = _st(request)
    row = s.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
    if row is None:
        raise _err(404, "not_found", f"Worker {worker_id} não existe.")
    return s.workers.dto(row)


@router.get("/workers/devices/unbound", response_model=None)
async def unbound_worker_devices(request: Request) -> object:
    """Aparelhos que os workers ANUNCIAM e que ainda não são instância deste parque (item 4.5).

    O inventário já chegava no `hello` (`devices[].serial`, `.adb_port`) e era descartado: o central só usava o
    que estivesse em `instances.external`. Aqui ele vira a lista do painel — é o primeiro passo de "conectar
    servidores novos e executar os mesmos comandos" sem editar `config.yaml`.
    """
    s = _st(request)
    conhecidos = {rt.id for rt in s.devices.devices.values()}
    propostas: list[WorkerDeviceProposal] = []
    for w in s.workers.dtos():
        for d in w.devices:
            if d.instance_id and d.instance_id in conhecidos:
                continue
            propostas.append(WorkerDeviceProposal(worker_id=w.id, worker_name=w.name, serial=d.serial,
                                                  avd_name=d.avd_name, state=d.state, adb_port=d.adb_port))
    return propostas


@router.post("/workers/{worker_id}/devices/adopt", status_code=201, response_model=None)
async def adopt_worker_device(request: Request, worker_id: str, body: AdoptDeviceBody) -> object:
    """Transforma um aparelho anunciado em instância AGORA — sem editar YAML, sem reiniciar o backend.

    A porta local do túnel é alocada por ESTE servidor (é quem conhece as portas em uso) e gravada junto com a
    porta de ADB do lado do worker. O mapa do túnel é reescrito no arquivo que `worker-tunnel.ps1 -MapaArquivo`
    relê a cada volta do laço: acrescentar aparelho deixa de exigir reinstalar a tarefa agendada.
    """
    s = _st(request)
    linha = s.db.one("SELECT id, devices FROM workers WHERE id=?", (worker_id,))
    if linha is None:
        raise _err(404, "not_found", f"Worker {worker_id} não existe.")
    declarado = next((d for d in (loads(linha["devices"]) or []) if d.get("serial") == body.serial), None)
    if declarado is None:
        raise _err(404, "unknown_device", f"O worker {worker_id} não anunciou o aparelho '{body.serial}'. "
                                         "Ele declara o inventário no `hello` e na batida.")
    if not declarado.get("adb_port"):
        raise _err(409, "no_adb_port", f"O aparelho '{body.serial}' foi anunciado sem porta de ADB: sem ela o "
                                      "túnel não tem para onde encaminhar. Atualize o agente do worker.")
    if declarado.get("instance_id") and declarado["instance_id"] in s.devices.devices:
        raise _err(409, "already_bound", f"O aparelho '{body.serial}' já é a instância "
                                        f"{declarado['instance_id']}.")
    # O id que o AGENTE já usa vence o id inventado aqui. Ele resolve o despacho pelo `instance_id` do
    # `worker.yaml` dele (`worker/agent.py`: "este worker não hospeda X"); criar a instância com outro nome faria
    # todo comando falhar do outro lado e a conferência cruzada acusar divergência na batida seguinte.
    iid = body.instance_id or declarado.get("instance_id") or None
    try:
        rt = s.devices.adotar_aparelho(worker_id, serial=body.serial, adb_port=int(declarado["adb_port"]),
                                       instance_id=iid, avd_name=declarado.get("avd_name"))
    except ValueError as exc:
        raise _err(409, "rejected", str(exc)) from exc
    # O aparelho novo já nasce com o ciclo de vida que aquele worker declarou saber executar.
    declarados = s.workers.verbs_de(worker_id)
    s.devices.bind_worker(worker_id, None if declarados is None
                          else sem_hibernacao(declarados, s.workers.hiberna(worker_id)))
    arquivo = s.devices.escrever_mapa_do_tunel(worker_id)
    return {"instance": s.devices.dto(rt), "tunnel_map_file": str(arquivo) if arquivo else None,
            "tunnel_map": s.devices.mapa_do_tunel(worker_id)}


@router.post("/workers/enroll", status_code=201, response_model=None)
async def enroll_worker(request: Request, body: WorkerEnrollBody | None = None) -> object:
    """Gera um token de inscrição de USO ÚNICO e prazo curto.

    O token em claro aparece nesta resposta e nunca mais: só o hash é guardado. É o que responde "ao configurado
    aqui, o servidor principal tem acesso" sem ninguém digitar credencial permanente numa máquina nova.
    """
    s = _st(request)
    token = s.workers.criar_inscricao((body or WorkerEnrollBody()).label)
    s.bus.emit("log", "Token de inscrição de worker gerado (uso único, validade de 1 h).")
    return {"enrollment_token": token, "expires_in_s": int(INSCRICAO_TTL_S)}


@router.post("/workers/{worker_id}/maintenance", response_model=None)
async def worker_maintenance(request: Request, worker_id: str, body: WorkerMaintenanceBody) -> object:
    """Manutenção suspende NOVAS atribuições e não derruba o que já está em voo."""
    s = _st(request)
    try:
        row = s.workers.set_maintenance(worker_id, body.on)
    except WorkerError as exc:
        raise _err(404 if exc.code == "not_found" else 409, exc.code, exc.message) from exc
    return s.workers.dto(row)


@router.delete("/workers/{worker_id}", status_code=200, response_model=None)
async def remove_worker(request: Request, worker_id: str, body: WorkerRemoveBody | None = None) -> object:
    """Fecha o procedimento que `docs/worker.md` já prometia: sem isto, um worker sem credencial ficava trancado
    do lado de fora para sempre — não dava para reinscrever o mesmo id nem apagar o registro sem editar o banco."""
    s = _st(request)
    try:
        s.workers.remove(worker_id, force=(body or WorkerRemoveBody()).force)
    except WorkerError as exc:
        raise _err(404 if exc.code == "not_found" else 409, exc.code, exc.message) from exc
    # O banco já desamarrou (UPDATE instances SET worker_id=NULL); o runtime em memória precisa do mesmo —
    # senão o painel segue mostrando o aparelho preso a um worker que não existe mais até reiniciar o backend.
    for rt in s.devices.devices.values():
        if rt.worker_id == worker_id:
            rt.worker_id = None
            rt.worker_verbs = None
            s.devices.publish(rt, f"{rt.id}: worker '{worker_id}' foi removido; aparelho ficou sem dono")
    s.bus.emit("worker.removed", f"Worker {worker_id} removido do painel.", data={"worker_id": worker_id})
    return {"ok": True, "worker_id": worker_id}


@router.post("/workers/{worker_id}/rotate-credential", response_model=None)
async def rotate_worker_credential(request: Request, worker_id: str) -> object:
    """Máquina comprometida: a credencial velha para de servir NA HORA, e esta resposta traz a nova uma única vez."""
    s = _st(request)
    try:
        token = s.workers.rotate_credential(worker_id)
    except WorkerError as exc:
        raise _err(404 if exc.code == "not_found" else 409, exc.code, exc.message) from exc
    s.bus.emit("log", f"Credencial do worker {worker_id} rotacionada no painel.", level="warn")
    return {"credential": token}


# ---------------------------------------------------------------- comando remoto (29.154, ADR-079)
# Todas as rotas abaixo exigem SESSÃO NOMEADA de operador: o token da API é compartilhado e prova conhecimento, não
# identidade (`main.py`), e o loopback passa sem token; para um comando executado numa máquina o nome de quem pediu é
# obrigatório e vem sempre da sessão, nunca do corpo. E nenhuma delas existe no host PÚBLICO do portal: lá respondem 404
# mesmo com credencial (`/api/workers` passa com credencial no host público; esta família não pode).


class ComandoRemotoBody(BaseModel):
    """Exatamente um entre `linha` e `argv`. A resposta NÃO repete a linha: ela pode ter sido digitada com pressa."""

    model_config = ConfigDict(extra="forbid")
    linha: str | None = Field(default=None, max_length=8192)
    argv: list[str] | None = Field(default=None, max_length=64)
    pasta: str | None = Field(default=None, max_length=1024)
    timeout_s: float | None = Field(default=None, gt=0, le=600)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class ComandoRemotoInterruptorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ligado: bool


def _acesso_ao_comando_remoto(request: Request) -> str:
    """Devolve o nome do operador, ou recusa: 404 no host público, 401 sem sessão nomeada."""
    s = _st(request)
    if host_de(request.headers.get("host")) in publicos_de(s.cfg):
        raise _err(404, "not_found", "Not Found")
    operador = getattr(request.state, "operador", None)
    if not operador:
        raise _err(401, "sem_operador", "o comando remoto exige uma sessão de operador nomeada")
    return str(operador)


def _erro_do_comando(exc: ErroDeComando) -> HTTPException:
    return _err(exc.status, exc.code, exc.message)


@router.get("/workers/{worker_id}/comando-remoto", response_model=None)
async def comando_remoto_interruptor(request: Request, worker_id: str) -> object:
    _acesso_ao_comando_remoto(request)
    return _st(request).workers.comandos.estado_do_interruptor(worker_id)


@router.put("/workers/{worker_id}/comando-remoto", response_model=None)
async def comando_remoto_ligar(request: Request, worker_id: str, body: ComandoRemotoInterruptorBody) -> object:
    operador = _acesso_ao_comando_remoto(request)
    try:
        return _st(request).workers.comandos.definir_interruptor(worker_id, body.ligado, operador)
    except ErroDeComando as exc:
        raise _erro_do_comando(exc) from exc


@router.post("/workers/{worker_id}/comandos", status_code=202, response_model=None)
async def comando_remoto_pedir(request: Request, worker_id: str, body: ComandoRemotoBody) -> object:
    operador = _acesso_ao_comando_remoto(request)
    try:
        row = await _st(request).workers.comandos.pedir(
            worker_id, operador, linha=body.linha, argv=body.argv, pasta=body.pasta, timeout_s=body.timeout_s,
            idempotency_key=body.idempotency_key)
    except ErroDeComando as exc:
        raise _erro_do_comando(exc) from exc
    return {"id": row["id"], "worker_id": row["worker_id"], "state": row["state"], "created_at": row["created_at"]}


@router.get("/workers/{worker_id}/comandos", response_model=None)
async def comando_remoto_listar(request: Request, worker_id: str, limite: int = 50) -> object:
    _acesso_ao_comando_remoto(request)
    return {"items": [dto_do_comando(r, com_saida=False)
                      for r in _st(request).workers.comandos.listar(worker_id, limite)]}


@router.get("/workers/{worker_id}/comandos/{exec_id}", response_model=None)
async def comando_remoto_ler(request: Request, worker_id: str, exec_id: str) -> object:
    _acesso_ao_comando_remoto(request)
    try:
        return dto_do_comando(_st(request).workers.comandos.obter(worker_id, exec_id))
    except ErroDeComando as exc:
        raise _erro_do_comando(exc) from exc


@router.post("/workers/{worker_id}/comandos/{exec_id}/cancelar", response_model=None)
async def comando_remoto_cancelar(request: Request, worker_id: str, exec_id: str) -> object:
    operador = _acesso_ao_comando_remoto(request)
    try:
        return dto_do_comando(await _st(request).workers.comandos.cancelar(worker_id, exec_id, operador))
    except ErroDeComando as exc:
        raise _erro_do_comando(exc) from exc
