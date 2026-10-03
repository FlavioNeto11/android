"""14.11: a CPU de cada emulador é medida de verdade, com `psutil.Process` que dura entre leituras.

Prova `simulated`: processo psutil falso (monkeypatch), com a mesma regra do real, a de que `cpu_percent(interval=None)`
de um objeto NOVO devolve 0,0 e só o objeto reaproveitado mede o intervalo. Nada aqui toca emulador ou processo real.
"""
from __future__ import annotations

import threading

import psutil
import pytest

from app.devices import emulator


class Mundo:
    """Os processos 'vivos' do SO falso: pid -> (create_time, CPU que gasta, pid do pai, RSS)."""

    def __init__(self) -> None:
        self.vivos: dict[int, dict[str, float | int]] = {}

    def nasce(self, pid: int, *, create_time: float, cpu: float, pai: int = 0, rss_mb: int = 100) -> None:
        self.vivos[pid] = {"ct": create_time, "cpu": cpu, "pai": pai, "rss": rss_mb * 1024 * 1024}

    def morre(self, pid: int) -> None:
        self.vivos.pop(pid, None)


class _Mem:
    def __init__(self, rss: int) -> None:
        self.rss = rss


@pytest.fixture
def mundo(monkeypatch: pytest.MonkeyPatch) -> Mundo:
    m = Mundo()

    class ProcFalso:
        def __init__(self, pid: int) -> None:
            if pid not in m.vivos:
                raise psutil.NoSuchProcess(pid)
            self.pid = pid
            self._ct = m.vivos[pid]["ct"]
            self._leu = False       # como o real: o 1º cpu_percent de um objeto não tem intervalo para medir

        def create_time(self) -> float:
            return float(self._ct)

        def _vivo(self) -> dict[str, float | int]:
            d = m.vivos.get(self.pid)
            if d is None or d["ct"] != self._ct:
                raise psutil.NoSuchProcess(self.pid)
            return d

        def children(self, recursive: bool = False) -> list["ProcFalso"]:
            self._vivo()
            return [ProcFalso(p) for p, d in sorted(m.vivos.items()) if d["pai"] == self.pid]

        def memory_info(self) -> _Mem:
            return _Mem(int(self._vivo()["rss"]))

        def cpu_percent(self, interval: float | None = None) -> float:
            d = self._vivo()
            if not self._leu:
                self._leu = True
                return 0.0
            return float(d["cpu"])

    monkeypatch.setattr(emulator.psutil, "Process", ProcFalso)
    return m


def test_segunda_leitura_devolve_cpu_real_e_o_objeto_e_o_mesmo(mundo: Mundo) -> None:
    mundo.nasce(10, create_time=1.0, cpu=5.0)
    mundo.nasce(11, create_time=1.1, cpu=110.0, pai=10)
    medidor = emulator.MedidorDeUso()

    primeira = medidor.ler(10)
    assert primeira is not None and primeira[1] == 0.0        # 1ª leitura de objeto novo: sem intervalo, 0,0
    launcher = medidor._por_pid[10].launcher
    qemu = medidor._por_pid[10].filhos[11]

    segunda = medidor.ler(10)
    assert segunda is not None and segunda[1] == 115.0        # lançador + qemu, o valor real
    assert segunda[0] == 200.0                                # RSS somado (100 MB cada)
    assert medidor._por_pid[10].launcher is launcher
    assert medidor._por_pid[10].filhos[11] is qemu            # o MESMO objeto, e não um novo por chamada
    assert medidor.ler(10) == (200.0, 115.0)


def test_process_usage_do_modulo_usa_o_medidor_unico(mundo: Mundo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emulator, "_MEDIDOR", emulator.MedidorDeUso())
    mundo.nasce(20, create_time=2.0, cpu=40.0)
    assert emulator.process_usage(20) == (100.0, 0.0)
    assert emulator.process_usage(20) == (100.0, 40.0)


def test_filho_novo_entra_e_filho_morto_sai(mundo: Mundo) -> None:
    mundo.nasce(10, create_time=1.0, cpu=1.0)
    mundo.nasce(11, create_time=1.1, cpu=100.0, pai=10)
    medidor = emulator.MedidorDeUso()
    medidor.ler(10)
    assert medidor.ler(10) == (200.0, 101.0)

    mundo.nasce(12, create_time=3.0, cpu=20.0, pai=10)
    novo = medidor.ler(10)
    assert novo is not None and novo[1] == 101.0              # o filho novo soma 0,0 na volta em que aparece
    assert set(medidor._por_pid[10].filhos) == {11, 12}
    assert medidor.ler(10) == (300.0, 121.0)                  # e na seguinte já conta o valor real

    mundo.morre(11)
    assert medidor.ler(10) == (200.0, 21.0)
    assert set(medidor._por_pid[10].filhos) == {12}


