"""A costura entre o gerenciador de aparelhos e a MÁQUINA (achado #165).

O problema que isto resolve: o ciclo de vida do emulador — guarda de RAM, subida do processo, `emu kill`,
snapshot/hibernação — só rodava com emulador de verdade, e o dublê dos testes entrava por `if self.io_factory
is not None` **dentro** do código de produção. Com a costura no lugar errado, o que a suíte provava era a
regra escrita no teste, não o caminho que roda em campo: o teste de recusa por capacidade chegava a escrever
ele mesmo a frase da recusa (`fake_boot_refusal`) e a comparar com ela.

Aqui a costura passa para a BORDA. `RealEmulatorBackend` é a máquina; `FakeEmulatorBackend` é um valor que o
teste escolhe. A decisão — quanta memória é preciso ter livre, o que dizer quando não há — continua uma só, no
gerenciador, e passa a ser exercitada pelos dois lados.

Esta passada cobre a memória livre e as chamadas de processo/snapshot. O boot em si (`_wait_boot`, veredito do
snapshot, adoção) segue com desvio por `io_factory` no gerenciador; está listado no achado como o que falta.

T.2 estendeu a costura a `stop_instance`: o desvio por `io_factory` que existia ali (linha antiga: aparelho
falso "hiberna" ou "desliga" sem passar pela decisão real) saiu, e o que decide se o snapshot foi salvo agora é
`save_snapshot` — real chama o console do emulador (`Adb.snapshot_save`), falso obedece `snapshot_ok`. O
aparelho falso passa a ganhar PID pela mesma `_spawn` do caminho real (ao FIM do "boot" simulado, não durante —
`test_cancelar_boot_local_interrompe_a_tarefa...` depende de `rt.pid is None` enquanto o boot está em
andamento), o que é o que torna a elegibilidade de hibernação (`rt.pid is not None`) exercitável por teste.
`_wait_boot` (veredito do snapshot durante o boot, adoção) segue de fora; ver `worker/executor.py` para a mesma
lacuna no agente remoto.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol

import psutil

from . import emulator as emu
from .adb import AdbError


class EmulatorBackend(Protocol):
    """O que o gerenciador precisa da máquina — e nada além disso."""

    def free_ram_mb(self) -> float:
        """Memória disponível AGORA, em MB. É a entrada da guarda de capacidade."""

    def start_process(self, cfg: Any, tools: Any, avd_name: str, console_port: int, android: Any, *,
                      wipe_data: bool, from_snapshot: bool) -> int:
        """Sobe o processo do emulador e devolve o PID."""

    def stop_process(self, adb: Any, pid: int | None, avd_name: str) -> str:
        """Encerra o processo. Devolve a frase de como ele foi encerrado."""

    def discard_snapshot(self, snapshot_dir: Path) -> None:
        """Apaga o snapshot daquele AVD. Nunca falha por ausência."""

    def save_snapshot(self, adb: Any, name: str) -> None:
        """Pede ao console do emulador para salvar o snapshot. Levanta `AdbError` se não confirmar."""

    def process_alive(self, pid: int | None, avd_name: str) -> bool:
        """O PID gravado ainda É o emulador deste AVD (PIDs são reciclados pelo SO)."""


class RealEmulatorBackend:
    """A máquina de verdade. Cada método é a chamada que já estava no gerenciador, sem nada a mais."""

    def free_ram_mb(self) -> float:
        return psutil.virtual_memory().available / 2**20

    def start_process(self, cfg: Any, tools: Any, avd_name: str, console_port: int, android: Any, *,
                      wipe_data: bool, from_snapshot: bool) -> int:
        return emu.start_process(cfg, tools, avd_name, console_port, android, wipe_data=wipe_data,
                                 from_snapshot=from_snapshot)

    def stop_process(self, adb: Any, pid: int | None, avd_name: str) -> str:
        return emu.stop_process(adb, pid, avd_name)

    def discard_snapshot(self, snapshot_dir: Path) -> None:
        shutil.rmtree(snapshot_dir, ignore_errors=True)

    def save_snapshot(self, adb: Any, name: str) -> None:
        adb.snapshot_save(name)

    def process_alive(self, pid: int | None, avd_name: str) -> bool:
        return emu.is_our_emulator(pid, avd_name)


class FakeEmulatorBackend:
    """A máquina que o teste escolhe.

    `free_mb` é generoso por padrão **de propósito**: sem isso, a suíte passaria a depender da memória livre
    desta máquina — que roda emuladores — e a guarda de capacidade recusaria boots do aparelho falso de forma
    intermitente. Quem quer provar a recusa baixa o número; quem não fala de RAM não paga por ela.
    """

    def __init__(self, free_mb: float = 64_000.0) -> None:
        self.free_mb = free_mb
        self.started: list[tuple[str, bool, bool]] = []     # (avd, wipe_data, from_snapshot)
        self.stopped: list[str] = []
        self.discarded: list[Path] = []
        self.saved: list[str] = []
        # T.2: o teste liga/desliga a confirmação do console (antes vivia em `DeviceManager.fake_snapshot_ok`,
        # dentro do desvio de `stop_instance` que este achado removeu).
        self.snapshot_ok = True
        self.next_pid = 40_000
        # T.2: o monitor (`_monitor_loop`) confere `process_alive` de todo aparelho ONLINE a cada volta; sem um
        # valor aqui, todo PID que o dublê inventa pareceria "morto" para o SO de verdade, e o monitor declararia
        # perda em massa. Por padrão nada morre sozinho; quem quer provar a queda soma o AVD aqui.
        self.dead: set[str] = set()

    def free_ram_mb(self) -> float:
        return self.free_mb

    def start_process(self, cfg: Any, tools: Any, avd_name: str, console_port: int, android: Any, *,
                      wipe_data: bool, from_snapshot: bool) -> int:
        self.started.append((avd_name, wipe_data, from_snapshot))
        self.next_pid += 1
        return self.next_pid

    def stop_process(self, adb: Any, pid: int | None, avd_name: str) -> str:
        self.stopped.append(avd_name)
        return "encerrado (dublê)"

    def discard_snapshot(self, snapshot_dir: Path) -> None:
        self.discarded.append(snapshot_dir)

    def save_snapshot(self, adb: Any, name: str) -> None:
        if not self.snapshot_ok:
            raise AdbError("snapshot save falhou: console não confirmou (dublê)")
        self.saved.append(name)

    def process_alive(self, pid: int | None, avd_name: str) -> bool:
        return bool(pid) and avd_name not in self.dead
