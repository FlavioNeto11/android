"""Redação do que NÃO vai ao Trello (servidor de terceiro): handles de conta, nomes das personas, IPs.

Os nomes e handles das personas são lidos do banco do central em modo ro quando ele existe (nunca ficam no
repositório); há uma lista fixa de reserva. Hash de commit com "@" (ex.: @ea1df281) é referência de código e fica.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
_RESERVA = {"lucas", "bruno", "andre", "andré", "felipe", "juliana", "beatriz", "carla", "zilda"}


def _nomes_sensiveis() -> list[str]:
    nomes: set[str] = set(_RESERVA)
    try:
        caminho = str(RAIZ / "data" / "poc.sqlite3").replace("\\", "/")
        con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        for (nome,) in con.execute("select name from personas"):
            if isinstance(nome, str) and nome.strip():
                nomes.add(nome.strip())
                nomes.update(p for p in nome.split() if len(p) > 2)
        for (handle,) in con.execute("select handle from profile_accounts"):
            if isinstance(handle, str) and handle.strip():
                nomes.add(handle.strip().lstrip("@"))
        con.close()
    except Exception:  # noqa: BLE001 - sem banco, fica a reserva
        pass
    return sorted(nomes, key=len, reverse=True)


_HANDLE = re.compile(r"(?<![\w/])@(?![0-9a-f]{7,8}\b)[A-Za-z0-9_.]{3,}")
#: nome.sobrenome seguido de 4+ dígitos é handle de persona mesmo sem o "@"
_HANDLE_SEM_ARROBA = re.compile(r"\b[a-z]+\.[a-z]+\d{4,}\b", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PERSONA = re.compile(r"\b(" + "|".join(re.escape(n) for n in _nomes_sensiveis()) + r")\b", re.I)


def redigir(texto: str) -> str:
    t = _HANDLE.sub("@[conta]", texto or "")
    t = _HANDLE_SEM_ARROBA.sub("[conta]", t)
    t = _PERSONA.sub("[persona]", t)
    return _IP.sub("[ip]", t)


def lista(valor: object, n: int) -> list[str]:
    """`arquivos`/`testes` do estado vêm como lista OU como string separada por vírgula: nunca fatiar string."""
    if isinstance(valor, str):
        partes = [x.strip() for x in re.split(r"[,;\n]+", valor) if x.strip()]
    elif isinstance(valor, (list, tuple)):
        partes = [str(x).strip() for x in valor if str(x).strip()]
    else:
        partes = []
    return partes[:n]
