"""Contrato entre o servidor central e um worker. Os dois lados importam ESTE arquivo — não há duas verdades.

Desenho de transporte: **o worker liga para o central**, nunca o contrário. Isso atravessa NAT sem abrir porta na
casa de ninguém (a restrição que você colocou), e reaproveita a ideia de replay por id que o WebSocket do painel
já usa.

O que NÃO está aqui de propósito: credencial de aplicativo. O canal de entrada sensível continua central; o worker
nunca recebe senha de conta.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Marca que o agente põe no progresso quando o aparelho está ESPERANDO vaga na fila de boot dele, e que o
#: central procura para adiar o prazo. Mora aqui porque é contrato entre os dois lados: a fila do worker existe
#: para não travar quatro emuladores em ANR, mas o central não a enxergava — o tempo parado na fila era contado
#: como tempo de boot e o comando virava "incerto" sem nada ter falhado.
MARCA_DE_FILA = "na fila de boot"

#: Marca em `Result.data["refused"]` do despacho que o agente recusou SEM executar porque a cerca não é maior que
#: a última executada naquele aparelho: ordem vencida, ou reentrega de um comando cujo desfecho já saiu do
#: diário. O desfecho vai `failed` ("nada foi executado agora") — nunca sucesso de efeito novo —, e o central
#: antigo o lê como a recusa de sempre. `data["last_fence"]` leva a maior cerca que o agente já executou.
RECUSA_CERCA_NAO_MAIOR = "fence_not_newer"

#: Versão do contrato. O central recusa worker de versão maior que a dele — é melhor recusar do que agir com
#: mensagens que não se entende. Worker MENOR é aceito enquanto o campo que falta tiver padrão.
PROTOCOL_VERSION = 1

#: Versão MÍNIMA que este servidor ainda atende. Existia só o teto (`> PROTOCOL_VERSION` é recusado), e um teto
#: sozinho promete compatibilidade para sempre com qualquer agente antigo — promessa que ninguém consegue
#: cumprir no dia em que um campo deixa de ter padrão sensato. Com o piso, o agente velho recebe uma recusa que
#: diz o que fazer ("atualize o agente") em vez de conectar e falhar mais adiante, longe da causa. Hoje o piso é
#: 1, que é a primeira versão: nada é recusado, e a regra passa a existir antes de precisar dela.
PROTOCOL_MIN = 1

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
    # ---------------------------------------------------------------- capacidades declaradas
    # O que o aparelho É, e não só que verbo ele aceita. Sem isto, o pré-voo de uma execução ou de uma
    # distribuição não tinha como dizer "este app é só ARM e este aparelho não traduz" ou "este fluxo precisa de
    # Play Services e esta imagem é AOSP": a incompatibilidade aparecia no meio, como
    # `INSTALL_FAILED_NO_MATCHING_ABIS`. Todos com padrão: worker de protocolo antigo continua aceito, e o que
    # ele não declara fica `None` — que quer dizer "não se sabe", nunca "não tem".
    kind: str | None = None                # emulator | physical | container
    system_image: str | None = None        # ex.: system-images;android-34;google_apis;x86_64
    api_level: int | None = None
    abis: list[str] = []
    play_store: bool | None = None


class WorkerResources(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cpu_percent: float | None = None
    cpu_count: int | None = None
    ram_total_mb: int | None = None
    ram_free_mb: int | None = None
    disk_free_gb: float | None = None
    #: Achado #63: sem o TOTAL a barra de disco do painel era sempre zero — "400 GB livres" não diz se
    #: sobra folga ou se a máquina está no limite. Opcional: worker de protocolo antigo segue aceito.
    disk_total_gb: float | None = None
    # ---------------------------------------------------------------- recursos EFETIVOS (adendo v0.20, C6)
    # `ram_free_mb` é a RAM do HOST (`psutil.virtual_memory`). Num contêiner ou numa unidade systemd com
    # `MemoryMax=`, o processo morre no limite do cgroup muito antes de o host ficar sem memória — e o central
    # decidia pelo número do host. Todos opcionais: `None` é "não se sabe", NUNCA "ilimitado". Agente antigo não
    # manda nenhum, e central antigo os ignora (`extra="ignore"`).
    #: Limite efetivo de cgroup (ou job), só quando é MENOR que a RAM do host.
    mem_limit_mb: int | None = None
    #: O que ainda cabe: o menor entre a RAM disponível do host e a folga sob o limite do cgroup.
    mem_available_mb: int | None = None
    #: CPUs utilizáveis pela quota (`cpu.max`) ou pelo conjunto (`cpuset`). Fração quando a quota é fracionária.
    cpu_effective: float | None = None
    swap_used_pct: float | None = None
    #: PSI `some avg10` de memória (Linux): % do tempo em que alguma tarefa esperou por memória nos últimos 10 s.
    #: É o sinal de thrash que a RAM livre não mostra (o android-01 em load 22 com "memória disponível").
    mem_pressure: float | None = None
    #: RAM já prometida a boots em andamento nesta máquina e ainda não refletida na memória disponível.
    reserved_mb: int | None = None
    #: Quando a medição foi feita, pelo relógio do worker (ISO).
    measured_at: str | None = None


#: Funcionalidades que o agente anuncia em `Hello.features` (adendo v0.20, C7). O central só usa o que foi
#: anunciado E aceito em `Welcome.accepted_features`; nome desconhecido é ignorado dos dois lados.
#: `boot_reservations`: o agente reserva RAM por boot antes de subir o emulador e informa o total em
#: `WorkerResources.reserved_mb` — então `reserved_mb` ausente/`None` de quem NÃO anuncia é "não se sabe".
FEATURE_RESERVA_DE_BOOT = "boot_reservations"
#: `observe_local`: o agente captura a tela NA MÁQUINA DELE (screencap pelo ADB local) e a codifica lá
#: (`devices/codificacao.py`); só o JPEG já reduzido atravessa o túnel, e por um canal de mídia SEPARADO do de
#: comando (`/api/worker/midia`), para uma imagem nunca atrasar batida, `ack` ou desfecho. É só a IMAGEM: a
#: hierarquia continua pelo Appium (`rt.io.page_source`). `uiautomator dump` concorre com a sessão UiAutomator2
#: (um cliente UiAutomation por vez) e a derrubaria; com `appium: local` a árvore já é produzida na origem; e o XML
#: tem de chegar ao central de qualquer jeito, porque é lá que a tela sensível é classificada.
FEATURE_OBSERVACAO_LOCAL = "observe_local"


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
    #: Aceleração de virtualização MEDIDA na máquina do worker: `kvm`, `kvm-inacessivel`, `kvm-ausente` no Linux;
    #: `None` ("não se sabe") no Windows, onde conferir o WHPX custa rodar `emulator -accel-check`. Sem KVM, um
    #: boot de 2 min vira dezenas de minutos, e hoje isso só aparecia como comando estourando prazo na outra
    #: máquina. Opcional com padrão: worker de protocolo antigo continua aceito (regra deste arquivo).
    accel: str | None = Field(default=None, max_length=40)
    appium_mode: AppiumMode = "central"
    appium_url: str | None = Field(default=None, max_length=200)
    #: Quantos aparelhos este worker aceita manter ligados ao mesmo tempo. Quem protege a máquina é ela mesma:
    #: `slots_used()` do central exclui aparelho externo de propósito.
    max_slots: int = Field(default=1, ge=1, le=64)
    #: O que o `worker.yaml` DESTA máquina diz — declarado para o painel mostrar "valor da máquina" ao lado do que
    #: o dono decidiu (`worker_limits` no central) e para o "voltar ao da máquina" ter para onde voltar. Agente
    #: antigo não declara (`None`), e o painel mostra só o que sabe.
    boot_parallelism: int | None = Field(default=None, ge=1, le=16)
    min_free_ram_mb: int | None = Field(default=None, ge=0, le=1_048_576)
    #: Verbos que o worker consegue executar. É isto que faz um aparelho remoto ganhar ciclo de vida de verdade.
    verbs: list[str] = []
    #: A MÁQUINA DELE salva snapshot? Quem sabe se `hibernate`/`wake` fazem sentido num aparelho remoto é o
    #: worker, não o `config.yaml` do central — o pré-voo consultava a configuração daqui e oferecia "Hibernar"
    #: para uma máquina que subia tudo a frio. Worker antigo não declara e o padrão é `False`, que é a resposta
    #: conservadora: o central deixa de oferecer o que não pode provar.
    hibernation: bool = False
    devices: list[WorkerDevice] = []
    resources: WorkerResources | None = None
    #: Comandos que o agente AINDA está executando quando (re)conecta. Queda de canal não cancela trabalho: o
    #: agente sobrevive à queda e diz o que continua na mão dele, para o central não tratar "incerto" como
    #: "acabou". Worker antigo manda a lista vazia, e nada quebra.
    inflight: list[str] = []
    #: A maior cerca que o agente JÁ EXECUTOU, por aparelho (`instance_id` → cerca), lida do diário dele. Existe
    #: por causa do banco restaurado (K-004): a cerca do central é `MAX(fence) + 1` no banco, e um backup mais
    #: antigo a faz voltar para trás, enquanto o diário do agente guarda a de antes — todo `start` era recusado
    #: como "cerca anterior à última executada" até alguém subir a cerca à mão no SQLite. Com isto o central
    #: despacha acima do que o agente já viu. Opcional com padrão: agente antigo manda nada e nada muda.
    fences: dict[str, int] = {}
    #: O que este agente implementa e confere (C7; ex.: `FEATURE_RESERVA_DE_BOOT`). Agente antigo manda nada, e
    #: o central segue o caminho anterior com ele.
    features: list[str] = []


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
    #: Cerca do despacho, devolvida como veio. Do worker ela é OBRIGATÓRIA — `on_result` recusa resultado sem
    #: cerca, senão a proteção seria opcional e quem a omitisse (agente velho, processo forjado) passaria direto.
    #: Continua com padrão `None` porque o próprio central fabrica `Result` sintético (timeout, queda do canal),
    #: e esses não passam por `on_result`.
    fence: int | None = None


class Welcome(BaseModel):
    """Resposta do central ao `hello`. Diz o ritmo das batidas para o worker não ter de adivinhar."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["welcome"] = "welcome"
    protocol: int = PROTOCOL_VERSION
    server_time: str
    heartbeat_s: float = 10.0
    #: Aparelhos que o central espera deste worker, por `instance_id` → serial de dentro do worker.
    expected_devices: dict[str, str] = {}
    #: Das `Hello.features`, as que o central VAI usar com este agente (C7). Mensagem de tipo novo só vai para
    #: quem teve a feature aceita. Agente antigo lê o `welcome` como dicionário e não procura esta chave.
    accepted_features: list[str] = []


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


