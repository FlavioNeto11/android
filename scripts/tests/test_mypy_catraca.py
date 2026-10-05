"""Testes puros da catraca do mypy (scripts/mypy-catraca.py, 29.102), sem rodar o mypy."""
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("mypy_catraca", Path(__file__).resolve().parents[1] / "mypy-catraca.py")
mc = importlib.util.module_from_spec(_SPEC)
sys.modules["mypy_catraca"] = mc
_SPEC.loader.exec_module(mc)


def test_le_a_contagem_do_resumo_do_mypy():
    assert mc.contar("a.py:1: error: x  [arg-type]\nFound 254 errors in 47 files (checked 367 source files)\n") == 254
    assert mc.contar("Found 1 error in 1 file (checked 3 source files)\n") == 1
    assert mc.contar("Success: no issues found in 367 source files\n") == 0
    assert mc.contar("mypy: can't find package 'app.x'\n") is None           # quebrou: contagem desconhecida


def test_so_reprova_quando_sobe_e_pede_para_baixar_o_teto_quando_desce():
    assert mc.veredito(255, 254)[0] == 1 and "SUBIU" in mc.veredito(255, 254)[1]
    assert mc.veredito(254, 254)[0] == 0
    rc, msg = mc.veredito(250, 254)
    assert rc == 0 and "Baixe" in msg and "250" in msg
    assert mc.veredito(None, 254)[0] == 1                                     # sem resumo nunca passa


def test_o_teto_do_repositorio_e_um_numero(tmp_path):
    assert mc.ler_teto() >= 0                                                  # o arquivo versionado se lê
    f = tmp_path / "teto.txt"
    f.write_text("# comentário\n\n12\n", encoding="utf-8")
    assert mc.ler_teto(f) == 12
