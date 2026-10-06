"""28.58: o aviso de disco baixo no central, nos dois lados do piso e nos degraus.

Prova `simulated`: leitor de disco, medida de pastas e relógio falsos; canal falso. O real (a primeira leitura no central,
com o número) é `not_run`.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.modules.avisos.domain import host as h
from app.modules.avisos.infrastructure.vigia_do_host import VigiaDoHost, medir_pasta_gb

from .test_avisos_servico import CanalFalso, Relogio, _backend, _cfg


class DiscoFalso:
    def __init__(self, livre: float | None, total: float = 500.0) -> None:
        self.livre, self.total, self.lidas = livre, total, 0

    def __call__(self, pasta: Path) -> tuple[float, float] | None:
        self.lidas += 1
        return None if self.livre is None else (self.livre, self.total)


def _montar(tmp_path: Path, disco: DiscoFalso, medidas: list[int] | None = None):  # type: ignore[no-untyped-def]
    cfg = _cfg(tmp_path)
    r = Relogio()
    servico, db, _ = _backend(cfg, "a", r, canal=CanalFalso())

    def medir() -> dict[str, float | None]:
        if medidas is not None:
            medidas.append(1)
        return {"backups": 12.4, "AVDs": 340.0, "capturas": None, "Docker": None}

    vigia = VigiaDoHost(cfg, servico.enfileirar_aviso, pronto=lambda: servico.ligado and servico.canal() is not None,
                        ler_disco=disco, relogio=r, medir=medir)
    return vigia, db, r


def _linhas(db) -> list[dict]:  # type: ignore[no-untyped-def]
    return [dict(x) for x in db.query("SELECT chave, tipo, titulo, corpo FROM avisos_entregas ORDER BY id")]


def _tique(vigia: VigiaDoHost):  # type: ignore[no-untyped-def]
    return asyncio.run(vigia.conferir_disco())


@pytest.mark.parametrize(("livre", "esperado"), [(100.0, None), (250.0, None), (99.9, 100.0), (80.0, 100.0),
                                                 (79.9, 80.0), (60.0, 80.0), (59.0, 60.0), (20.1, 40.0), (20.0, 40.0),
                                                 (19.9, 20.0), (0.5, 20.0)])
def test_degrau_do_disco(livre: float, esperado: float | None) -> None:
    assert h.degrau_do_disco(livre, 100.0, 20.0) == esperado


def test_acima_do_piso_nao_avisa_nem_mede(tmp_path: Path) -> None:
    medidas: list[int] = []
    vigia, db, _ = _montar(tmp_path, DiscoFalso(100.0), medidas)
    assert _tique(vigia) is None and _linhas(db) == [] and medidas == []


def test_abaixo_do_piso_avisa_com_o_que_ocupa(tmp_path: Path) -> None:
    vigia, db, _ = _montar(tmp_path, DiscoFalso(85.0))
    aviso = _tique(vigia)
    assert aviso is not None
    (linha,) = _linhas(db)
    assert linha["tipo"] == h.TIPO_DO_DISCO and linha["chave"].endswith(":0") and ":100:" in linha["chave"]
    assert "85 GB livres (abaixo de 100 GB)" in linha["titulo"]
    corpo = linha["corpo"]
    assert "Livre: 85 GB de 500 GB" in corpo
    assert "backups 12 GB" in corpo and "AVDs 340 GB" in corpo and "capturas não medido" in corpo and "Docker não medido" in corpo
    assert "Crítico: nada." in corpo and "abaixo de 80 GB" in corpo        # acima de 60 GB não é crítico
    assert "apaga" not in corpo.replace("não apaga nada", "")


def test_abaixo_de_60_e_critico_e_espera_o_dono(tmp_path: Path) -> None:
    vigia, db, _ = _montar(tmp_path, DiscoFalso(55.0))
    _tique(vigia)
    (linha,) = _linhas(db)
    assert "Crítico: backups e criação de aparelho podem falhar." in linha["corpo"]
    assert "Espera você:" in linha["corpo"] and "a Central não apaga nada" in linha["corpo"]
    assert "abaixo de 60 GB" in linha["titulo"]


def test_degraus_avisam_uma_vez_cada_e_so_para_baixo(tmp_path: Path) -> None:
    disco = DiscoFalso(95.0)
    vigia, db, _ = _montar(tmp_path, disco)
    for livre in (95.0, 90.0, 85.0):                        # mesmo degrau (100): um aviso só
        disco.livre = livre
        _tique(vigia)
    assert len(_linhas(db)) == 1
    disco.livre = 79.0
    _tique(vigia)
    assert len(_linhas(db)) == 2
    disco.livre = 85.0                                      # subiu um degrau, ainda abaixo do piso: não repete
    assert _tique(vigia) is None
    disco.livre = 79.0                                      # e voltar ao degrau já avisado também não
    assert _tique(vigia) is None and len(_linhas(db)) == 2
    disco.livre = 20.0                                      # cai de uma vez para vários degraus: um aviso, o do degrau fundo
    _tique(vigia)
    assert len(_linhas(db)) == 3 and ":40:" in _linhas(db)[-1]["chave"]


def test_voltar_ao_piso_rearma_e_a_recaida_do_mesmo_dia_avisa(tmp_path: Path) -> None:
    disco = DiscoFalso(90.0)
    vigia, db, _ = _montar(tmp_path, disco)
    _tique(vigia)
    disco.livre = 130.0
    assert _tique(vigia) is None                            # rearmou
    disco.livre = 90.0
    assert _tique(vigia) is not None                        # recaída: outro episódio, chave nova
    chaves = [x["chave"] for x in _linhas(db)]
    assert len(chaves) == 2 and chaves[0] != chaves[1]


def test_reinicio_no_mesmo_dia_nao_repete_o_aviso(tmp_path: Path) -> None:
    disco = DiscoFalso(90.0)
    vigia, db, r = _montar(tmp_path, disco)
    _tique(vigia)
    novo = VigiaDoHost(vigia.cfg, vigia._avisar, pronto=vigia._pronto, ler_disco=disco, relogio=r,  # noqa: SLF001
                       medir=lambda: {"backups": 1.0})
    _tique(novo)                                            # processo novo, episódio 0, mesma chave do dia
    assert len(_linhas(db)) == 1


def test_leitura_do_disco_falhou_nao_avisa(tmp_path: Path) -> None:
    vigia, db, _ = _montar(tmp_path, DiscoFalso(None))
    assert _tique(vigia) is None and _linhas(db) == []


def test_desligado_nao_le_nem_avisa(tmp_path: Path) -> None:
    disco = DiscoFalso(10.0)
    vigia, db, _ = _montar(tmp_path, disco)
    vigia.cfg.file.avisos.disco.enabled = False
    assert _tique(vigia) is None and disco.lidas == 0 and _linhas(db) == []


def test_piso_e_degrau_vem_da_config(tmp_path: Path) -> None:
    disco = DiscoFalso(45.0)
    vigia, db, _ = _montar(tmp_path, disco)
    vigia.cfg.file.avisos.disco.piso_gb, vigia.cfg.file.avisos.disco.degrau_gb = 50.0, 10.0
    _tique(vigia)
    assert ":50:" in _linhas(db)[0]["chave"]


def test_a_medida_das_pastas_e_cacheada(tmp_path: Path) -> None:
    medidas: list[int] = []
    disco = DiscoFalso(90.0)
    vigia, _, _ = _montar(tmp_path, disco, medidas)
    _tique(vigia)
    disco.livre = 70.0
    _tique(vigia)
    assert len(medidas) == 1                                # dois avisos, uma varredura de pastas


def test_medir_pasta_soma_so_os_arquivos_e_respeita_o_orcamento(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "x.bin").write_bytes(b"0" * 1000)
    (tmp_path / "a" / "b" / "y.bin").write_bytes(b"0" * 3000)
    assert medir_pasta_gb(tmp_path / "a") == pytest.approx(4000 / 2**30)
    assert medir_pasta_gb(tmp_path / "nao-existe") is None
    assert medir_pasta_gb(tmp_path / "a", orcamento_s=0.0) is None      # estourou o tempo: "não medido"


@pytest.mark.skipif(os.name != "nt", reason="junção é do Windows")
def test_medir_pasta_nao_segue_juncao(tmp_path: Path) -> None:
    import subprocess

    alvo, medida = tmp_path / "alvo", tmp_path / "medida"
    alvo.mkdir()
    medida.mkdir()
    (alvo / "grande.bin").write_bytes(b"0" * 5000)
    (medida / "p.bin").write_bytes(b"0" * 10)
    feito = subprocess.run(["cmd", "/c", "mklink", "/J", str(medida / "j"), str(alvo)], capture_output=True)
    if feito.returncode != 0:
        pytest.skip("sem permissão para criar junção")
    assert medir_pasta_gb(medida) == pytest.approx(10 / 2**30)


def test_o_leitor_e_o_da_saude() -> None:
    from app.devices.diagnostics import ler_disco

    livre, total = ler_disco(Path.cwd()) or (0.0, 0.0)
    assert total > 0 and 0 <= livre <= total
