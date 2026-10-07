"""O PG dirigido da suíte no contêiner descartável `farm-pg-rapido`, com o disco medido (29.99).

Até a suíte 35 o contêiner subia à mão (`docker run`, banco.md, "O portão de PostgreSQL de uma suíte") e a fase só
via o disco depois de quebrar: os 467 arquivos da 35 juntos encheram o tmpfs de 4 GB e deram 181 failed e 823 errors
(`psycopg.errors.DiskFull`), um erro por teste em vez de uma linha. Este script:

- sobe o contêiner SEMPRE com a mesma configuração: tmpfs de 4 GB, durabilidade desligada e WAL mínimo
  (`wal_level=minimal`, `max_wal_size=256MB`). Com o padrão (`replica`, 1 GB), o WAL parava perto de 950 MB em cada
  metade da 35; o banco descartável não precisa de nenhum;
- espera a primeira conexão aceita pelo TCP (a do servidor temporário do `initdb` não conta);
- roda a lista em N partes, recriando o contêiner entre elas (o tmpfs volta vazio), com o pytest em prioridade ociosa;
- amostra a cada 30 s: tmpfs, `pg_wal`, `base`, nº de esquemas de teste e o tamanho de `pg_class`, `pg_attribute` e
  `pg_depend`. É o que separa as duas hipóteses do estouro da 35 (esquema que a faxina não pega ou catálogo inchado);
- passou de 85 % do tmpfs: mata a árvore do pytest e para com UMA linha que diz quanto, onde e quando;
- para na primeira parte vermelha (a regra do funil: parar e mandar a linha).

Só stdlib. Não chama IA, não toca aparelho nem o banco do ambiente central; mexe só no contêiner `farm-pg-rapido`.

Uso (da raiz do repositório):

    python scripts/pg-rapido.py --lista afetados.txt --partes 2 --resumo resumo.txt
    python scripts/pg-rapido.py --lista afetados.txt --partes 2 --simular     # só diz o que faria
    python scripts/pg-rapido.py --amostrar                                     # uma amostra do contêiner de pé
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, Sequence

RAIZ = Path(__file__).resolve().parent.parent
NOME = "farm-pg-rapido"
IMAGEM = "postgres:17-alpine"
PORTA = 55434
DADOS = "/var/lib/postgresql/data"
TMPFS_MB = 4096
LIMITE = 0.85                 # fração do tmpfs a partir da qual a parte é abortada
INTERVALO_S = 30
PRAZO_DO_COMANDO_S = 30       # um `docker exec` preso não pode parar o laço das amostras (X1 da leitura do #385)
SEM_AMOSTRA_AVISO = 3         # amostras seguidas sem a linha do `df` até o aviso (X2)
RAM_MINIMA_GB = 7.0           # banco.md: os 4 GB do tmpfs e a folga dos emuladores
DSN = f"postgresql://postgres:teste@127.0.0.1:{PORTA}/farm"
#: Durabilidade desligada (o banco é descartável) e WAL mínimo: sem réplica nem arquivo, o PostgreSQL não precisa
#: guardar WAL além do checkpoint, e o checkpoint de 1 min recicla cedo.
CONFIG = ("fsync=off", "synchronous_commit=off", "full_page_writes=off", "max_connections=200",
          "wal_level=minimal", "max_wal_senders=0", "max_wal_size=256MB", "min_wal_size=64MB",
          "checkpoint_timeout=1min")
CATALOGO_SQL = ("select (select count(*) from pg_namespace where nspname ~ '^t[0-9a-f]{12}$'),"
                " pg_total_relation_size('pg_class')/1048576, pg_total_relation_size('pg_attribute')/1048576,"
                " pg_total_relation_size('pg_depend')/1048576")

WORKERS_PADRAO = 8            # `-n` do pytest da rodada serial; a paralela divide (ver `workers_por_instancia`)
PARALELO_MAXIMO = 4          # 29.197: acima disso a RAM (tmpfs somado) e o disco do host deixam de ser folga

Executar = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Instancia:
    """Um contêiner do PG descartável: nome, porta e tmpfs próprios. A rodada serial usa só a de sempre (`PADRAO`); a
    paralela (29.197) sobe uma por fila de partes, cada uma com a sua porta e o seu DSN."""
    nome: str = NOME
    porta: int = PORTA
    tmpfs_mb: int = TMPFS_MB

    @property
    def dsn(self) -> str:
        return f"postgresql://postgres:teste@127.0.0.1:{self.porta}/farm"


PADRAO = Instancia()


def instancias(n: int, tmpfs_total_mb: int = TMPFS_MB) -> list[Instancia]:
    """`n` contêineres. Com 1 é a instância de sempre (4 GB). Com mais, o tmpfs TOTAL se divide entre eles (o orçamento
    de RAM não cresce; o pico medido na suíte 59 foi 9 % de 4 GB por parte de 256 arquivos): o primeiro mantém nome e
    porta de sempre e os outros levam `-2`, `-3`… e a porta seguinte."""
    if n < 1 or n > PARALELO_MAXIMO:
        raise ValueError(f"paralelo de 1 a {PARALELO_MAXIMO}")
    if n == 1:
        return [PADRAO]
    cada = tmpfs_total_mb // n
    return [Instancia(NOME if k == 1 else f"{NOME}-{k}", PORTA + k - 1, cada) for k in range(1, n + 1)]


class Processo(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%SZ")


def comando_docker_run(inst: Instancia = PADRAO) -> list[str]:
    # `--pull=never`: imagem faltando é erro na hora, não um download no meio da vez do PG.
    tmpfs = f"{inst.tmpfs_mb // 1024}g" if inst.tmpfs_mb % 1024 == 0 else f"{inst.tmpfs_mb}m"
    cmd = ["docker", "run", "-d", "--pull=never", "--name", inst.nome, "-e", "POSTGRES_PASSWORD=teste", "-e", "POSTGRES_DB=farm",
           "-p", f"127.0.0.1:{inst.porta}:5432", "--tmpfs", f"{DADOS}:rw,size={tmpfs}", IMAGEM]
    for c in CONFIG:
        cmd += ["-c", c]
    return cmd


def partes(arquivos: Sequence[str], n: int) -> list[list[str]]:
    """Fatias contíguas na ordem dada, a primeira com a sobra (234 + 233 = 467): nada sai, nada repete."""
    if n < 1:
        raise ValueError("partes >= 1")
    tam = math.ceil(len(arquivos) / n) if arquivos else 0
    return [list(arquivos[i:i + tam]) for i in range(0, len(arquivos), tam)] if tam else []


@dataclass(frozen=True)
class Amostra:
    hora: str
    usado_mb: int
    total_mb: int
    wal_mb: int | None
    base_mb: int | None
    esquemas: int | None = None
    pg_class_mb: int | None = None
    pg_attribute_mb: int | None = None
    pg_depend_mb: int | None = None

    @property
    def fracao(self) -> float:
        return self.usado_mb / self.total_mb if self.total_mb else 0.0

    def linha(self) -> str:
        cat = ("" if self.esquemas is None else f" esquemas={self.esquemas} pg_class={self.pg_class_mb}"
               f" pg_attribute={self.pg_attribute_mb} pg_depend={self.pg_depend_mb}")
        return (f"{self.hora} tmpfs {self.usado_mb} de {self.total_mb} MB ({self.fracao:.0%})"
                f" wal={_mb(self.wal_mb)} base={_mb(self.base_mb)}{cat}")


def _mb(v: int | None) -> str:
    return "?" if v is None else str(v)


def ler_disco(saida: str) -> tuple[int, int, int | None, int | None]:
    """Saída de `df -m DADOS | tail -1; du -sm DADOS/pg_wal DADOS/base` → (usado, total, wal, base) em MB.

    O `df` decide (é dele o tamanho do tmpfs e o aborto); sem a linha dele, `ValueError`. As do `du` são lidas pelo
    caminho e podem faltar (`None`): o `du` tropeça em arquivo que some no meio da contagem, o comum com esquemas
    sendo criados e apagados, e isso não pode apagar a amostra."""
    linhas = [ln.split() for ln in saida.strip().splitlines() if ln.strip()]
    df = next((ln for ln in linhas if len(ln) >= 6 and ln[-1] == DADOS), None)
    if df is None:
        raise ValueError("sem a linha do df")
    wal = next((int(ln[0]) for ln in linhas if len(ln) == 2 and ln[1].endswith("/pg_wal") and ln[0].isdigit()), None)
    base = next((int(ln[0]) for ln in linhas if len(ln) == 2 and ln[1].endswith("/base") and ln[0].isdigit()), None)
    return int(df[2]), int(df[1]), wal, base


def ler_catalogo(saida: str) -> tuple[int, int, int, int] | None:
    campos = saida.split()
    return (int(campos[0]), int(campos[1]), int(campos[2]), int(campos[3])) if len(campos) == 4 else None


def amostrar(executar: Executar, hora: Callable[[], str] = agora, inst: Instancia = PADRAO) -> Amostra | None:
    """Uma amostra do contêiner de pé; `None` se a linha do `df` não veio (a amostra falha, a fase não).

    O rc NÃO decide (leitura do #385, A1): ele seria o do `du`, que sai com 1 quando um arquivo some no meio; com a base
    mexendo muito (a hipótese do estouro da 35), quase toda amostra viraria `None` e o aborto não dispararia. O erro
    do `du` vai para o `/dev/null` e o `exit 0` fecha; quem decide é a leitura do `df`."""
    disco = executar(["docker", "exec", inst.nome, "sh", "-c",
                      f"df -m {DADOS} | tail -1; du -sm {DADOS}/pg_wal {DADOS}/base 2>/dev/null; exit 0"])
    try:
        usado, total, wal, base = ler_disco(disco.stdout)
    except (IndexError, ValueError):
        return None
    cat = executar(["docker", "exec", inst.nome, "psql", "-U", "postgres", "-d", "farm", "-tA", "-F", " ", "-c",
                    CATALOGO_SQL])
    valores = ler_catalogo(cat.stdout) if cat.returncode == 0 else None
    return Amostra(hora(), usado, total, wal, base, *(valores or (None, None, None, None)))


def deve_abortar(a: Amostra | None, limite: float = LIMITE) -> bool:
    return a is not None and a.fracao >= limite


def ram_livre_gb() -> float | None:
    if os.name != "nt":
        return None
    import ctypes

    class _Estado(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

    e = _Estado()
    e.dwLength = ctypes.sizeof(_Estado)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(e))
    return round(e.ullAvailPhys / 1024 ** 3, 1)


def _executar(cmd: Sequence[str], prazo_s: float = PRAZO_DO_COMANDO_S) -> "subprocess.CompletedProcess[str]":
    """Roda o comando com prazo. Estourou: rc 124 (o do `timeout` do coreutils), sem levantar, e quem chama trata como
    falha comum (a amostra vira `None`, o `pg_isready` tenta de novo)."""
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}       # Git Bash não reescreve /var/lib/... no docker exec
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, env=env, check=False, timeout=prazo_s)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(cmd), 124, "", f"excedeu {prazo_s:.0f} s")


def python_do_pytest() -> Path:
    python = RAIZ / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return python if python.exists() else Path(sys.executable)   # worktree sem venv: o Python que roda o script


# --- 29.117: a árvore do pytest num Job Object (Windows) -----------------------------------------------------------
# O `taskkill /T` montava a árvore pelo ParentProcessId, que o Windows não limpa quando o pai morre: um processo alheio
# e antigo cujo pai morto tinha o PID do pytest de agora entrava na árvore e morria com /F. Regra: nunca matar o que não
# é comprovadamente descendente do nosso pytest. O job é essa prova: o pytest entra nele SUSPENSO (antes de rodar uma
# instrução, então nenhum worker do xdist nasce fora), e todo filho dele nasce dentro. Com KILL_ON_JOB_CLOSE, o único
# handle do job é o deste script: se ele morrer de fora (o pwsh pai fechado), o kernel mata a árvore inteira.
if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.OpenThread.restype = wintypes.HANDLE
    _k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.ResumeThread.restype = wintypes.DWORD
    _k32.ResumeThread.argtypes = [wintypes.HANDLE]
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.ReleaseMutex.argtypes = [wintypes.HANDLE]

    class _Basica(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                      "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class _Estendida(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basica), ("IoInfo", _Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _Thread(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
                    ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]

    _k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Thread)]
    _k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Thread)]

_CREATE_SUSPENDED = 0x4
_KILL_ON_JOB_CLOSE = 0x2000
_INFO_ESTENDIDA = 9           # JobObjectExtendedLimitInformation
_INVALIDO = ctypes.c_void_p(-1).value if os.name == "nt" else None


def _erro_win(o_que: str) -> OSError:
    return OSError(f"{o_que}: erro {ctypes.get_last_error()} do Windows")


def _criar_job() -> int:
    job = _k32.CreateJobObjectW(None, None)       # handle não herdável: o filho não segura o próprio job
    if not job:
        raise _erro_win("CreateJobObject")
    info = _Estendida()
    info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(job, _INFO_ESTENDIDA, ctypes.byref(info), ctypes.sizeof(info)):
        erro = _erro_win("SetInformationJobObject")
        _k32.CloseHandle(job)
        raise erro
    return job


def _retomar(pid: int) -> None:
    """Solta a thread principal do processo criado suspenso (um processo novo tem uma thread só)."""
    snap = _k32.CreateToolhelp32Snapshot(0x4, 0)   # TH32CS_SNAPTHREAD
    if not snap or snap == _INVALIDO:
        raise _erro_win("CreateToolhelp32Snapshot")
    try:
        t = _Thread()
        t.dwSize = ctypes.sizeof(_Thread)
        ok = _k32.Thread32First(snap, ctypes.byref(t))
        while ok:
            if t.th32OwnerProcessID == pid:
                h = _k32.OpenThread(0x2, False, t.th32ThreadID)   # THREAD_SUSPEND_RESUME
                if not h:
                    raise _erro_win("OpenThread")
                try:
                    if _k32.ResumeThread(h) == 0xFFFFFFFF:
                        raise _erro_win("ResumeThread")
                finally:
                    _k32.CloseHandle(h)
                return
            ok = _k32.Thread32Next(snap, ctypes.byref(t))
        raise OSError(f"a thread do processo {pid} não apareceu para ser retomada")
    finally:
        _k32.CloseHandle(snap)


def _entrar_no_job(job: int, proc: "subprocess.Popen[bytes]") -> None:
    if not _k32.AssignProcessToJobObject(job, int(proc._handle)):  # type: ignore[attr-defined]
        raise _erro_win("AssignProcessToJobObject")


def lancar_em_job(cmd: Sequence[str], **popen: object) -> "subprocess.Popen[bytes]":
    """`Popen` com a árvore presa: no Windows, num Job Object (o processo nasce suspenso, entra no job e só então
    roda); fora dele, num grupo de processos próprio. O job fica em `proc.job`.

    - Não entrou no job (o script já está num job que não aceita aninhamento): segue SEM job, e `proc.aviso_do_job`
      diz isso; no aborto, só o PID dele morre e a árvore pode sobrar. Nunca volta ao `taskkill /T`.
    - Não retomou (o `ResumeThread` falhou): o processo, ainda suspenso, morre com o job, e o erro sobe; um pytest
      suspenso para sempre não fica para trás."""
    if os.name != "nt":
        return subprocess.Popen(list(cmd), start_new_session=True, **popen)  # type: ignore[call-overload]
    job = _criar_job()
    flags = int(popen.pop("creationflags", 0)) | _CREATE_SUSPENDED  # type: ignore[call-overload]
    try:
        proc = subprocess.Popen(list(cmd), creationflags=flags, **popen)  # type: ignore[call-overload]
    except BaseException:
        _fechar_handle(job)
        raise
    aviso = None
    try:
        _entrar_no_job(job, proc)
    except OSError as exc:
        _fechar_handle(job)
        job, aviso = None, (f"o pytest não entrou no job ({exc}): segue sem job; no aborto só o PID dele morre, e os "
                            "filhos podem sobrar")
    try:
        _retomar(proc.pid)
    except BaseException:
        if job is not None:
            _terminar_job(job)
            _fechar_handle(job)
        if proc.poll() is None:
            proc.kill()                              # suspenso: não rodou nada
        proc.wait()
        raise
    proc.job = job  # type: ignore[attr-defined]
    proc.aviso_do_job = aviso  # type: ignore[attr-defined]
    return proc


def _terminar_job(job: int) -> bool:
    return bool(_k32.TerminateJobObject(job, 1))


def _fechar_handle(h: object) -> None:
    _k32.CloseHandle(h)


def fechar_job(proc: Processo) -> None:
    """Fecha o handle do job (com KILL_ON_JOB_CLOSE, o que sobrou da árvore morre junto)."""
    job = getattr(proc, "job", None)
    if job is not None and os.name == "nt":
        _fechar_handle(job)
        proc.job = None  # type: ignore[attr-defined]


def _lancar_pytest(arquivos: Sequence[str], saida: Path, dsn: str = DSN, workers: int = WORKERS_PADRAO) -> Processo:
    flags = getattr(subprocess, "IDLE_PRIORITY_CLASS", 0)
    arq = saida.open("w", encoding="utf-8")
    return lancar_em_job([str(python_do_pytest()), "-m", "pytest", "-q", "-n", str(workers), "-p", "no:cacheprovider",
                          *arquivos],
                         cwd=RAIZ / "backend", env={**os.environ, "TEST_DATABASE_URL": dsn},
                         stdout=arq, stderr=subprocess.STDOUT, creationflags=flags)


def matar_arvore(proc: Processo, executar: Executar) -> str | None:
    """O pytest com `-n 8` tem filhos: matar só o pai deixa os workers vivos e órfãos (lição do K-099). No Windows, o
    Job Object dele (29.117; nunca o `taskkill /T`, que pegava processo alheio pelo PID reusado); fora dele, o grupo
    inteiro (o `lancar_em_job` abre o pytest num grupo próprio). Devolve `None` quando a árvore morreu, ou a frase do
    que falhou, para a linha do aborto dizer."""
    job = getattr(proc, "job", None)
    if proc.poll() is not None:
        # Saiu sozinho entre a amostra e o aborto: nada a relatar. Um worker que tenha ficado no job morre com ele.
        if job is not None:
            _terminar_job(job)
        fechar_job(proc)
        return None
    if os.name == "nt":
        if job is None:
            # Sem job, não há prova de quem é descendente: só o próprio processo, pelo handle dele.
            proc.kill()  # type: ignore[attr-defined]
            problema: str | None = "o pytest não estava num job: só ele foi morto, os filhos podem ter ficado"
        else:
            problema = None if _terminar_job(job) else "o TerminateJobObject falhou"
    else:
        r = executar(["kill", "-KILL", "--", f"-{proc.pid}"])
        problema = None if r.returncode == 0 else f"o kill da árvore saiu com rc={r.returncode}"
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        problema = (problema + "; " if problema else "") + "o pytest não saiu em 30 s"
    fechar_job(proc)                                   # N1 da leitura do 29.117: o handle não espera o script sair
    return problema


def recriar(executar: Executar, dormir: Callable[[float], None], prazo_s: float = 240,
            relatar: Callable[[str], None] | None = None, inst: Instancia = PADRAO) -> float | None:
    """Contêiner novo com a configuração do script; segundos até aceitar conexão TCP, ou `None` no prazo. O erro do
    `docker run` (a imagem que falta com o `--pull=never`, a porta ocupada) vai ao `relatar`, se houver."""
    executar(["docker", "rm", "-f", inst.nome])
    run = executar(comando_docker_run(inst))
    if run.returncode != 0:
        if relatar is not None:
            erro = " ".join((run.stderr or run.stdout or "").split())[:300]
            relatar(f"docker run saiu com rc={run.returncode}: {erro or 'sem mensagem'}")
        return None
    inicio = time.monotonic()
    while time.monotonic() - inicio < prazo_s:
        # TCP de propósito: o servidor temporário do `initdb` só escuta o socket local, e aceitá-lo deu 1913 erros
        # "the database system is starting up" na suíte 18.
        if executar(["docker", "exec", inst.nome, "pg_isready", "-h", "127.0.0.1", "-p", "5432", "-U", "postgres",
                     "-d", "farm"]).returncode == 0:
            return round(time.monotonic() - inicio, 1)
        dormir(2)
    return None


def ultima_contagem(saida: Path) -> str:
    try:
        linhas = saida.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return next((ln.strip() for ln in reversed(linhas) if " passed" in ln or " failed" in ln or " error" in ln), "")


def rodar_parte(rotulo: str, arquivos: Sequence[str], saida: Path, relatar: Callable[[str], None], *,
                executar: Executar = _executar, lancar: Callable[[Sequence[str], Path], Processo] = _lancar_pytest,
                dormir: Callable[[float], None] = time.sleep, intervalo_s: float = INTERVALO_S,
                limite: float = LIMITE, inst: Instancia = PADRAO) -> int:
    """Uma parte: contêiner novo, pytest, amostras. 0 verde; o rc do pytest se vermelho; 3 abortada pelo disco;
    8 se o contêiner não aceitou conexão; 11 se o pytest não subiu (29.117: não retomou do suspenso).

    O1 do 29.113: o que interromper a parte (Ctrl-C, um `OSError` do `--resumo`, qualquer exceção) passa pelo
    `except`, que mata a árvore do pytest se ela ainda estiver viva. Sem isso, o pytest `-n 8` e os workers seguiam
    contra o contêiner, e no Windows o filho não morre com o pai (a contaminação do K-101). O pai morto DE FORA (o pwsh
    que o chamou fechado) não passa por aqui: isso pede um Job Object com KILL_ON_JOB_CLOSE, em item próprio."""
    subida = recriar(executar, dormir, relatar=relatar, inst=inst)
    if subida is None:
        relatar(f"{rotulo} o contêiner não aceitou conexão {agora()}")
        return 8
    onde = "" if inst == PADRAO else f" conteiner={inst.nome}:{inst.porta} tmpfs={inst.tmpfs_mb}MB"
    relatar(f"{rotulo} aceitou em {subida} s; inicio {agora()} arquivos={len(arquivos)} python={python_do_pytest()}{onde}")
    try:
        proc = lancar(arquivos, saida)
    except OSError as exc:
        relatar(f"{rotulo} o pytest NÃO SUBIU: {exc} {agora()}")
        return 11
    aviso = getattr(proc, "aviso_do_job", None)
    if aviso:
        relatar(f"{rotulo} ATENÇÃO: {aviso}")
    try:
        return _acompanhar(rotulo, proc, saida, relatar, executar=executar, dormir=dormir, intervalo_s=intervalo_s,
                           limite=limite, inst=inst)
    except BaseException:
        # Só na interrupção: nas saídas normais o pytest já terminou (`wait`) ou a árvore já foi morta (o aborto).
        if proc.poll() is None:
            problema = matar_arvore(proc, executar)
            try:
                relatar(f"{rotulo} INTERROMPIDA {agora()}: a árvore do pytest foi morta"
                        + (f" | ATENÇÃO: {problema}" if problema else ""))
            except Exception:  # noqa: BLE001 — o relato que falhou pode ser a própria causa (o `--resumo`)
                print(f"{rotulo} INTERROMPIDA: a árvore do pytest foi morta", file=sys.stderr, flush=True)
        fechar_job(proc)                               # já saído: um worker que tenha sobrado morre com o job
        raise


def _acompanhar(rotulo: str, proc: Processo, saida: Path, relatar: Callable[[str], None], *, executar: Executar,
                dormir: Callable[[float], None], intervalo_s: float, limite: float, inst: Instancia = PADRAO) -> int:
    """O laço das amostras de uma parte com o pytest já lançado (o `rodar_parte` cuida de matar a árvore)."""
    pico: Amostra | None = None
    sem_amostra = 0
    while proc.poll() is None:
        dormir(intervalo_s)
        a = amostrar(executar, inst=inst)
        if a is not None and (pico is None or a.usado_mb > pico.usado_mb):
            pico = a
        sem_amostra = 0 if a is not None else sem_amostra + 1
        if sem_amostra == SEM_AMOSTRA_AVISO:
            # Uma linha por série: sem o df, o aborto pelo disco não tem com o que decidir.
            relatar(f"{rotulo} SEM AMOSTRA do df há {sem_amostra * intervalo_s:.0f} s ({agora()}): o aborto pelo disco "
                    "está cego até a amostra voltar")
        if deve_abortar(a, limite):
            if proc.poll() is not None:
                # N3 do 29.113: o pytest saiu sozinho entre a amostra e o aborto. O resultado é o dele (verde ou
                # vermelho), não "ABORTADA"; o disco alto fica no pico, abaixo.
                break
            problema = matar_arvore(proc, executar)
            relatar(f"{rotulo} ABORTADA pelo disco: {a.linha() if a else ''}"
                    + (f" | ATENÇÃO: {problema}" if problema else ""))
            return 3
    rc = proc.wait()
    fechar_job(proc)                                   # um worker que tenha sobrado morre com o job (29.117)
    relatar(f"{rotulo} rc={rc} fim {agora()} | {ultima_contagem(saida)}")
    fim = amostrar(executar, inst=inst)
    relatar(f"{rotulo} pico: {pico.linha() if pico else 'sem amostra'}")
    relatar(f"{rotulo} no fim: {fim.linha() if fim else 'sem amostra'}")
    return rc


_TRAVA: list[object] = []      # o mutex (Windows) ou o arquivo com flock: vivo enquanto o processo vive
NOME_DA_TRAVA = NOME            # os testes trocam (N3 da leitura do 29.117): nunca a trava de uma rodada real
ESCOPO_DA_TRAVA: list[str] = []  # o que a trava disse, para a linha da rodada
_ACESSO_NEGADO = 5              # ERROR_ACCESS_DENIED


def _criar_mutex(nome: str) -> int | None:
    """O handle do mutex, ou `None` quando ele JÁ EXISTE com uma DACL que nos nega acesso (erro 5): criado por
    outra rodada, de outra sessão ou de outro usuário. Criar um mutex no `Global\\` não pede privilégio; o erro 5 ali
    é o objeto alheio, não falta de direito de criar."""
    h = _k32.CreateMutexW(None, False, nome)
    if not h:
        if ctypes.get_last_error() == _ACESSO_NEGADO:
            return None
        raise _erro_win(f"CreateMutex {nome}")
    return h


def tentar_travar() -> bool:
    """Nota 3 da leitura do 29.113: uma rodada por vez na máquina (o nome do contêiner e a porta são fixos, e o
    `recriar` da segunda apagava o contêiner da primeira). Mutex nomeado no Windows e `flock` fora dele: o sistema
    solta os dois quando o processo morre, então não há trava velha. `False` = outra rodada está em curso."""
    if _TRAVA:
        return True
    if os.name == "nt":
        # Só o Global\\, que vale entre sessões. M1 da leitura do 29.117: recuar ao Local\\ no erro 5 deixava duas
        # rodadas correrem (o mutex existe, é da outra). O erro 5 é recusa (rc 10); outro erro sobe (rc 12): nunca
        # "segue sem trava" calado.
        nome = f"Global\\{NOME_DA_TRAVA}"
        h = _criar_mutex(nome)
        if h is None:
            ESCOPO_DA_TRAVA[:] = [f"{nome} existe e nega acesso (erro 5): é de outra rodada, de outra sessão ou usuário"]
            return False
        ESCOPO_DA_TRAVA[:] = [nome]
        if _k32.WaitForSingleObject(h, 0) not in (0, 0x80):       # WAIT_OBJECT_0, WAIT_ABANDONED
            _fechar_handle(h)
            return False
        _TRAVA.append(h)
        return True
    import fcntl
    import tempfile

    arq = open(Path(tempfile.gettempdir()) / f"{NOME_DA_TRAVA}.trava", "w")  # noqa: SIM115 — fica aberto: é a trava
    try:
        fcntl.flock(arq, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        arq.close()
        return False
    _TRAVA.append(arq)
    return True


def soltar_trava() -> None:
    while _TRAVA:
        t = _TRAVA.pop()
        if os.name == "nt":
            _k32.ReleaseMutex(t)
            _fechar_handle(t)
        else:
            t.close()  # type: ignore[attr-defined]


def parar(executar: Executar, relatar: Callable[[str], None], inst: Instancia = PADRAO) -> bool:
    """N1 do 29.113: o `docker stop` que falha (ou estoura o prazo) deixava o tmpfs de 4 GB de pé sem aviso. Só para e
    relata; o `docker rm -f` fica como sugestão na linha, para quem confere antes de apagar."""
    r = executar(["docker", "stop", inst.nome])
    if r.returncode == 0:
        return True
    if "no such container" in (r.stderr or r.stdout or "").lower():
        return True                                    # o `docker run` nem subiu (rc 8): não há o que parar
    erro = " ".join((r.stderr or r.stdout or "").split())[:200]
    relatar(f"ATENÇÃO: docker stop {inst.nome} saiu com rc={r.returncode} ({erro or 'sem mensagem'}); o tmpfs de "
            f"{inst.tmpfs_mb // 1024} GB pode seguir de pé: confira com `docker ps` e pare com `docker rm -f {inst.nome}`")
    return False


def workers_por_instancia(paralelo: int, pedido: int = 0) -> int:
    """`-n` de cada pytest: o pedido (`--workers`); sem pedido, 8 na rodada serial (a de sempre) e 6 por instância na
    paralela (a proposta do 29.197: 2 x 6 = 12 workers contra os 8 de hoje, em dois servidores)."""
    if pedido > 0:
        return pedido
    return WORKERS_PADRAO if paralelo == 1 else 6


def rodar_partes(fatias: Sequence[Sequence[str]], insts: Sequence[Instancia], pasta: Path, relatar: Callable[[str], None],
                 subiram: list[Instancia], *, workers: int = WORKERS_PADRAO, **opcoes: object) -> int:
    """As partes numa fila, uma instância (contêiner) por vez em cada fio. 0 se todas verdes; senão o rc da parte vermelha
    de MENOR número (9 = sem RAM). `subiram` recebe cada instância que chegou a ser usada, para o `main` parar todas.

    - Uma instância: roda no próprio fio, byte a byte como antes do 29.197 (a exceção sobe ao `main`, que para o contêiner).
    - Várias: um fio por instância, cada um com o seu contêiner, a sua porta e o seu DSN. Parte vermelha NÃO mata a parte
      que o outro fio está rodando (ela termina e é relatada; é o que dá a medida limpa); só impede que saiam novas partes da
      fila. Interrompido de fora, os fios são daemon e o Job Object de cada pytest (KILL_ON_JOB_CLOSE) leva a árvore junto.
    - `opcoes` vai ao `rodar_parte` (executar, dormir, intervalo_s, limite, lancar)."""
    fila = list(enumerate(fatias, 1))
    trava = threading.Lock()
    rcs: dict[int, int] = {}
    vermelho = threading.Event()

    def seguro(linha: str) -> None:
        with trava:
            relatar(linha)

    def trabalhar(inst: Instancia) -> None:
        extra: dict[str, object] = dict(opcoes)
        if "lancar" not in extra and (inst != PADRAO or workers != WORKERS_PADRAO):
            extra["lancar"] = lambda arquivos, saida: _lancar_pytest(arquivos, saida, inst.dsn, workers)
        if inst != PADRAO:
            extra["inst"] = inst
        while not vermelho.is_set():
            with trava:
                if not fila:
                    return
                i, f = fila.pop(0)
            rotulo = f"pg parte {i}/{len(fatias)}"
            livre = ram_livre_gb()
            if livre is not None and livre < RAM_MINIMA_GB:
                seguro(f"{rotulo} NÃO RODOU: {livre} GB livres, abaixo de {RAM_MINIMA_GB} {agora()}")
                rcs[i] = 9
                vermelho.set()
                return
            with trava:
                if inst not in subiram:
                    subiram.append(inst)
            try:
                rc = rodar_parte(rotulo, f, pasta / f"pg_parte{i}.txt", seguro if len(insts) > 1 else relatar, **extra)  # type: ignore[arg-type]
            except BaseException as exc:  # noqa: BLE001 — num fio, a exceção não pode virar verde por omissão
                if len(insts) == 1:
                    raise
                seguro(f"{rotulo} ERRO no fio de {inst.nome}: {type(exc).__name__}: {exc} {agora()}")
                rcs[i] = 13
                vermelho.set()
                return
            rcs[i] = rc
            if rc != 0:
                seguro(f"{rotulo} PAROU (rc={rc}); as partes seguintes não rodaram")
                vermelho.set()
                return

    if len(insts) == 1:
        trabalhar(insts[0])
    else:
        fios = [threading.Thread(target=trabalhar, args=(inst,), daemon=True, name=f"pg-{inst.nome}") for inst in insts]
        for fio in fios:
            fio.start()
        for fio in fios:
            fio.join()
    vermelha = next((rcs[i] for i in sorted(rcs) if rcs[i] != 0), 0)
    if vermelha == 0 and len(rcs) < len(fatias):
        seguro(f"pg: só {len(rcs)} de {len(fatias)} partes terminaram, sem nenhuma vermelha: resultado NÃO verde {agora()}")
        return 13
    return vermelha


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lista", type=Path, help="arquivo com um teste por linha, relativo a backend/")
    ap.add_argument("--partes", type=int, default=2)
    ap.add_argument("--resumo", type=Path, help="acrescenta cada linha do resumo também a este arquivo")
    ap.add_argument("--saidas", type=Path, default=None, help="pasta das saídas do pytest (padrão: a do resumo)")
    ap.add_argument("--paralelo", type=int, default=1,
                    help=f"contêineres ao mesmo tempo (1 a {PARALELO_MAXIMO}; 1 = como sempre). O tmpfs total (4 GB) se divide entre eles "
                         "e cada um leva um fio e uma porta; as partes saem da mesma fila (29.197)")
    ap.add_argument("--workers", type=int, default=0,
                    help="`-n` do pytest de cada contêiner (padrão: 8 com 1 contêiner, 6 por contêiner com mais)")
    ap.add_argument("--simular", action="store_true", help="só diz o que faria")
    ap.add_argument("--amostrar", action="store_true", help="uma amostra do contêiner de pé e sai")
    args = ap.parse_args(argv)

    def relatar(linha: str) -> None:
        print(linha, flush=True)
        if args.resumo:
            with args.resumo.open("a", encoding="utf-8") as f:
                f.write(linha + "\n")

    if args.amostrar:
        a = amostrar(_executar)
        relatar(a.linha() if a else f"{NOME}: sem amostra (o contêiner está de pé?)")
        return 0 if a else 1
    if not args.lista:
        ap.error("--lista é obrigatória (ou --amostrar)")
    backend = RAIZ / "backend"
    todos = [ln.strip() for ln in args.lista.read_text(encoding="utf-8").splitlines()
             if ln.strip() and "conftest" not in ln and (backend / ln.strip()).exists()]
    fatias = partes(todos, args.partes)
    try:
        insts = instancias(args.paralelo)
    except ValueError as exc:
        ap.error(str(exc))
    if args.workers < 0:
        ap.error("--workers >= 0")
    workers = workers_por_instancia(len(insts), args.workers)
    if args.simular:
        for inst in insts:
            relatar("docker: " + " ".join(comando_docker_run(inst)))
        if len(insts) > 1 or workers != WORKERS_PADRAO:
            relatar(f"rodada: paralelo={len(insts)} workers={workers} por contêiner "
                    f"(tmpfs {', '.join(f'{i.nome}:{i.porta}={i.tmpfs_mb}MB' for i in insts)})")
        for i, f in enumerate(fatias, 1):
            relatar(f"parte {i}/{len(fatias)}: {len(f)} arquivos (de {len(todos)}), {f[0]} … {f[-1]}")
        return 0
    pasta = args.saidas or (args.resumo.parent if args.resumo else Path.cwd())
    ESCOPO_DA_TRAVA.clear()
    try:
        livre_da_trava = tentar_travar()
    except OSError as exc:
        relatar(f"NÃO RODOU: a trava de uma rodada por vez não pôde ser criada ({exc}); nenhum contêiner foi tocado "
                f"{agora()}")
        return 12
    if not livre_da_trava:
        detalhe = f"; {ESCOPO_DA_TRAVA[0]}" if ESCOPO_DA_TRAVA and "nega acesso" in ESCOPO_DA_TRAVA[0] else ""
        relatar(f"NÃO RODOU: outra rodada do pg-rapido está em curso (trava {NOME_DA_TRAVA}{detalhe}); nenhum "
                f"contêiner foi tocado {agora()}")
        return 10
    subiram: list[Instancia] = []
    try:
        rc = rodar_partes(fatias, insts[:max(1, len(fatias))], pasta, relatar, subiram, workers=workers)
        if rc != 0:
            return rc
        extra = "" if len(insts) == 1 and workers == WORKERS_PADRAO else f" (paralelo {len(insts)}, -n {workers} por contêiner)"
        relatar(f"pg verde: {len(fatias)} partes, {len(todos)} arquivos{extra} {agora()}")
        return 0
    finally:
        # Q2 da leitura do 29.113: interrompido (Ctrl-C, exceção), o contêiner também para; antes, o tmpfs de 4 GB
        # ficava preso na RAM até a próxima rodada. Sem parte iniciada, não há contêiner a parar. Com várias instâncias,
        # para cada uma que subiu (29.197).
        for inst in subiram:
            # N4 da leitura: o `parar` (ou o relato dele) que levanta aqui SUBSTITUIRIA a exceção original, que é o
            # motivo da interrupção. Cai no stderr, como no `rodar_parte`.
            try:
                parar(_executar, relatar) if inst == PADRAO else parar(_executar, relatar, inst)
            except BaseException as exc:  # noqa: BLE001 — inclusive um segundo Ctrl-C durante o stop
                print(f"ATENÇÃO: o docker stop {inst.nome} não terminou ({type(exc).__name__}: {exc}); confira com "
                      f"`docker ps`", file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
