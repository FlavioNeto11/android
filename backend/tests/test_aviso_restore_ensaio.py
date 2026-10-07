"""28.60: o aviso do ensaio de restauração, lido do `ultimo.json` por um tique do vigia do host.

Prova `simulated`: `ultimo.json` falso (bom, falho, pulado, velho, ausente, JSON quebrado), canal falso, sem rede. O real
(forçar uma falha numa cópia de teste e ver a rotina chegar ao Telegram) é `not_run`.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from app.modules.avisos.domain import host as h
from app.modules.avisos.domain.mensagem import (
    JANELA,
    ROTINA,
    ROTULOS,
    TIPOS_DA_JANELA,
    entrega_do_tipo,
    nivel_do_tipo,
)
from app.modules.avisos.infrastructure.fila_sql import SEM_AGRUPAR
from app.modules.avisos.infrastructure.vigia_do_host import ILEGIVEIS_PARA_AVISAR, VigiaDoHost

from .test_avisos_servico import CanalFalso, Relogio, _backend, _cfg


def _montar(tmp_path: Path, **cfg_ensaio: object):  # type: ignore[no-untyped-def]
    cfg = _cfg(tmp_path)
    arquivo = tmp_path / "ultimo.json"
    cfg.file.avisos.restore_ensaio.ultimo_json = str(arquivo)
    for k, v in cfg_ensaio.items():
        setattr(cfg.file.avisos.restore_ensaio, k, v)
    r = Relogio()
    servico, db, _ = _backend(cfg, "a", r, canal=CanalFalso())
    vigia = VigiaDoHost(cfg, servico.enfileirar_aviso, pronto=lambda: servico.ligado and servico.canal() is not None,
                        ler_disco=lambda p: None, relogio=r)
    return vigia, db, r, arquivo


def _veredito(r, resultado: str = "ok", *, horas: float = 1.0, **extra: object) -> dict:  # type: ignore[no-untyped-def]
    quando = r() - timedelta(hours=horas)
    base = {"ts_utc": quando.strftime("%Y-%m-%dT%H:%M:%SZ"), "resultado": resultado, "motivo": "",
            "copia": "20261005-023011", "idade_h": 5.0}
    base.update(extra)
    return base


def _grava(arquivo: Path, dados: object) -> None:
    arquivo.write_text(json.dumps(dados), encoding="utf-8")


def _linhas(db) -> list[dict]:  # type: ignore[no-untyped-def]
    return [dict(x) for x in db.query("SELECT chave, tipo, titulo, corpo FROM avisos_entregas ORDER BY id")]


def test_veredito_bom_nao_avisa(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path)
    _grava(arquivo, _veredito(r))
    assert vigia.conferir_ensaio() == [] and _linhas(db) == []


def test_arquivo_ausente_nao_avisa_nem_na_primeira_semana(tmp_path: Path) -> None:
    vigia, db, _, _ = _montar(tmp_path)
    for _ in range(3):
        assert vigia.conferir_ensaio() == []
    assert _linhas(db) == []


def test_falhou_avisa_uma_vez_por_veredito(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path)
    dados = _veredito(r, "falhou", motivo="migração da cópia (126) difere da do manifesto (127)")
    _grava(arquivo, dados)
    for _ in range(4):                                   # o tique de 15 em 15 min relê o mesmo arquivo
        vigia.conferir_ensaio()
    linhas = _linhas(db)
    assert len(linhas) == 1 and linhas[0]["tipo"] == h.TIPO_DO_ENSAIO
    assert linhas[0]["chave"] == f"restore-ensaio:falhou:{dados['ts_utc']}"
    assert "falhou (cópia de 05/10)" in linhas[0]["titulo"]
    assert "migração da cópia (126) difere" in linhas[0]["corpo"] and "Crítico:" in linhas[0]["corpo"]
    assert "Espera você:" in linhas[0]["corpo"]
    r.avancar(3600)
    _grava(arquivo, _veredito(r, "falhou"))               # o ensaio da semana seguinte: outro carimbo, outro aviso
    vigia.conferir_ensaio()
    assert len(_linhas(db)) == 2


def test_motivo_com_caminho_nao_sai(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path)
    _grava(arquivo, _veredito(r, "falhou", motivo=r"restore falhou em C:\Users\fulano\data\backups\x"))
    vigia.conferir_ensaio()
    texto = " ".join(f"{x['titulo']} {x['corpo']}" for x in _linhas(db))
    assert "fulano" not in texto and "backups" not in texto and "\\" not in texto and "Users" not in texto
    assert "não conseguiu restaurar a cópia de 05/10." in texto


def test_pulado_usa_frase_fixa(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path)
    _grava(arquivo, _veredito(r, "pulado", copia="", motivo=r"não há cópia com manifesto em data\backups"))
    vigia.conferir_ensaio()
    (linha,) = _linhas(db)
    assert linha["chave"].startswith("restore-ensaio:pulado:") and "sem cópia para ensaiar" in linha["titulo"]
    assert "backups" not in linha["corpo"] and "\\" not in linha["corpo"]
    r.avancar(60)
    _grava(arquivo, _veredito(r, "pulado", copia="20261005-023011", motivo="a cópia mais nova não é SQLite (PostgreSQL ...)"))
    vigia.conferir_ensaio()
    assert "PostgreSQL" in _linhas(db)[-1]["titulo"]


def test_veredito_velho_avisa_um_por_dia_utc(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path)
    _grava(arquivo, _veredito(r, "ok", horas=240))        # 10 dias: passou das 192 h
    vigia.conferir_ensaio()
    vigia.conferir_ensaio()
    (linha,) = _linhas(db)
    assert linha["chave"].startswith("restore-ensaio:velho:") and "sem rodar há 10 dias" in linha["titulo"]
    r.avancar(86400)                                      # o dia seguinte: mais um, enquanto durar
    vigia.conferir_ensaio()
    assert len(_linhas(db)) == 2


def test_veredito_de_3_dias_nao_e_velho_com_o_padrao_semanal(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path)
    _grava(arquivo, _veredito(r, "ok", horas=72))         # terça: a 48 h literal alarmaria toda semana
    assert vigia.conferir_ensaio() == [] and _linhas(db) == []
    (tmp_path / "b").mkdir()
    vigia2, db2, r2, arquivo2 = _montar(tmp_path / "b", idade_max_h=48)
    _grava(arquivo2, _veredito(r2, "ok", horas=72))
    assert len(vigia2.conferir_ensaio()) == 1             # o limite é configurável


def test_json_quebrado_avisa_so_na_segunda_leitura_ruim(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path)
    arquivo.write_text("{ isto não é json", encoding="utf-8")
    for _ in range(ILEGIVEIS_PARA_AVISAR - 1):
        assert vigia.conferir_ensaio() == []
    assert len(vigia.conferir_ensaio()) == 1 and len(vigia.conferir_ensaio()) == 1
    (linha,) = _linhas(db)                                # a mesma chave do dia: uma linha só
    assert linha["chave"].startswith("restore-ensaio:ilegivel:") and "ilegível" in linha["titulo"]
    _grava(arquivo, _veredito(r))                         # voltou a ler: zera a contagem
    assert vigia.conferir_ensaio() == []


def test_json_sem_resultado_ou_sem_carimbo_conta_como_ilegivel(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path)
    for dados in ({"resultado": "ok"}, {"ts_utc": "2026-10-05T10:00:00Z", "resultado": "talvez"}, [1, 2], "x"):
        assert h.veredito_de(dados) is None
    _grava(arquivo, {"resultado": "falhou"})
    for _ in range(ILEGIVEIS_PARA_AVISAR):
        vigia.conferir_ensaio()
    assert len(_linhas(db)) == 1


def test_desligado_ou_canal_fora_nao_grava(tmp_path: Path) -> None:
    vigia, db, r, arquivo = _montar(tmp_path, enabled=False)
    _grava(arquivo, _veredito(r, "falhou"))
    assert vigia.conferir_ensaio() == [] and _linhas(db) == []
    (tmp_path / "x").mkdir()
    desligado = _cfg(tmp_path / "x", ligado=False)
    servico, db2, _ = _backend(desligado, "a", Relogio(), canal=CanalFalso())
    assert servico.enfileirar_aviso(h.aviso_do_ensaio_ilegivel(Relogio()())) is False and _linhas(db2) == []


def test_tipo_e_rotina_na_janela_sem_cartao_e_sem_agrupar() -> None:
    for tipo in (h.TIPO_DO_ENSAIO, h.TIPO_DO_DISCO):
        assert nivel_do_tipo(tipo) == ROTINA and entrega_do_tipo(tipo) == JANELA and tipo in TIPOS_DA_JANELA
        assert tipo not in ROTULOS and not tipo.startswith(SEM_AGRUPAR)