class ResultAck(BaseModel):
    """O central confirma que RECEBEU e tratou o desfecho. Só então o agente pode apagar o resultado do diário
    local dele. Sem isto, um resultado produzido com o canal caído se perderia para sempre — e "a rede piscou"
    voltaria a significar "a ação falhou"."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["result_ack"] = "result_ack"
    command_id: str


class Limits(BaseModel):
    """Os limites que o DONO decidiu para esta máquina no painel (tela Limites → Por servidor).

    Chega na PRIMEIRA batida de cada conexão (não junto do `welcome`: ver `WorkerRegistry.on_heartbeat`) e de novo
    a cada mudança. Campo `None` = "o do `worker.yaml`": o agente volta
    ao valor do arquivo dele. Agente antigo ignora o tipo desconhecido (o laço de recepção dele só reage a
    `dispatch`/`cancel`/`result_ack`/`refused`), e a guarda do central continua valendo — ele só não aplica o
    número novo do lado de lá.
    """

    model_config = ConfigDict(extra="ignore")
    type: Literal["limits"] = "limits"
    max_slots: int | None = Field(default=None, ge=1, le=64)
    #: Quem produz esta mensagem é o central, a partir do que o painel aceitou (`models.ServerLimitsPatch`): o teto
    #: tem de ser o MESMO de lá, senão o contrato promete 16 e o painel recusa acima de 10 (achado da documentação
    #: de 24/09, backlog B3). O `hello` continua aceitando até 16, que é só o que a máquina declara.
    boot_parallelism: int | None = Field(default=None, ge=1, le=10)
    min_free_ram_mb: int | None = Field(default=None, ge=0, le=1_048_576)


class Refused(BaseModel):
    """O central recusa a conexão e diz por quê, em vez de fechar o socket calado."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["refused"] = "refused"
    code: str
    message: str