def test_filho_que_morre_entre_a_listagem_e_a_leitura_nao_derruba_a_medida(mundo: Mundo,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    mundo.nasce(10, create_time=1.0, cpu=1.0)
    mundo.nasce(11, create_time=1.1, cpu=100.0, pai=10)
    medidor = emulator.MedidorDeUso()
    medidor.ler(10)
    filho = medidor._por_pid[10].filhos[11]

    def morreu(interval: float | None = None) -> float:
        raise psutil.NoSuchProcess(11)

    monkeypatch.setattr(filho, "cpu_percent", morreu)
    resultado = medidor.ler(10)
    assert resultado is not None and resultado[1] == 1.0
    assert 11 not in medidor._por_pid[10].filhos


def test_pid_reutilizado_com_outro_create_time_gera_objeto_novo(mundo: Mundo) -> None:
    mundo.nasce(10, create_time=1.0, cpu=50.0)
    medidor = emulator.MedidorDeUso()
    medidor.ler(10)
    assert medidor.ler(10) == (100.0, 50.0)
    antigo = medidor._por_pid[10].launcher

    mundo.morre(10)
    mundo.nasce(10, create_time=9.0, cpu=80.0)               # o SO devolveu o número 10 a outro processo
    lido = medidor.ler(10)
    assert lido == (100.0, 0.0)                               # objeto novo: de novo sem intervalo, nada do dono anterior
    assert medidor._por_pid[10].launcher is not antigo
    assert medidor.ler(10) == (100.0, 80.0)


def test_filho_com_pid_reciclado_troca_de_objeto(mundo: Mundo) -> None:
    mundo.nasce(10, create_time=1.0, cpu=1.0)
    mundo.nasce(11, create_time=1.1, cpu=60.0, pai=10)
    medidor = emulator.MedidorDeUso()
    medidor.ler(10)
    medidor.ler(10)
    antigo = medidor._por_pid[10].filhos[11]
    mundo.morre(11)
    mundo.nasce(11, create_time=7.0, cpu=30.0, pai=10)
    medidor.ler(10)
    assert medidor._por_pid[10].filhos[11] is not antigo
    assert medidor.ler(10) == (200.0, 31.0)


def test_processo_que_morreu_devolve_none_e_sai_do_cache(mundo: Mundo) -> None:
    mundo.nasce(10, create_time=1.0, cpu=5.0)
    medidor = emulator.MedidorDeUso()
    medidor.ler(10)
    assert medidor.tamanho() == 1
    mundo.morre(10)
    assert medidor.ler(10) is None
    assert medidor.tamanho() == 0


def test_cache_nao_cresce_com_pid_que_morreu_nem_com_pid_que_ninguem_le_mais(mundo: Mundo,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    agora = [1000.0]
    monkeypatch.setattr(emulator.time, "monotonic", lambda: agora[0])
    medidor = emulator.MedidorDeUso(ttl_s=60.0)
    # 200 emuladores que ligam, são lidos uma vez e param (hibernam, trocam de pid) sem passar pelo caminho de erro
    for pid in range(100, 300):
        mundo.nasce(pid, create_time=float(pid), cpu=1.0)
        medidor.ler(pid)
        agora[0] += 1.0
        assert medidor.tamanho() <= 61                        # só o que foi lido dentro do TTL fica
    mundo.nasce(5, create_time=5.0, cpu=1.0)
    agora[0] += 120.0
    medidor.ler(5)
    assert medidor.tamanho() == 1                             # os 200 esquecidos foram purgados
    mundo.morre(5)
    assert medidor.ler(5) is None
    assert medidor.tamanho() == 0


def test_leituras_simultaneas_de_threads_do_pool_nao_corrompem_o_cache(mundo: Mundo) -> None:
    mundo.nasce(10, create_time=1.0, cpu=10.0)
    mundo.nasce(11, create_time=1.1, cpu=100.0, pai=10)
    medidor = emulator.MedidorDeUso()
    medidor.ler(10)
    erros: list[BaseException] = []

    def volta() -> None:
        try:
            for _ in range(200):
                assert medidor.ler(10) == (200.0, 110.0)
        except BaseException as e:  # noqa: BLE001 - o teste reporta qualquer falha da thread
            erros.append(e)

    ts = [threading.Thread(target=volta) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert erros == []
    assert medidor.tamanho() == 1
