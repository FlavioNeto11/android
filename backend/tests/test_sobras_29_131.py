"""29.131: as sobras das leituras do #433 (29.124), do #435 (29.125), do #441 (29.127) e do #443 (29.132).

Tudo `simulated`: relógios, processos, portas e o Appium são falsos; nenhum processo de verdade sobe.
"""
from __future__ import annotations

import inspect
import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest

from app import marca_de_partida, supervisor
from app.automation import appium_server as appium_mod
from app.db import Database
from app.devices import emulator as emu
from app.devices import manager as manager_mod
from app.devices.manager import DeviceManager
from app.vigia_do_laco import PREFIXO, VigiaDoLaco

from .conftest import Harness, make_config
from .test_appium_start_pid_novo import _subir_com
from .test_saude_do_appium import _com_appium_instalado, _server
from .test_supervisor_partida import Bancada


# ================================================================== #433, R1: a marca anda dentro do AppState
def test_migrate_avisa_cada_migracao_aplicada_e_o_aviso_nao_derruba(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    vistas: list[str] = []

    def acompanha(versao: str) -> None:
        vistas.append(versao)
        raise RuntimeError("quem acompanha quebrou")

    aplicadas = Database(cfg.db_dsn).migrate(ao_aplicar=acompanha)
    assert vistas == aplicadas, "um aviso por migração aplicada, e o erro do aviso não interrompeu nenhuma"


async def test_o_appstate_reescreve_a_marca_nas_migracoes_e_depois_dos_aparelhos(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fases: list[str] = []
    original = marca_de_partida.gravar

    def grava(pasta: Path, fase: str, **kw: Any) -> None:
        fases.append(fase)
        original(pasta, fase, **kw)

    monkeypatch.setattr(marca_de_partida, "gravar", grava)
    monkeypatch.setenv(marca_de_partida.VARIAVEL, "id-da-subida")
    monkeypatch.setenv(marca_de_partida.PASTA, str(tmp_path / "sup"))
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        assert marca_de_partida.MIGRANDO in fases and marca_de_partida.APARELHOS in fases
        assert fases.index(marca_de_partida.MIGRANDO) < fases.index(marca_de_partida.APARELHOS)
        marca = marca_de_partida.ler(tmp_path / "sup")
        assert marca is not None and marca.id == "id-da-subida"
    finally:
        if h.state is not None:
            await h.state.stop()


def test_main_grava_antes_e_depois_do_estado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import estado_com_marca

    monkeypatch.setenv(marca_de_partida.VARIAVEL, "id-da-subida")
    monkeypatch.setenv(marca_de_partida.PASTA, str(tmp_path))
    vista_durante: list[str | None] = []

    def fabrica(_cfg: Any) -> Any:
        m = marca_de_partida.ler(tmp_path)
        vista_durante.append(m.fase if m else None)
        return "estado"

    assert estado_com_marca(make_config(tmp_path), fabrica) == "estado"   # type: ignore[arg-type]
    assert vista_durante == [marca_de_partida.ANTES_DO_ESTADO]
    marca = marca_de_partida.ler(tmp_path)
    assert marca is not None and marca.fase == marca_de_partida.ESTADO_PRONTO


def test_sem_fabrica_vale_o_appstate_do_modulo_na_hora_da_chamada(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Suíte 39: com o padrão `fabrica=AppState` preso na definição, trocar `main.AppState` não valia, e o teste do
    contêiner subia o estado de verdade."""
    from app import main as principal

    monkeypatch.setattr(principal, "AppState", lambda _cfg: "trocado")
    assert principal.estado_com_marca(make_config(tmp_path)) == "trocado"   # type: ignore[comparison-overlap]


# ================================================================== #433: a recusa por backend alheio zera a conta
def test_backend_alheio_respondendo_zera_os_reinicios_seguidos() -> None:
    b = Bancada()
    b.sup.run(ciclos=1)
    b.processos[-1].codigo = 1
    b.sup.run(ciclos=1)
    assert b.sup.reinicios_seguidos == 1
    b.processos[-1].codigo = 1
    b.sup._saudavel = lambda: True                        # outro backend responde na porta
    b.sup.run(ciclos=1)
    assert b.sup.relatorio.recusou_por_ja_haver_backend >= 1
    assert b.sup.reinicios_seguidos == 0


# ================================================================== #433, N1: a pilha citada diz a idade
def test_a_linha_do_kill_diz_ha_quanto_tempo_a_pilha_foi_gravada(tmp_path: Path,
                                                                  caplog: pytest.LogCaptureFixture) -> None:
    despejo = tmp_path / f"{PREFIXO}20261005T130000Z-1.txt"
    despejo.write_text("pilha", encoding="utf-8")
    antigo = time.time() - 200
    os.utime(despejo, (antigo, antigo))

    class Proc:
        pid = 4242

        def poll(self) -> None:
            return None

    s = supervisor.Supervisor(iniciar=Proc, saudavel=lambda: False, encerrar=lambda p: None, dormir=lambda s: None,
                              despejo=lambda: despejo)
    s.proc = Proc()
    with caplog.at_level(logging.WARNING, logger="poc.supervisor"):
        s.run(ciclos=3)
    assert "(gravada há 200 s)" in caplog.text or "(gravada há 201 s)" in caplog.text


def test_na_partida_os_despejos_saem_a_cada_120_s(tmp_path: Path) -> None:
    agora = [1000.0]
    v = VigiaDoLaco(tmp_path, limite_s=10, partida_s=60, redespejo_s=30, relogio=lambda: agora[0])
    instantes: list[float] = []
    for _ in range(400):
        agora[0] += 1
        if v.conferir() is not None:
            instantes.append(agora[0] - 1000.0)
    assert instantes == [61.0, 181.0, 301.0]


# ================================================================== #435: o texto e o teste
def test_o_kill_orphan_cita_o_criterio_certo() -> None:
    doc = inspect.getdoc(appium_mod.AppiumServer._kill_orphan) or ""
    assert "supervisor.e_emulador" in doc and "mesmo critério do supervisor" not in doc


# ================================================================== #441: o `\n` na dúvida e as guardas do -1
def test_erro_ao_ler_o_fim_do_log_poe_o_n_por_garantia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "emulator-avd.log"
    log.write_bytes(b"linha inteira\n")
    original = Path.open

    def abre(self: Path, *a: Any, **k: Any) -> Any:
        if self == log:
            raise PermissionError("compartilhamento negado")
        return original(self, *a, **k)

    monkeypatch.setattr(Path, "open", abre)
    assert emu._termina_em_quebra(log) is False, "na dúvida, o \\n a mais é o lado seguro"
    monkeypatch.setattr(Path, "open", original)
    vazio = tmp_path / "vazio.log"
    vazio.write_bytes(b"")
    assert emu._termina_em_quebra(vazio) is True
    assert emu._termina_em_quebra(tmp_path / "nao-existe.log") is True


def test_offset_desconhecido_nem_abre_o_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "emulator-avd.log"
    log.write_bytes(("INFO | " + emu.LINHA_DO_DIALOGO_DE_CRASH + "\n").encode())

    def proibido(self: Path, *a: Any, **k: Any) -> Any:
        raise AssertionError(f"leu o log com offset desconhecido: {self}")

    monkeypatch.setattr(Path, "open", proibido)
    assert emu.dialogo_de_crash(log, emu.OFFSET_DESCONHECIDO) is False


async def test_veredito_com_offset_desconhecido_nem_abre_o_log(harness: Harness,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    assert harness.state is not None
    devs, rt = harness.state.devices, harness.state.devices.get("android-01")
    rt.boot_log_offset = emu.OFFSET_DESCONHECIDO

    def proibido(self: Path, *a: Any, **k: Any) -> Any:
        raise AssertionError(f"leu o log com offset desconhecido: {self}")

    monkeypatch.setattr(Path, "open", proibido)
    assert devs._snapshot_verdict(rt) is None


def test_offset_do_spawn_e_os_dois_sites_que_o_usam(tmp_path: Path) -> None:
    log = tmp_path / "emulator-avd.log"
    assert DeviceManager._offset_do_spawn(log) == 0
    log.write_bytes(b"x" * 10)
    assert DeviceManager._offset_do_spawn(log) == 10
    fonte = inspect.getsource(manager_mod.DeviceManager)
    assert fonte.count("rt.boot_log_offset = self._offset_do_spawn(log_path)") == 2, "o spawn e a retentativa a frio"


# ================================================================== #443: o dono da porta também decide
def test_o_novo_morre_o_is_up_falha_e_o_dono_da_porta_decide(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """S1 da leitura do #443: na saturação, o `is_up` de 2 s falha também; o 13056 escutando na porta basta."""
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    reaproveitou: list[bool] = []

    def reuse() -> bool:
        reaproveitou.append(True)
        return True

    criados = _subir_com(monkeypatch, server, roteiro=["Welcome to Appium\n"], morre=True, responde=[False])
    # Ninguém escuta na hora de subir; quando o novo já morreu, o anterior está na porta. O `is_up` falha sempre.
    monkeypatch.setattr(server, "_donos_da_porta", lambda: [13056] if criados and criados[0].returncode else [])
    monkeypatch.setattr(server, "_reuse_running", reuse)
    assert server.start(wait_s=5) is True
    assert reaproveitou == [True] and len(criados) == 1, "o morto com o anterior na porta devolve a decisão"


def test_a_porta_ser_do_novo_prova_sem_a_frase_do_listener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uma versão do Appium que mude a frase não deixa o `start()` girar 60 s e dar falso com o servidor vivo."""
    server = _server(tmp_path)
    _com_appium_instalado(server, tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_reuse_running", lambda: False)
    criados = _subir_com(monkeypatch, server, roteiro=["Loaded 3 filtering rules\n", "HTTP server ready\n"],
                         morre=False, responde=[True])
    # Como o de verdade: as regras saem no log ANTES de a porta ser ligada; a porta passa a ser dele depois.
    monkeypatch.setattr(server, "_donos_da_porta",
                        lambda: [36048] if criados and not criados[0]._roteiro else [])
    assert server.start(wait_s=5) is True
    assert server.log_masking_active is True
