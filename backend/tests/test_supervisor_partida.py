"""29.124: o supervisor distingue "subindo devagar" de "travado" pela marca da partida.

No incidente de 05/10, com o disco saturado, a partida do backend passou da carência (90 s e mais três conferências)
e virou laço: cinco kills em 18 min. A marca (`id`, `fase`, `ts`) é desta subida pelo id que o supervisor sorteia e
passa em `POC_PARTIDA_ID`; o PID não serve, porque no Windows o `python.exe` do venv é um lançador.

Tudo falso: partida, saúde, sono, relógios e marca. Nenhum processo sobe.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app import marca_de_partida, supervisor
from app.marca_de_partida import Marca
from app.supervisor import Supervisor

from .conftest import Harness


class ProcessoFalso:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.codigo: int | None = None

    def poll(self) -> int | None:
        return self.codigo

    def terminate(self) -> None:
        self.codigo = 0

    def kill(self) -> None:
        self.codigo = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.codigo or 0


class Bancada:
    """Supervisor com relógio falso que anda com o sono; o backend nunca responde (a partida é lenta ou travou)."""

    def __init__(self, *, saude: list[bool] | None = None) -> None:
        self.saude = list(saude or [])
        self.processos: list[ProcessoFalso] = []
        self.ids_passados: list[str | None] = []
        self.sonos: list[float] = []
        self.agora = 0.0
        self.marca: Marca | None = None
        self.sorteios = 0
        self.sup = Supervisor(iniciar=self._iniciar, saudavel=self._saudavel, encerrar=lambda p: p.terminate(),
                              dormir=self._dormir, carencia_s=90.0, intervalo_s=15.0, espera_min_s=5.0,
                              espera_max_s=60.0, ler_marca=lambda: self.marca, nova_partida=self._sortear,
                              relogio=lambda: self.agora, relogio_de_parede=lambda: 1_000_000.0 + self.agora)

    def _sortear(self) -> str:
        self.sorteios += 1
        return f"partida-{self.sorteios}"

    def _iniciar(self) -> ProcessoFalso:
        self.ids_passados.append(self.sup.partida_id)
        p = ProcessoFalso(pid=1000 + len(self.processos))
        self.processos.append(p)
        return p

    def _saudavel(self) -> bool:
        vivo = self.processos and self.processos[-1].poll() is None
        if not vivo:
            return False
        return self.saude.pop(0) if self.saude else False

    def _dormir(self, s: float) -> None:
        self.sonos.append(s)
        self.agora += s

    def marcar(self, fase: str, *, ha_s: float = 0.0, ident: str | None = None) -> None:
        """A marca que o backend teria gravado `ha_s` segundos atrás (relógio de parede)."""
        self.marca = Marca(ident or str(self.sup.partida_id), fase, 1_000_000.0 + self.agora - ha_s)


# ================================================================== a tolerância
def test_partida_lenta_desta_subida_nao_conta_como_falha() -> None:
    b = Bancada()
    b.sup.run(ciclos=1)                                   # sobe e dorme a carência
    for _ in range(8):                                    # 8 conferências mudas: 120 s além da carência
        b.marcar(marca_de_partida.ANTES_DO_ESTADO, ha_s=5.0)
        b.sup.run(ciclos=1)
    assert len(b.processos) == 1, "partida lenta, mas viva e desta subida, não pode virar kill"
    assert b.sup.relatorio.esperou_a_partida == 8
    assert b.sup.relatorio.conferencias_falhas == 0


def test_marca_de_outra_subida_nao_da_tolerancia() -> None:
    b = Bancada()
    b.sup.run(ciclos=1)
    b.marcar(marca_de_partida.ESTADO_PRONTO, ident="partida-de-outra-subida")
    b.sup.run(ciclos=3)
    assert b.sup.relatorio.esperou_a_partida == 0
    assert b.sup.relatorio.reiniciou_por_silencio == 1, "a regra de sempre: três falhas e reinício"
    assert len(b.processos) == 2


def test_sem_marca_vale_a_regra_de_sempre() -> None:
    b = Bancada()
    b.sup.run(ciclos=4)
    assert b.sup.relatorio.esperou_a_partida == 0
    assert b.sup.relatorio.reiniciou_por_silencio == 1


def test_marca_parada_alem_do_prazo_conta_como_falha_mesmo_abaixo_do_teto() -> None:
    b = Bancada()
    b.sup.run(ciclos=1)                                   # 90 s desde a subida: bem abaixo dos 600
    b.marcar(marca_de_partida.ESTADO_PRONTO, ha_s=supervisor.PRAZO_DA_FASE_S + 1)
    b.sup.run(ciclos=3)
    assert b.agora < supervisor.TETO_DA_PARTIDA_S
    assert b.sup.relatorio.esperou_a_partida == 0
    assert b.sup.relatorio.reiniciou_por_silencio == 1


def test_acima_do_teto_conta_como_falha_mesmo_com_marca_recente() -> None:
    b = Bancada()
    b.sup.run(ciclos=1)
    while b.agora < supervisor.TETO_DA_PARTIDA_S:
        b.marcar(marca_de_partida.INICIANDO, ha_s=1.0)
        b.sup.run(ciclos=1)
    assert len(b.processos) == 1, "abaixo do teto, com a marca andando, a partida ainda é tolerada"
    b.marcar(marca_de_partida.INICIANDO, ha_s=1.0)
    b.sup.run(ciclos=3)
    assert b.sup.relatorio.reiniciou_por_silencio == 1
    assert len(b.processos) == 2


def test_backend_no_ar_e_mudo_segue_a_regra_das_tres_falhas() -> None:
    b = Bancada()
    b.sup.run(ciclos=1)
    b.marcar(marca_de_partida.NO_AR, ha_s=1.0)
    b.sup.run(ciclos=3)
    assert b.sup.relatorio.esperou_a_partida == 0
    assert b.sup.relatorio.reiniciou_por_silencio == 1


def test_marca_com_horario_no_futuro_nao_da_tolerancia() -> None:
    b = Bancada()
    b.sup.run(ciclos=1)
    b.marcar(marca_de_partida.INICIANDO, ha_s=-60.0)
    b.sup.run(ciclos=3)
    assert b.sup.relatorio.esperou_a_partida == 0


def test_erro_ao_ler_a_marca_e_sem_marca_e_nao_derruba_o_supervisor() -> None:
    b = Bancada()

    def explode() -> Marca | None:
        raise OSError("disco")

    b.sup._ler_marca = explode
    b.sup.run(ciclos=4)
    assert b.sup.relatorio.reiniciou_por_silencio == 1


def test_cada_subida_sorteia_um_id_novo_e_o_passa_ao_backend() -> None:
    b = Bancada()
    b.sup.run(ciclos=4)                                   # sobe, 3 falhas, sobe de novo
    assert b.ids_passados == ["partida-1", "partida-2"]
    b.marcar(marca_de_partida.INICIANDO, ident="partida-1")   # a marca que a subida morta deixou
    b.sup.run(ciclos=1)
    assert b.sup.relatorio.esperou_a_partida == 0


# ================================================================== o teto de reinícios
def test_reinicios_seguidos_passam_a_pausa_longa_e_conferencia_boa_zera() -> None:
    b = Bancada()
    b.sup.run(ciclos=1)
    for _ in range(supervisor.TETO_DE_REINICIOS):
        b.processos[-1].codigo = 1                        # morre na partida, de novo e de novo
        b.sup.run(ciclos=1)
    assert b.sup.relatorio.pausas_longas == 1
    assert supervisor.PAUSA_LONGA_S in b.sonos
    b.saude = [True]
    b.sup.run(ciclos=1)
    assert b.sup.reinicios_seguidos == 0
    b.processos[-1].codigo = 1
    b.sup.run(ciclos=1)
    assert b.sonos[-2] == 5.0, "depois de uma conferência boa, o próximo reinício volta a ser rápido"


# ================================================================== o lançamento
def test_iniciar_backend_passa_o_id_e_nao_repassa_um_id_herdado(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    capturas: list[dict[str, str]] = []

    def popen(*_a: Any, env: dict[str, str], **_k: Any) -> ProcessoFalso:
        capturas.append(env)
        return ProcessoFalso(1)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv(marca_de_partida.VARIAVEL, "id-herdado")
    supervisor.iniciar_backend(tmp_path, tmp_path / "logs", "id-desta-subida")
    supervisor.iniciar_backend(tmp_path, tmp_path / "logs", None)
    assert capturas[0][marca_de_partida.VARIAVEL] == "id-desta-subida"
    assert marca_de_partida.VARIAVEL not in capturas[1]
    assert capturas[0][marca_de_partida.PASTA] == str(tmp_path), "N5: a pasta onde o supervisor lê marca e despejo"
    assert "PATH" in capturas[0], "o backend precisa da própria configuração: o ambiente segue herdado"


# ================================================================== a marca em disco
def test_marca_grava_so_id_fase_e_ts_e_le_de_volta(tmp_path: Path) -> None:
    marca_de_partida.gravar(tmp_path, marca_de_partida.INICIANDO, partida_id="abc", agora=lambda: 123.5)
    assert json.loads((tmp_path / marca_de_partida.ARQUIVO).read_text(encoding="utf-8")) == {
        "id": "abc", "fase": "iniciando", "ts": 123.5}
    assert marca_de_partida.ler(tmp_path) == Marca("abc", "iniciando", 123.5)
    assert [p.name for p in tmp_path.iterdir()] == [marca_de_partida.ARQUIVO], "o temporário não pode sobrar"


def test_sem_id_nao_grava(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(marca_de_partida.VARIAVEL, raising=False)
    marca_de_partida.gravar(tmp_path, marca_de_partida.ANTES_DO_ESTADO)
    assert not (tmp_path / marca_de_partida.ARQUIVO).exists()


def test_erro_de_escrita_e_engolido(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def falha(*_a: Any) -> None:
        raise PermissionError("o supervisor está lendo")

    monkeypatch.setattr(os, "replace", falha)
    marca_de_partida.gravar(tmp_path, marca_de_partida.NO_AR, partida_id="abc")   # não levanta


@pytest.mark.parametrize("conteudo", ['{"id": "abc", "fase": "inic', "[]", '{"id": 1, "fase": "x", "ts": 1}',
                                      '{"id": "a", "fase": "x", "ts": true}', '{"id": "a", "fase": "x"}'])
def test_marca_parcial_ou_estranha_e_sem_marca(tmp_path: Path, conteudo: str) -> None:
    (tmp_path / marca_de_partida.ARQUIVO).write_text(conteudo, encoding="utf-8")
    assert marca_de_partida.ler(tmp_path) is None


def test_sem_arquivo_e_sem_marca(tmp_path: Path) -> None:
    assert marca_de_partida.ler(tmp_path) is None


def test_sob_o_supervisor_marca_e_despejo_vao_para_as_pastas_dele(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """N5 da leitura do #421: com `paths.logs_dir` mudado no config, o despejo ia para um lugar e o supervisor
    procurava em outro. A pasta que o supervisor lê é a que ele passa ao backend."""
    cfg_data, cfg_logs = tmp_path / "cfg-data", tmp_path / "outro-logs"
    monkeypatch.delenv(marca_de_partida.PASTA, raising=False)
    assert marca_de_partida.pasta_do_supervisor(cfg_data) == cfg_data
    assert marca_de_partida.pasta_do_supervisor(cfg_logs, "logs") == cfg_logs
    monkeypatch.setenv(marca_de_partida.PASTA, str(tmp_path / "sup"))
    assert marca_de_partida.pasta_do_supervisor(cfg_data) == tmp_path / "sup"
    assert marca_de_partida.pasta_do_supervisor(cfg_logs, "logs") == tmp_path / "sup" / "logs"


# ================================================================== as fases no backend
async def test_o_ciclo_de_vida_marca_iniciando_e_depois_no_ar(harness: Harness,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setenv(marca_de_partida.VARIAVEL, "id-da-subida")
    monkeypatch.delenv(marca_de_partida.PASTA, raising=False)
    vistas: list[str | None] = []

    class EstadoFalso:
        async def start(self) -> None:
            m = marca_de_partida.ler(harness.cfg.data_dir)
            vistas.append(m.fase if m else None)

        async def stop(self) -> None:
            pass

    app = create_app(harness.cfg, state=EstadoFalso())  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        m = marca_de_partida.ler(harness.cfg.data_dir)
    assert vistas == [marca_de_partida.INICIANDO]
    assert m is not None and (m.id, m.fase) == ("id-da-subida", marca_de_partida.NO_AR)
