"""29.138: os achados das revisões automáticas do #443 (29.132), conferidos e implementados.

- Supervisor (Copilot): a saúde que respondeu prova que ESTE processo acabou a partida. Se a marca final não foi
  gravada (a escrita engole o erro), o arquivo fica em `iniciando`; sem lembrar da resposta, um laço travado depois
  disso era tolerado como partida lenta por até 240/600 s.
- `_own_orphan` (Copilot): o teste do dono alheio está em `test_appium_start_pid_novo.py`.
- `_subir` (Codex, P1): um Appium anterior ainda subindo escreve no mesmo `appium.log` e pode pôr a frase do ouvinte
  depois do `offset` deste processo. Com os donos da porta visíveis, só a porta ser deste processo prova.

Tudo falso: nenhum processo sobe, nenhum `node` roda.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import marca_de_partida
from app.automation.appium_server import LISTENER_MARKER

from .test_appium_start_pid_novo import _subir_com
from .test_saude_do_appium import _com_appium_instalado, _server
from .test_supervisor_partida import Bancada

_FRASE = f"Appium REST http interface {LISTENER_MARKER} http://127.0.0.1:4723\n"


# ================================================================== supervisor
def test_depois_de_a_saude_responder_a_marca_velha_nao_tolera_o_silencio() -> None:
    b = Bancada(saude=[True])
    b.sup.run(ciclos=1)                                   # sobe
    b.marcar(marca_de_partida.INICIANDO, ha_s=5.0)        # a marca `no_ar` nunca foi gravada
    b.sup.run(ciclos=1)                                   # a saúde respondeu: a partida acabou
    for _ in range(3):                                    # depois, mudo, com a marca ainda recente em `iniciando`
        b.marcar(marca_de_partida.INICIANDO, ha_s=5.0)
        b.sup.run(ciclos=1)
    assert b.sup.relatorio.esperou_a_partida == 0, "o laço travado depois do 'no ar' não é partida lenta"
    assert b.sup.relatorio.reiniciou_por_silencio == 1, "a regra das três falhas"
    assert len(b.processos) == 2


def test_a_subida_nova_volta_a_ter_a_tolerancia_da_partida() -> None:
    b = Bancada(saude=[True])
    b.sup.run(ciclos=1)
    b.sup.run(ciclos=1)                                   # respondeu: acabou a partida desta subida
    b.sup.run(ciclos=3)                                   # mudo: três falhas, derruba e sobe outro
    assert len(b.processos) == 2
    assert b.sup.respondeu_nesta_subida is False, "a subida nova ainda não respondeu"
    b.marcar(marca_de_partida.MIGRANDO, ha_s=5.0)
    b.sup.run(ciclos=1)
    assert b.sup.relatorio.esperou_a_partida == 1, "a partida do processo novo é tolerada como sempre"


# ================================================================== _subir (Codex P1)
def test_a_frase_no_log_nao_prova_se_a_porta_e_de_outro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O 13056 (anterior, ainda subindo) escuta na porta e escreveu a frase depois do offset; o novo é o 36048."""
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    _subir_com(monkeypatch, server, roteiro=["Loaded 3 filtering rules\n", _FRASE], morre=False, responde=[True],
               donos=[13056])
    assert server._subir(wait_s=1) is False
    assert "não respondeu a tempo" in server.detail
    assert not server._pid_file.exists(), "o appium.pid não aponta para quem não ligou a porta"


def test_a_porta_do_novo_prova_mesmo_depois_de_outro_escutar(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    criados = _subir_com(monkeypatch, server, roteiro=["Loaded 3 filtering rules\n", _FRASE], morre=False,
                         responde=[True], donos=[13056])
    donos = [[13056], [13056]]                            # duas voltas com o anterior; depois, só o novo
    monkeypatch.setattr(server, "_donos_da_porta", lambda: donos.pop(0) if donos else [criados[-1].pid])
    assert server._subir(wait_s=5) is True
    assert server._pid_file.read_text(encoding="ascii") == "36048"


def test_sem_ver_os_donos_a_frase_do_log_ainda_prova(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O sistema não deixa ver quem escuta (`_donos_da_porta` vazio): vale a frase, como antes."""
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    _subir_com(monkeypatch, server, roteiro=["Loaded 3 filtering rules\n", _FRASE], morre=False, responde=[True])
    assert server._subir(wait_s=5) is True
    assert server.log_masking_active is True
