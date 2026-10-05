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


def test_python_sem_mypy_diz_o_que_falta_e_reprova(monkeypatch, capsys):
    import subprocess
    monkeypatch.delenv(mc.VARIAVEL, raising=False)
    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        [], 1, stdout="", stderr="python.exe: No module named mypy\n"))
    assert mc.main() == 2
    assert "requirements-dev" in capsys.readouterr().out


# ------------------------------------------------------------------ 29.144: o mypy num Python à parte
def test_sem_python_a_parte_o_comando_e_o_de_sempre(tmp_path):
    cmd, env = mc.comando(None, tmp_path, env={"PYTHONPATH": "x"})
    assert cmd[:3] == [sys.executable, "-m", "mypy"]
    assert "--python-executable" not in cmd
    assert env["PYTHONPATH"] == "x"                                            # nada muda no ambiente


def test_com_python_a_parte_o_mypy_analisa_o_venv_do_backend(tmp_path):
    venv = tmp_path / ".venv"
    sp = mc._site_packages(venv)
    if sp is not None:                                                         # fora do Windows o glob pede a pasta
        sp.mkdir(parents=True)
    else:
        (venv / "lib" / "python3.13" / "site-packages").mkdir(parents=True)
    mypy = tmp_path / "mypy" / "python.exe"
    cmd, env = mc.comando(mypy, tmp_path, env={"PYTHONPATH": "antes"})
    assert cmd[:3] == [str(mypy), "-m", "mypy"]
    i = cmd.index("--python-executable")
    assert cmd[i + 1] == str(mc._python_do_venv(venv))
    assert cmd[-6:] == ["-p", "app.contracts", "-p", "app.modules", "-p", "app.shared"]
    partes = env["PYTHONPATH"].split(mc.os.pathsep)
    assert partes[0] == str(mc._site_packages(venv)) and partes[-1] == "antes"   # o plugin do pydantic acha o pacote


def test_a_variavel_e_a_opcao_escolhem_o_python_do_mypy(monkeypatch, tmp_path):
    import subprocess
    vistos = []

    def run(cmd, **kw):
        vistos.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, stdout="Success: no issues found in 3 source files\n", stderr="")

    monkeypatch.setattr(mc.subprocess, "run", run)
    monkeypatch.setattr(mc, "ler_teto", lambda: 0)
    monkeypatch.setenv(mc.VARIAVEL, str(tmp_path / "pela-variavel.exe"))
    assert mc.main() == 0
    assert mc.main(["--python", str(tmp_path / "pela-opcao.exe")]) == 0       # a opção vence a variável
    monkeypatch.delenv(mc.VARIAVEL)
    assert mc.main() == 0
    assert vistos == [str(tmp_path / "pela-variavel.exe"), str(tmp_path / "pela-opcao.exe"), sys.executable]
