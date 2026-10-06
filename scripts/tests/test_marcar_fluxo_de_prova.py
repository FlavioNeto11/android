"""31.130: scripts/marcar-fluxo-de-prova.py marca, pelo id, os fluxos de prova salvos antes da marca e a sessão de
treino de origem. Banco INVENTADO num diretório temporário (migrado pelo código); nenhum dado do parque é lido.

Nível de prova: `simulated`."""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ / "backend") not in sys.path:
    sys.path.insert(0, str(RAIZ / "backend"))

from app.db import Database  # noqa: E402

SCRIPT = RAIZ / "scripts" / "marcar-fluxo-de-prova.py"
_spec = importlib.util.spec_from_file_location("marcar_fluxo_de_prova", SCRIPT)
assert _spec is not None and _spec.loader is not None
marcador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(marcador)

AGORA = "2026-10-06T10:00:00.000Z"


def _banco(pasta: Path) -> Path:
    caminho = pasta / "poc.sqlite3"
    db = Database(caminho)
    db.migrate()
    for sid in ("trn-prova", "trn-real"):
        db.execute("INSERT INTO training_sessions(id, instance_id, intent, status, created_at, updated_at)"
                   " VALUES (?,?,?,?,?,?)", (sid, "android-01", "buscar", "saved", AGORA, AGORA))
    for fid, fonte, ref in (("f-prova", "training:trn-prova", "ref-prova"), ("f-real", "training:trn-real", "ref-real"),
                            ("f-execucao", None, "ref-execucao")):
        db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, status, uses, created_at, source,"
                   " ref_publico) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (fid, fid, f"busque {fid}", f"busque {fid}", "{}", "disabled", 0, AGORA, fonte, ref))
    db.close()
    return caminho


def _marcas(caminho: Path) -> dict[str, int | None]:
    db = Database(caminho)
    try:
        return {**{str(r["id"]): r["nascido_de_prova"] for r in db.query("SELECT id, nascido_de_prova FROM flows")},
                **{str(r["id"]): r["nascido_de_prova"] for r in db.query("SELECT id, nascido_de_prova FROM training_sessions")}}
    finally:
        db.close()


def test_o_ensaio_conta_sem_gravar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco(tmp_path)
    assert marcador.main(["--banco", str(banco), "--fluxo", "f-prova"]) == 0
    saida = capsys.readouterr().out
    assert "ENSAIO" in saida and "fluxos_marcados=1, sessoes_marcadas=1" in saida and "marcados f-prova" in saida
    assert set(_marcas(banco).values()) == {None}                       # o original não foi tocado


def test_aplicar_exige_backup_e_marca_uma_vez(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco(tmp_path)
    with pytest.raises(SystemExit):
        marcador.main(["--banco", str(banco), "--fluxo", "f-prova", "--aplicar"])
    with pytest.raises(SystemExit):
        marcador.main(["--banco", str(banco), "--fluxo", "f-prova", "--aplicar", "--backup", str(tmp_path / "nao-existe")])
    backup = tmp_path / "backup"
    backup.mkdir()
    shutil.copy(banco, backup / "poc.sqlite3")
    # pela referência pública também; a sessão de origem vai junto; o fluxo de execução não tem sessão
    assert marcador.main(["--banco", str(banco), "--fluxo", "ref-prova", "--fluxo", "f-execucao", "--aplicar",
                          "--backup", str(backup)]) == 0
    assert _marcas(banco) == {"f-prova": 1, "f-execucao": 1, "f-real": None, "trn-prova": 1, "trn-real": None}
    capsys.readouterr()
    assert marcador.main(["--banco", str(banco), "--fluxo", "f-prova", "--aplicar", "--backup", str(backup)]) == 0
    saida = capsys.readouterr().out
    assert "fluxos_marcados=0, sessoes_marcadas=0" in saida and "ja_marcados f-prova" in saida   # idempotente


def test_id_inexistente_sai_com_codigo_1_e_banco_antigo_aborta(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco(tmp_path)
    assert marcador.main(["--banco", str(banco), "--fluxo", "f-nao-existe"]) == 1
    assert "inexistentes f-nao-existe" in capsys.readouterr().out
    db = Database(banco)
    # a MAIOR migração some (não a 122 pelo nome: com uma migração depois dela, o banco continuaria igual ao código)
    db.execute("DELETE FROM schema_migrations WHERE version=(SELECT MAX(version) FROM schema_migrations)")
    db.close()
    assert marcador.main(["--banco", str(banco), "--fluxo", "f-prova"]) == 2          # código e banco divergem
    assert marcador.main(["--banco", str(tmp_path / "nada.sqlite3"), "--fluxo", "f-prova"]) == 2
