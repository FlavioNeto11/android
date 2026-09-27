"""Corpos de requisição HTTP do parque: aparelho, worker, comando, controle manual e limites por servidor.

Movidos literalmente de `app/models.py` (design §16, fase K: "primeiro os corpos"); `app.models` reexporta os
MESMOS objetos, e o nome de cada classe continua sendo o nome do esquema no contrato HTTP. Este módulo não
importa `app.models`: o reexport importa daqui, e o caminho de volta fecharia um ciclo de import de topo.

Ficaram em `app.models`: `InstanceActionBody` (o despacho de comandos também o importa) e `BulkBody`, que o
compõe; `ManualInput`, que o gerenciador de aparelhos importa.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AdoptDeviceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Serial do aparelho DENTRO do worker, como o agente o anunciou (ex.: `emulator-5554`).
    serial: str = Field(min_length=1, max_length=80)
    #: Id da instância a criar. Vazio = o próximo livre do prefixo configurado.
    instance_id: str | None = Field(default=None, max_length=60)


class WorkerEnrollBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=120)


class WorkerMaintenanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool


class WorkerRemoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Desconecta o canal e ignora comando em voo — para a máquina que nunca mais volta.
    force: bool = False


class InstancePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str | None = None
    account_label: str | None = Field(default=None, max_length=80)
    # Máquina que hospeda este aparelho. `null` devolve o aparelho a esta máquina. Existe porque amarrar
    # instância a worker exigia `UPDATE` direto no banco — e o que não tem rota não tem como ser operado.
    worker_id: str | None = Field(default=None, max_length=64)
    #: Mudar de máquina um aparelho que hospeda perfil com sessão pronta apaga o acesso àquela sessão: os dados
    #: ficam no disco da máquina antiga. Sem esta confirmação a troca é recusada com 409 (item 4.4 / E9).
    confirm_locality_change: bool = False


class CommandResolveBody(BaseModel):
    """A decisão de uma pessoa sobre um comando `uncertain`. `note` é o que ela observou — o que separa
    "marquei como sucesso" de "abri o aparelho, os dados estavam apagados, então o reset aconteceu"."""

    model_config = ConfigDict(extra="forbid")
    outcome: Literal["succeeded", "failed", "cancelled"]
    note: str | None = Field(default=None, max_length=400)
    requested_by: str | None = Field(default=None, max_length=60)


class CommandCancelBody(BaseModel):
    """O pedido de cancelamento de um comando ainda aberto. Pedir não é ter cancelado: o desfecho continua vindo
    de quem executa, e por isso aqui não há `outcome` nenhum para escolher."""

    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=400)
    requested_by: str | None = Field(default=None, max_length=60)


class ReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: str


class ServerLimitsPatch(BaseModel):
    """Limites de UMA máquina (tela Limites → Por servidor). Campo ausente = não mexe; `null` = volta ao valor
    da máquina (o `worker.yaml` dela; para este servidor, o padrão do `config.yaml`)."""

    model_config = ConfigDict(extra="forbid")
    max_slots: int | None = Field(default=None, ge=1, le=64)
    # Mesmo teto de `limits.boot_parallelism` (config.py) e da mensagem `Limits` do protocolo. Acima do que o
    # `worker.yaml` aceita (8) de propósito: é decisão do dono no painel, e o agente não revalida na atribuição.
    boot_parallelism: int | None = Field(default=None, ge=1, le=10)
    max_working: int | None = Field(default=None, ge=1, le=64)
    min_free_ram_mb: int | None = Field(default=None, ge=0, le=1_048_576)
