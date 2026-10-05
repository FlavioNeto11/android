"""29.132: o `start()` do Appium só dá "subiu" quando é ESTE processo que ligou a porta, e a readoção acha o nosso
Appium pelo dono da porta, não só pelo `appium.pid`.

O incidente (05/10 13:10Z): com a máquina saturada, o `is_up` de 2 s não viu o Appium anterior (13056, nosso, com as
regras), e o backend subiu outro (36048). O `is_up` seguinte foi respondido pelo 13056; o `_confirm_masking` leu o
log do 36048 antes de ele carregar as regras ("não confirmado"), e o `appium.pid` virou 36048. O 36048 nunca ligou a
porta e sumiu; o backend seguinte chamou o 13056 de "servidor externo". Tudo falso aqui: nenhum `node` sobe.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from app.automation import appium_server as mod
from app.automation.appium_server import LISTENER_MARKER, AppiumServer

from .test_saude_do_appium import _com_appium_instalado, _server, _Vivo, _write_rules


class _PopenFalso:
    """O `node` novo: escreve no log (o `stdout`) o que o roteiro mandar, e morre se o roteiro mandar."""

    def __init__(self, roteiro: list[str], morre: bool, *_a: Any, **kw: Any) -> None:
        self.pid = 36048
        self.returncode: int | None = None
        self._out = kw["stdout"].name
        self._roteiro, self._morre = roteiro, morre

    def poll(self) -> int | None:
        if self._roteiro:
            with open(self._out, "ab") as fh:
                fh.write(self._roteiro.pop(0).encode())
            return None
        if self._morre:
            self.returncode = 1
        return self.returncode


def _subir_com(monkeypatch: pytest.MonkeyPatch, server: AppiumServer, *, roteiro: list[str], morre: bool,
               responde: list[bool], donos: list[int] | None = None) -> list[_PopenFalso]:
    criados: list[_PopenFalso] = []
    # Quem escuta na porta, sob controle do teste: sem isto, o teste veria o Appium de verdade desta máquina.
    monkeypatch.setattr(server, "_donos_da_porta", lambda: list(donos or []))

    def popen(*a: Any, **kw: Any) -> _PopenFalso:
        criados.append(_PopenFalso(list(roteiro), morre, *a, **kw))
        return criados[-1]

    respostas = list(responde)
    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    monkeypatch.setattr(server, "is_up", lambda timeout=2.0: respostas.pop(0) if len(respostas) > 1 else respostas[0])
    monkeypatch.setattr(mod.time, "sleep", lambda _s: criados[-1].poll() if criados else None)
    return criados


def test_quem_responde_na_porta_nao_e_o_novo_ate_o_log_dele_dizer(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Outro servidor responde desde o começo; o novo só conta quando escreve `listener started` (e as regras)."""
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_reuse_running", lambda: False)        # o que responde foi trocado: sobe outro
    _subir_com(monkeypatch, server, roteiro=["Welcome to Appium\n", "Loaded 3 filtering rules\n",
                                             f"Appium REST http interface {LISTENER_MARKER} http://127.0.0.1:4723\n"],
               morre=False, responde=[True])
    assert server.start(wait_s=5) is True
    assert server.log_masking_active is True, "o 'não confirmado' das 13:10:58Z: leu o log antes das regras"
    assert server._pid_file.read_text(encoding="ascii") == "36048"


