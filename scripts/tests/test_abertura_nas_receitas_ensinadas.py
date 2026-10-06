"""31.138: a linha de comando de scripts/abertura-nas-receitas-ensinadas.py (ensaio por padrão, `--aplicar` com
backup, banco de outra migração aborta). Banco INVENTADO e migrado pelo código num diretório temporário; a troca em
si é provada em backend/tests/test_treino_reparo_da_abertura.py.

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

SCRIPT = RAIZ / "scripts" / "abertura-nas-receitas-ensinadas.py"
_spec = importlib.util.spec_from_file_location("abertura_nas_receitas_ensinadas", SCRIPT)
assert _spec is not None and _spec.loader is not None
passe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(passe)


def _banco(pasta: Path) -> Path:
    caminho = pasta / "poc.sqlite3"
    db = Database(caminho)
    db.migrate()
    db.close()
    return caminho


def test_ensaio_aplicar_e_banco_de_outra_migracao(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco(tmp_path)
    assert passe.main(["--banco", str(banco)]) == 0
    assert "ENSAIO" in (saida := capsys.readouterr().out) and "fluxos_lidos=0, sem_abertura_antes=0, trocadas=0" in saida
    with pytest.raises(SystemExit):
        passe.main(["--banco", str(banco), "--aplicar"])
    backup = tmp_path / "backup"
    backup.mkdir()
    shutil.copy(banco, backup / "poc.sqlite3")
    assert passe.main(["--banco", str(banco), "--aplicar", "--backup", str(backup)]) == 0
    assert "APLICADO" in capsys.readouterr().out
    db = Database(banco)
    versao = db.scalar("SELECT MAX(version) FROM schema_migrations")
    db.execute("DELETE FROM schema_migrations WHERE version=?", (versao,))
    db.close()
    assert passe.main(["--banco", str(banco), "--aplicar", "--backup", str(backup)]) == 2      # aborta sem gravar
    assert passe.main(["--banco", str(tmp_path / "nada.sqlite3")]) == 2
