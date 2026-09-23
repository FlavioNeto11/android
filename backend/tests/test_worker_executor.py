"""O executor do worker: os verbos que rodam NA máquina do agente (achados #158 e #37).

Zero testes até aqui. `grep app.worker` em `backend/tests` não achava `executor.py`: as 205 linhas que decidem se
um `start` esperou o boot, se o prazo virou `uncertain` e se os boots sobem um a um só tinham sido exercitadas à
mão, na LAN, e o único `start` remoto registrado terminou `uncertain` (c-20260921172322-6f7fdc, boot > 480 s).

O outro defeito que estes testes travam é o do achado #37: `adb.state()` é um subprocess com timeout de 8 s e era
chamado DIRETO no laço de eventos a cada sondagem do boot. Enquanto ele bloqueava, o agente não batia o coração,
não respondia ping e não tratava `Ack`/`Cancel`. Aqui isso vira asserção: a sondagem acontece fora da thread do
laço, e quem quebrar essa propriedade descobre no teste, não no parque.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.devices.adb import AdbError
from app.worker import executor as executor_mod
from app.worker.executor import VerbFailed, VerbRefused, VerbUncertain, WorkerExecutor
from app.worker.settings import DeviceSpec, WorkerSettings


class AdbFalso:
    """`adb` de mentira que anota em qual thread foi chamado — é essa a prova do achado #37."""

    def __init__(self, *, pronto_depois_de: int = 2, nunca_boota: bool = False) -> None:
        self.pronto_depois_de = pronto_depois_de
        self.nunca_boota = nunca_boota
        self.sondagens = 0
        self.threads: set[str] = set()
        self.preparou = False
        self.acertou_relogio = False
        self.snapshot_erro: str | None = None
        self.snapshots: list[str] = []
        self.liberar: threading.Event | None = None

    def _anotar(self) -> None:
        self.threads.add("principal" if threading.current_thread() is threading.main_thread() else "auxiliar")

    def state(self) -> str:
        self._anotar()
        self.sondagens += 1
        if self.liberar is not None:
            self.liberar.wait(5)
        if self.nunca_boota or self.sondagens < self.pronto_depois_de:
            return "offline"
        return "device"

    def boot_completed(self) -> bool:
        self._anotar()
        return not self.nunca_boota

    def prepare_for_automation(self) -> None:
        self.preparou = True

    def sync_clock(self) -> tuple[int, int]:
        self.acertou_relogio = True
        return (0, 0)

    def snapshot_save(self, nome: str) -> None:
        if self.snapshot_erro:
            raise AdbError(self.snapshot_erro)
        self.snapshots.append(nome)


def _executor(tmp_path: Path, *, quantos: int = 1, **kw: Any) -> WorkerExecutor:
    devices = [DeviceSpec(instance_id=f"android-{i + 1:02d}", avd_name=f"worker-{i + 1:02d}",
                          console_port=5554 + 2 * i) for i in range(quantos)]
    settings = WorkerSettings(worker_id="worker-lan-01", name="Notebook", work_dir=str(tmp_path / "farm"),
                              sdk_root=str(tmp_path / "sdk-que-nao-existe"),   # nenhum SDK real é lido aqui
                              devices=devices, **kw)
    return WorkerExecutor(settings, settings.to_config())


def _sem_guarda_de_ram(ex: WorkerExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "_guarda_de_ram", lambda _spec: None)


def _estado_falso(ex: WorkerExecutor, monkeypatch: pytest.MonkeyPatch, valor: str,
                  threads: set[str] | None = None) -> None:
    def estado(_spec: Any) -> tuple[str, str | None]:
        if threads is not None:
            threads.add("principal" if threading.current_thread() is threading.main_thread() else "auxiliar")
        return valor, None

    monkeypatch.setattr(ex, "estado", estado)


