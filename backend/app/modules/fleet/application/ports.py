"""Portas que o contexto do parque CONSOME (design §3: a porta pertence a quem consome).

O estado observado do aparelho vive em memória, no `DeviceRuntime` do gerenciador (`devices/manager.py`): a tabela
`instances` guarda o desejado (014), não o observado. Quem lê `device.state` precisa enxergar o runtime sem importar o
gerenciador — que é infraestrutura do legado, com adb, emulador e Appium atrás.

`DeviceManager.devices` (um `dict[str, DeviceRuntime]`) cumpre `Mapping[str, DeviceRuntimeView]` sem adaptador: os
membros são `@property`, e propriedade de `Protocol` aceita subtipo (`InstanceState` é `str`; `list[str]` é
`Sequence[str]`). O `bootstrap` liga as partes; o `DeviceRuntime` não importa este `Protocol`.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class DeviceRuntimeView(Protocol):
    """O que a leitura de `device.state` vê de um aparelho em memória — só leitura, sem efeito."""

    @property
    def state(self) -> str: ...

    @property
    def state_detail(self) -> str | None: ...

    @property
    def readiness_phase(self) -> str: ...

    #: Aparelho-loja (Play Store): nunca executa tarefa.
    @property
    def store(self) -> bool: ...

    #: Aparelho de outra máquina, ligado pelo worker que o hospeda.
    @property
    def external(self) -> bool: ...

    #: Verbos que o worker declarou neste aparelho; `None` = nenhum worker declarou (ou ele caiu).
    @property
    def worker_verbs(self) -> Sequence[str] | None: ...

    @property
    def snapshot_valid(self) -> bool: ...
