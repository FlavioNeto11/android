"""Item 10.2 — achado #144: rotação de log cobria só `backend.log`; o resto crescia ou se perdia.

Este arquivo cobre o pedaço SEGURO de testar sem processo real: `backend.err.log` parava de duplicar cada linha
do `backend.log` (já rotacionado e retido) quando não há terminal na frente — é só quando alguém roda
`python -m app.main` à mão que o console volta a fazer sentido.
"""
from __future__ import annotations

import logging
import logging.handlers

from app.main import setup_logging

from .conftest import Harness


def _restaurar(root: logging.Logger, originais: list[logging.Handler]) -> None:
    root.handlers = originais


async def test_sem_terminal_o_console_nao_entra_so_o_arquivo_rotacionado(harness: Harness, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = logging.getLogger()
    originais = list(root.handlers)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    try:
        setup_logging(harness.cfg)
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], logging.handlers.TimedRotatingFileHandler)  # type: ignore[attr-defined]
    finally:
        _restaurar(root, originais)


async def test_com_terminal_o_console_continua_junto_do_arquivo(harness: Harness, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = logging.getLogger()
    originais = list(root.handlers)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    try:
        setup_logging(harness.cfg)
        assert len(root.handlers) == 2
        tipos = {type(h) for h in root.handlers}
        assert logging.StreamHandler in tipos
    finally:
        _restaurar(root, originais)
