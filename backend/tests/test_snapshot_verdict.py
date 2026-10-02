"""T.2: o veredito do snapshot durante o boot (`DeviceManager._snapshot_verdict`) tinha só dublê (`lambda _rt: True`).

O emulador decide sozinho se carrega o snapshot e, se não carregar, segue em boot a frio: só o LOG diz qual dos dois
aconteceu, e com a máquina carregada a linha pode demorar. Por isso o veredito é tri-estado, e `None` ("o log ainda não
disse") não pode virar nem sucesso nem recusa: `_wait_boot` só reage a `True`/`False`. Teste puro: um arquivo de log em
`tmp_path`, o `rt` do harness, nenhum emulador.
"""
from __future__ import annotations

from typing import Any

import pytest

from .conftest import Harness

PREFIXO = "emulator: INFO: boot properties applied\n"
CARREGADO = "emulator: Successfully loaded snapshot 'poc'\n"
RECUSADO = "emulator: cannot load snapshot 'poc': hardware mismatch\n"
CARREGANDO = "emulator: Loading snapshot 'poc'...\n"


def _devs_e_rt(h: Harness) -> tuple[Any, Any]:
    assert h.state is not None
    return h.state.devices, h.state.devices.get("android-01")


def _log(h: Harness, rt: Any, texto: str, *, offset: int = 0) -> None:
    caminho = h.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(texto.encode("utf-8"))
    rt.boot_log_offset = offset


async def test_log_com_snapshot_carregado_e_verdadeiro(harness: Harness) -> None:
    devs, rt = _devs_e_rt(harness)
    _log(harness, rt, PREFIXO + CARREGADO)
    assert devs._snapshot_verdict(rt) is True


@pytest.mark.parametrize("frase", ["emulator: cannot load snapshot 'poc': hardware mismatch",
                                   "emulator: Failed to load snapshot 'poc'"])
async def test_as_duas_frases_de_recusa_dao_falso(harness: Harness, frase: str) -> None:
    devs, rt = _devs_e_rt(harness)
    _log(harness, rt, PREFIXO + frase + "\n")
    assert devs._snapshot_verdict(rt) is False


async def test_log_que_ainda_nao_disse_nada_e_none_e_nao_sucesso_nem_recusa(harness: Harness) -> None:
    devs, rt = _devs_e_rt(harness)
    _log(harness, rt, PREFIXO + CARREGANDO)                # carregando, sem veredito
    assert devs._snapshot_verdict(rt) is None
    _log(harness, rt, "")                                  # e o log vazio
    assert devs._snapshot_verdict(rt) is None


async def test_texto_anterior_ao_offset_do_boot_e_ignorado(harness: Harness) -> None:
    """O log é anexado entre boots: a linha do boot ANTERIOR não pode valer para este. O offset é o tamanho do arquivo
    no instante em que este boot começou (`rt.boot_log_offset`)."""
    devs, rt = _devs_e_rt(harness)
    n = lambda s: len(s.encode("utf-8"))                   # noqa: E731
    _log(harness, rt, CARREGADO + CARREGANDO, offset=n(CARREGADO))
    assert devs._snapshot_verdict(rt) is None              # o "carregado" é do boot de antes
    # e o contrário: a recusa velha não condena o boot novo, que carregou
    _log(harness, rt, RECUSADO + CARREGADO, offset=n(RECUSADO))
    assert devs._snapshot_verdict(rt) is True
    # sem offset (0), a mesma recusa velha vale: é a prova de que o offset é quem decide
    _log(harness, rt, RECUSADO, offset=0)
    assert devs._snapshot_verdict(rt) is False


async def test_arquivo_de_log_ausente_e_none(harness: Harness) -> None:
    devs, rt = _devs_e_rt(harness)
    (harness.cfg.logs_dir / f"emulator-{rt.avd_name}.log").unlink(missing_ok=True)
    assert devs._snapshot_verdict(rt) is None
