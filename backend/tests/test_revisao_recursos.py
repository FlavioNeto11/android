"""Revisão independente F8 da frente F5 (recursos do worker, reserva de RAM, admissão).

Cada teste prova um cenário do roteiro de revisão. Os que FALHAM por provar um defeito ficam marcados com
`xfail(strict=True)`: o arquivo roda verde com o defeito documentado, e quem corrigir o defeito vê o XPASS virar
vermelho e tira a marca. Nada aqui sobe emulador, fala `adb`, lê `/proc` ou cgroup desta máquina, ou toca rede fora
do loopback: a memória é sempre injetada (`medir_recursos`, leitor de arquivo falso), o emulador é o falso da suíte
do executor e o central é o `CentralFalso` da suíte do agente.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.devices import recursos
from app.devices.recursos import RecursosEfetivos, medir
from app.metricas import metricas
from app.worker import executor as executor_mod
from app.worker.agent import Agent
from app.worker.executor import ReservasDeRam, VerbRefused
from app.workers.protocol import Heartbeat, WorkerResources
from app.workers.registry import BATIDA_VELHA_S, PISO_RAM_MB, WorkerCapacity, ram_efetiva_mb
from app.util import now, to_iso

from .test_worker_agent import CentralFalso, _dispatch, _encerrar, _esperar, _settings
from .test_worker_executor import AdbFalso, _ate, _estado_falso, _executor, _memoria, _sem_emulador
from .test_workers import _hello, _registro

MB = 1024 * 1024
WORKER = "worker-lan-01"


def _cap(**kw: Any) -> WorkerCapacity:
    base: dict[str, Any] = dict(connected=True, maintenance=False, max_slots=6, ram_free_mb=46000,
                                disk_free_gb=400.0, last_seen_at="2026-09-26T12:00:00Z", stale=False,
                                degraded_detail=None, idade_s=3.0)
    return WorkerCapacity(WORKER, "Notebook da LAN", **{**base, **kw})


# ================================================================ DEFEITOS (xfail estrito)
@pytest.mark.xfail(strict=True, reason="defeito F8: com o limite do cgroup legível e o uso (`memory.current`) "
                                       "ilegível, `medir` declara o LIMITE inteiro como disponível — uso "
                                       "desconhecido vira uso zero (C6: null é desconhecido)")
def test_f8_limite_legivel_com_uso_ilegivel_nao_vira_folga_inteira() -> None:
    """Contêiner com `memory.max` de 16 GB num host de 64 GB (60 GB livres) e `memory.current` que não pôde ser
    lido. O uso dentro do limite pode ser 0 ou 15,9 GB: a folga é DESCONHECIDA. `medir` devolve 16384, a guarda
    do boot faz 16384 − 0 − 2700 ≥ 4096 e admite — o OOM do cgroup mata o emulador se o uso real já for alto."""
    unidade = "/sys/fs/cgroup/system.slice/farm-worker.service"
    rec = medir(ler={"/proc/self/cgroup": "0::/system.slice/farm-worker.service\n",
                     f"{unidade}/memory.max": str(16384 * MB)}.get,
                linux=True, memoria=lambda: SimpleNamespace(total=65536 * MB, available=60000 * MB),
                swap=lambda: SimpleNamespace(total=0, percent=0.0), cpus=lambda: 8, agora=lambda: "t")
    assert rec.mem_limit_mb == 16384
    assert rec.mem_available_mb is None, (f"uso ilegível e a folga saiu {rec.mem_available_mb} MB: o limite foi "
                                          "tratado como folga inteira")


def _pids_vivos(ex: Any, subidos: list[str], ja_no_ar: tuple[str, ...] = ()) -> None:
    """`pid_do_avd` falso: um emulador que SUBIU continua no ar até alguém o parar — e nesta suíte ninguém para."""
    def pid(avd_name: str) -> int | None:
        return 7000 + len(avd_name) if (avd_name in subidos or avd_name in ja_no_ar) else None

    ex.pid_do_avd = pid


@pytest.mark.xfail(strict=True, reason="defeito F8: `start` cancelado no meio do boot solta a reserva com o "
                                       "emulador ainda no ar (ninguém o para; o desfecho é `uncertain`), e o "
                                       "próximo boot gasta a mesma RAM")
async def test_f8_cancelar_o_boot_nao_libera_a_ram_de_um_emulador_que_segue_no_ar(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RAM para UM boot (custo + mínimo + 500 MB). O `start` de A sobe o emulador e é cancelado na espera do boot.
    O agente responde `uncertain` ("o emulador já tinha sido iniciado") e NÃO derruba o processo: A continua
    subindo e alocando. A premissa da própria reserva ("o emulador admitido ainda não alocou nada") vale igual
    aqui — mas o `finally` já soltou a reserva, e o `start` de B lê a mesma memória e é admitido."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, quantos=2, boot_parallelism=1, max_slots=2, min_free_ram_mb=4096)
    a, b = ex.settings.devices
    custo = ex._custo_de_ram(a)
    _memoria(ex, custo + 4096 + 500)
    subidos = _sem_emulador(monkeypatch)
    _pids_vivos(ex, subidos)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    preso = AdbFalso(pronto_depois_de=1)
    preso.liberar = threading.Event()
    adbs = {a.serial: preso, b.serial: AdbFalso(pronto_depois_de=1)}
    monkeypatch.setattr(ex, "adb_for", lambda spec: adbs[spec.serial])

    tarefa = asyncio.create_task(ex.run("start", a, {"boot_timeout_s": 5}))
    try:
        await _ate(lambda: ex.reservas.total_mb() > 0 and preso.sondagens > 0, "o boot de A começar")
        tarefa.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarefa
    finally:
        preso.liberar.set()
    assert ex.pid_do_avd(a.avd_name) is not None, "premissa: o emulador de A segue no ar depois do cancelamento"

    with pytest.raises(VerbRefused):
        await ex.run("start", b, {"boot_timeout_s": 5})
    assert subidos == [a.avd_name], "B subiu na RAM que o emulador de A (vivo) vai ocupar"


@pytest.mark.xfail(strict=True, reason="defeito F8 (preexistente a 1104d50): `_guarda_de_vagas` conta PROCESSO "
                                       "numa thread e não conta boots admitidos que ainda não criaram o processo; "
                                       "com boot_parallelism>1 dois `start` passam de `max_slots`")
async def test_f8_guarda_de_vagas_conta_boot_admitido_que_ainda_nao_subiu(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`max_slots=2`, um emulador já no ar, RAM de sobra, `boot_parallelism=2`. Os dois `start` passam pela guarda
    de vagas dentro da fila antes de qualquer um chegar a `start_process` (a janela é `apply_hardware`, em outra
    thread). O livro de reservas saberia que há um boot em voo, mas a guarda de vagas não o consulta."""
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, quantos=3, boot_parallelism=2, max_slots=2, min_free_ram_mb=1024)
    no_ar, x, y = ex.settings.devices
    _memoria(ex, 64_000)
    subidos = _sem_emulador(monkeypatch)
    _pids_vivos(ex, subidos, ja_no_ar=(no_ar.avd_name,))
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex.avd, "apply_hardware", lambda *_a, **_k: time.sleep(0.3))
    adbs = {d.serial: AdbFalso(pronto_depois_de=1) for d in ex.settings.devices}
    monkeypatch.setattr(ex, "adb_for", lambda spec: adbs[spec.serial])

    saidas = await asyncio.wait_for(asyncio.gather(ex.run("start", x, {"boot_timeout_s": 5}),
                                                   ex.run("start", y, {"boot_timeout_s": 5}),
                                                   return_exceptions=True), timeout=15)
    recusas = [s for s in saidas if isinstance(s, VerbRefused)]
    assert len(recusas) == 1 and len(subidos) == 1, (
        f"com max_slots=2 e um já no ar, subiram {subidos} além de {no_ar.avd_name}")


