"""Recursos declarativos, fase H, segunda parte: `apply`, `verify` e `reconcile` dos quatro providers.

O que cada teste protege, em uma frase:

* aplicar duas vezes não abre dois comandos — nem no dublê do canal, nem no despacho de verdade (harness);
* `on_missing` decide quem dispara: `apply` pede, `wait` deixa com o despacho de hoje, `ask` é pessoa; ler (`observe`)
  vale para os três;
* o mundo pode mudar entre planejar e aplicar: ação que o recurso não pede mais não vira comando;
* `uncertain` não se repete e só fecha com prova observada DEPOIS do comando — nunca como falha;
* reconciliar é do backend que hospeda o aparelho (`so_meu`);
* `verify` só prova com leitura positiva;
* o vocabulário do kernel é o do legado (estados de comando, abertos, incertos);
* o canal real recusa antes de gravar o que o despacho recusaria depois (aparelho ocupado, fora do ar).

Nível de prova: `simulated` — banco de teste, dublês do runtime e do canal, e o harness (porta base 5640) com o
`FakeEmulatorBackend` para o despacho de verdade. Nenhum aparelho real, nenhuma IA.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any
from collections.abc import Mapping

import pytest

from app.commands.states import COMMAND_OPEN, COMMAND_UNSETTLED
from app.db import Database
from app.models import CommandState, InstanceState
from app.modules.applications.infrastructure.app_installation import AppInstallationProvider
from app.modules.execution.application.resources import ResourceConvergence
from app.modules.execution.infrastructure.command_bus import DespachoCommandBus, command_bus
from app.modules.execution.infrastructure.providers import resource_providers
from app.modules.fleet.infrastructure.device_state import DeviceStateProvider
from app.modules.identity.infrastructure.account_session import AccountBindingProvider, AppSessionProvider
from app.planning.catalog import session_provider_of
from app.shared.commands import (OPEN, UNSETTLED, CommandRef, CommandStatus, RunRef, key_prefix, sequence_of)
from app.shared.convergence import REQUESTED_BY, VerifyStatus
from app.shared.resources import ActionPurpose, DriftStatus, ResourceSpec, Target

from .conftest import Harness
from .test_leitura_de_recursos import (AGORA, RuntimeFalso, _credencial, _estado_do_app, _instancia,
                                       _parque_do_instagram, _perfil, _sessao, _vincular, banco)

__all__ = ["banco"]          # a fixture vem do teste de leitura: o mesmo banco migrado

ANTES = "2026-09-27T11:00:00.000Z"
PEDIDO = "2026-09-27T12:00:00.000Z"
DEPOIS = "2026-09-27T13:00:00.000Z"
ALVO = Target("android-01")


def _spec(kind: str, target: str | None, desired: Any, on_missing: str) -> ResourceSpec:
    return ResourceSpec.of(kind, target, desired, on_missing)


def device(on_missing: str = "apply") -> ResourceSpec:
    return _spec("device.state", None, "online", on_missing)


def instalacao(on_missing: str = "apply") -> ResourceSpec:
    return _spec("app.installation", "instagram", {"release": "promoted"}, on_missing)


def sessao(on_missing: str = "apply") -> ResourceSpec:
    return _spec("app.session", "instagram", {"session": "ready", "account": "bound_profile"}, on_missing)


VINCULO = _spec("account.binding", None, "bound", "ask")


class BusFalso:
    """Dublê do `CommandBus`: os comandos num dicionário, com a chave única de `commands`."""

    def __init__(self, *, hospeda: bool = True) -> None:
        self.comandos: dict[str, CommandRef] = {}
        self.pedidos: list[dict[str, Any]] = []
        self.hospeda = hospeda

    def request(self, instance_id: str, verb: str, params: Mapping[str, str], *, requested_by: str,
                run_ref: RunRef | None, idempotency_key: str, reason: str) -> CommandRef:
        for c in self.comandos.values():
            if c.idempotency_key == idempotency_key:
                return replace(c, deduplicated=True)
        self.pedidos.append({"instance_id": instance_id, "verb": verb, "params": dict(params),
                             "requested_by": requested_by, "run_ref": run_ref, "key": idempotency_key})
        cid = f"c-{len(self.comandos) + 1}"
        self.comandos[cid] = CommandRef(instance_id=instance_id, verb=verb, command_id=cid,
                                        status=CommandStatus.dispatched, idempotency_key=idempotency_key,
                                        created_at=PEDIDO, reason=reason)
        return self.comandos[cid]

    def _do_prefixo(self, instance_id: str, verb: str, prefixo: str) -> list[CommandRef]:
        return sorted((c for c in self.comandos.values() if c.instance_id == instance_id and c.verb == verb
                       and (c.idempotency_key or "").startswith(prefixo + "#")),
                      key=lambda c: sequence_of(c.idempotency_key))

    def latest(self, instance_id: str, verb: str, key_prefix: str) -> CommandRef | None:
        linhas = self._do_prefixo(instance_id, verb, key_prefix)
        return linhas[-1] if linhas else None

    def unsettled(self, instance_id: str, verb: str, key_prefix: str) -> list[CommandRef]:
        return [c for c in self._do_prefixo(instance_id, verb, key_prefix) if c.status is CommandStatus.uncertain]

    def settle(self, command_id: str, *, proof: str) -> CommandRef:
        self.comandos[command_id] = replace(self.comandos[command_id], status=CommandStatus.succeeded, reason=proof)
        return self.comandos[command_id]

    def hosts(self, instance_id: str) -> bool:
        return self.hospeda

    def marcar(self, command_id: str, status: CommandStatus) -> None:
        self.comandos[command_id] = replace(self.comandos[command_id], status=status)


def _app_no_parque(db: Database, *, estado: str | None = "missing", **campos: Any) -> None:
    """android-01 é aparelho do Instagram; a promovida-alvo é a r-447 (`_parque_do_instagram`)."""
    _parque_do_instagram(db)
    _instancia(db, "android-01", 1, app_id="instagram")
    if estado is not None:
        _estado_do_app(db, "android-01", state=estado, **campos)


def _uma_acao(provider: Any, spec: ResourceSpec, alvo: Target = ALVO) -> Any:
    acoes = provider.plan(provider.diff(spec, provider.read_current_state(spec.ref, alvo)))
    assert len(acoes) == 1, acoes
    return acoes[0]


def _contar(db: Database) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM commands") or 0)


# =============================================================================================== o kernel
def test_vocabulario_de_comando_do_kernel_e_o_do_legado() -> None:
    assert {s.value for s in CommandStatus} == {s.value for s in CommandState}
    assert {s.value for s in OPEN} == {s.value for s in COMMAND_OPEN}
    assert {s.value for s in UNSETTLED} == {s.value for s in COMMAND_UNSETTLED}


def test_chave_de_idempotencia_cabe_no_campo_do_comando() -> None:
    curta = key_prefix("android-01", "app.installation", "instagram", "app.install")
    assert curta == "res:android-01:app.installation:instagram:app.install"
    longa = key_prefix("android-01", "app.installation", "x" * 200, "app.install")
    assert len(longa + "#99999") <= 120 and longa.startswith("res:")
    assert sequence_of(f"{curta}#7") == 7 and sequence_of("chave-do-painel") == 0


# =============================================================================================== apply
def test_apply_pede_uma_vez_e_a_segunda_passada_nao_abre_outro(banco: Database) -> None:
    _app_no_parque(banco)
    bus = BusFalso()
    p = AppInstallationProvider(banco, bus=bus)
    acao = _uma_acao(p, instalacao())
    assert (acao.purpose, acao.verb) == (ActionPurpose.converge, "app.install")
    primeiro = p.apply(acao, spec=instalacao(), run_ref=RunRef(run_id="r-1", objective_id="o-1"))
    assert primeiro.requested and not primeiro.deduplicated
    assert bus.pedidos == [{"instance_id": "android-01", "verb": "app.install",
                            "params": {"package": "com.instagram.android", "release_id": "r-447"},
                            "requested_by": REQUESTED_BY, "run_ref": RunRef(run_id="r-1", objective_id="o-1"),
                            "key": "res:android-01:app.installation:instagram:app.install#1"}]
    # Segunda passada: o aparelho ainda não mudou (o comando está em voo), o plano ainda pede `app.install` — e
    # nenhum comando novo nasce.
    segundo = p.apply(_uma_acao(p, instalacao()), spec=instalacao())
    assert segundo.command_id == primeiro.command_id and segundo.deduplicated
    assert len(bus.pedidos) == 1 and len(bus.comandos) == 1


def test_comando_terminado_libera_nova_tentativa_com_a_chave_seguinte(banco: Database) -> None:
    _app_no_parque(banco)
    bus = BusFalso()
    p = AppInstallationProvider(banco, bus=bus)
    primeiro = p.apply(_uma_acao(p, instalacao()), spec=instalacao())
    assert primeiro.command_id is not None
    bus.marcar(primeiro.command_id, CommandStatus.rejected)      # recusado no pré-voo: nada foi tocado
    segundo = p.apply(_uma_acao(p, instalacao()), spec=instalacao())
    assert segundo.command_id != primeiro.command_id and segundo.idempotency_key is not None
    assert segundo.idempotency_key.endswith("#2")


def test_uncertain_nao_se_repete(banco: Database) -> None:
    _app_no_parque(banco)
    bus = BusFalso()
    p = AppInstallationProvider(banco, bus=bus)
    primeiro = p.apply(_uma_acao(p, instalacao()), spec=instalacao())
    assert primeiro.command_id is not None
    bus.marcar(primeiro.command_id, CommandStatus.uncertain)
    de_novo = p.apply(_uma_acao(p, instalacao()), spec=instalacao())
    assert de_novo.command_id == primeiro.command_id and de_novo.status is CommandStatus.uncertain
    assert de_novo.deduplicated and "não se repete" in (de_novo.reason or "")
    assert len(bus.pedidos) == 1


def test_on_missing_decide_quem_dispara(banco: Database) -> None:
    _app_no_parque(banco)
    bus = BusFalso()
    p = AppInstallationProvider(banco, bus=bus)
    # `wait`: a convergência é do despacho de hoje (a porta do app); o recurso não pede nada.
    esperar = p.apply(_uma_acao(p, instalacao("wait")), spec=instalacao("wait"))
    assert not esperar.requested and "wait" in (esperar.reason or "")
    # `ask`: o plano já é de pessoa, sem verbo — e ação de pessoa não vira comando.
    pessoa = _uma_acao(p, instalacao("ask"))
    assert pessoa.purpose is ActionPurpose.ask and pessoa.verb is None
    with pytest.raises(ValueError):
        p.apply(pessoa, spec=instalacao("ask"))
    assert bus.pedidos == []


def test_ler_vale_mesmo_com_wait_e_ask(banco: Database) -> None:
    """`unknown` se responde lendo (`app.verify`, `session.verify`), qualquer que seja o `on_missing`."""
    _app_no_parque(banco, estado="verifying")                       # instalação sem desfecho
    bus = BusFalso()
    p = AppInstallationProvider(banco, bus=bus)
    ler = _uma_acao(p, instalacao("wait"))
    assert (ler.purpose, ler.verb) == (ActionPurpose.observe, "app.verify")
    ref = p.apply(ler, spec=instalacao("wait"))
    assert ref.requested and bus.pedidos[0]["params"] == {"package": "com.instagram.android"}

    _perfil(banco, "p-tadeu", "tadeu")
    _vincular(banco, "p-tadeu", "android-01")
    _credencial(banco, "p-tadeu")
    s = _sessoes_com(banco, bus)
    ler_sessao = _uma_acao(s, sessao("ask"))                        # nunca verificada: não se sabe
    assert (ler_sessao.purpose, ler_sessao.verb) == (ActionPurpose.observe, "session.verify")
    assert s.apply(ler_sessao, spec=sessao("ask")).requested
    assert bus.pedidos[-1]["params"] == {"profile_id": "p-tadeu", "app_id": "instagram"}


def _sessoes_com(db: Database, bus: BusFalso) -> AppSessionProvider:
    return AppSessionProvider(db, tem_provedor_de_sessao=lambda pkg: session_provider_of(pkg) == "instagram",
                              session_max_age_s=3600, unknown_retry_cap=3, agora=lambda: AGORA, bus=bus)


def test_sessao_deslogada_conecta_so_com_apply_e_com_credencial(banco: Database) -> None:
    _app_no_parque(banco, estado=None)
    _perfil(banco, "p-tadeu", "tadeu")
    _vincular(banco, "p-tadeu", "android-01")
    _sessao(banco, "p-tadeu", "auth_required")
    bus = BusFalso()
    s = _sessoes_com(banco, bus)
    # sem credencial no cofre: é de pessoa (ADR-025), e não há comando
    assert s.diff(sessao(), s.read_current_state(sessao().ref, ALVO)).status is DriftStatus.blocked
    _credencial(banco, "p-tadeu")
    conectar = _uma_acao(s, sessao())
    assert (conectar.purpose, conectar.verb) == (ActionPurpose.converge, "session.connect")
    assert s.apply(conectar, spec=sessao()).requested
    assert bus.pedidos[0]["verb"] == "session.connect"


def test_apply_relê_e_nao_pede_o_que_o_recurso_nao_pede_mais(banco: Database) -> None:
    _app_no_parque(banco)
    bus = BusFalso()
    p = AppInstallationProvider(banco, bus=bus)
    acao = _uma_acao(p, instalacao())                                # planejada com o app faltando
    # entre planejar e aplicar, a porta do app instalou a promovida
    banco.execute("UPDATE device_app_state SET state='installed', installed_release_id='r-447',"
                  " observed_version_code=447 WHERE instance_id='android-01'")
    ref = p.apply(acao, spec=instalacao())
    assert not ref.requested and "não pede mais" in (ref.reason or "")
    assert bus.pedidos == []


def test_vinculo_nunca_vira_comando(banco: Database) -> None:
    _instancia(banco, "android-01", 1)
    bus = BusFalso()
    p = AccountBindingProvider(banco, bus=bus)
    acao = _uma_acao(p, VINCULO)
    assert acao.purpose is ActionPurpose.ask and acao.verb is None
    with pytest.raises(ValueError):
        p.apply(acao, spec=VINCULO)
    assert bus.pedidos == []


def test_aparelho_parado_pede_start_ao_canal(banco: Database) -> None:
    _instancia(banco, "android-01", 1)
    bus = BusFalso()
    p = DeviceStateProvider(banco, {"android-01": RuntimeFalso(state="stopped", readiness_phase="not_running",
                                                               worker_verbs=["start", "stop"])}, bus=bus)
    ref = p.apply(_uma_acao(p, device()), spec=device())
    assert ref.requested and bus.pedidos[0]["verb"] == "start" and bus.pedidos[0]["params"] == {}
    assert p.apply(_uma_acao(p, device()), spec=device()).deduplicated and len(bus.pedidos) == 1


def test_sem_canal_o_provider_e_so_leitura(banco: Database) -> None:
    _app_no_parque(banco)
    p = AppInstallationProvider(banco)
    with pytest.raises(RuntimeError):
        p.apply(_uma_acao(p, instalacao()), spec=instalacao())


# =============================================================================================== verify
def test_verify_so_prova_com_leitura_positiva(banco: Database) -> None:
    _app_no_parque(banco, estado=None)
    p = AppInstallationProvider(banco)
    assert p.verify(instalacao(), ALVO).status is VerifyStatus.unknown        # nunca inspecionado
    _estado_do_app(banco, "android-01", state="installed", installed_release_id="r-440", observed_version_code=440)
    atrasado = p.verify(instalacao(), ALVO)
    assert atrasado.status is VerifyStatus.not_proved and atrasado.drift.status is DriftStatus.diverged
    banco.execute("UPDATE device_app_state SET installed_release_id='r-447', observed_version_code=447")
    assert p.verify(instalacao(), ALVO).status is VerifyStatus.proved


# =============================================================================================== reconcile
def _incerto(bus: BusFalso, verb: str, prefixo: str, *, criado: str = PEDIDO) -> str:
    cid = f"c-{len(bus.comandos) + 1}"
    bus.comandos[cid] = CommandRef(instance_id="android-01", verb=verb, command_id=cid,
                                   status=CommandStatus.uncertain, idempotency_key=f"{prefixo}#1", created_at=criado)
    return cid


def test_uncertain_so_fecha_com_prova_posterior_ao_comando(banco: Database) -> None:
    _app_no_parque(banco, estado="installed", installed_release_id="r-440", observed_version_code=440,
                   verified_at=DEPOIS)
    bus = BusFalso()
    p = AppInstallationProvider(banco, bus=bus)
    cid = _incerto(bus, "app.install", key_prefix("android-01", "app.installation", "instagram", "app.install"))

    # lido depois, mas ainda atrasado: sem prova, continua incerto — e não vira falha
    r = p.reconcile(instalacao(), ALVO)
    assert r.hosted and [c.command_id for c in r.still_uncertain] == [cid] and r.settled == ()
    assert bus.comandos[cid].status is CommandStatus.uncertain

    # na promovida, mas a leitura é de ANTES do comando: não diz nada sobre ele
    banco.execute("UPDATE device_app_state SET installed_release_id='r-447', observed_version_code=447,"
                  " verified_at=?", (ANTES,))
    r = p.reconcile(instalacao(), ALVO)
    assert r.verify is not None and r.verify.status is VerifyStatus.proved
    assert [c.command_id for c in r.still_uncertain] == [cid] and bus.comandos[cid].status is CommandStatus.uncertain

    # na promovida, lida depois do comando: prova
    banco.execute("UPDATE device_app_state SET verified_at=?", (DEPOIS,))
    r = p.reconcile(instalacao(), ALVO)
    assert [c.command_id for c in r.settled] == [cid] and r.still_uncertain == ()
    assert bus.comandos[cid].status is CommandStatus.succeeded
    assert "verificado pelo estado real" in (bus.comandos[cid].reason or "")


def test_reconcile_so_no_hospedeiro(banco: Database) -> None:
    _app_no_parque(banco, estado="installed", installed_release_id="r-447", observed_version_code=447,
                   verified_at=DEPOIS)
    bus = BusFalso(hospeda=False)
    p = AppInstallationProvider(banco, bus=bus)
    cid = _incerto(bus, "app.install", key_prefix("android-01", "app.installation", "instagram", "app.install"))
    r = p.reconcile(instalacao(), ALVO)
    assert not r.hosted and r.verify is None and r.settled == ()
    assert bus.comandos[cid].status is CommandStatus.uncertain


def test_sessao_incerta_fecha_com_a_sessao_verificada_depois(banco: Database) -> None:
    _app_no_parque(banco, estado=None)
    _perfil(banco, "p-tadeu", "tadeu")
    _vincular(banco, "p-tadeu", "android-01")
    _credencial(banco, "p-tadeu")
    _sessao(banco, "p-tadeu", "session_ready", verificada=AGORA.replace(hour=13))
    bus = BusFalso()
    s = _sessoes_com(banco, bus)
    cid = _incerto(bus, "session.connect", key_prefix("android-01", "app.session", "instagram", "session.connect"))
    r = s.reconcile(sessao(), ALVO)
    assert [c.command_id for c in r.settled] == [cid]


# =============================================================================================== o despacho de verdade
async def test_apply_de_device_state_pelo_despacho_e_idempotente(harness: Harness) -> None:
    """`start` pelo `pedir_ciclo_de_vida` (o caminho do rodízio, com o `LocalWorker`), e a segunda passada síncrona
    — antes de qualquer `await` — reencontra o mesmo comando em vez de abrir outro."""
    s = harness.state
    assert s is not None
    rt = s.devices.devices["android-01"]
    await s.devices.stop_instance(rt)                   # o harness sobe com os aparelhos no ar
    assert rt.state in (InstanceState.stopped, InstanceState.absent), rt.state
    conv = ResourceConvergence(resource_providers(s.db, s.devices.devices, session_max_age_s=3600,
                                                  unknown_retry_cap=3, bus=command_bus(s)))
    antes = _contar(s.db)
    primeiro = conv.apply_next([device()], ALVO, run_ref=RunRef(run_id="r-teste"))
    assert primeiro.command is not None and primeiro.command.requested, primeiro.detail
    segundo = conv.apply_next([device()], ALVO)
    assert segundo.command is not None and segundo.command.command_id == primeiro.command.command_id
    assert segundo.command.deduplicated
    assert _contar(s.db) == antes + 1
    linha = s.commands.get(primeiro.command.command_id)
    assert (linha["verb"], linha["requested_by"]) == ("start", REQUESTED_BY)
    assert linha["idempotency_key"] == "res:android-01:device.state:-:start#1"

    cid = primeiro.command.command_id
    await harness.wait(lambda: s.commands.get(cid)["state"] not in {c.value for c in COMMAND_OPEN},
                       what="o start fechar")
    assert s.commands.get(cid)["state"] == CommandState.succeeded.value
    # No ar (subindo a escada ou pronto): a terceira passada não tem o que pedir.
    terceiro = conv.apply_next([device()], ALVO)
    assert terceiro.command is None and _contar(s.db) == antes + 1


async def test_canal_real_recusa_antes_de_gravar(harness: Harness) -> None:
    """Ocupado ou fora do ar: o despacho gravaria um `rejected` a cada passada; o canal recusa sem gravar nada."""
    s = harness.state
    assert s is not None
    bus = DespachoCommandBus(s)
    rt = s.devices.devices["android-01"]
    rt.state = InstanceState.stopped                    # o harness sobe com os aparelhos no ar
    antes = _contar(s.db)
    fora = bus.request("android-01", "app.verify", {"package": "com.instagram.android"}, requested_by=REQUESTED_BY,
                       run_ref=None, idempotency_key="res:android-01:app.installation:instagram:app.verify#1",
                       reason="teste")
    assert not fora.requested and "online" in (fora.reason or "")
    rt.state = InstanceState.online
    # Um comando do painel ainda em voo no aparelho: "um aparelho, uma operação".
    s.commands.create(command_id="c-do-painel", instance_id="android-01", verb="app.install",
                      idempotency_key="painel-0001")
    ocupado = bus.request("android-01", "app.verify", {"package": "com.instagram.android"},
                          requested_by=REQUESTED_BY, run_ref=None,
                          idempotency_key="res:android-01:app.installation:instagram:app.verify#1", reason="teste")
    assert not ocupado.requested and "c-do-painel" in (ocupado.reason or "")
    assert _contar(s.db) == antes + 1                                # só o do painel
    s.commands.transition("c-do-painel", CommandState.failed, reason="fim do teste")


async def test_app_verify_pelo_despacho_de_verdade(harness: Harness) -> None:
    """`app.verify` vira o comando da rota de verificação (`ReleaseService.verify_on`), com o pacote e a trilha da
    execução nos parâmetros; a segunda passada, com ele em voo, não abre outro."""
    s = harness.state
    assert s is not None
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-01'")
    s.release_repo.upsert_app_state("android-01", "com.instagram.android", state="verifying", pending_op=None)
    rt = s.devices.devices["android-01"]
    rt.state = InstanceState.online
    liberar = __import__("asyncio").Event()
    lidos: list[str] = []

    async def reler(rt_: Any, package: str, installer: Any) -> dict[str, Any]:
        lidos.append(package)
        await liberar.wait()
        return {"ok": True}

    s.releases.verify_on = reler  # type: ignore[assignment]
    conv = ResourceConvergence(resource_providers(s.db, s.devices.devices, session_max_age_s=3600,
                                                  unknown_retry_cap=3, bus=command_bus(s)))
    antes = _contar(s.db)
    passo = conv.apply_next([instalacao("wait")], ALVO, run_ref=RunRef(run_id="r-9", objective_id="o-9"))
    assert passo.line is not None and passo.line.drift.ref.kind.value == "app.installation"
    assert passo.command is not None and passo.command.requested, passo.detail
    linha = s.commands.get(passo.command.command_id)
    assert linha["verb"] == "app.verify" and '"run_id":"r-9"' in (linha["params"] or "")
    de_novo = conv.apply_next([instalacao("wait")], ALVO)
    assert de_novo.command is not None and de_novo.command.command_id == passo.command.command_id
    assert _contar(s.db) == antes + 1
    liberar.set()
    cid = passo.command.command_id
    await harness.wait(lambda: s.commands.get(cid)["state"] == CommandState.succeeded.value,
                       what="a releitura fechar o comando")
    assert lidos == ["com.instagram.android"]


async def test_reconcile_pelo_despacho_so_no_hospedeiro_e_com_prova(harness: Harness) -> None:
    """Um `start` incerto: aparelho parado não prova nada; de outro backend, nem se olha; no ar e pronto, fecha."""
    s = harness.state
    assert s is not None
    chave = key_prefix("android-02", "device.state", None, "start") + "#1"
    linha, _ = s.commands.create(command_id="c-incerto-1", instance_id="android-02", verb="start",
                                 idempotency_key=chave, requested_by=REQUESTED_BY)
    s.commands.transition("c-incerto-1", CommandState.dispatched)
    s.commands.transition("c-incerto-1", CommandState.uncertain, reason="prazo de boot estourado")
    rt = s.devices.devices["android-02"]
    p = DeviceStateProvider(s.db, s.devices.devices, bus=command_bus(s))
    alvo = Target("android-02")

    # aplicar de novo não repete o incerto
    rt.state, rt.readiness_phase = InstanceState.stopped, "not_running"
    antes = _contar(s.db)
    repetido = p.apply(_uma_acao(p, device(), alvo), spec=device())
    assert repetido.command_id == "c-incerto-1" and repetido.deduplicated and _contar(s.db) == antes

    r = p.reconcile(device(), alvo)
    assert r.hosted and [c.command_id for c in r.still_uncertain] == ["c-incerto-1"]

    rt.state, rt.readiness_phase = InstanceState.online, "ready"
    s.db.execute("UPDATE instances SET hosted_by='outro-backend' WHERE id='android-02'")
    r = p.reconcile(device(), alvo)
    assert not r.hosted and s.commands.get("c-incerto-1")["state"] == CommandState.uncertain.value

    s.db.execute("UPDATE instances SET hosted_by=NULL WHERE id='android-02'")
    r = p.reconcile(device(), alvo)
    assert [c.command_id for c in r.settled] == ["c-incerto-1"]
    fechado = s.commands.get("c-incerto-1")
    assert fechado["state"] == CommandState.succeeded.value and "verificado pelo estado real" in fechado["reason"]