def _sem_emulador(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Substitui o `emulator.start_process`/`stop_process` reais. Nenhum processo é criado nesta suíte."""
    subidos: list[str] = []

    def start_process(_cfg: Any, _tools: Any, avd_name: str, *_a: Any, **_k: Any) -> int:
        subidos.append(avd_name)
        return 4000 + len(subidos)

    monkeypatch.setattr(executor_mod.emu, "start_process", start_process)
    monkeypatch.setattr(executor_mod.emu, "stop_process", lambda *_a, **_k: "processo encerrado")
    return subidos


# ---------------------------------------------------------------- start espera o boot
async def test_start_so_volta_depois_que_o_android_respondeu(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """O contrato que separa o caminho remoto do local de antes: `start` não é "mandei subir", é "subiu"."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path)
    subidos = _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    adb = AdbFalso(pronto_depois_de=3)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)

    saida = await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    assert saida["started"] is True and saida["pid"] == 4001
    assert subidos == ["worker-01"]
    assert adb.sondagens >= 3, "voltou antes de o Android responder"
    assert adb.preparou and adb.acertou_relogio


async def test_boot_que_nao_termina_vira_uncertain_e_nunca_falha(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """`uncertain` é o desfecho honesto: o emulador SUBIU, então não dá para dizer que nada aconteceu."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: AdbFalso(nunca_boota=True))

    with pytest.raises(VerbUncertain) as saida:
        await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 0.05})
    assert "não completou o boot" in str(saida.value)


async def test_a_sondagem_do_boot_sai_da_thread_do_laco_de_eventos(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado #37: `adb.state()` é subprocess com timeout de 8 s. Chamado no laço, ele travava o agente inteiro a
    cada sondagem — sem batida, sem ping, sem tratar `Ack`/`Cancel`. O mesmo vale para `estado()`, que varre
    processos com psutil."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    threads_do_estado: set[str] = set()
    _estado_falso(ex, monkeypatch, "stopped", threads_do_estado)
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    adb = AdbFalso(pronto_depois_de=2)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)

    await ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5})
    assert adb.threads == {"auxiliar"}, f"o adb foi chamado no laço de eventos: {adb.threads}"
    assert threads_do_estado == {"auxiliar"}, f"estado() foi chamado no laço de eventos: {threads_do_estado}"


async def test_boots_sobem_um_a_um_com_boot_parallelism_1(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Subir quatro emuladores juntos travou os quatro em ANR (medido em 19/09). O semáforo é o que impede isso —
    e ele nunca tinha sido exercitado."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, quantos=2, boot_parallelism=1)
    subidos = _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    # Os dois boots ficam presos até serem soltos, um de cada vez. Qual deles pega o semáforo primeiro não
    # importa — importa que o segundo não comece enquanto o primeiro não terminar.
    por_avd = {d.avd_name: AdbFalso(pronto_depois_de=1) for d in ex.settings.devices}
    for falso in por_avd.values():
        falso.liberar = threading.Event()
    por_serial = {d.serial: por_avd[d.avd_name] for d in ex.settings.devices}
    monkeypatch.setattr(ex, "adb_for", lambda spec: por_serial[spec.serial])

    tarefas = [asyncio.create_task(ex.run("start", d, {"boot_timeout_s": 5})) for d in ex.settings.devices]
    await asyncio.sleep(0.05)
    assert len(subidos) == 1, f"dois emuladores subiram ao mesmo tempo: {subidos}"
    outro = next(a for a in por_avd if a != subidos[0])
    assert por_avd[outro].sondagens == 0, "o segundo começou a sondar o boot antes da sua vez"
    por_avd[subidos[0]].liberar.set()                     # o primeiro termina e libera a vaga
    for _ in range(500):
        if len(subidos) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(subidos) == 2, "o segundo emulador não subiu depois que a vaga abriu"
    por_avd[outro].liberar.set()
    await asyncio.wait_for(asyncio.gather(*tarefas), timeout=10)
    assert sorted(subidos) == ["worker-01", "worker-02"]


# ---------------------------------------------------------------- recusas e desfechos negativos
async def test_guarda_de_ram_recusa_antes_de_subir_qualquer_coisa(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """`VerbRefused` é a promessa de que o aparelho ficou INTACTO: o central pode afirmar isso, então nada pode
    ter sido iniciado antes da recusa."""
    ex = _executor(tmp_path, ram_per_device_mb=4096, min_free_ram_mb=8192)
    # A máquina do teste não decide o desfecho: a memória livre é declarada aqui.
    monkeypatch.setattr(executor_mod.psutil, "virtual_memory",
                        lambda: SimpleNamespace(available=2048 * 1024 * 1024))
    subidos = _sem_emulador(monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)

    with pytest.raises(VerbRefused) as saida:
        await ex.run("start", ex.settings.devices[0], {})
    assert "RAM insuficiente" in str(saida.value)
    assert subidos == [], "recusou e mesmo assim subiu o emulador"


async def test_hibernar_sem_snapshot_vira_verb_failed_e_o_aparelho_desliga(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """A regra única do achado #155, na ponta do worker: era `succeeded` com `hibernated: False` no corpo, e o
    painel dizia "Hibernada" sobre um aparelho que vai bootar a frio. `VerbFailed` diz a verdade sem mentir na
    outra direção — o aparelho DESLIGOU, então também não é recusa."""
    ex = _executor(tmp_path, android={"hibernation": True})   # o verbo só existe onde a máquina hiberna
    _sem_emulador(monkeypatch)
    _estado_falso(ex, monkeypatch, "running")
    adb = AdbFalso()
    adb.snapshot_erro = "console do emulador não respondeu ao snapshot save"
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)

    with pytest.raises(VerbFailed) as saida:
        await ex.run("hibernate", ex.settings.devices[0], {})
    assert "o próximo boot será a frio" in str(saida.value)
    assert adb.snapshots == []


async def test_hibernar_com_snapshot_salvo_e_sucesso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ex = _executor(tmp_path, android={"hibernation": True})
    _sem_emulador(monkeypatch)
    _estado_falso(ex, monkeypatch, "running")
    adb = AdbFalso()
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)

    saida = await ex.run("hibernate", ex.settings.devices[0], {})
    assert saida["hibernated"] is True and adb.snapshots == [executor_mod.SNAPSHOT]


async def test_verbo_fora_do_catalogo_e_recusado(tmp_path: Path) -> None:
    ex = _executor(tmp_path)
    with pytest.raises(VerbRefused):
        await ex.run("install_apk", ex.settings.devices[0], {})


# ---------------------------------------------------------------- a fila de boot e a guarda de RAM
async def test_guarda_de_ram_e_reavaliada_depois_da_espera_na_fila(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """A guarda era avaliada ANTES de entrar na fila: N `start` simultâneos liam a mesma memória livre e passavam
    todos, embora só o primeiro tivesse a RAM que a conta prometia. Agora a pergunta é feita na vez de cada um —
    e quem chega à frente do emulador sem RAM é recusado sem ter tocado em nada."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, quantos=2, boot_parallelism=1, ram_per_device_mb=2048, min_free_ram_mb=4096)
    subidos = _sem_emulador(monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    # Sobra RAM enquanto ninguém subiu; depois do primeiro emulador, não sobra mais.
    monkeypatch.setattr(executor_mod.psutil, "virtual_memory",
                        lambda: SimpleNamespace(available=(16384 if not subidos else 4096) * 1024 * 1024))
    por_avd = {d.avd_name: AdbFalso(pronto_depois_de=1) for d in ex.settings.devices}
    primeiro = ex.settings.devices[0]
    por_avd[primeiro.avd_name].liberar = threading.Event()
    por_serial = {d.serial: por_avd[d.avd_name] for d in ex.settings.devices}
    monkeypatch.setattr(ex, "adb_for", lambda spec: por_serial[spec.serial])

    t1 = asyncio.create_task(ex.run("start", primeiro, {"boot_timeout_s": 5}))
    await asyncio.sleep(0.05)
    assert subidos == [primeiro.avd_name], "o primeiro não chegou a subir"
    t2 = asyncio.create_task(ex.run("start", ex.settings.devices[1], {"boot_timeout_s": 5}))
    await asyncio.sleep(0.05)                       # o segundo está na FILA, e a RAM acabou nesse meio-tempo
    por_avd[primeiro.avd_name].liberar.set()
    await asyncio.wait_for(t1, timeout=10)
    with pytest.raises(VerbRefused) as saida:
        await asyncio.wait_for(t2, timeout=10)
    assert "RAM insuficiente" in str(saida.value)
    assert subidos == [primeiro.avd_name], "o segundo subiu apesar de a RAM ter acabado"


async def test_a_espera_na_fila_de_boot_e_dita_em_progresso(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """O central dá 540 s ao `start` e contava a espera na fila como tempo de boot — o comando virava "incerto"
    sem nada ter falhado. O agente agora DIZ que está esperando, e o painel mostra."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    recados: list[str] = []
    ex = _executor(tmp_path, quantos=2, boot_parallelism=1)
    ex.progress = recados.append
    subidos = _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    por_avd = {d.avd_name: AdbFalso(pronto_depois_de=1) for d in ex.settings.devices}
    for falso in por_avd.values():
        falso.liberar = threading.Event()
    por_serial = {d.serial: por_avd[d.avd_name] for d in ex.settings.devices}
    monkeypatch.setattr(ex, "adb_for", lambda spec: por_serial[spec.serial])

    tarefas = [asyncio.create_task(ex.run("start", d, {"boot_timeout_s": 5})) for d in ex.settings.devices]
    await asyncio.sleep(0.05)
    assert any("fila de boot" in m for m in recados), recados
    for falso in por_avd.values():
        falso.liberar.set()
    await asyncio.wait_for(asyncio.gather(*tarefas), timeout=10)
    assert len(subidos) == 2


async def test_cancelar_na_fila_de_boot_nao_sobe_emulador_nenhum(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """O ponto de cancelamento que faltava (achado #23). A espera na fila é o trecho mais longo de um `start` em
    lote e é onde o aparelho ainda está INTACTO: cancelar ali tem de impedir o boot, não só abandonar a espera —
    e o efeito continua não marcado, que é o que autoriza o agente a responder `cancelled`."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, quantos=2, boot_parallelism=1)
    subidos = _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    por_avd = {d.avd_name: AdbFalso(pronto_depois_de=1) for d in ex.settings.devices}
    por_avd[ex.settings.devices[0].avd_name].liberar = threading.Event()
    por_serial = {d.serial: por_avd[d.avd_name] for d in ex.settings.devices}
    monkeypatch.setattr(ex, "adb_for", lambda spec: por_serial[spec.serial])

    efeito: list[str | None] = []

    async def start_na_fila(spec: Any) -> None:
        try:
            await ex.run("start", spec, {"boot_timeout_s": 5})
        finally:
            efeito.append(executor_mod.EFEITO_INICIADO.get())

    primeiro = asyncio.create_task(ex.run("start", ex.settings.devices[0], {"boot_timeout_s": 5}))
    await asyncio.sleep(0.05)
    segundo = asyncio.create_task(start_na_fila(ex.settings.devices[1]))
    await asyncio.sleep(0.05)
    assert subidos == [ex.settings.devices[0].avd_name], "o segundo subiu antes da vez dele"

    segundo.cancel()                                # o painel pediu o cancelamento enquanto ele esperava vaga
    with pytest.raises(asyncio.CancelledError):
        await segundo
    assert efeito == [None], "o efeito foi marcado sem o aparelho ter sido tocado"

    por_avd[ex.settings.devices[0].avd_name].liberar.set()
    await asyncio.wait_for(primeiro, timeout=10)
    assert subidos == [ex.settings.devices[0].avd_name], "o emulador cancelado subiu assim mesmo"


# ---------------------------------------------------------------- acordar é acordar, não ligar a frio
async def test_acordar_sem_hibernacao_na_maquina_e_recusado(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Com `hibernation: false` o emulador sobe com `-no-snapshot` (boot a frio de minutos) e o agente respondia
    `succeeded` com `from_snapshot: true`: sucesso com dado falso. Agora recusa, e nada é iniciado."""
    ex = _executor(tmp_path)
    subidos = _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)

    with pytest.raises(VerbRefused) as saida:
        await ex.run("wake", ex.settings.devices[0], {})
    assert "hibernação desligada" in str(saida.value) and subidos == []


async def test_acordar_sem_snapshot_salvo_e_recusado_mesmo_com_hibernacao_ligada(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ex = _executor(tmp_path, android={"hibernation": True})
    subidos = _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)

    with pytest.raises(VerbRefused) as saida:
        await ex.run("wake", ex.settings.devices[0], {})
    assert "não há snapshot salvo" in str(saida.value) and subidos == []


async def test_acordar_com_snapshot_salvo_sobe_do_snapshot_e_diz_a_verdade(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, android={"hibernation": True})
    spec = ex.settings.devices[0]
    (ex.cfg.avd_home / f"{spec.avd_name}.avd" / "snapshots" / executor_mod.SNAPSHOT).mkdir(parents=True)
    pedidos: list[bool] = []

    def start_process(_cfg: Any, _tools: Any, _avd: str, *_a: Any, from_snapshot: bool = False, **_k: Any) -> int:
        pedidos.append(from_snapshot)
        return 4321

    monkeypatch.setattr(executor_mod.emu, "start_process", start_process)
    monkeypatch.setattr(executor_mod.emu, "stop_process", lambda *_a, **_k: "processo encerrado")
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: AdbFalso(pronto_depois_de=1))

    saida = await ex.run("wake", spec, {"boot_timeout_s": 5})
    assert pedidos == [True] and saida["from_snapshot"] is True


async def test_start_nao_afirma_snapshot_que_a_maquina_nao_carrega(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """`from_snapshot` no resultado é afirmação sobre o que aconteceu, não eco do que foi pedido."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path)                       # hibernação desligada nesta máquina
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: AdbFalso(pronto_depois_de=1))

    saida = await ex.run("start", ex.settings.devices[0], {"from_snapshot": True, "boot_timeout_s": 5})
    assert saida["from_snapshot"] is False


# ---------------------------------------------------------------- vagas da máquina do agente
async def test_start_acima_de_max_slots_e_recusado_pelo_proprio_agente(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """`max_slots` era só declaração exibida no painel do central: o agente tinha guarda de RAM no `start` e
    nenhuma de vagas. A máquina tem de se proteger sozinha — o central decide ONDE ligar, mas quem sabe quantos
    emuladores já estão no ar nesta máquina é quem está nela."""
    ex = _executor(tmp_path, quantos=3, max_slots=2)
    subidos = _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    ligados = {d.avd_name for d in ex.settings.devices[:2]}
    monkeypatch.setattr(ex, "pid_do_avd", lambda avd: 4242 if avd in ligados else None)

    with pytest.raises(VerbRefused) as recusa:
        await ex.run("start", ex.settings.devices[2], {"boot_timeout_s": 5})
    assert "2 aparelho(s) ligado(s)" in str(recusa.value)
    assert subidos == [], "recusou e subiu assim mesmo"


async def test_com_vaga_livre_o_agente_nao_recusa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A outra metade da regra: a guarda conta os OUTROS aparelhos, nunca o que está sendo pedido — senão um
    worker de uma vaga só nunca ligaria nada."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, quantos=3, max_slots=2)
    subidos = _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    ligado = ex.settings.devices[0].avd_name
    monkeypatch.setattr(ex, "pid_do_avd", lambda avd: 4242 if avd == ligado else None)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: AdbFalso(pronto_depois_de=1))

    saida = await ex.run("start", ex.settings.devices[2], {"boot_timeout_s": 5})
    assert saida["started"] is True and subidos == [ex.settings.devices[2].avd_name]


async def test_a_vaga_e_conferida_DENTRO_da_fila_de_boot(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """A mesma lição da guarda de RAM: conferida só ANTES da fila, N `start` simultâneos leem todos "nenhum no
    ar" e passam todos — e a máquina sobe mais emuladores do que declarou aceitar."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, quantos=2, max_slots=1, boot_parallelism=1)
    ligados: set[str] = set()
    subidos: list[str] = []

    def start_process(_cfg: Any, _tools: Any, avd_name: str, *_a: Any, **_k: Any) -> int:
        subidos.append(avd_name)
        ligados.add(avd_name)                     # a partir daqui o processo existe nesta máquina
        return 4000 + len(subidos)

    monkeypatch.setattr(executor_mod.emu, "start_process", start_process)
    monkeypatch.setattr(executor_mod.emu, "stop_process", lambda *_a, **_k: "processo encerrado")
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "pid_do_avd", lambda avd: 4242 if avd in ligados else None)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: AdbFalso(pronto_depois_de=1))

    # Os dois pedidos chegam juntos, com a máquina vazia: a conferência de fora deixa os dois passarem.
    resultados = await asyncio.gather(*(ex.run("start", d, {"boot_timeout_s": 5}) for d in ex.settings.devices),
                                      return_exceptions=True)
    recusas = [r for r in resultados if isinstance(r, VerbRefused)]
    assert len(subidos) == 1, f"o worker de UMA vaga subiu {len(subidos)} emuladores: {subidos}"
    assert len(recusas) == 1 and "1 aparelho(s) ligado(s)" in str(recusas[0])


async def test_hibernar_com_hibernacao_desligada_e_recusado_sem_tocar_no_emulador(tmp_path: Path,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """O par de `_v_wake`: sem `android.hibernation`, "hibernar" desligaria SEM salvar snapshot e responderia
    sucesso — e o `wake` seguinte subiria a frio. O agente já não anuncia o verbo (`sem_hibernacao`); este guard
    é para o despacho que ainda chegar de um central antigo."""
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _estado_falso(ex, monkeypatch, "running")
    adb = AdbFalso()
    monkeypatch.setattr(ex, "adb_for", lambda _spec: adb)
    with pytest.raises(VerbRefused) as saida:
        await ex.run("hibernate", ex.settings.devices[0], {})
    assert "android.hibernation" in str(saida.value)
    assert adb.snapshots == []


async def test_start_reaplica_o_hardware_do_avd_existente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Antes, `hw.ramSize` só entrava ao CRIAR o AVD: mudar `ram_mb` no worker.yaml não mudava nada nos aparelhos
    existentes, e o android-12 seguiu com 1536 MB depois de a configuração pedir mais."""
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: AdbFalso(pronto_depois_de=1))
    aplicados: list[tuple[str, int]] = []
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex.avd, "apply_hardware", lambda nome, a: aplicados.append((nome, a.ram_efetiva())))
    await ex.run("start", ex.settings.devices[0], {})
    assert aplicados == [("worker-01", 2048)]           # o perfil da imagem, não 1536


def test_custo_de_ram_sem_numero_explicito_e_o_medido_do_perfil(tmp_path: Path) -> None:
    ex = _executor(tmp_path)                            # worker.yaml sem ram_mb
    assert ex._custo_de_ram(ex.settings.devices[0]) == 2700
    com_numero = _executor(tmp_path, android={"ram_mb": 1536})
    assert com_numero._custo_de_ram(com_numero.settings.devices[0]) == com_numero.settings.ram_per_device_mb