@pytest.mark.xfail(strict=True, reason="defeito F8: com `mem_available_mb` nulo, a admissão do central cai na RAM "
                                       "do HOST e ignora um `mem_limit_mb` conhecido e menor")
def test_f8_limite_conhecido_com_disponivel_nulo_nao_admite_pela_ram_do_host() -> None:
    """Batida com limite de cgroup de 1500 MB e disponível desconhecido. O teto é conhecido e está abaixo do piso de
    2048 MB, mas `ram_para_boot_mb` usa `ram_free_mb` (46 GB do host) e admite. O agente desta onda não produz esse
    formato (`medir` só deixa `mem_available_mb` nulo quando o host também é nulo); o contrato C6, sim."""
    res = WorkerResources(ram_free_mb=46000, mem_available_mb=None, mem_limit_mb=1500)
    efetiva = ram_efetiva_mb(res)
    assert efetiva is None or efetiva <= 1500, f"RAM efetiva {efetiva} MB acima do limite conhecido de 1500 MB"
    assert _cap(ram_free_mb=46000, mem_available_mb=None, mem_limit_mb=1500).sem_recurso() is not None


# ---------------------------------------------------------------- agente de ponta a ponta (cenário 8 e métrica)
def _recursos_sem_medida() -> RecursosEfetivos:
    return RecursosEfetivos(ram_total_mb=None, ram_free_mb=None, mem_limit_mb=None, mem_available_mb=None,
                            cpu_count=None, cpu_effective=None, swap_used_pct=None, mem_pressure=None,
                            measured_at="2026-09-26T12:00:00.000Z")


