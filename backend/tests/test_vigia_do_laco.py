"""29.121: o vigia do laço de eventos grava a pilha de todas as threads quando o laço para de bater."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path

import pytest

from app import supervisor as sup
from app import vigia_do_laco
from app.vigia_do_laco import PREFIXO, VigiaDoLaco, ultimo_despejo

from .conftest import Harness


class _Relogio:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora


def _vigia(pasta: Path, relogio: _Relogio, **kw: float) -> VigiaDoLaco:
    return VigiaDoLaco(pasta, limite_s=10, partida_s=120, redespejo_s=30, relogio=relogio, **kw)  # type: ignore[arg-type]


def test_laco_batendo_nao_despeja(tmp_path: Path) -> None:
    r = _Relogio()
    v = _vigia(tmp_path, r)
    for _ in range(30):
        r.agora += 1
        v.bater()
        assert v.conferir() is None
    assert not list(tmp_path.glob(f"{PREFIXO}*"))


def test_laco_parado_despeja_a_pilha_de_todas_as_threads(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """O arquivo traz o cabeçalho com o atraso e a pilha de TODAS as threads, inclusive a que segura o laço."""
    r = _Relogio()
    v = _vigia(tmp_path, r)
    v.bater()
    pronto, soltar = threading.Event(), threading.Event()

    def presa_no_disco() -> None:              # uma thread com nome reconhecível na pilha
        pronto.set()
        soltar.wait(10)
    t = threading.Thread(target=presa_no_disco, name="presa")
    t.start()
    pronto.wait(5)
    try:
        r.agora += 10.5
        with caplog.at_level(logging.WARNING, logger="poc.vigia"):
            arquivo = v.conferir()
    finally:
        soltar.set()
        t.join(5)
    assert arquivo is not None and arquivo.exists()
    texto = arquivo.read_text(encoding="utf-8")
    assert texto.startswith("laço de eventos sem batida há 10.5 s (laço)")
    assert "presa_no_disco" in texto and "test_laco_parado_despeja" in texto      # as duas threads
    assert str(arquivo) in caplog.text


def test_um_episodio_tem_no_maximo_tres_despejos_espacados(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    r = _Relogio()
    v = _vigia(tmp_path, r)
    v.bater()
    despejos = []
    for _ in range(200):                      # 200 s parado, conferindo a cada segundo
        r.agora += 1
        if (a := v.conferir()) is not None:
            despejos.append(round(r.agora - 1000))
    assert despejos == [11, 41, 71]           # no limite, e a cada 30 s, até 3
    with caplog.at_level(logging.WARNING, logger="poc.vigia"):
        v.bater()
        assert v.conferir() is None
    assert "voltou a bater depois de 200.0 s parado (3 despejo(s) de pilha)" in caplog.text
    r.agora += 15                             # episódio novo: despeja de novo
    assert v.conferir() is not None


def test_a_partida_tem_prazo_proprio_antes_da_primeira_batida(tmp_path: Path) -> None:
    """A partida presa (sem batida nenhuma) também deixa pilha, mas com o prazo da partida, não o do laço."""
    r = _Relogio()
    v = _vigia(tmp_path, r)
    r.agora += 60
    assert v.conferir() is None               # 60 s sem batida na partida: normal
    r.agora += 61
    arquivo = v.conferir()
    assert arquivo is not None
    assert "(partida (antes da primeira batida))" in arquivo.read_text(encoding="utf-8").splitlines()[0]


def test_o_prazo_da_partida_vence_antes_da_primeira_conferencia_do_supervisor() -> None:
    """C1 da leitura do #421: com 120 s, o despejo da partida presa síncrona empatava com o kill (≈120 s depois do
    `Popen`). A carência é o piso de qualquer kill, com ou sem a tolerância da partida do 29.124 (que só o adia)."""
    assert vigia_do_laco.PARTIDA_S + vigia_do_laco.INTERVALO_S < sup.CARENCIA_S


def test_excecao_fora_do_disco_tambem_conta_no_teto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """N2: uma exceção que não é `OSError` no despejo também gasta a tentativa; sem isto, um arquivo por segundo."""
    def quebra(**_k: object) -> None:
        raise RuntimeError("faulthandler")

    monkeypatch.setattr(vigia_do_laco.faulthandler, "dump_traceback", quebra)
    r = _Relogio()
    v = _vigia(tmp_path, r)
    r.agora += 200
    for _ in range(100):
        r.agora += 1
        v.conferir()
    assert len(list(tmp_path.glob(f"{PREFIXO}*.txt"))) <= vigia_do_laco.DESPEJOS_POR_EPISODIO


def test_ultimo_despejo_ignora_o_que_some_no_meio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    some = tmp_path / f"{PREFIXO}20261005T130000Z-1.txt"
    fica = tmp_path / f"{PREFIXO}20261005T130100Z-1.txt"
    some.write_text("x", encoding="utf-8")
    fica.write_text("x", encoding="utf-8")
    original = Path.stat

    def stat(self: Path, *a: object, **k: object) -> os.stat_result:
        if self.name == some.name:
            raise FileNotFoundError(self)
        return original(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", stat)
    assert ultimo_despejo(tmp_path) == fica


def test_ficam_so_os_mais_novos(tmp_path: Path) -> None:
    for i in range(25):
        (tmp_path / f"{PREFIXO}20261005T1200{i:02d}Z-1.txt").write_text("velho", encoding="utf-8")
    r = _Relogio()
    v = _vigia(tmp_path, r, manter=20)
    r.agora += 200
    assert v.conferir() is not None
    restantes = sorted(p.name for p in tmp_path.glob(f"{PREFIXO}*.txt"))
    assert len(restantes) == 20 and f"{PREFIXO}20261005T120000Z-1.txt" not in restantes


def test_disco_que_falha_ainda_avisa_sem_derrubar(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    arquivo_no_lugar_da_pasta = tmp_path / "nao-e-pasta"
    arquivo_no_lugar_da_pasta.write_text("x", encoding="utf-8")
    r = _Relogio()
    v = _vigia(arquivo_no_lugar_da_pasta, r)
    r.agora += 200
    with caplog.at_level(logging.WARNING, logger="poc.vigia"):
        assert v.conferir() is None
    assert "a pilha não pôde ser gravada" in caplog.text


async def test_a_batida_vem_do_laco_e_o_laco_preso_e_visto(tmp_path: Path) -> None:
    """Fim a fim com relógio real: a tarefa bate pelo laço; um `time.sleep` no laço (o bloqueio que se procura)
    deixa a batida envelhecer, e a thread do vigia despeja sozinha."""
    v = VigiaDoLaco(tmp_path, limite_s=1.0, partida_s=5, redespejo_s=10, intervalo_s=0.1)
    batidas = asyncio.create_task(v.laco_de_batidas())
    v.iniciar()
    try:
        await asyncio.sleep(0.3)
        assert not list(tmp_path.glob(f"{PREFIXO}*"))
        time.sleep(3)                         # o laço preso de propósito (folga larga sobre o limite, para carga)
        await asyncio.sleep(0.2)
        arquivos = list(tmp_path.glob(f"{PREFIXO}*.txt"))
        assert len(arquivos) == 1
        assert "test_a_batida_vem_do_laco" in arquivos[0].read_text(encoding="utf-8")   # a pilha do laço preso
    finally:
        v.parar()
        batidas.cancel()


def test_ultimo_despejo_so_os_recentes(tmp_path: Path) -> None:
    velho = tmp_path / f"{PREFIXO}20261005T120000Z-1.txt"
    novo = tmp_path / f"{PREFIXO}20261005T130000Z-1.txt"
    velho.write_text("x", encoding="utf-8")
    novo.write_text("x", encoding="utf-8")
    antigo = time.time() - 3600
    os.utime(velho, (antigo, antigo))
    assert ultimo_despejo(tmp_path) == novo
    os.utime(novo, (antigo, antigo))
    assert ultimo_despejo(tmp_path) is None


class _Proc:
    pid = 4242

    def poll(self) -> None:
        return None


def test_supervisor_cita_o_despejo_na_linha_do_kill(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    despejo = tmp_path / f"{PREFIXO}20261005T130000Z-1.txt"
    s = sup.Supervisor(iniciar=_Proc, saudavel=lambda: False, encerrar=lambda p: None, dormir=lambda s: None,
                       despejo=lambda: despejo)
    s.proc = _Proc()
    with caplog.at_level(logging.WARNING, logger="poc.supervisor"):
        s.run(ciclos=3)
    assert f"encerrando o backend (pid 4242): 3 conferências seguidas sem resposta; pilha do laço travado em {despejo}" \
        in caplog.text


def test_erro_ao_procurar_o_despejo_nao_impede_o_kill(caplog: pytest.LogCaptureFixture) -> None:
    encerrados: list[object] = []

    def explode() -> Path | None:
        raise FileNotFoundError("sumiu entre a lista e o stat")

    s = sup.Supervisor(iniciar=_Proc, saudavel=lambda: False, encerrar=encerrados.append, dormir=lambda s: None,
                       despejo=explode)
    s.proc = _Proc()
    with caplog.at_level(logging.WARNING, logger="poc.supervisor"):
        s.run(ciclos=3)
    assert len(encerrados) == 1, "o backend travado tem de morrer mesmo sem a citação"
    assert "sem despejo de pilha do vigia" in caplog.text


def test_supervisor_sem_despejo_diz_que_nao_ha(caplog: pytest.LogCaptureFixture) -> None:
    s = sup.Supervisor(iniciar=_Proc, saudavel=lambda: False, encerrar=lambda p: None, dormir=lambda s: None)
    s.proc = _Proc()
    with caplog.at_level(logging.WARNING, logger="poc.supervisor"):
        s.run(ciclos=3)
    assert "3 conferências seguidas sem resposta; sem despejo de pilha do vigia" in caplog.text


class _EstadoQueSobe:
    """O `AppState` falso: a partida espera (é onde um laço preso apareceria) e diz se a batida já começou."""
    def __init__(self, vigia: VigiaDoLaco) -> None:
        self.vigia = vigia
        self.bateu_antes_de_subir: bool | None = None

    async def start(self) -> None:
        await asyncio.sleep(0.05)
        self.bateu_antes_de_subir = self.vigia._bateu                    # noqa: SLF001

    async def stop(self) -> None:
        return None


async def test_a_batida_comeca_antes_da_partida_do_estado(harness: Harness, tmp_path: Path) -> None:
    """A partida do `AppState` presa no laço também tem de deixar pilha: a batida começa ANTES do `poc.start()`."""
    from app.main import create_app

    v = VigiaDoLaco(tmp_path, intervalo_s=0.01)
    estado = _EstadoQueSobe(v)
    app = create_app(harness.cfg, state=estado, vigia=v)                  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        assert estado.bateu_antes_de_subir is True
    await asyncio.sleep(0.05)
    antes = v._ultima                                                      # noqa: SLF001
    await asyncio.sleep(0.05)
    assert v._ultima == antes, "a batida seguiu depois do fim do lifespan"  # noqa: SLF001
