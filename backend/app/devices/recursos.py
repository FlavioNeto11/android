"""Recursos EFETIVOS desta máquina: o que o processo — e os emuladores filhos dele — pode de fato usar.

Por que existe: `psutil.virtual_memory()` lê o HOST. Num contêiner, ou numa unidade systemd com `MemoryMax=`
(`config/farm-worker.service`), o limite que mata o emulador é o do cgroup, e ele chega muito antes de o host ficar
sem memória. A guarda do agente e a admissão do central decidiam pelo número do host — o que ali parecia folga
era o OOM killer esperando o próximo boot.

Regras:
- **Ausente é `None`, nunca ilimitado.** `max`, arquivo que não existe, texto ilegível e o "sem limite" do cgroup
  v1 viram `None`. Quem consome decide o que fazer com "não se sabe" (o central é conservador).
- **Nada inventado no Windows.** Limite de job, quota de CPU e pressão de memória não são medidos lá; ficam
  `None`. A RAM disponível é a do host, que é o que o Windows de fato oferece ao processo.
- **Leitor de arquivo injetável**: o parsing de cgroup v1/v2 e PSI é testado com arquivos falsos, na suíte do
  Windows, sem Linux nenhum.

Mora em `devices/` e não na raiz de `app/` de propósito: o instalador do agente (`scripts/worker-install.*`) copia
só `worker/`, `workers/`, `devices/`, `security/` e quatro arquivos soltos. Um módulo novo na raiz derrubaria o
agente instalado com `ImportError` no primeiro `import`. Importa só `psutil` e `util`, pelo mesmo motivo de
`perfis.py`: o central também pode usá-lo sem ciclo.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from typing import Any, Callable

import psutil

from ..util import now_iso

#: Caminho → conteúdo do arquivo, ou `None` quando ele não existe ou não pôde ser lido.
LeitorDeArquivo = Callable[[str], "str | None"]

MB = 1024 * 1024
RAIZ_V2 = "/sys/fs/cgroup"
#: Onde cada controlador do cgroup v1 costuma estar montado. `cpu` aparece como `cpu,cpuacct` em muitas
#: distribuições (e às vezes com os dois nomes, um link para o outro).
RAIZES_V1 = {"memory": ("/sys/fs/cgroup/memory",),
             "cpu": ("/sys/fs/cgroup/cpu", "/sys/fs/cgroup/cpu,cpuacct"),
             "cpuset": ("/sys/fs/cgroup/cpuset",)}
#: O "sem limite" do cgroup v1 é um número (`9223372036854771712`), não a palavra `max`. Qualquer coisa acima
#: disto é esse sentinela — nenhuma máquina tem 1 EiB de RAM.
SEM_LIMITE_V1 = 1 << 60
PSI_MEMORIA = "/proc/pressure/memory"


def ler_arquivo(caminho: str) -> str | None:
    try:
        with open(caminho, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


@dataclass(frozen=True)
class RecursosEfetivos:
    """O que a batida do worker declara (`WorkerResources`, adendo v0.20 C6), menos o que é do agente
    (`reserved_mb`) e do disco."""

    ram_total_mb: int | None
    ram_free_mb: int | None            # disponível no HOST
    mem_limit_mb: int | None           # limite de cgroup, só quando menor que o host
    mem_available_mb: int | None       # o menor entre o host e a folga sob o limite
    cpu_count: int | None
    cpu_effective: float | None
    swap_used_pct: float | None
    mem_pressure: float | None
    measured_at: str

    def campos(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- parsing (puro, testável com arquivos falsos)
def _inteiro(texto: str | None) -> int | None:
    """Número de um arquivo de cgroup. `max`, vazio, ausente ou ilegível → `None`."""
    if texto is None:
        return None
    t = texto.strip()
    if not t or t == "max":
        return None
    try:
        return int(t)
    except ValueError:
        return None


def grupos_do_processo(ler: LeitorDeArquivo) -> dict[str, str]:
    """`/proc/self/cgroup` → `{controlador: caminho}`. A linha do v2 (`0::/caminho`) vira a chave `""`.

    Máquina híbrida tem as duas: controlador listado no v1 (`4:memory:/…`) é lido no v1, que é onde o kernel
    o está contabilizando.
    """
    saida: dict[str, str] = {}
    for linha in (ler("/proc/self/cgroup") or "").splitlines():
        partes = linha.strip().split(":", 2)
        if len(partes) != 3:
            continue
        _, controladores, caminho = partes
        if not controladores:
            saida.setdefault("", caminho)
        for c in controladores.split(","):
            if c:
                saida.setdefault(c, caminho)
    return saida


def niveis(raiz: str, caminho: str) -> list[str]:
    """Do cgroup do processo até a raiz da montagem, inclusive.

    O limite pode estar em qualquer ancestral (a fatia do systemd, o contêiner), e a raiz do v2 nem tem
    `memory.max`: ler só `/sys/fs/cgroup/memory.max` é o erro clássico que acha "sem limite" num serviço com
    `MemoryMax=`. Também cobre o contêiner v1 sem namespace de cgroup, em que o caminho listado é o do host e
    o do contêiner está montado na raiz.
    """
    partes = [p for p in caminho.split("/") if p and p != ".."]
    return ["/".join([raiz, *partes[:i]]) for i in range(len(partes), -1, -1)]


def _inativo(ler: LeitorDeArquivo, nivel: str, chave: str) -> int:
    """Cache de arquivo inativo do cgroup (`memory.stat`). `memory.current` o inclui, e ele é recuperável sem
    matar ninguém — é a mesma conta de "working set" do kubelet. Sem o arquivo, 0: a folga sai MENOR (erro barato)."""
    for linha in (ler(f"{nivel}/memory.stat") or "").splitlines():
        nome, _, valor = linha.partition(" ")
        if nome == chave:
            return _inteiro(valor) or 0
    return 0


def memoria_do_cgroup(ler: LeitorDeArquivo, grupos: dict[str, str],
                      host_total_mb: int | None) -> tuple[int | None, int | None]:
    """`(limite_mb, folga_mb)`: o menor limite numérico entre o processo e a raiz, e a menor folga medida NO
    nível de cada limite (`limite − uso`). Limite que não é menor que o host não conta: é como não ter.

    Limite conhecido com uso desconhecido fica só como teto (`folga = None` daquele nível): a folga não é
    inventada.
    """
    if "memory" in grupos:
        raizes, max_, uso, inativo = RAIZES_V1["memory"], "memory.limit_in_bytes", "memory.usage_in_bytes", \
            "total_inactive_file"
        caminho = grupos["memory"]
    elif "" in grupos:
        raizes, max_, uso, inativo = (RAIZ_V2,), "memory.max", "memory.current", "inactive_file"
        caminho = grupos[""]
    else:
        return None, None
    limite: int | None = None
    folga: int | None = None
    for raiz in raizes:
        for nivel in niveis(raiz, caminho):
            maximo = _inteiro(ler(f"{nivel}/{max_}"))
            if maximo is None or maximo >= SEM_LIMITE_V1:
                continue
            limite_mb = maximo // MB
            if host_total_mb is not None and limite_mb >= host_total_mb:
                continue
            limite = limite_mb if limite is None else min(limite, limite_mb)
            atual = _inteiro(ler(f"{nivel}/{uso}"))
            if atual is not None:
                em_uso = max(0, atual - _inativo(ler, nivel, inativo))
                f = max(0, (maximo - em_uso) // MB)
                folga = f if folga is None else min(folga, f)
    return limite, folga


def contar_cpus(lista: str | None) -> int | None:
    """`0-3,6` → 5. Vazio ou ilegível → `None`."""
    if not lista or not lista.strip():
        return None
    total = 0
    try:
        for parte in lista.strip().split(","):
            if "-" in parte:
                a, b = parte.split("-", 1)
                total += int(b) - int(a) + 1
            elif parte.strip():
                int(parte)
                total += 1
    except ValueError:
        return None
    return total or None


def cpu_do_cgroup(ler: LeitorDeArquivo, grupos: dict[str, str]) -> float | None:
    """CPUs utilizáveis: o menor entre a quota (quota/período, em qualquer ancestral) e o conjunto efetivo."""
    candidatos: list[float] = []
    if "cpu" in grupos:
        for raiz in RAIZES_V1["cpu"]:
            for nivel in niveis(raiz, grupos["cpu"]):
                quota = _inteiro(ler(f"{nivel}/cpu.cfs_quota_us"))
                periodo = _inteiro(ler(f"{nivel}/cpu.cfs_period_us"))
                if quota is not None and quota > 0 and periodo:
                    candidatos.append(quota / periodo)
    elif "" in grupos:
        for nivel in niveis(RAIZ_V2, grupos[""]):
            partes = (ler(f"{nivel}/cpu.max") or "").split()
            if len(partes) == 2 and partes[0] != "max":
                quota, periodo = _inteiro(partes[0]), _inteiro(partes[1])
                if quota and periodo:
                    candidatos.append(quota / periodo)
    if "cpuset" in grupos:
        arquivos = [f"{n}/{a}" for r in RAIZES_V1["cpuset"] for n in niveis(r, grupos["cpuset"])
                    for a in ("cpuset.effective_cpus", "cpuset.cpus")]
    elif "" in grupos:
        arquivos = [f"{n}/cpuset.cpus.effective" for n in niveis(RAIZ_V2, grupos[""])]
    else:
        arquivos = []
    # O conjunto EFETIVO do nível mais fundo já considera os ancestrais: o primeiro legível basta.
    for arquivo in arquivos:
        if (n := contar_cpus(ler(arquivo))) is not None:
            candidatos.append(float(n))
            break
    return round(min(candidatos), 2) if candidatos else None


def pressao_de_memoria(ler: LeitorDeArquivo) -> float | None:
    """PSI de memória, `some avg10`. Kernel sem PSI (ou fora do Linux) → `None`."""
    for linha in (ler(PSI_MEMORIA) or "").splitlines():
        campos = linha.split()
        if not campos or campos[0] != "some":
            continue
        for campo in campos[1:]:
            nome, _, valor = campo.partition("=")
            if nome == "avg10":
                try:
                    return float(valor)
                except ValueError:
                    return None
    return None


# ---------------------------------------------------------------- medição
def medir(*, ler: LeitorDeArquivo | None = None, linux: bool | None = None,
          memoria: Callable[[], Any] | None = None, swap: Callable[[], Any] | None = None,
          cpus: Callable[[], int | None] | None = None, agora: Callable[[], str] | None = None) -> RecursosEfetivos:
    """Uma foto dos recursos efetivos. Tudo injetável; o padrão é esta máquina.

    As funções do `psutil` são resolvidas NA CHAMADA (e não como padrão do parâmetro): teste que troca
    `psutil.virtual_memory` continua valendo para quem mede por aqui.
    """
    ler = ler or ler_arquivo
    linux = sys.platform.startswith("linux") if linux is None else linux
    vm = (memoria or psutil.virtual_memory)()
    total_mb = int(vm.total / MB) if getattr(vm, "total", None) else None
    livre_mb = int(vm.available / MB) if getattr(vm, "available", None) is not None else None
    try:
        sw = (swap or psutil.swap_memory)()
        swap_pct: float | None = round(float(sw.percent), 1) if getattr(sw, "total", 0) else 0.0
    except Exception:  # noqa: BLE001 - swap ilegível não pode impedir a batida
        swap_pct = None
    limite = folga = cpu_ef = pressao = None
    if linux:
        grupos = grupos_do_processo(ler)
        limite, folga = memoria_do_cgroup(ler, grupos, total_mb)
        cpu_ef = cpu_do_cgroup(ler, grupos)
        pressao = pressao_de_memoria(ler)
    conhecidos = [v for v in (livre_mb, folga, limite) if v is not None]
    disponivel = min(conhecidos) if livre_mb is not None and conhecidos else None
    return RecursosEfetivos(
        ram_total_mb=total_mb, ram_free_mb=livre_mb, mem_limit_mb=limite, mem_available_mb=disponivel,
        cpu_count=(cpus or (lambda: psutil.cpu_count(logical=True)))(), cpu_effective=cpu_ef,
        swap_used_pct=swap_pct, mem_pressure=pressao, measured_at=(agora or now_iso)())
