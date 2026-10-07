"""Trava do funil (29.196): o conftest pula `carga` so com a trava viva. Sem CPU, sem subprocesso (rapido; nao leva o marcador `carga`)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import conftest as trava_mod

pytestmark = pytest.mark.skipif(os.name != "nt", reason="a checagem do pid vivo e do Windows")


class Item:
    def __init__(self, carga: bool):
        self.keywords = {"carga": 1} if carga else {}
        self.marcas: list = []

    def add_marker(self, m):
        self.marcas.append(m)


def _grava(caminho: Path, pid: int) -> None:
    caminho.write_text(json.dumps({"pid": pid, "run": "x/run.txt"}), encoding="utf-8")


def _pula(items):
    trava_mod.pytest_collection_modifyitems(None, items)
    return [bool(i.marcas) for i in items]


def test_trava_viva_pula_so_o_marcado(tmp_path, monkeypatch):
    t = tmp_path / "funil-ativo.json"
    _grava(t, os.getpid())
    monkeypatch.setenv("FARM_FUNIL_TRAVA", str(t))
    monkeypatch.delenv("FARM_FUNIL_RODANDO", raising=False)
    monkeypatch.delenv("FARM_FUNIL_CARGA", raising=False)
    assert _pula([Item(True), Item(False)]) == [True, False]


@pytest.mark.parametrize("ambiente", ["FARM_FUNIL_RODANDO", "FARM_FUNIL_CARGA"])
def test_o_proprio_funil_e_o_override_rodam_tudo(tmp_path, monkeypatch, ambiente):
    t = tmp_path / "funil-ativo.json"
    _grava(t, os.getpid())
    monkeypatch.setenv("FARM_FUNIL_TRAVA", str(t))
    monkeypatch.setenv(ambiente, "1")
    assert _pula([Item(True)]) == [False]


def test_pid_morto_arquivo_ruim_ou_antigo_nao_e_funil(tmp_path, monkeypatch):
    t = tmp_path / "funil-ativo.json"
    monkeypatch.setenv("FARM_FUNIL_TRAVA", str(t))
    monkeypatch.delenv("FARM_FUNIL_RODANDO", raising=False)
    monkeypatch.delenv("FARM_FUNIL_CARGA", raising=False)
    assert _pula([Item(True)]) == [False], "sem arquivo"
    t.write_text("{nao e json", encoding="utf-8")
    assert _pula([Item(True)]) == [False], "arquivo ruim"
    _grava(t, 4_000_000)
    assert _pula([Item(True)]) == [False], "pid que nao existe"
    _grava(t, os.getpid())
    velho = time.time() - 13 * 3600
    os.utime(t, (velho, velho))
    assert _pula([Item(True)]) == [False], "trava com mais de 12 h e lixo"


def test_caminho_padrao_e_o_data_do_checkout_central(monkeypatch):
    monkeypatch.delenv("FARM_FUNIL_TRAVA", raising=False)
    caminho = trava_mod.caminho_da_trava()
    assert caminho.name == "funil-ativo.json" and caminho.parent.name == "data"
