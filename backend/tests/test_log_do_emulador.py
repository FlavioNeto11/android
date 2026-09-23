"""O log do emulador remoto — a peça que o plano, o commit do agente e a docstring do pacote `workers` citavam
como entregue e que não existia.

O caso real: um boot na outra máquina termina `uncertain` (android-15, 21/09) e o operador recebe uma frase. O
log está em `work_dir/logs/emulator-<avd>.log`, na casa de outra pessoa, e nada no painel o alcança.

O que passa a valer, e é o que estes testes trancam:
  1. um verbo `emulator_log` no agente, que lê o log e não toca no aparelho;
  2. a cauda do log viajando no `result.data` quando `start`/`wake`/`restart`/`reset` terminam mal — nas DUAS
     máquinas, na mesma forma;
  3. redação de segredo antes de qualquer byte sair da máquina;
  4. o desfecho chegando ao painel pelo `CommandDTO`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.commands.store import command_dto
from app.devices import emulator as emu
from app.models import CommandState
from app.worker import executor as executor_mod
from app.worker.executor import VerbUncertain

from .conftest import Harness
from .test_worker_executor import AdbFalso, _estado_falso, _executor, _sem_emulador, _sem_guarda_de_ram


# ---------------------------------------------------------------- redação
def test_redige_o_que_parece_segredo_antes_de_sair_da_maquina() -> None:
    """O log é saída de processo: ninguém DEVERIA escrever segredo nele — e é por isso que o filtro existe aqui,
    em vez de depender de ninguém nunca errar."""
    bruto = ("emulator: iniciando\n"
             "GET /x Authorization: Bearer abcdef0123456789ABCDEF\n"
             "conectando com token=tok_super_secreto_123\n"
             "senha: hunter2-do-parque\n"
             "api_key = AKIA000111222333\n")
    limpo = emu.redigir(bruto)
    for vazado in ("abcdef0123456789ABCDEF", "tok_super_secreto_123", "hunter2-do-parque", "AKIA000111222333"):
        assert vazado not in limpo, limpo
    assert "«removido»" in limpo
    assert "emulator: iniciando" in limpo          # o que não é segredo continua legível


def test_cauda_do_log_traz_o_fim_do_arquivo_e_o_caminho(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "emulator-worker-01.log").write_text("começo\n" + ("x" * 5000) + "\nfim do boot\n", encoding="utf-8")
    saida = emu.log_do_emulador(logs, "worker-01", max_bytes=200)
    assert saida["emulator_log"].strip().endswith("fim do boot")
    assert "começo" not in saida["emulator_log"], "a cauda é o fim do arquivo, não o começo"
    assert saida["emulator_log_path"].endswith("emulator-worker-01.log")
    assert 0 < saida["emulator_log_bytes"] <= 260


def test_log_que_nao_existe_nao_derruba_nada(tmp_path: Path) -> None:
    saida = emu.log_do_emulador(tmp_path, "avd-que-nunca-subiu")
    assert saida["emulator_log"] == "" and saida["emulator_log_path"]


# ---------------------------------------------------------------- rotação (item 10.2, achado #144)
def test_log_grande_e_rotacionado_para_log_1_e_o_pequeno_fica(tmp_path: Path) -> None:
    """`emulator-<avd>.log` era só `append`: um aparelho de tarefa longa (ou um laço de falha de sessão
    reiniciando o Android sem parar) nunca via o arquivo encolher entre um boot e outro."""
    grande = tmp_path / "emulator-worker-01.log"
    grande.write_bytes(b"x" * (9 * 1024 * 1024))
    emu._rotate_log(grande, limite_bytes=8 * 1024 * 1024)
    assert not grande.exists()
    rotacionado = tmp_path / "emulator-worker-01.log.1"
    assert rotacionado.exists() and rotacionado.stat().st_size == 9 * 1024 * 1024

    pequeno = tmp_path / "emulator-worker-02.log"
    pequeno.write_bytes(b"y" * 100)
    emu._rotate_log(pequeno, limite_bytes=8 * 1024 * 1024)
    assert pequeno.exists() and pequeno.stat().st_size == 100         # abaixo do limite: não mexe


def test_rotacao_anterior_e_substituida_nao_acumulada(tmp_path: Path) -> None:
    caminho = tmp_path / "emulator-worker-01.log"
    (tmp_path / "emulator-worker-01.log.1").write_text("rotação de uma sessão bem mais antiga", encoding="utf-8")
    caminho.write_bytes(b"z" * (9 * 1024 * 1024))
    emu._rotate_log(caminho, limite_bytes=8 * 1024 * 1024)
    conteudo = (tmp_path / "emulator-worker-01.log.1").read_bytes()
    assert conteudo == b"z" * (9 * 1024 * 1024)        # o `.log.1` velho não sobrevive ao lado do novo


# ---------------------------------------------------------------- o agente
async def test_verbo_emulator_log_le_o_log_e_nao_toca_no_aparelho(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """O verbo que faltava. Ele é leitura pura: nenhum `start_process`, nenhum `adb`."""
    ex = _executor(tmp_path)
    subidos = _sem_emulador(monkeypatch)
    spec = ex.settings.devices[0]
    logs = ex.cfg.logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"emulator-{spec.avd_name}.log").write_text("qemu: falha ao alocar RAM\ntoken=nao_deve_vazar\n",
                                                        encoding="utf-8")

    saida = await ex.run("emulator_log", spec, {})
    assert "qemu: falha ao alocar RAM" in saida["emulator_log"]
    assert "nao_deve_vazar" not in saida["emulator_log"]
    assert subidos == [], "ler o log não pode ligar nada"


async def test_boot_incerto_leva_a_cauda_do_log_junto_do_motivo(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """É o coração do item: o boot que não terminou vai para o central com o log, não só com a frase.

    `VerbRefused` fica de fora de propósito — recusa acontece ANTES de agir, e não há emulador de que falar.
    """
    monkeypatch.setattr(executor_mod, "INTERVALO_SONDA_S", 0.01)
    ex = _executor(tmp_path)
    _sem_emulador(monkeypatch)
    _sem_guarda_de_ram(ex, monkeypatch)
    _estado_falso(ex, monkeypatch, "stopped")
    monkeypatch.setattr(ex.avd, "exists", lambda _n: True)
    monkeypatch.setattr(ex, "adb_for", lambda _spec: AdbFalso(nunca_boota=True))
    spec = ex.settings.devices[0]
    ex.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    (ex.cfg.logs_dir / f"emulator-{spec.avd_name}.log").write_text("emulator: PANIC: Missing GPU\n",
                                                                   encoding="utf-8")

    with pytest.raises(VerbUncertain) as saida:
        await ex.run("start", spec, {"boot_timeout_s": 0.05})
    assert "PANIC: Missing GPU" in (saida.value.dados or {})["emulator_log"]


async def test_o_agente_manda_o_log_no_resultado_do_comando(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """A ponta do agente: `VerbUncertain.dados` precisa virar `result.data`, senão o log morre na outra máquina."""
    from app.worker.agent import Agent
    from app.workers.protocol import Dispatch

    ex = _executor(tmp_path)
    spec = ex.settings.devices[0]
    agente = Agent(ex.settings)
    agente.executor = ex
    enviados: list[dict[str, Any]] = []

    async def send(payload: dict[str, Any]) -> bool:
        enviados.append(payload)
        return True

    monkeypatch.setattr(agente, "_send", send)

    async def run(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise VerbUncertain("o aparelho não completou o boot", dados={"emulator_log": "emulator: PANIC"})

    monkeypatch.setattr(ex, "run", run)
    await agente._executar(Dispatch(command_id="c-1", fence=1, verb="start", instance_id=spec.instance_id,
                                    serial="emulator-5554"))
    resultado = next(p for p in enviados if p["type"] == "result")
    assert resultado["outcome"] == "uncertain"
    assert resultado["data"]["emulator_log"] == "emulator: PANIC"


# ---------------------------------------------------------------- o central e o painel
async def test_boot_local_que_falha_manda_o_log_para_o_painel(tmp_path: Path) -> None:
    """A mesma forma no aparelho DAQUI: o `LocalWorker` anexa a cauda ao desfecho, e o `CommandDTO` a expõe.

    Sem isso o operador teria o log de um lado (arquivo na máquina) e o desfecho do outro (painel), que é
    exatamente a distância que este item existe para fechar.
    """
    from app.main import create_app

    h = Harness(tmp_path, 3)
    await h.boot()
    assert h.state is not None
    devs, rt = h.state.devices, h.state.devices.get("android-01")
    await devs.stop_instance(rt)
    devs.fake_boot_refusal = "Capacidade do host atingida: o emulador não subiu"
    h.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    (h.cfg.logs_dir / f"emulator-{rt.avd_name}.log").write_text(
        "emulator: ERROR: x86_64 emulation requires hardware acceleration\npassword=nao_deve_vazar\n",
        encoding="utf-8")
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post("/api/instances/android-01/actions/start",
                                json={"idempotency_key": "log-local-1"})).json()["command_id"]
            await h.wait(lambda: h.state.commands.get(cid)["state"] in  # type: ignore[union-attr]
                         (CommandState.failed.value, CommandState.uncertain.value), what="o comando fechar mal")
            dto = command_dto(h.state.commands.get(cid))
        assert dto.emulator_log and "requires hardware acceleration" in dto.emulator_log
        assert "nao_deve_vazar" not in dto.emulator_log
    finally:
        await h.state.stop()


async def test_comando_que_deu_certo_nao_carrega_log(tmp_path: Path) -> None:
    """O log é diagnóstico de fracasso. Anexá-lo a todo `start` bem-sucedido encheria o banco e a tela de ruído."""
    h = Harness(tmp_path, 3)
    await h.boot()
    assert h.state is not None
    try:
        rt = h.state.devices.get("android-01")
        await h.state.devices.stop_instance(rt)
        from app.main import create_app

        app = create_app(h.cfg, state=h.state)
        app.state.poc = h.state
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post("/api/instances/android-01/actions/start",
                                json={"idempotency_key": "log-local-2"})).json()["command_id"]
            await h.wait(lambda: h.state.commands.get(cid)["state"] ==  # type: ignore[union-attr]
                         CommandState.succeeded.value, what="o start concluir")
            assert command_dto(h.state.commands.get(cid)).emulator_log is None
    finally:
        await h.state.stop()
