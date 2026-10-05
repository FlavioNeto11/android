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

Executar = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class Processo(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%SZ")


def comando_docker_run() -> list[str]:
    cmd = ["docker", "run", "-d", "--name", NOME, "-e", "POSTGRES_PASSWORD=teste", "-e", "POSTGRES_DB=farm",
           "-p", f"127.0.0.1:{PORTA}:5432", "--tmpfs", f"{DADOS}:rw,size={TMPFS_MB // 1024}g", IMAGEM]
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
    wal_mb: int
    base_mb: int
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
                f" wal={self.wal_mb} base={self.base_mb}{cat}")


def ler_disco(saida: str) -> tuple[int, int, int, int]:
    """Saída de `df -m DADOS | tail -1; du -sm DADOS/pg_wal DADOS/base` → (usado, total, wal, base) em MB."""
    linhas = [ln.split() for ln in saida.strip().splitlines() if ln.strip()]
    df, wal, base = linhas[0], linhas[1], linhas[2]
    return int(df[2]), int(df[1]), int(wal[0]), int(base[0])


def ler_catalogo(saida: str) -> tuple[int, int, int, int] | None:
    campos = saida.split()
    return (int(campos[0]), int(campos[1]), int(campos[2]), int(campos[3])) if len(campos) == 4 else None


def amostrar(executar: Executar, hora: Callable[[], str] = agora) -> Amostra | None:
    """Uma amostra do contêiner de pé; `None` se ele não respondeu (a amostra falha, a fase não)."""
    disco = executar(["docker", "exec", NOME, "sh", "-c",
                      f"df -m {DADOS} | tail -1; du -sm {DADOS}/pg_wal {DADOS}/base"])
    if disco.returncode != 0:
        return None
    try:
        usado, total, wal, base = ler_disco(disco.stdout)
    except (IndexError, ValueError):
        return None
    cat = executar(["docker", "exec", NOME, "psql", "-U", "postgres", "-d", "farm", "-tA", "-F", " ", "-c",
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


def _executar(cmd: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}       # Git Bash não reescreve /var/lib/... no docker exec
    return subprocess.run(list(cmd), capture_output=True, text=True, env=env, check=False)


def _lancar_pytest(arquivos: Sequence[str], saida: Path) -> Processo:
    python = RAIZ / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    flags = getattr(subprocess, "IDLE_PRIORITY_CLASS", 0)
    arq = saida.open("w", encoding="utf-8")
    return subprocess.Popen([str(python), "-m", "pytest", "-q", "-n", "8", "-p", "no:cacheprovider", *arquivos],
                            cwd=RAIZ / "backend", env={**os.environ, "TEST_DATABASE_URL": DSN},
                            stdout=arq, stderr=subprocess.STDOUT, creationflags=flags)


def matar_arvore(proc: Processo, executar: Executar) -> None:
    """O pytest com `-n 8` tem filhos: matar só o pai deixa os workers vivos e órfãos (lição do K-099)."""
    if os.name == "nt":
        executar(["taskkill", "/T", "/F", "/PID", str(proc.pid)])
    else:
        os.kill(proc.pid, 9)
    try:
        proc.wait(timeout=30)
    except Exception:
        pass


def recriar(executar: Executar, dormir: Callable[[float], None], prazo_s: float = 240) -> float | None:
    """Contêiner novo com a configuração do script; segundos até aceitar conexão TCP, ou `None` no prazo."""
    executar(["docker", "rm", "-f", NOME])
    if executar(comando_docker_run()).returncode != 0:
        return None
    inicio = time.monotonic()
    while time.monotonic() - inicio < prazo_s:
        # TCP de propósito: o servidor temporário do `initdb` só escuta o socket local, e aceitá-lo deu 1913 erros
        # "the database system is starting up" na suíte 18.
        if executar(["docker", "exec", NOME, "pg_isready", "-h", "127.0.0.1", "-p", "5432", "-U", "postgres",
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
                limite: float = LIMITE) -> int:
    """Uma parte: contêiner novo, pytest, amostras. 0 verde; o rc do pytest se vermelho; 3 abortada pelo disco;
    8 se o contêiner não aceitou conexão."""
    subida = recriar(executar, dormir)
    if subida is None:
        relatar(f"{rotulo} o contêiner não aceitou conexão {agora()}")
        return 8
    relatar(f"{rotulo} aceitou em {subida} s; inicio {agora()} arquivos={len(arquivos)}")
    proc = lancar(arquivos, saida)
    pico: Amostra | None = None
    while proc.poll() is None:
        dormir(intervalo_s)
        a = amostrar(executar)
        if a is not None and (pico is None or a.usado_mb > pico.usado_mb):
            pico = a
        if deve_abortar(a, limite):
            matar_arvore(proc, executar)
            relatar(f"{rotulo} ABORTADA pelo disco: {a.linha() if a else ''}")
            return 3
    rc = proc.wait()
    relatar(f"{rotulo} rc={rc} fim {agora()} | {ultima_contagem(saida)}")
    fim = amostrar(executar)
    relatar(f"{rotulo} pico: {pico.linha() if pico else 'sem amostra'}")
    relatar(f"{rotulo} no fim: {fim.linha() if fim else 'sem amostra'}")
    return rc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lista", type=Path, help="arquivo com um teste por linha, relativo a backend/")
    ap.add_argument("--partes", type=int, default=2)
    ap.add_argument("--resumo", type=Path, help="acrescenta cada linha do resumo também a este arquivo")
    ap.add_argument("--saidas", type=Path, default=None, help="pasta das saídas do pytest (padrão: a do resumo)")
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
    if args.simular:
        relatar("docker: " + " ".join(comando_docker_run()))
        for i, f in enumerate(fatias, 1):
            relatar(f"parte {i}/{len(fatias)}: {len(f)} arquivos (de {len(todos)}), {f[0]} … {f[-1]}")
        return 0
    pasta = args.saidas or (args.resumo.parent if args.resumo else Path.cwd())
    for i, f in enumerate(fatias, 1):
        rotulo = f"pg parte {i}/{len(fatias)}"
        livre = ram_livre_gb()
        if livre is not None and livre < RAM_MINIMA_GB:
            relatar(f"{rotulo} NÃO RODOU: {livre} GB livres, abaixo de {RAM_MINIMA_GB} {agora()}")
            return 9
        rc = rodar_parte(rotulo, f, pasta / f"pg_parte{i}.txt", relatar)
        if rc != 0:
            relatar(f"{rotulo} PAROU (rc={rc}); as partes seguintes não rodaram")
            _executar(["docker", "stop", NOME])
            return rc
    _executar(["docker", "stop", NOME])
    relatar(f"pg verde: {len(fatias)} partes, {len(todos)} arquivos {agora()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