# ---------------------------------------------------------------- observação na origem (`observe_local`)
#: Teto do corpo de UMA imagem no canal de mídia. Um JPEG cheio de 1080×2400 a q72 fica em centenas de KB; a
#: prévia leva cheia + miniatura. 8 MiB é folga para tela grande sem abrir a porta para qualquer tamanho.
MIDIA_MAX_BYTES = 8 * 1024 * 1024
#: Teto do cabeçalho JSON do corpo: largura, altura, nomes e tamanhos das partes. Alguns bytes na prática.
MIDIA_CABECALHO_MAX = 16 * 1024
#: As partes que uma imagem pode levar: `cheia` (evidência, prévia, quem pediu a imagem inteira), `miniatura`
#: (grade da prévia) e `modelo` (já no `lado_max` que o modelo vê).
PARTES_DE_MIDIA = ("cheia", "miniatura", "modelo")
#: Maior lado aceito, em pixels: nenhuma tela real passa disto, e o número entra em conta de coordenada.
LADO_MAX_ACEITO = 10_000
_SOI_JPEG = b"\xff\xd8\xff"


class ObserveImage(BaseModel):
    """Pedido de imagem ao agente. Só vai para quem teve `observe_local` aceito no `welcome` (C7).

    O resultado NÃO volta por este canal: o agente abre `/api/worker/midia`, apresenta `request_id` e
    `upload_token` (uso único, válido até o prazo) e manda o corpo de `empacotar_midia`. Pelo canal de comando só
    volta `ObserveResult` — a falha, ou as dimensões de um pedido `so_dimensoes`.

    `so_dimensoes`: o tamanho da tela sem pixel nenhum sair da máquina do worker. É o que o central pede numa tela
    sensível, em que só precisa da largura e da altura.
    """

    model_config = ConfigDict(extra="ignore")
    type: Literal["observe_image"] = "observe_image"
    request_id: str = Field(min_length=8, max_length=64)
    instance_id: str
    serial: str
    previa: bool = False
    cheia: bool = False
    lado_max: int | None = Field(default=None, ge=64, le=LADO_MAX_ACEITO)
    so_dimensoes: bool = False
    upload_token: str = Field(min_length=16, max_length=128)
    timeout_s: float = Field(default=20.0, gt=0, le=120)
    max_bytes: int = Field(default=MIDIA_MAX_BYTES, ge=1024, le=MIDIA_MAX_BYTES)


