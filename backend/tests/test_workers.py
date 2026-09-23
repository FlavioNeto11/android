"""Registro de workers: identidade, inscrição de uso único, batida, cerca e despacho.

Estes testes cobrem a LÓGICA do canal com um agente falso. O transporte WebSocket em si é fino e é validado
contra o worker de verdade — está dito assim de propósito, para não confundir "testado com simulação" com
"provado em infraestrutura real".
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.workers.protocol import Dispatch, Heartbeat, Hello, Result, WorkerDevice, WorkerResources
from app.workers.registry import HEARTBEAT_S, WorkerError, WorkerRegistry
from .conftest import Harness, make_config


def _registro(tmp_path: Path) -> WorkerRegistry:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    # quando a suite e apontada para la. Com `db_path` ele abriria SQLite mesmo dentro da corrida do
    # outro banco — cobertura que parece existir e nao existe.
    db = Database(cfg.db_dsn)
    db.migrate()
    return WorkerRegistry(db)


def _hello(**kw: Any) -> Hello:
    base = dict(worker_id="worker-lan-01", name="Notebook da LAN", agent_version="0.1.0", os="windows",
                max_slots=6, verbs=["start", "stop", "hibernate", "reset", "home"],
                devices=[WorkerDevice(serial="emulator-5554", avd_name="worker-01", state="online", adb_port=5555)],
                resources=WorkerResources(cpu_count=12, ram_total_mb=65273, ram_free_mb=46367))
    return Hello(**{**base, **kw})


class AgenteFalso:
    """Registra o que o central mandou e responde como um worker responderia."""

    def __init__(self) -> None:
        self.enviados: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> None:
        self.enviados.append(payload)


# ---------------------------------------------------------------- inscrição
def test_inscricao_e_de_uso_unico_e_devolve_credencial_uma_vez(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    token = reg.criar_inscricao("notebook da sala")
    # O token em claro existe uma vez; o banco guarda só o hash.
    assert reg.db.scalar("SELECT COUNT(*) FROM worker_enrollments WHERE token_hash=?", (token,)) == 0

    credencial = reg.autenticar(_hello(), token=None, enrollment=token)
    assert credencial and len(credencial) > 20
    # Segunda vez com o MESMO token: recusado, e com o motivo.
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(worker_id="worker-lan-02"), token=None, enrollment=token)
    assert exc.value.code == "enrollment_used"


def test_credencial_certa_entra_e_errada_nao(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    credencial = reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    assert reg.autenticar(_hello(), token=credencial, enrollment=None) == ""
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(), token="credencial-errada", enrollment=None)
    assert exc.value.code == "bad_credential"


def test_worker_desconhecido_explica_o_caminho(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(), token="qualquer", enrollment=None)
    assert exc.value.code == "not_enrolled"
    assert "Gere um token de inscrição no painel" in exc.value.message


def test_protocolo_do_agente_mais_novo_que_o_servidor_e_recusado(tmp_path: Path) -> None:
    """Melhor recusar do que agir com mensagens que não se entende."""
    reg = _registro(tmp_path)
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(protocol=99), token=None, enrollment=reg.criar_inscricao())
    assert exc.value.code == "protocol_too_new"


# ---------------------------------------------------------------- declaração e saúde
def test_o_worker_declara_o_que_e_e_isso_aparece_no_dto(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(appium_mode="local", appium_url="http://127.0.0.1:4723"), token=None,
                   enrollment=reg.criar_inscricao())
    dto = reg.dtos()[0]
    assert dto.id == "worker-lan-01" and dto.name == "Notebook da LAN"
    assert dto.appium_mode == "local" and dto.max_slots == 6
    assert "hibernate" in dto.verbs and dto.resources.ram_total_mb == 65273
    assert [d.serial for d in dto.devices] == ["emulator-5554"]
    assert dto.connected is False                      # declarou-se, mas o canal ainda não está instalado


def test_socket_caindo_nao_marca_offline_mas_a_falta_de_batida_marca(tmp_path: Path) -> None:
    """Socket cai por rede piscando. Tratar as duas coisas como a mesma é o defeito que o STF arrastou por anos."""
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.attach("worker-lan-01", AgenteFalso().send)
    reg.detach("worker-lan-01", "cabo solto")
    assert reg.dtos()[0].observed_state == "online"     # ainda dentro do prazo da batida
    assert reg.dtos()[0].state_detail == "cabo solto"

    # Envelhece a última batida além do limite e ceifa.
    reg.db.execute("UPDATE workers SET last_seen_at='2020-01-01T00:00:00.000Z' WHERE id=?", ("worker-lan-01",))
    assert reg.reap() == ["worker-lan-01"]
    assert reg.dtos()[0].state == "offline"


def test_manutencao_e_decisao_e_nao_esconde_o_estado_observado(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.attach("worker-lan-01", AgenteFalso().send)
    reg.set_maintenance("worker-lan-01", True)
    dto = reg.dtos()[0]
    assert dto.state == "maintenance" and dto.observed_state == "online" and dto.maintenance is True
    assert "manutenção" in (reg.aceita_trabalho("worker-lan-01") or "")
    reg.set_maintenance("worker-lan-01", False)
    assert reg.aceita_trabalho("worker-lan-01") is None


def test_worker_desconectado_nao_aceita_trabalho(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    assert "não está conectado" in (reg.aceita_trabalho("worker-lan-01") or "")


# ---------------------------------------------------------------- remoção e rotação de credencial (#168 / #150)
def test_remover_worker_desconhecido_reclama(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    with pytest.raises(WorkerError) as exc:
        reg.remove("worker-fantasma")
    assert exc.value.code == "not_found"


def test_remover_worker_conectado_e_recusado_sem_force(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.attach("worker-lan-01", AgenteFalso().send)
    with pytest.raises(WorkerError) as exc:
        reg.remove("worker-lan-01")
    assert exc.value.code == "connected"
    assert reg.db.one("SELECT id FROM workers WHERE id=?", ("worker-lan-01",)) is not None  # nada mudou

    reg.remove("worker-lan-01", force=True)             # force desconecta e apaga
    assert reg.db.one("SELECT id FROM workers WHERE id=?", ("worker-lan-01",)) is None
    assert "worker-lan-01" not in reg.live


def test_worker_removido_nao_volta_com_a_credencial_antiga(tmp_path: Path) -> None:
    """Contrato de revogação: remover é revogar. A credencial que o notebook perdido guarda para de valer.

    Sem esta asserção, `remove` poderia apagar a linha e ainda assim deixar o agente antigo reentrar num caminho
    que recriasse o registro — que é exatamente o buraco que a remoção existe para fechar.
    """
    reg = _registro(tmp_path)
    credencial = reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.remove("worker-lan-01")

    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(), token=credencial, enrollment=None)
    assert exc.value.code == "not_enrolled"

    # E o token de inscrição já usado também não ressuscita o id: é preciso gerar outro no painel.
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(), token=None, enrollment="token-que-nunca-existiu")
    assert exc.value.code == "unknown_enrollment"

    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())   # com token novo, volta
    assert reg.db.one("SELECT id FROM workers WHERE id=?", ("worker-lan-01",)) is not None


def test_remover_worker_com_comando_em_voo_e_recusado_sem_force(tmp_path: Path) -> None:
    from app.commands.store import CommandStore
    from app.models import CommandState

    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    cmds = CommandStore(reg.db)
    row, _ = cmds.create(command_id="cmd-1", instance_id="android-03", verb="stop", idempotency_key="k-1")
    cmds.transition("cmd-1", CommandState.dispatched, worker_id="worker-lan-01")

    with pytest.raises(WorkerError) as exc:
        reg.remove("worker-lan-01")
    assert exc.value.code == "open_commands"

    cmds.transition("cmd-1", CommandState.running)
    cmds.transition("cmd-1", CommandState.succeeded)    # comando terminou: remoção volta a ser possível
    reg.remove("worker-lan-01")
    assert reg.db.one("SELECT id FROM workers WHERE id=?", ("worker-lan-01",)) is None


async def test_remover_worker_desamarra_as_instancias_em_vez_de_prende_las(tmp_path: Path) -> None:
    from .conftest import Harness

    h = Harness(tmp_path, 3)
    await h.boot()
    assert h.state is not None
    reg = h.state.workers
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", ("worker-lan-01", "android-03"))
    try:
        reg.remove("worker-lan-01")
        linha = h.state.db.one("SELECT worker_id FROM instances WHERE id=?", ("android-03",))
        assert linha is not None and linha["worker_id"] is None    # aparelho sobrevive, sem dono fantasma
    finally:
        await h.state.stop()


def test_rotacionar_credencial_troca_o_hash_e_derruba_a_conexao(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    credencial_antiga = reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.attach("worker-lan-01", AgenteFalso().send)

    nova = reg.rotate_credential("worker-lan-01")
    assert nova and nova != credencial_antiga
    assert "worker-lan-01" not in reg.live               # a conexão viva caiu na hora

    # A credencial antiga não serve mais; a nova sim.
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(), token=credencial_antiga, enrollment=None)
    assert exc.value.code == "bad_credential"
    assert reg.autenticar(_hello(), token=nova, enrollment=None) == ""


def test_rotacionar_credencial_de_worker_desconhecido_reclama(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    with pytest.raises(WorkerError) as exc:
        reg.rotate_credential("worker-fantasma")
    assert exc.value.code == "not_found"


# ---------------------------------------------------------------- batida redundante não vira evento (achados #17/#143)
def test_batidas_identicas_chamam_on_change_uma_vez_e_on_metrics_sempre(tmp_path: Path) -> None:
    """O defeito do achado: TODA batida (uma a cada 10 s por worker) virava `worker.updated` persistido — 57 %
    do log de eventos era isto. Agora só a mudança OBSERVÁVEL (estado, detalhe, inventário) chama `on_change`;
    o recurso (CPU/RAM/disco) segue em toda batida, mas por `on_metrics` — que quem chama publica como evento
    EFÊMERO (`worker.metrics`, ver `events.EPHEMERAL_KINDS` e `AppState._publish_worker_metrics`)."""
    mudou: list[str] = []
    metricas: list[tuple[str, Any]] = []
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    reg = WorkerRegistry(db, on_change=mudou.append, on_metrics=lambda wid, res: metricas.append((wid, res)))
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    assert mudou == []          # autenticar/upsert não passa por on_heartbeat

    for _ in range(5):
        reg.on_heartbeat("worker-lan-01", Heartbeat(
            resources=WorkerResources(ram_free_mb=40000, cpu_percent=42.0),
            devices=[WorkerDevice(serial="emulator-5554", state="online")]))
    assert mudou == ["worker-lan-01"]        # 5 batidas iguais, 1 evento persistido
    assert len(metricas) == 5                # recurso chega a CADA batida, mas fora do log

    # Inventário mudando (novo aparelho) É observável: chama on_change de novo.
    reg.on_heartbeat("worker-lan-01", Heartbeat(
        resources=WorkerResources(ram_free_mb=39000),
        devices=[WorkerDevice(serial="emulator-5554", state="online"),
                 WorkerDevice(serial="emulator-5556", state="booting")]))
    assert mudou == ["worker-lan-01", "worker-lan-01"]
    assert len(metricas) == 6


def test_o_evento_efemero_leva_a_hora_da_batida_mesmo_sem_recursos(harness: Harness) -> None:
    """Medido no painel: com a Infraestrutura aberta, "Último contato" chegava a "há 2 min 33 s — os dados abaixo
    podem estar desatualizados" com a API dizendo que a batida tinha 4 s. A batida não muda nada observável,
    então (por desenho) não há `worker.updated`; e o efêmero só levava recurso. O selo de dado velho, que
    existe para denunciar worker calado, acusava todo worker vivo depois de um minuto de tela aberta."""
    emitidos: list[tuple[str, dict[str, Any]]] = []
    harness.state.bus.emit = lambda kind, msg, **kw: emitidos.append((kind, kw.get("data") or {}))  # type: ignore[method-assign]

    harness.state._publish_worker_metrics("worker-lan-01", WorkerResources(ram_free_mb=1000, cpu_percent=5.0))
    harness.state._publish_worker_metrics("worker-lan-01", None)     # protocolo antigo: sem recurso

    assert [k for k, _ in emitidos] == ["worker.metrics", "worker.metrics"]
    com, sem = (d for _, d in emitidos)
    assert com["resources"]["ram_free_mb"] == 1000 and "T" in com["last_seen_at"]
    assert "resources" not in sem and "T" in sem["last_seen_at"], "sem recurso ainda há batida a contar"


def test_batida_sem_recursos_nao_chama_on_metrics(tmp_path: Path) -> None:
    """Worker de protocolo antigo pode mandar batida sem `resources`; sem valor nenhum não há o que publicar."""
    metricas: list[Any] = []
    reg = _registro(tmp_path)
    reg.on_metrics = lambda wid, res: metricas.append(res)  # type: ignore[method-assign]
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.on_heartbeat("worker-lan-01", Heartbeat())
    assert metricas == [None]


def test_batida_atualiza_recursos_e_inventario(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.on_heartbeat("worker-lan-01", Heartbeat(
        resources=WorkerResources(ram_free_mb=40000, cpu_percent=42.0),
        devices=[WorkerDevice(serial="emulator-5554", state="online"),
                 WorkerDevice(serial="emulator-5556", state="booting")]))
    dto = reg.dtos()[0]
    assert dto.resources.ram_free_mb == 40000 and dto.resources.cpu_percent == 42.0
    assert len(dto.devices) == 2


# ---------------------------------------------------------------- relógio (achado #142)
def test_batida_com_relogio_desalinhado_marca_degraded(tmp_path: Path) -> None:
    """Reproduz o achado: ~97 s de desvio medido entre central e worker do parque. Sem fonte NTP em nenhuma das
    duas máquinas, é isto que precisa aparecer na Infraestrutura em vez de ficar silencioso."""
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.on_heartbeat("worker-lan-01", Heartbeat(clock_offset_s=-97.0))
    dto = reg.dtos()[0]
    assert dto.state == "degraded"
    assert dto.state_detail is not None and "-97.0" in dto.state_detail


def test_batida_com_relogio_alinhado_no_worker_antes_degradado_volta_a_online(tmp_path: Path) -> None:
    """O desvio é medido a cada batida: se o worker reconecta (ou o operador ajusta o relógio) e o desvio some,
    o estado tem de acompanhar — em vez de prender o worker em `degraded` para sempre."""
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.on_heartbeat("worker-lan-01", Heartbeat(clock_offset_s=97.0))
    assert reg.dtos()[0].state == "degraded"
    reg.on_heartbeat("worker-lan-01", Heartbeat(clock_offset_s=0.4))
    dto = reg.dtos()[0]
    assert dto.state == "online"
    assert dto.state_detail is None


def test_batida_sem_desvio_relatado_nao_mexe_em_detalhe_de_outra_causa(tmp_path: Path) -> None:
    """Worker de protocolo antigo manda `clock_offset_s=None`: isso não pode apagar um `state_detail` que veio
    de outro motivo (ex.: `detach` registrando por que caiu)."""
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.db.execute("UPDATE workers SET state_detail=? WHERE id=?", ("motivo não relacionado a relógio",
                                                                    "worker-lan-01"))
    reg.on_heartbeat("worker-lan-01", Heartbeat())
    dto = reg.dtos()[0]
    assert dto.state == "online"
    assert dto.state_detail == "motivo não relacionado a relógio"


def test_calculo_do_desvio_de_relogio_e_puro() -> None:
    from datetime import datetime, timezone

    from app.worker.agent import clock_offset_seconds

    central = datetime(2026, 9, 21, 21, 22, 0, tzinfo=timezone.utc)
    local_atrasado = datetime(2026, 9, 21, 21, 20, 23, tzinfo=timezone.utc)   # ~97 s atrás do central
    desvio = clock_offset_seconds(central.isoformat(), local_atrasado)
    assert desvio is not None and 96.5 < desvio < 97.5
    assert clock_offset_seconds(None, local_atrasado) is None
    assert clock_offset_seconds("lixo-nao-e-data", local_atrasado) is None


# ---------------------------------------------------------------- despacho
async def _pronto(tmp_path: Path) -> tuple[WorkerRegistry, AgenteFalso]:
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteFalso()
    reg.attach("worker-lan-01", agente.send)
    return reg, agente


async def test_despacho_espera_o_desfecho_do_worker(tmp_path: Path) -> None:
    reg, agente = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-1", fence=1, verb="stop", instance_id="android-09", serial="emulator-5554")

    async def responde() -> None:
        await asyncio.sleep(0.02)
        assert reg.on_ack("worker-lan-01", "c-1") is True
        assert reg.on_result("worker-lan-01", Result(command_id="c-1", outcome="succeeded"), fence=1) is True

    tarefa = asyncio.create_task(responde())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "succeeded"
    assert agente.enviados[0]["verb"] == "stop" and agente.enviados[0]["fence"] == 1


async def test_worker_que_nao_responde_no_prazo_deixa_o_resultado_INCERTO(tmp_path: Path) -> None:
    """Nunca falha por silêncio: o worker pode ter agido e não conseguido responder."""
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-2", fence=1, verb="reset", instance_id="android-09", serial="emulator-5554",
                   timeout_s=0.05)
    r = await reg.dispatch("worker-lan-01", msg)
    assert r.outcome == "uncertain" and "não respondeu" in (r.reason or "")


async def test_espera_na_fila_de_boot_empurra_o_prazo_em_vez_de_virar_incerto(tmp_path: Path) -> None:
    """A fila do worker (uma por vez, contra o ANR) não pode virar "resultado incerto" no central.

    Antes, o prazo corria desde o despacho: com `boot_parallelism=1` e boots de 104-192 s, o quarto `start` de um
    lote só COMEÇARIA depois de 312 s dos 540 s — e o comando fecharia incerto sem nada ter falhado.
    """
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-fila", fence=1, verb="start", instance_id="android-09", serial="emulator-5554",
                   timeout_s=0.08)

    async def espera_na_fila_e_responde() -> None:
        await asyncio.sleep(0.04)
        assert reg.adiar("worker-lan-01", "c-fila", 0.6) is True      # "estou na fila de boot"
        await asyncio.sleep(0.12)                                     # passou do prazo ORIGINAL
        assert reg.on_result("worker-lan-01", Result(command_id="c-fila", outcome="succeeded"), fence=1) is True

    tarefa = asyncio.create_task(espera_na_fila_e_responde())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "succeeded"


async def test_adiar_comando_que_nao_esta_em_voo_nao_inventa_prazo(tmp_path: Path) -> None:
    reg, _ = await _pronto(tmp_path)
    assert reg.adiar("worker-lan-01", "c-que-nao-existe", 30) is False
    assert reg.adiar("worker-desconhecido", "c-1", 30) is False


async def test_resultado_com_cerca_velha_e_recusado(tmp_path: Path) -> None:
    """Worker que voltou do limbo não sobrescreve o presente."""
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-3", fence=7, verb="stop", instance_id="android-09", serial="emulator-5554",
                   timeout_s=0.3)

    async def responde_velho() -> None:
        await asyncio.sleep(0.02)
        assert reg.on_result("worker-lan-01", Result(command_id="c-3", outcome="succeeded"), fence=3) is False

    tarefa = asyncio.create_task(responde_velho())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "uncertain"                     # o resultado velho foi descartado; o prazo decidiu


async def test_queda_do_canal_deixa_o_que_estava_em_voo_INCERTO(tmp_path: Path) -> None:
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-4", fence=1, verb="stop", instance_id="android-09", serial="emulator-5554",
                   timeout_s=5)

    async def cai() -> None:
        await asyncio.sleep(0.02)
        reg.detach("worker-lan-01", "socket fechou")

    tarefa = asyncio.create_task(cai())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "uncertain" and "socket fechou" in (r.reason or "")


async def test_conexao_nova_do_mesmo_worker_derruba_a_anterior(tmp_path: Path) -> None:
    """Um dono por worker, sempre — senão dois canais disputariam o mesmo aparelho."""
    reg, _ = await _pronto(tmp_path)
    msg = Dispatch(command_id="c-5", fence=1, verb="stop", instance_id="android-09", serial="emulator-5554",
                   timeout_s=5)

    async def reconecta() -> None:
        await asyncio.sleep(0.02)
        reg.attach("worker-lan-01", AgenteFalso().send)

    tarefa = asyncio.create_task(reconecta())
    r = await reg.dispatch("worker-lan-01", msg)
    await tarefa
    assert r.outcome == "uncertain" and "substituiu" in (r.reason or "")


async def test_despacho_para_worker_offline_falha_na_hora(tmp_path: Path) -> None:
    """Falha ao ENVIAR é o único caso em que se pode afirmar que nada aconteceu."""
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    with pytest.raises(WorkerError) as exc:
        await reg.dispatch("worker-lan-01", Dispatch(command_id="c-6", fence=1, verb="stop",
                                                    instance_id="android-09", serial="emulator-5554"))
    assert exc.value.code == "worker_offline"


def test_prazo_da_batida_e_coerente_com_o_ceifador() -> None:
    """Se o ceifador rodasse mais rápido que a batida, todo worker saudável seria marcado offline."""
    assert HEARTBEAT_S > 0


# ---------------------------------------------------------------- roteamento: painel → agente
class AgenteQueResponde(AgenteFalso):
    """Agente falso que responde ao despacho como o agente real responderia."""

    def __init__(self, registry: WorkerRegistry, worker_id: str, outcome: str = "succeeded",
                 reason: str | None = None) -> None:
        super().__init__()
        self.reg, self.worker_id, self.outcome, self.reason = registry, worker_id, outcome, reason

    async def send(self, payload: dict[str, Any]) -> None:
        await super().send(payload)
        if payload.get("type") != "dispatch":
            return
        cid, cerca = payload["command_id"], payload["fence"]
        self.reg.on_ack(self.worker_id, cid)
        self.reg.on_result(self.worker_id, Result(command_id=cid, outcome=self.outcome, reason=self.reason,
                                                 data={"started": True}), fence=cerca)


async def _harness_com_worker(tmp_path: Path, outcome: str = "succeeded", reason: str | None = None):
    """Harness com `android-03` hospedado num worker conectado, como ficaria em produção."""
    import httpx

    from app.main import create_app
    from .conftest import Harness

    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    reg = h.state.workers
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    agente = AgenteQueResponde(reg, "worker-lan-01", outcome, reason)
    reg.attach("worker-lan-01", agente.send)
    # Amarra a instância ao worker, como o cadastro do servidor faria.
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", ("worker-lan-01", "android-03"))
    rt = h.state.devices.get("android-03")
    rt.worker_id = "worker-lan-01"
    h.state.devices.bind_worker("worker-lan-01", list(_hello().verbs))

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return h, reg, agente, cliente


async def test_com_worker_o_aparelho_remoto_ganha_ciclo_de_vida(tmp_path: Path) -> None:
    """É a correção de fundo: antes, `stop` num aparelho de outra máquina era recusado porque não havia agente."""
    h, _reg, agente, cliente = await _harness_com_worker(tmp_path)
    try:
        async with cliente as c:
            inst = next(i for i in (await c.get("/api/instances")).json() if i["id"] == "android-03")
            # Com o agente declarando ciclo de vida, o aparelho passa a aceitar o que antes era recusado.
            assert {"stop", "hibernate", "reset", "start"} <= set(inst["supported_verbs"])

            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "w-stop-0001"})
            assert r.status_code == 202, r.text
            cid = r.json()["command_id"]
            await h.wait(lambda: h.state.commands.get(cid)["state"] == "succeeded",  # type: ignore[union-attr]
                         what="comando concluído pelo worker")
            registro = h.state.commands.get(cid)          # type: ignore[union-attr]
            assert registro["worker_id"] == "worker-lan-01"
            # O despacho levou a cerca, e o agente a devolveu.
            despacho = next(p for p in agente.enviados if p.get("type") == "dispatch")
            assert despacho["verb"] == "stop" and despacho["instance_id"] == "android-03"
            assert despacho["fence"] == registro["fence"]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_desfecho_incerto_do_worker_chega_como_incerto_ao_painel(tmp_path: Path) -> None:
    h, _reg, _a, cliente = await _harness_com_worker(tmp_path, outcome="uncertain",
                                                     reason="o aparelho não completou o boot em 480 s")
    try:
        async with cliente as c:
            r = await c.post("/api/instances/android-03/actions/start", json={"idempotency_key": "w-start-0001"})
            cid = r.json()["command_id"]
            await h.wait(lambda: h.state.commands.get(cid)["state"] == "uncertain",  # type: ignore[union-attr]
                         what="comando incerto")
            assert "não completou o boot" in h.state.commands.get(cid)["reason"]   # type: ignore[union-attr]
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_worker_que_cai_deixa_o_aparelho_sem_ciclo_de_vida_de_novo(tmp_path: Path) -> None:
    """Sem agente do outro lado, o painel para de oferecer botão que não faria nada."""
    h, reg, _a, cliente = await _harness_com_worker(tmp_path)
    try:
        async with cliente as c:
            reg.detach("worker-lan-01", "socket fechou")
            h.state.devices.bind_worker("worker-lan-01", None)    # type: ignore[union-attr]
            inst = next(i for i in (await c.get("/api/instances")).json() if i["id"] == "android-03")
            assert "stop" not in inst["supported_verbs"] and "reset" not in inst["supported_verbs"]
            r = await c.post("/api/instances/android-03/actions/stop", json={"idempotency_key": "w-stop-0002"})
            assert r.status_code == 409
            assert "outra máquina" in r.json()["detail"]["message"]
    finally:
        if h.state is not None:
            await h.state.stop()


# ---------------------------------------------------------------- versão do agente e aceleração (itens 10.1/10.4)
def test_agente_com_versao_diferente_da_do_central_aparece_como_defasado(tmp_path: Path) -> None:
    """Achado #137: a cópia do agente na máquina do worker não é checkout, e as duas pontas diziam `0.1.0`.
    Não havia como saber pelo painel que aquela máquina roda código de três semanas atrás."""
    from app.version import agent_version

    reg = _registro(tmp_path)
    token = reg.criar_inscricao()
    reg.autenticar(_hello(agent_version="0.1.0+velho00"), token=None, enrollment=token)
    dto = next(d for d in reg.dtos() if d.id == "worker-lan-01")
    assert dto.agent_outdated is True
    assert dto.expected_agent_version == agent_version()

    # Mesma versão do central: nada a apontar.
    linha = reg.db.one("SELECT * FROM workers WHERE id=?", ("worker-lan-01",))
    reg.db.execute("UPDATE workers SET agent_version=? WHERE id=?", (agent_version(), "worker-lan-01"))
    assert reg.dto(reg.db.one("SELECT * FROM workers WHERE id=?", ("worker-lan-01",))).agent_outdated is False
    assert linha is not None


def test_versao_ausente_nao_conta_como_defasada(tmp_path: Path) -> None:
    """Linha antiga, sem versão registrada: "não se sabe" não é "defasado". Acusar aqui seria alarme sem fato."""
    reg = _registro(tmp_path)
    token = reg.criar_inscricao()
    reg.autenticar(_hello(), token=None, enrollment=token)
    reg.db.execute("UPDATE workers SET agent_version=NULL WHERE id=?", ("worker-lan-01",))
    assert reg.dto(reg.db.one("SELECT * FROM workers WHERE id=?", ("worker-lan-01",))).agent_outdated is False


def test_o_proprio_central_nunca_aparece_defasado(tmp_path: Path) -> None:
    reg = _registro(tmp_path)
    token = reg.criar_inscricao()
    reg.autenticar(_hello(agent_version="0.1.0+velho00"), token=None, enrollment=token)
    reg.local_worker_id = "worker-lan-01"       # comparar o central consigo mesmo não informa nada
    assert reg.dto(reg.db.one("SELECT * FROM workers WHERE id=?", ("worker-lan-01",))).agent_outdated is False


def test_a_aceleracao_declarada_no_hello_chega_ao_painel(tmp_path: Path) -> None:
    """Achado #180: sem KVM utilizável o emulador não sobe em tempo útil, e isso só aparecia como comando
    estourando prazo do outro lado da rede."""
    reg = _registro(tmp_path)
    token = reg.criar_inscricao()
    reg.autenticar(_hello(os="linux", accel="kvm-inacessivel"), token=None, enrollment=token)
    assert reg.dto(reg.db.one("SELECT * FROM workers WHERE id=?", ("worker-lan-01",))).accel == "kvm-inacessivel"

    # Worker antigo (sem o campo) não passa a declarar nada por herança da conexão anterior.
    reg.db.execute("DELETE FROM workers WHERE id=?", ("worker-lan-01",))
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    assert reg.dto(reg.db.one("SELECT * FROM workers WHERE id=?", ("worker-lan-01",))).accel is None


def test_protocolo_abaixo_do_piso_e_recusado_com_o_que_fazer(tmp_path: Path) -> None:
    """Havia só o teto (`> PROTOCOL_VERSION`), e teto sozinho promete compatibilidade eterna com agente antigo."""
    from app.workers.protocol import PROTOCOL_MIN

    reg = _registro(tmp_path)
    with pytest.raises(WorkerError) as exc:
        reg.autenticar(_hello(protocol=PROTOCOL_MIN - 1), token=None, enrollment=reg.criar_inscricao())
    assert exc.value.code == "protocol_too_old"
    assert "atualize o agente" in exc.value.message