def test_o_novo_que_morre_sem_ligar_a_porta_devolve_a_decisao_ao_reaproveitamento(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    server._pid_file.parent.mkdir(parents=True, exist_ok=True)
    server._pid_file.write_text("13056", encoding="ascii")
    reaproveitou: list[bool] = []

    def reuse() -> bool:
        # Só é chamado na 2ª volta: na 1ª, o `is_up` não viu ninguém e o reaproveitamento nem foi consultado.
        reaproveitou.append(True)
        return True

    # a 1ª conferência não vê ninguém (máquina saturada); depois, o anterior responde
    _subir_com(monkeypatch, server, roteiro=["Welcome to Appium\n"], morre=True, responde=[False, True])
    monkeypatch.setattr(server, "_reuse_running", reuse)
    assert server.start(wait_s=5) is True
    assert reaproveitou == [True], "a decisão sobre o que responde voltou ao reaproveitamento"
    assert server._pid_file.read_text(encoding="ascii") == "13056", "o appium.pid apontou para quem nunca ligou"


def test_o_novo_que_morre_e_ninguem_responde_e_falha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    _subir_com(monkeypatch, server, roteiro=[], morre=True, responde=[False])
    assert server.start(wait_s=5) is False
    assert "encerrou ao iniciar" in server.detail
    assert not server._pid_file.exists()


def test_o_dono_da_porta_e_o_orfao_mesmo_com_o_appium_pid_de_outro(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """O `appium.pid` aponta para o 36048 (morto); o 13056 é nosso e escuta na porta: é ele o órfão a provar."""
    server = _server(tmp_path)
    cmd = _com_appium_instalado(server, tmp_path, monkeypatch)
    server._pid_file.parent.mkdir(parents=True, exist_ok=True)
    server._pid_file.write_text("36048", encoding="ascii")
    nosso = _Vivo("node", cmd)

    def processo(pid: int) -> _Vivo:
        if pid == 13056:
            return nosso
        raise psutil.NoSuchProcess(pid)

    conexao = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=server.cfg.file.appium.port),
                              pid=13056)
    monkeypatch.setattr(mod.psutil, "Process", processo)
    monkeypatch.setattr(mod.psutil, "net_connections", lambda kind="tcp": [conexao])
    assert server._own_orphan() == 13056


def test_dono_da_porta_alheio_e_externo_mesmo_com_o_gravado_nosso_vivo(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """29.138 (achado do Copilot no #443): quem recebe as requisições é o 4000, alheio. Provar o mascaramento no 777
    (nosso, vivo, sem escutar) certificaria o servidor errado; antes, o arquivo ainda valia e isto devolvia 777."""
    server = _server(tmp_path)
    cmd = _com_appium_instalado(server, tmp_path, monkeypatch)
    server._pid_file.parent.mkdir(parents=True, exist_ok=True)
    server._pid_file.write_text("777", encoding="ascii")
    alheio, nosso = _Vivo("node", ["node", "C:/outro/projeto/appium.js"]), _Vivo("node", cmd)
    monkeypatch.setattr(mod.psutil, "Process", lambda pid: {4000: alheio, 777: nosso}[pid])
    conexao = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=server.cfg.file.appium.port),
                              pid=4000)
    monkeypatch.setattr(mod.psutil, "net_connections", lambda kind="tcp": [conexao])
    assert server._own_orphan() is None


def test_sem_ver_as_conexoes_vale_o_appium_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    cmd = _com_appium_instalado(server, tmp_path, monkeypatch)
    server._pid_file.parent.mkdir(parents=True, exist_ok=True)
    server._pid_file.write_text("777", encoding="ascii")

    def negado(kind: str = "tcp") -> list[Any]:
        raise psutil.AccessDenied(0)

    monkeypatch.setattr(mod.psutil, "net_connections", negado)
    monkeypatch.setattr(mod.psutil, "Process", lambda pid: _Vivo("node", cmd))
    assert server._own_orphan() == 777


def test_o_13056_readotado_pelo_dono_da_porta_prova_a_mascara(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Fim a fim no `_reuse_running`: o órfão achado pela porta passa pela mesma prova (linha de comando + regras)."""
    server = _server(tmp_path)
    cmd = _com_appium_instalado(server, tmp_path, monkeypatch)
    regras = _write_rules(server.cfg)
    server._pid_file.parent.mkdir(parents=True, exist_ok=True)
    server._pid_file.write_text("36048", encoding="ascii")
    nosso = _Vivo("node", cmd + ["--log-filters", str(regras)])
    monkeypatch.setattr(mod.psutil, "Process",
                        lambda pid: nosso if pid == 13056 else (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
    conexao = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=server.cfg.file.appium.port),
                              pid=13056)
    monkeypatch.setattr(mod.psutil, "net_connections", lambda kind="tcp": [conexao])
    assert server._reuse_running() is True
    assert server.log_masking_active is True and server.pid == 13056
    assert "readotado" in server.detail
