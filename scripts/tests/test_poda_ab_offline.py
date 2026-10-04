"""31.35/31.52: o A/B offline da poda (`scripts/poda-ab-offline.py`) mede o que diz e não vaza texto da página.

O 31.35 fecha com o número deste script sobre as árvores reais que o diagnóstico do 31.52 grava (android-09, na janela
do deploy 32). Antes de confiar no número, o script é conferido aqui com árvores montadas: a poda tira só a UI do Chrome
declarada em `UI_DO_NAVEGADOR`, a `url_bar` fica, a árvore sem UI do navegador não muda, a leitura do banco pega só as
evidências do 31.52 e a saída leva só números e ids.

Nível de prova: `simulated` (árvores e banco montados; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_spec = importlib.util.spec_from_file_location("poda_ab_offline", ROOT / "scripts" / "poda-ab-offline.py")
poda = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = poda
_spec.loader.exec_module(poda)  # type: ignore[union-attr]

CHROME = "com.android.chrome"
SEGREDO = "manchete-que-nao-pode-vazar"          # texto da página: nunca na saída


def _el(i: int, rid: str, texto: str = "", desc: str = "") -> dict[str, Any]:
    return {"id": f"e{i}", "text": texto, "desc": desc, "resource_id": rid, "class_name": "android.widget.TextView",
            "package": CHROME, "bounds": [0, i * 60, 700, i * 60 + 50], "clickable": True, "enabled": True,
            "focused": False, "scrollable": False, "editable": False, "checked": False, "password": False}


def _arvore(*, com_ui: bool = True) -> dict[str, Any]:
    ui = [_el(0, f"{CHROME}:id/toolbar", desc="Barra"), _el(1, f"{CHROME}:id/menu_button", desc="Mais opções"),
          _el(2, f"{CHROME}:id/tab_switcher_button", desc="2 guias"),
          _el(3, f"{CHROME}:id/url_bar", texto="g1.globo.com/…")] if com_ui else []
    pagina = [_el(10 + i, "", texto=f"{SEGREDO} {i}") for i in range(5)]
    return {"package": CHROME, "elements": ui + pagina}


def test_a_poda_tira_so_a_ui_do_chrome_e_mantem_a_url_bar() -> None:
    m = poda.medir(_arvore(), poda.MAX_LINHAS)
    assert m["elementos"] == 9
    assert 0 < m["chars_com_poda"] < m["chars_sem_poda"] and 0 < m["reducao"] < 1
    t = poda.arvore(_arvore())
    linhas = t.prompt_lines(poda.MAX_LINHAS, ocultar=poda.UI_DO_NAVEGADOR[CHROME])
    assert any("g1.globo.com" in x for x in linhas)                    # a url_bar fica (31.52)
    assert not any("Mais opções" in x or "2 guias" in x for x in linhas)


def test_arvore_sem_ui_do_navegador_nao_muda() -> None:
    m = poda.medir(_arvore(com_ui=False), poda.MAX_LINHAS)
    assert m["chars_com_poda"] == m["chars_sem_poda"] and m["reducao"] == 0.0
    outro = {**_arvore(), "package": "com.instagram.android"}       # pacote fora de UI_DO_NAVEGADOR: nada é podado
    m = poda.medir(outro, poda.MAX_LINHAS)
    assert m["reducao"] == 0.0


def test_do_banco_le_so_as_evidencias_do_31_52(tmp_path: Path) -> None:
    banco = tmp_path / "poc.sqlite3"
    c = sqlite3.connect(banco)
    c.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, run_id TEXT, kind TEXT, note TEXT, path TEXT, ts TEXT)")
    c.executemany("INSERT INTO evidence(run_id, kind, note, path, ts) VALUES (?,?,?,?,?)", [
        ("r-1", "hierarchy", "31.52: antes da poda", "a.json", "2026-10-04T22:00:00Z"),
        ("r-2", "hierarchy", "outra nota", "b.json", "2026-10-04T22:01:00Z"),
        ("r-3", "screenshot", "31.52: antes da poda", "c.png", "2026-10-04T22:02:00Z"),
        ("r-4", "hierarchy", "31.52: antes da poda", None, "2026-10-04T22:03:00Z")])
    c.commit()
    c.close()
    fontes = poda.do_banco(banco, tmp_path / "ev")
    assert fontes == [("r-1", tmp_path / "ev" / "a.json")]


def test_a_saida_leva_so_numeros_e_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    arquivo = tmp_path / "arvore.json"
    arquivo.write_text(json.dumps(_arvore()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["poda-ab-offline.py", str(arquivo)])
    poda.main()
    saida = capsys.readouterr().out
    assert SEGREDO not in saida and "g1.globo.com" not in saida
    resumo = json.loads(saida.strip().splitlines()[-1])
    assert resumo["arvores"] == 1 and resumo["reducao_mediana"] > 0


def test_sem_arvore_gravada_diz_que_nao_ha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    banco = tmp_path / "vazio.sqlite3"
    c = sqlite3.connect(banco)
    c.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, run_id TEXT, kind TEXT, note TEXT, path TEXT, ts TEXT)")
    c.commit()
    c.close()
    monkeypatch.setattr(sys, "argv", ["poda-ab-offline.py", "--banco", str(banco)])
    poda.main()
    assert json.loads(capsys.readouterr().out.strip())["arvores"] == 0