async def _start_sem_medicao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CentralFalso, list[str]]:
    """Agente real, executor real, central falso: um `start` numa máquina cuja RAM não pôde ser medida."""
    metricas.limpar()
    subidos = _sem_emulador(monkeypatch)
    async with CentralFalso(heartbeat_s=0.05) as central:
        agente = Agent(_settings(tmp_path, central.url), enrollment="token-de-inscricao-de-teste")
        ex = agente.executor
        ex.medir_recursos = _recursos_sem_medida                       # type: ignore[assignment]
        monkeypatch.setattr(ex, "estado", lambda _spec: ("stopped", None))
        monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
        ex.pid_do_avd = lambda _avd: None                              # type: ignore[assignment]
        tarefa = asyncio.create_task(agente.run_forever())
        await asyncio.wait_for(central.conectado.wait(), timeout=5)
        await central.enviar(_dispatch())
        await _esperar(lambda: central.de_tipo("result"), "o desfecho chegar")
        n = len(central.de_tipo("heartbeat"))
        await _esperar(lambda: len(central.de_tipo("heartbeat")) > n, "uma batida depois do desfecho")
        await _encerrar(tarefa)
    return central, subidos


async def test_f8_medicao_ausente_nunca_vira_boot_bem_sucedido(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Cenário 8: RAM não medida → a guarda recusa → o desfecho no fio é `failed` com o motivo, nunca
    `succeeded`, e nenhum emulador sobe. A batida declara a RAM como nula (desconhecida), não como zero."""
    central, subidos = await _start_sem_medicao(tmp_path, monkeypatch)
    resultado = central.de_tipo("result")[0]
    assert resultado["outcome"] == "failed", resultado
    assert "não pôde ser medida" in (resultado.get("reason") or "")
    assert subidos == []
    batida = central.de_tipo("heartbeat")[-1]["resources"]
    assert batida["mem_available_mb"] is None and batida["ram_free_mb"] is None and batida["reserved_mb"] == 0


@pytest.mark.xfail(strict=True, reason="defeito F8: `capacidade.reserva` é contada só na memória do processo do "
                                       "AGENTE; nada a leva ao central (nem batida, nem mensagem), e só o central "
                                       "grava janela e serve /api/desempenho — a métrica reservada nunca aparece")
async def test_f8_metrica_de_reserva_chega_ao_central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Na suíte, agente e central dividem o mesmo `metricas` do processo, e `metricas.valor(...)` passa — em
    produção são dois processos em duas máquinas. A prova tem de ser pelo FIO: algo que o agente mandou precisa
    carregar a contagem da recusa."""
    central, _ = await _start_sem_medicao(tmp_path, monkeypatch)
    assert metricas.valor("capacidade.reserva", resultado="recusada", motivo="desconhecido") == 1, \
        "premissa: a recusa foi contada no processo do agente"
    fio = [json.dumps(m, ensure_ascii=False) for m in central.recebidos]
    assert any("capacidade.reserva" in m for m in fio), "nenhuma mensagem do agente levou a métrica ao central"


# ================================================================ COBERTURA (passam: provam o cenário)
def test_batida_velha_recusa_e_a_batida_fresca_volta_a_admitir_sem_flag_grudada(tmp_path: Path) -> None:
    """Cenário 2, pela porta de verdade (`WorkerRegistry.capacidade`): o limiar é `BATIDA_VELHA_S`, recalculado a
    cada leitura a partir de `last_seen_at`; a batida seguinte reabre a admissão."""
    reg = _registro(tmp_path)
    reg.autenticar(_hello(), token=None, enrollment=reg.criar_inscricao())
    reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(ram_free_mb=46367, disk_free_gb=400.0)))
    assert reg.capacidade(WORKER).sem_recurso() is None

    def envelhecer(segundos: float) -> None:
        reg.db.execute("UPDATE workers SET last_seen_at=? WHERE id=?",
                       (to_iso(now() - timedelta(seconds=segundos)), WORKER))

    envelhecer(BATIDA_VELHA_S - 5)
    assert reg.capacidade(WORKER).stale is False and reg.capacidade(WORKER).sem_recurso() is None
    envelhecer(BATIDA_VELHA_S + 5)
    velha = reg.capacidade(WORKER)
    motivo = velha.sem_recurso()
    assert velha.stale is True and motivo is not None and "sem medição recente" in motivo
    reg.on_heartbeat(WORKER, Heartbeat(resources=WorkerResources(ram_free_mb=46367, disk_free_gb=400.0)))
    fresca = reg.capacidade(WORKER)
    assert fresca.stale is False and fresca.sem_recurso() is None, "a recusa por batida velha ficou grudada"


