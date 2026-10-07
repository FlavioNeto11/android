"""29.174: o `com-teto-de-cpu.ps1` limita DE VERDADE a CPU de uma árvore de processos (Job Object do Windows).

Prova simulada no sentido do projeto (carga sintética no host de teste, nunca o funil nem os emuladores): um queimador de CPU com um
trabalhador por thread lógico roda sob o teto, e o teste mede quanto da CPU total a árvore usou. Dura ~20 s.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
WRAPPER = SCRIPTS / "com-teto-de-cpu.ps1"
PWSH = shutil.which("pwsh")

precisa_windows = pytest.mark.skipif(PWSH is None or sys.platform != "win32", reason="Job Object: só Windows com pwsh")
pytestmark = pytest.mark.carga  # CPU/subprocessos: pula enquanto um funil roda (conftest.py, 29.196)

QUEIMADOR = '''
import ctypes, json, os, subprocess, sys, time
d = float(sys.argv[1])
ncpu = os.cpu_count()
filho = "import sys,time; t=time.perf_counter(); [None for _ in iter(lambda: time.perf_counter() - t < %s, False)]; print(time.process_time())"
ini = time.perf_counter()
procs = [subprocess.Popen([sys.executable, "-c", filho % d], stdout=subprocess.PIPE, text=True) for _ in range(ncpu)]
cpu = sum(float(p.communicate()[0].strip()) for p in procs)
wall = time.perf_counter() - ini
k = ctypes.windll.kernel32
k.GetCurrentProcess.restype = ctypes.c_void_p
a, s = ctypes.c_size_t(), ctypes.c_size_t()
k.GetProcessAffinityMask(ctypes.c_void_p(k.GetCurrentProcess()), ctypes.byref(a), ctypes.byref(s))
print(json.dumps({"ratio": cpu / (wall * ncpu), "wall": wall, "mascara": hex(a.value), "ncpu": ncpu}))
'''


def _wrapper(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([PWSH, "-NoProfile", "-File", str(WRAPPER), *args], capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def _queimar(tmp: Path, *opcoes: str, segundos: float = 6.0) -> dict:
    arq = tmp / "queimador.py"
    arq.write_text(QUEIMADOR, encoding="utf-8")
    r = _wrapper(*opcoes, "-ComandoJson", json.dumps([sys.executable, str(arq), str(segundos)]))
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads([l for l in r.stdout.splitlines() if l.startswith("{")][-1])


@precisa_windows
class TestTetoDeCpu:
    def test_simular_mostra_o_plano_sem_executar_nada(self):
        r = _wrapper("-Teto", "40", "-NucleosE", "-Simular")
        assert r.returncode == 0, r.stderr
        plano = json.loads(r.stdout)
        assert plano["teto_pct"] == 40 and plano["threads_totais"] >= 1
        assert plano["teto_em_threads"] == pytest.approx(plano["threads_totais"] * 0.4, abs=0.06)
        assert re.fullmatch(r"0x[0-9A-F]+", plano["mascara"]) and plano["mascara"] != "0x0"
        total_de_threads = sum(c["threads"] for c in plano["threads_por_classe"])
        assert total_de_threads == plano["threads_totais"], "toda thread lógica tem uma classe de eficiência"

    @pytest.mark.parametrize("args", [["-Teto", "0", "-Simular"], ["-Teto", "101", "-Simular"],
                                      ["-NucleosE", "-Afinidade", "0xF", "-Simular"], ["-Afinidade", "abc", "-Simular"],
                                      ["-Afinidade", "0", "-Simular"], ["-Teto", "40"],
                                      ["-Linha", "x", "-ComandoJson", "[]"], ["-ComandoJson", "nao-e-json"], ["-ComandoJson", "[]"]])
    def test_entrada_invalida_e_recusada(self, args):
        r = _wrapper(*args)
        assert r.returncode != 0

    def test_o_codigo_de_saida_e_os_argumentos_do_comando_passam(self, tmp_path):
        argv = [sys.executable, "-c", "import sys; print('a b', sys.argv[1:]); sys.exit(7)", "um dois", "tres"]
        r = _wrapper("-Teto", "50", "-ComandoJson", json.dumps(argv))
        assert r.returncode == 7, r.stdout + r.stderr
        assert "'um dois', 'tres'" in r.stdout and "teto 50 %" in r.stdout

    def test_o_teto_limita_a_arvore_inteira_inclusive_os_netos(self, tmp_path):
        livre = _queimar_sem_teto(tmp_path)
        if os.environ.get("FARM_FUNIL_RODANDO") and livre["ratio"] <= 0.5:
            pytest.skip(f"controle sem valor: o funil ja roda sob o proprio teto de CPU (sem teto a carga usou {livre['ratio']:.0%})")
        preso = _queimar(tmp_path, "-Teto", "25")
        assert livre["ratio"] > 0.5, f"controle: sem teto a carga deveria usar a maior parte da CPU ({livre})"
        assert preso["ratio"] < 0.40, f"com teto de 25 % a árvore usou {preso['ratio']:.0%} da CPU total"
        assert preso["ratio"] < livre["ratio"] * 0.7

    def test_a_forma_linha_roda_pelo_cmd_dentro_do_job(self, tmp_path):
        r = _wrapper("-Teto", "50", "-Linha", f'"{sys.executable}" -c "import sys; print(1234); sys.exit(3)"')
        assert r.returncode == 3, r.stdout + r.stderr
        assert "1234" in r.stdout

    @pytest.mark.parametrize("linha", ['pwsh -NoProfile -Command "exit 0"', f'"{PWSH}" -NoProfile -Command "exit 0"',
                                       'cmd /c pwsh -NoProfile -Command "exit 0"'])
    def test_recusa_o_comando_que_usa_pwsh_que_escapa_do_job(self, linha):
        r = _wrapper("-Teto", "50", "-Linha", linha)
        assert r.returncode == 125, r.stdout + r.stderr
        assert "RECUSADO" in r.stdout and "escapa do job" in r.stdout
        assert "teto agora" not in r.stdout and "usou" not in r.stdout, "o comando nem chegou a rodar"

    def test_permitir_pwsh_roda_mas_avisa(self):
        r = _wrapper("-Teto", "50", "-PermitirPwsh", "-Linha", 'pwsh -NoProfile -Command "exit 0"')
        assert r.returncode == 0, r.stdout + r.stderr
        assert "AVISO" in r.stdout and "RECUSADO" not in r.stdout

    @pytest.mark.parametrize("args", [["-ComandoJson", json.dumps([sys.executable, "-c", "print(1)"])],
                                      ["-Linha", f'"{sys.executable}" -c "print(1)"'], ["-Linha", "powershell -NoProfile -Command exit"]])
    def test_nao_avisa_para_python_nem_para_powershell_5(self, args):
        r = _wrapper("-Teto", "50", *args)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "AVISO" not in r.stdout

    def test_powershell_5_fica_dentro_do_job_e_a_cpu_e_contada(self):
        r = _wrapper("-Teto", "50", "-Linha",
                     'powershell -NoProfile -Command "$s=Get-Date; while(((Get-Date)-$s).TotalSeconds -lt 3){}"')
        assert r.returncode == 0, r.stdout + r.stderr
        m = re.search(r"usou (\d+),(\d) s de CPU", r.stdout)
        assert m and float(f"{m.group(1)}.{m.group(2)}") >= 2.0, r.stdout

    def test_batimento_mostra_a_cpu_da_arvore_enquanto_o_comando_roda(self):
        queima = "import time; t=time.time()\nwhile time.time()-t<4: pass"
        r = _wrapper("-Teto", "50", "-BatimentoS", "1", "-ComandoJson", json.dumps([sys.executable, "-c", queima]))
        assert r.returncode == 0, r.stdout + r.stderr
        batidas = re.findall(r": (\d+) s: .+ (\d+),(\d) s de CPU", r.stdout)
        assert len(batidas) >= 2, r.stdout
        assert float(f"{batidas[-1][1]}.{batidas[-1][2]}") >= 1.5, "a CPU da árvore cresce durante a execução"
        assert "AVISO" not in r.stdout

    def test_batimento_acusa_arvore_com_zero_de_cpu_fora_do_job(self):
        r = _wrapper("-Teto", "50", "-BatimentoS", "1", "-ZeroAposS", "1", "-PermitirPwsh", "-Linha",
                     'pwsh -NoProfile -Command "$s=Get-Date; while(((Get-Date)-$s).TotalSeconds -lt 3){}"')
        assert r.returncode == 0, r.stdout + r.stderr
        assert "FORA do job" in r.stdout

    def test_arquivo_de_teto_troca_o_teto_do_job_que_ja_roda(self, tmp_path):
        ctl = tmp_path / "teto.txt"
        ctl.write_text("25", encoding="utf-8")
        filho = (f"import time; time.sleep(1.5); open(r'{ctl}','w').write('40'); time.sleep(5); open(r'{ctl}','w').write('abc'); "
                 "time.sleep(3); open(r'" + str(ctl) + "','w').write('10'); time.sleep(3)")
        r = _wrapper("-Teto", "25", "-BatimentoS", "0", "-ArquivoDeTeto", str(ctl), "-ComandoJson",
                     json.dumps([sys.executable, "-c", filho]))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "teto agora 40 % (era 25 %)" in r.stdout
        assert "teto agora 10 % (era 40 %)" in r.stdout, "valor inválido no meio não derruba nem troca o teto"
        assert r.stdout.count("percentual de 1 a 100") == 1, "o aviso do valor inválido sai uma vez só"

    def test_batimento_zero_desliga(self):
        r = _wrapper("-Teto", "50", "-BatimentoS", "0", "-ComandoJson", json.dumps([sys.executable, "-c", "print(1)"]))
        assert r.returncode == 0 and not re.search(r": \d+ s: ", r.stdout)

    def test_afinidade_explicita_chega_ao_comando(self, tmp_path):
        r = _queimar(tmp_path, "-Teto", "100", "-Afinidade", "0xF", segundos=1.0)
        assert r["mascara"] == "0xf"

    def test_nucleos_e_aplica_a_mascara_do_plano(self, tmp_path):
        plano = json.loads(_wrapper("-Teto", "100", "-NucleosE", "-Simular").stdout)
        r = _queimar(tmp_path, "-Teto", "100", "-NucleosE", segundos=1.0)
        assert int(r["mascara"], 16) == int(plano["mascara"], 16)

    def test_se_o_wrapper_morre_a_arvore_morre_junto(self, tmp_path):
        pidfile = tmp_path / "pid.txt"
        dorme = f"import os,time; open(r'{pidfile}','w').write(str(os.getpid())); time.sleep(120)"
        p = subprocess.Popen([PWSH, "-NoProfile", "-File", str(WRAPPER), "-Teto", "50", "-ComandoJson",
                              json.dumps([sys.executable, "-c", dorme])],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if pidfile.exists() and pidfile.read_text().strip():
                    break
                time.sleep(0.2)
            filho = int(pidfile.read_text().strip())
            subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)   # só o wrapper, que é nosso
            vivo = True
            for _ in range(50):
                saida = subprocess.run(["tasklist", "/FI", f"PID eq {filho}", "/NH"], capture_output=True, text=True).stdout
                vivo = str(filho) in saida
                if not vivo:
                    break
                time.sleep(0.2)
            assert not vivo, "o filho ficou órfão consumindo CPU"
        finally:
            p.kill()


def _queimar_sem_teto(tmp: Path, segundos: float = 6.0) -> dict:
    arq = tmp / "queimador.py"
    arq.write_text(QUEIMADOR, encoding="utf-8")
    r = subprocess.run([sys.executable, str(arq), str(segundos)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.splitlines()[-1])


def test_o_script_nao_mexe_em_wsl_tunel_relogio_nem_mata_processo_alheio():
    texto = WRAPPER.read_text(encoding="utf-8")
    codigo = texto[texto.index("#>") + 2:]
    for proibido in ("wslconfig", "wsl.exe", "Stop-Process", "taskkill", "w32tm", "ssh", "TerminateJobObject", "Set-Date"):
        assert proibido not in codigo, proibido
