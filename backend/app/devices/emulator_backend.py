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
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol

import psutil

from . import emulator as emu


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
        self.next_pid = 40_000

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
