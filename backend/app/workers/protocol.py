"""Contrato entre o servidor central e um worker. Os dois lados importam ESTE arquivo — não há duas verdades.

Desenho de transporte: **o worker liga para o central**, nunca o contrário. Isso atravessa NAT sem abrir porta na
casa de ninguém (a restrição que você colocou), e reaproveita a ideia de replay por id que o WebSocket do painel
já usa.

O que NÃO está aqui de propósito: credencial de aplicativo. O canal de entrada sensível continua central; o worker
nunca recebe senha de conta.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Versão do contrato. O central recusa worker de versão maior que a dele — é melhor recusar do que agir com
#: mensagens que não se entende. Worker MENOR é aceito enquanto o campo que falta tiver padrão.
PROTOCOL_VERSION = 1

#: Modo do Appium no worker. `local` = há um Appium na máquina dele (menor latência, ADB e Appium na rede privada
#: dele); `central` = o Appium deste servidor dirige o aparelho pelo túnel, como já está provado em campo.
AppiumMode = Literal["local", "central"]


class WorkerDevice(BaseModel):
    """Um aparelho que o worker hospeda e se oferece para operar."""

    model_config = ConfigDict(extra="ignore")
    #: Serial ADB **de dentro do worker** (ex.: `emulator-5554`). O central nunca usa este valor para falar com o
    #: aparelho: quem fala com ele é o worker. O central usa para casar inventário e mostrar na infraestrutura.
    serial: str
    #: Nome do AVD, quando é emulador que o worker cria. Vazio para aparelho físico.
    avd_name: str | None = None
    state: str = "unknown"                 # online | stopped | hibernated | booting | absent | error | unknown
    detail: str | None = None
    #: Porta local do worker onde o ADB dele escuta este aparelho — o que o túnel precisa encaminhar.
    adb_port: int | None = None
    #: Instância do parque à qual este aparelho está amarrado, quando o worker já sabe.
    instance_id: str | None = None


class WorkerResources(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cpu_percent: float | None = None
    cpu_count: int | None = None
    ram_total_mb: int | None = None
    ram_free_mb: int | None = None
    disk_free_gb: float | None = None


class Hello(BaseModel):
    """Primeira mensagem do worker. É o registro: ele DECLARA o que é e o que sabe fazer.

    Declarativo de propósito — o central não adivinha capacidade de máquina que não conhece, e é a declaração que
    alimenta a recusa explicada do pré-voo.
    """

    model_config = ConfigDict(extra="ignore")
    type: Literal["hello"] = "hello"
    protocol: int = PROTOCOL_VERSION
    worker_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    agent_version: str = Field(max_length=40)
    os: str = Field(max_length=40)                  # windows | linux | darwin
    os_version: str | None = Field(default=None, max_length=120)
    appium_mode: AppiumMode = "central"
    appium_url: str | None = Field(default=None, max_length=200)
    #: Quantos aparelhos este worker aceita manter ligados ao mesmo tempo. Quem protege a máquina é ela mesma:
    #: `slots_used()` do central exclui aparelho externo de propósito.
    max_slots: int = Field(default=1, ge=1, le=64)
    #: Verbos que o worker consegue executar. É isto que faz um aparelho remoto ganhar ciclo de vida de verdade.
    verbs: list[str] = []
    devices: list[WorkerDevice] = []
    resources: WorkerResources | None = None


class Heartbeat(BaseModel):
    """Batida do worker. Ausência dela é o que marca o worker indisponível — não um socket fechado, que pode ser
    só a rede piscando."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["heartbeat"] = "heartbeat"
    resources: WorkerResources | None = None
    devices: list[WorkerDevice] = []
    #: `Welcome.server_time` menos o relógio local do worker, em segundos, calculado uma vez na conexão.
    #: Positivo = relógio do worker atrasado em relação ao central. `None` só em worker de protocolo antigo
    #: (campo opcional — regra do arquivo: novo campo tem padrão, worker menor continua aceito).
    clock_offset_s: float | None = None


class Ack(BaseModel):
    """Recebi o comando. Separado do resultado de propósito: "entreguei e não sei se chegou" é um estado real, e é
    o que distingue falha de rede de falha de execução."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["ack"] = "ack"
    command_id: str


class Progress(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["progress"] = "progress"
    command_id: str
    message: str = Field(max_length=400)


class Result(BaseModel):
    """Desfecho. `uncertain` existe para o worker poder dizer "agi e não sei o que aconteceu" — sem isso ele teria
    de escolher entre mentir sucesso e mentir falha."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["result"] = "result"
    command_id: str
    outcome: Literal["succeeded", "failed", "uncertain", "cancelled"]
    reason: str | None = Field(default=None, max_length=400)
    data: dict[str, Any] | None = None


class Welcome(BaseModel):
    """Resposta do central ao `hello`. Diz o ritmo das batidas para o worker não ter de adivinhar."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["welcome"] = "welcome"
    protocol: int = PROTOCOL_VERSION
    server_time: str
    heartbeat_s: float = 10.0
    #: Aparelhos que o central espera deste worker, por `instance_id` → serial de dentro do worker.
    expected_devices: dict[str, str] = {}


class Dispatch(BaseModel):
    """Comando indo para o worker.

    `fence` é a cerca: o worker devolve o mesmo valor no resultado, e resultado com cerca velha é RECUSADO. É o
    que impede um worker que voltou do limbo de sobrescrever o estado atual.
    """

    model_config = ConfigDict(extra="ignore")
    type: Literal["dispatch"] = "dispatch"
    command_id: str
    fence: int
    verb: str
    instance_id: str
    serial: str
    params: dict[str, Any] = {}
    #: Prazo em segundos. Estourado, o central marca o comando `uncertain` — nunca sucesso nem falha.
    timeout_s: float = 300.0


class Cancel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["cancel"] = "cancel"
    command_id: str


class Refused(BaseModel):
    """O central recusa a conexão e diz por quê, em vez de fechar o socket calado."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["refused"] = "refused"
    code: str
    message: str


#: Mensagens que o worker envia.
UPSTREAM = {"hello": Hello, "heartbeat": Heartbeat, "ack": Ack, "progress": Progress, "result": Result}
#: Mensagens que o central envia.
DOWNSTREAM = {"welcome": Welcome, "dispatch": Dispatch, "cancel": Cancel, "refused": Refused}


def parse_upstream(raw: dict[str, Any]) -> Hello | Heartbeat | Ack | Progress | Result:
    """Converte o que veio do worker no modelo certo. Tipo desconhecido é erro explícito, não silêncio."""
    tipo = raw.get("type")
    modelo = UPSTREAM.get(tipo if isinstance(tipo, str) else "")
    if modelo is None:
        raise ValueError(f"mensagem de worker desconhecida: {tipo!r}")
    return modelo.model_validate(raw)  # type: ignore[return-value]