class ObserveResult(BaseModel):
    """O agente diz pelo canal de comando o que não é mídia: a falha (`ok=False`), ou as dimensões de um pedido
    `so_dimensoes`. Imagem nunca vem aqui."""

    model_config = ConfigDict(extra="ignore")
    type: Literal["observe_result"] = "observe_result"
    request_id: str = Field(max_length=64)
    ok: bool
    error: str | None = Field(default=None, max_length=300)
    largura: int | None = Field(default=None, ge=1, le=LADO_MAX_ACEITO)
    altura: int | None = Field(default=None, ge=1, le=LADO_MAX_ACEITO)


class EnvioDeMidia(BaseModel):
    """Primeira mensagem (texto) no canal de mídia; a segunda é o corpo binário."""

    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=64)
    upload_token: str = Field(min_length=16, max_length=128)


def empacotar_midia(largura: int, altura: int, partes: dict[str, bytes], **extra: Any) -> bytes:
    """O corpo de uma imagem no canal de mídia: 4 bytes (tamanho do cabeçalho, big-endian), o cabeçalho JSON e as
    partes JPEG em sequência. Sem multipart nem base64: nada a mais para o agente instalar ou para o túnel levar."""
    cabecalho = json.dumps({**extra, "largura": int(largura), "altura": int(altura),
                            "partes": [[nome, len(dados)] for nome, dados in partes.items()]}).encode("utf-8")
    return len(cabecalho).to_bytes(4, "big") + cabecalho + b"".join(partes.values())


