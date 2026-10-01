"""Trava de rede: dentro do contexto, qualquer tentativa de abrir conexão levanta `NetworkBlocked`.

Usada pelo benchmark em toda rodada que NÃO é `--provider jev`, e pelos testes. É defesa em profundidade — o
`RealJevProvider` já não abre rede sem `enabled=True`. Conexões locais (AF_UNIX / loopback) também são bloqueadas:
o benchmark offline não precisa de nenhuma.
"""
from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator


class NetworkBlocked(RuntimeError):
    pass


@contextmanager
def no_network() -> Iterator[None]:
    originals = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)

    def _blocked(*_a, **_k):
        raise NetworkBlocked("tentativa de rede bloqueada pelo piloto JEV (modo offline)")

    socket.socket.connect = _blocked          # type: ignore[assignment,method-assign]
    socket.socket.connect_ex = _blocked       # type: ignore[assignment,method-assign]
    socket.create_connection = _blocked       # type: ignore[assignment]
    socket.getaddrinfo = _blocked             # type: ignore[assignment]
    try:
        yield
    finally:
        (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo) = originals
