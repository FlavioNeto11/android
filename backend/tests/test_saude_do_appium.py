"""Item 3.4 — a readoção do Appium órfão deixa de zerar o mascaramento sem olhar: agora comprova pela linha de
comando (--log-filters apontando para o arquivo certo) e pelo conteúdo do arquivo de regras, ou nega.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.automation.appium_server import LOG_FILTER_RULES, AppiumServer
from app.devices.sdk import SdkTools

from .conftest import make_config


class _FakeProc:
    def __init__(self, cmdline: list[str]):
        self._cmdline = cmdline

    def cmdline(self) -> list[str]:
        return self._cmdline


def _server(tmp_path: Path) -> AppiumServer:
    cfg = make_config(tmp_path)
    return AppiumServer(cfg, SdkTools(cfg))


def _write_rules(cfg: Any, rules: Any = LOG_FILTER_RULES) -> Path:
    path = cfg.data_dir / "appium-log-filters.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_prova_o_mascaramento_com_cmdline_e_arquivo_corretos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    filtros = _write_rules(server.cfg)
    monkeypatch.setattr("app.automation.appium_server.psutil.Process",
                        lambda pid: _FakeProc(["node", "index.js", "--log-filters", str(filtros)]))
    assert server._prove_masking(12345) is True


def test_nao_prova_sem_a_flag_log_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    _write_rules(server.cfg)
    monkeypatch.setattr("app.automation.appium_server.psutil.Process",
                        lambda pid: _FakeProc(["node", "index.js", "--port", "4723"]))
    assert server._prove_masking(12345) is False


def test_nao_prova_quando_o_arquivo_de_regras_foi_alterado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    filtros = _write_rules(server.cfg, rules=[{"pattern": "adulterado", "flags": "g", "replacer": "x"}])
    monkeypatch.setattr("app.automation.appium_server.psutil.Process",
                        lambda pid: _FakeProc(["node", "index.js", "--log-filters", str(filtros)]))
    assert server._prove_masking(12345) is False


def test_readocao_marca_mascaramento_ativo_quando_comprovado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    filtros = _write_rules(server.cfg)
    server._pid_file.parent.mkdir(parents=True, exist_ok=True)
    server._pid_file.write_text("777", encoding="ascii")
    monkeypatch.setattr(server, "is_up", lambda timeout=2.0: True)
    monkeypatch.setattr(server, "_own_orphan", lambda: 777)
    monkeypatch.setattr("app.automation.appium_server.psutil.Process",
                        lambda pid: _FakeProc(["node", "index.js", "--log-filters", str(filtros)]))
    assert server.start() is True
    assert server.log_masking_active is True
    assert "comprovado" in (server.detail or "")


def test_readocao_sem_prova_mantem_mascaramento_inativo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    server._pid_file.parent.mkdir(parents=True, exist_ok=True)
    server._pid_file.write_text("777", encoding="ascii")
    monkeypatch.setattr(server, "is_up", lambda timeout=2.0: True)
    monkeypatch.setattr(server, "_own_orphan", lambda: 777)
    monkeypatch.setattr("app.automation.appium_server.psutil.Process",
                        lambda pid: _FakeProc(["node", "index.js", "--port", "4723"]))
    assert server.start() is True
    assert server.log_masking_active is False
    assert "não comprovado" in (server.detail or "")