def desempacotar_midia(corpo: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """O inverso de `empacotar_midia`, conferindo tudo: quem manda é um processo de outra máquina, e o que sai
    daqui vira imagem para o modelo e coordenada de toque. `ValueError` com o motivo em qualquer desvio."""
    if len(corpo) < 4:
        raise ValueError("corpo curto demais")
    n = int.from_bytes(corpo[:4], "big")
    if n <= 0 or n > MIDIA_CABECALHO_MAX or 4 + n > len(corpo):
        raise ValueError("cabeçalho de tamanho inválido")
    try:
        cab = json.loads(corpo[4:4 + n].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("cabeçalho ilegível") from exc
    if not isinstance(cab, dict):
        raise ValueError("cabeçalho não é objeto")
    for lado in ("largura", "altura"):
        v = cab.get(lado)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= LADO_MAX_ACEITO:
            raise ValueError(f"{lado} inválida")
    lista = cab.get("partes")
    if not isinstance(lista, list):
        raise ValueError("partes ausentes")
    partes: dict[str, bytes] = {}
    pos = 4 + n
    for item in lista:
        if (not isinstance(item, list) or len(item) != 2 or item[0] not in PARTES_DE_MIDIA or item[0] in partes
                or not isinstance(item[1], int) or isinstance(item[1], bool) or item[1] <= 0):
            raise ValueError("parte inválida")
        dados = corpo[pos:pos + item[1]]
        if len(dados) != item[1] or not dados.startswith(_SOI_JPEG):
            raise ValueError(f"parte '{item[0]}' não é JPEG")
        partes[item[0]] = dados
        pos += item[1]
    if pos != len(corpo):
        raise ValueError("bytes sobrando depois das partes")
    return cab, partes


#: Mensagens que o worker envia.
UPSTREAM = {"hello": Hello, "heartbeat": Heartbeat, "ack": Ack, "progress": Progress, "result": Result,
            "observe_result": ObserveResult}
#: Mensagens que o central envia.
DOWNSTREAM = {"welcome": Welcome, "dispatch": Dispatch, "cancel": Cancel, "refused": Refused,
              "result_ack": ResultAck, "limits": Limits, "observe_image": ObserveImage}


def parse_upstream(raw: dict[str, Any]) -> Hello | Heartbeat | Ack | Progress | Result | ObserveResult:
    """Converte o que veio do worker no modelo certo. Tipo desconhecido é erro explícito, não silêncio."""
    tipo = raw.get("type")
    modelo = UPSTREAM.get(tipo if isinstance(tipo, str) else "")
    if modelo is None:
        raise ValueError(f"mensagem de worker desconhecida: {tipo!r}")
    return modelo.model_validate(raw)  # type: ignore[return-value]
