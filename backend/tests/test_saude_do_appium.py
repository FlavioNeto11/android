"""Item 3.4 — a readoção do Appium órfão deixa de zerar o mascaramento sem olhar: agora comprova pela linha de
comando (--log-filters apontando para o arquivo certo) e pelo conteúdo do arquivo de regras, ou nega.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psutil
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
    """Desde o K-039 fora do deploy, o órfão sem prova é TROCADO (`test_supervisao_do_central.py`). Só é readotado
    quando a troca não dá (sem permissão, sem Appium para subir outro), e aí o mascaramento continua inativo."""
    server = _server(tmp_path)
    server._pid_file.parent.mkdir(parents=True, exist_ok=True)
    server._pid_file.write_text("777", encoding="ascii")
    monkeypatch.setattr(server, "is_up", lambda timeout=2.0: True)
    monkeypatch.setattr(server, "_own_orphan", lambda: 777)
    monkeypatch.setattr(server, "_kill_orphan", lambda pid: "sem permissão para encerrá-lo (AccessDenied)")
    monkeypatch.setattr("app.automation.appium_server.psutil.Process",
                        lambda pid: _FakeProc(["node", "index.js", "--port", "4723"]))
    assert server.start() is True
    assert server.pid == 777
    assert server.log_masking_active is False
    assert "não comprovado" in (server.detail or "")
    assert "sem permissão" in (server.detail or "")


# ---------------------------------------------------------------- as travas antes de encerrar o órfão (K-039)
class _Vivo:
    """Um `psutil.Process` falso que registra o `kill`."""

    def __init__(self, nome: str, cmdline: list[str] | None = None, filhos: list["_Vivo"] | None = None,
                 nome_ilegivel: bool = False, kill_negado: bool = False) -> None:
        self.nome, self._cmdline, self.filhos = nome, cmdline or [], filhos or []
        self.nome_ilegivel, self.kill_negado, self.morto = nome_ilegivel, kill_negado, False

    def cmdline(self) -> list[str]:
        return self._cmdline

    def name(self) -> str:
        if self.nome_ilegivel:
            raise psutil.AccessDenied(1)
        return self.nome

    def children(self, recursive: bool = False) -> list["_Vivo"]:
        assert recursive
        return self.filhos

    def kill(self) -> None:
        if self.kill_negado:
            raise psutil.AccessDenied(1)
        self.morto = True


def _com_appium_instalado(server: AppiumServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    pasta = tmp_path / "tools" / "appium"
    (pasta / "node_modules" / "appium").mkdir(parents=True)
    (pasta / "node_modules" / "appium" / "index.js").write_text("", encoding="utf-8")
    server.cfg.file.appium.dir = str(pasta)
    monkeypatch.setattr("app.automation.appium_server.shutil.which", lambda _n: "/usr/bin/node")
    monkeypatch.setattr("app.automation.appium_server.psutil.wait_procs",
                        lambda procs, timeout=None: ([p for p in procs if p.morto], [p for p in procs if not p.morto]))
    return ["node", str(pasta / "node_modules" / "appium" / "index.js"), "server", "--port", "4723"]


def test_sem_appium_para_subir_outro_o_orfao_fica(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trocar um Appium degradado por nenhum derrubaria a automação inteira."""
    server = _server(tmp_path)
    cmd = _com_appium_instalado(server, tmp_path, monkeypatch)
    orfao = _Vivo("node", cmd)
    monkeypatch.setattr("app.automation.appium_server.psutil.Process", lambda pid: orfao)
    monkeypatch.setattr("app.automation.appium_server.shutil.which", lambda _n: None)
    assert "não há Appium instalado" in (server._kill_orphan(777) or "")
    assert orfao.morto is False


def test_pid_reciclado_para_outro_processo_nao_e_encerrado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entre `_own_orphan` e o tiro, o PID pode ter virado outro processo: a linha de comando é conferida de novo."""
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    outro = _Vivo("node", ["node", "C:/outro/projeto/vite.js"])
    monkeypatch.setattr("app.automation.appium_server.psutil.Process", lambda pid: outro)
    assert "já não é o Appium deste projeto" in (server._kill_orphan(777) or "")
    assert outro.morto is False


def test_encerrar_o_orfao_poupa_emulador_e_filho_de_nome_ilegivel(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    cmd = _com_appium_instalado(server, tmp_path, monkeypatch)
    server._pid_file.write_text("777", encoding="ascii")
    filhos = [_Vivo("emulator.exe"), _Vivo("qemu-system-x86_64.exe"), _Vivo("adb.exe"), _Vivo("chromedriver.exe"),
              _Vivo("?", nome_ilegivel=True)]
    orfao = _Vivo("node.exe", cmd, filhos)
    monkeypatch.setattr("app.automation.appium_server.psutil.Process", lambda pid: orfao)
    assert server._kill_orphan(777) is None
    assert orfao.morto is True
    assert [f.morto for f in filhos] == [False, False, True, True, False]
    assert not server._pid_file.exists(), "o PID gravado aponta para quem acabou de sair"


def test_sem_permissao_para_encerrar_o_orfao_devolve_o_motivo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    cmd = _com_appium_instalado(server, tmp_path, monkeypatch)
    server._pid_file.write_text("777", encoding="ascii")
    orfao = _Vivo("node.exe", cmd, kill_negado=True)
    monkeypatch.setattr("app.automation.appium_server.psutil.Process", lambda pid: orfao)
    assert "sem permissão" in (server._kill_orphan(777) or "")
    assert server._pid_file.exists()
