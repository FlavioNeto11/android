"""Trava do funil (29.196): teste marcado `carga` pula enquanto um funil roda.

Um funil de corte mede CPU e pressao nos aparelhos; teste de script que queima CPU ou sobe muitos subprocessos (queimadores do wrapper, o
amostrador de verdade, powershell em serie) durante ele contamina a medida (aconteceu no 59: +12 % de python por 2 min na etapa do PG).
Entao esses arquivos levam `pytestmark = pytest.mark.carga` e este conftest os PULA quando existe a trava do funil:

  - a trava e o arquivo `data/funil-ativo.json` do CHECKOUT CENTRAL (worktrees e checkouts dividem o mesmo; `git rev-parse --git-common-dir`),
    escrito pelo `scripts/funil.ps1` quando o encadeamento comeca e apagado quando acaba (`FARM_FUNIL_TRAVA` troca o caminho);
  - a trava so vale com o processo dela vivo (nunca `os.kill(pid, 0)`: no Windows isso MATA o processo) e com menos de 12 h;
  - o proprio funil roda tudo com `FARM_FUNIL_RODANDO=1` e entao os testes de carga rodam normalmente;
  - `FARM_FUNIL_CARGA=1` roda mesmo assim (de proposito, sabendo que mexe na medida).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ESCOPO_MAXIMO_S = 12 * 3600


def caminho_da_trava() -> Path:
    env = os.environ.get("FARM_FUNIL_TRAVA")
    if env:
        return Path(env)
    aqui = Path(__file__).resolve().parent
    try:
        comum = subprocess.run(["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True, cwd=aqui, timeout=10).stdout.strip()
        base = Path(comum) if Path(comum).is_absolute() else (aqui / comum)
        return base.resolve().parent / "data" / "funil-ativo.json"
    except (OSError, subprocess.SubprocessError):
        return aqui.parents[1] / "data" / "funil-ativo.json"


def processo_vivo(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x1000, 0, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        codigo = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(codigo))) and codigo.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def funil_ativo() -> dict | None:
    """O conteudo da trava se ha um funil vivo; senao None. Qualquer duvida (arquivo ruim, sem pid) = sem funil."""
    try:
        trava = caminho_da_trava()
        dados = json.loads(trava.read_text(encoding="utf-8-sig"))
        if time.time() - trava.stat().st_mtime > ESCOPO_MAXIMO_S:
            return None
        return dados if processo_vivo(int(dados["pid"])) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "carga: gera carga de CPU ou muitos subprocessos; pula enquanto um funil roda (trava em data/funil-ativo.json)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("FARM_FUNIL_RODANDO") or os.environ.get("FARM_FUNIL_CARGA"):
        return
    ativo = funil_ativo()
    if ativo is None:
        return
    motivo = (f"funil ativo (pid {ativo.get('pid')}, run {Path(str(ativo.get('run', '?'))).name}): teste de carga pula; "
              "rode depois do funil ou com FARM_FUNIL_CARGA=1")
    pular = pytest.mark.skip(reason=motivo)
    for item in items:
        if "carga" in item.keywords:
            item.add_marker(pular)