def test_reserva_liberada_duas_vezes_nao_fica_negativa_nem_leva_a_do_vizinho() -> None:
    """Cenário 4: liberar é idempotente e por OBJETO — duas reservas do mesmo aparelho não se confundem."""
    livro = ReservasDeRam()
    r1 = livro.tomar("android-01", 2700)
    r2 = livro.tomar("android-01", 2700)
    livro.liberar(r1)
    livro.liberar(r1)
    livro.liberar(None)
    assert livro.total_mb() == 2700 and len(livro) == 1
    livro.liberar(r2)
    livro.liberar(r2)
    assert livro.total_mb() == 0 and len(livro) == 0


async def test_zero_medido_e_recusa_por_ram_e_nao_por_desconhecido(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Cenário 6: 0 MB é um número (recusa por `ram`), nulo é desconhecido (recusa por `desconhecido`) — nos dois
    lados. Nenhum dos dois vira "cabe"."""
    metricas.limpar()
    ex = _executor(tmp_path)
    subidos = _sem_emulador(monkeypatch)
    _pids_vivos(ex, subidos)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    _memoria(ex, 0)
    with pytest.raises(VerbRefused) as saida:
        await ex.run("start", ex.settings.devices[0], {})
    assert "RAM insuficiente" in str(saida.value) and subidos == [] and ex.reservas.total_mb() == 0
    assert metricas.valor("capacidade.reserva", resultado="recusada", motivo="ram") == 1

    assert _cap(ram_free_mb=46000, mem_available_mb=0).sem_recurso() is not None
    assert _cap(ram_free_mb=0, mem_available_mb=None).sem_recurso() is not None
    nada = _cap(ram_free_mb=None, mem_available_mb=None, reserved_mb=0).sem_recurso()
    assert nada is not None and "não informou RAM" in nada
    # `reserved_mb` presente com `mem_available_mb` nulo: desconta da RAM do host, não é ignorado.
    assert _cap(ram_free_mb=4000, mem_available_mb=None, reserved_mb=2700).sem_recurso() is not None


def test_obs_reserved_mb_nulo_desconta_zero_decisao_documentada() -> None:
    """Observação (não defeito provado): `reserved_mb` nulo — agente antigo, que não reserva — desconta ZERO. É
    decisão escrita em `ram_para_boot_mb`, e contraria a letra de C6 ("null é desconhecido") e o comentário de
    `FEATURE_RESERVA_DE_BOOT` ("`reserved_mb` nulo de quem não anuncia é 'não se sabe'"). Fica fixado aqui."""
    cap = _cap(ram_free_mb=PISO_RAM_MB + 100, mem_available_mb=None, reserved_mb=None)
    assert cap.ram_para_boot_mb() == PISO_RAM_MB + 100 and cap.sem_recurso() is None


async def test_metrica_de_reserva_so_tem_rotulos_de_conjunto_pequeno(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Cenário 7: concedida, recusada por RAM e recusada por medição ausente — e nenhum rótulo com id de aparelho,
    de worker ou número livre."""
    metricas.limpar()
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path, quantos=3, min_free_ram_mb=1024, max_slots=3)
    _pids_vivos(ex, _sem_emulador(monkeypatch))
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: AdbFalso(pronto_depois_de=1))
    ok, sem_ram, sem_medida = ex.settings.devices
    _memoria(ex, 64_000)
    await ex.run("start", ok, {"boot_timeout_s": 5})
    _memoria(ex, 100)
    with pytest.raises(VerbRefused):
        await ex.run("start", sem_ram, {})
    _memoria(ex, None)
    with pytest.raises(VerbRefused):
        await ex.run("start", sem_medida, {})

    series = [c["rotulos"] for c in metricas.snapshot()["contadores"] if c["nome"] == "capacidade.reserva"]
    assert {tuple(sorted(r.items())) for r in series} == {
        (("resultado", "concedida"),),
        (("motivo", "ram"), ("resultado", "recusada")),
        (("motivo", "desconhecido"), ("resultado", "recusada")),
    }
    ids = {d.instance_id for d in ex.settings.devices} | {d.avd_name for d in ex.settings.devices} | {WORKER}
    assert not any(v in ids for r in series for v in r.values())


def test_cgroup_max_em_todo_nivel_nao_vira_numero_e_o_menor_limite_vale() -> None:
    """Cenário 5, casos que a suíte de `recursos` não junta: `max` com quebra de linha em todos os níveis e um
    limite no avô menor que o do pai — vale o menor, com a folga medida no nível dele."""
    base = "/sys/fs/cgroup"
    arquivos = {"/proc/self/cgroup": "0::/a/b\n",
                f"{base}/a/b/memory.max": "max\n", f"{base}/a/b/memory.current": str(500 * MB),
                f"{base}/a/memory.max": str(6000 * MB), f"{base}/a/memory.current": str(1000 * MB),
                f"{base}/memory.max": "max\n"}
    limite, folga = recursos.memoria_do_cgroup(arquivos.get, recursos.grupos_do_processo(arquivos.get), 16384)
    assert (limite, folga) == (6000, 5000)
    so_max = {k: ("max\n" if k.endswith("memory.max") else v) for k, v in arquivos.items()}
    assert recursos.memoria_do_cgroup(so_max.get, recursos.grupos_do_processo(so_max.get), 16384) == (None, None)
