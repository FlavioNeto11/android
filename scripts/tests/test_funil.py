"""29.196: o funil de um corte (`scripts/funil.ps1`) encadeia as etapas, roda sob o teto de CPU e grava um run.txt padronizado.

Simulado (`simulated`): as etapas rodam COMANDOS FALSOS (gancho `-ComandosDeTeste`: python que imprime contagens no formato do
pytest e sai com o código pedido); nenhum pytest, npm, mypy nem PG de verdade. O encadeamento roda em `powershell.exe` 5.1, que é o
hospedeiro do funil real, e a prova do teto usa o wrapper de verdade (`com-teto-de-cpu.ps1`).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
FUNIL = SCRIPTS / "funil.ps1"
PS51 = shutil.which("powershell")

precisa_ps51 = pytest.mark.skipif(PS51 is None or sys.platform != "win32", reason="precisa do Windows PowerShell 5.1")
pytestmark = pytest.mark.carga  # CPU/subprocessos: pula enquanto um funil roda (conftest.py, 29.196)


def py(codigo: str) -> list[str]:
    return [sys.executable, "-c", codigo]


def imprime(texto: str, rc: int = 0) -> list[str]:
    return py(f"import sys; print({texto!r}); sys.exit({rc})")


def _funil(tmp: Path, comandos: dict | None, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    cmd = [PS51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(FUNIL), "-Raiz", str(tmp), "-Saida", str(tmp / "run.txt"),
           "-Python", sys.executable, *args]
    if comandos is not None:
        arq = tmp / "comandos.json"
        arq.write_text(json.dumps(comandos), encoding="utf-8")
        cmd += ["-ComandosDeTeste", str(arq)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def _registros(tmp: Path) -> list[dict]:
    """Cada linha do run.txt vira {tipo, campos}: o mesmo parse que o comparar.py faz."""
    saida = []
    for linha in (tmp / "run.txt").read_text(encoding="utf-8-sig").splitlines():
        tipo, _, resto = linha.partition(" ")
        campos = {k: (a or b) for k, a, b in re.findall(r'(\w+)=(?:"([^"]*)"|(\S+))', resto)}
        saida.append({"tipo": tipo, **campos})
    return saida


TODAS_OK = {
    "scripts": [imprime("5 passed, 1 skipped in 1.0s")],
    "sqlite": [imprime("12647 passed, 14 skipped, 2 warnings in 1128.79s (0:18:48)")],
    "frontend": [imprime("Test Files  2 passed (2)"), imprime("      Tests  7 passed (7)")],
    "catracas": [imprime("89 passed in 5s"), imprime("7 passed in 1s")],
    "mypy": [imprime("mypy no codigo novo: 257 erros, no teto (257).")],
}


@precisa_ps51
class TestPlano:
    def test_simular_lista_etapas_tetos_e_a_linha_interna(self, tmp_path):
        r = _funil(tmp_path, TODAS_OK, "-Simular", "-TetoPorEtapa", "pg=40,sqlite=30")
        assert r.returncode == 0, r.stdout + r.stderr
        plano = json.loads(r.stdout)
        etapas = {e["chave"]: e for e in plano["etapas"]}
        assert list(etapas) == ["scripts", "sqlite", "frontend", "catracas", "mypy", "pg"]
        assert etapas["scripts"]["teto"] == 25 and etapas["sqlite"]["teto"] == 30 and etapas["pg"]["teto"] == 40
        assert etapas["pg"]["status_previsto"] == "pulado", "sem -ListaPg a etapa do PG é pulada"
        assert plano["relanca_sob_wrapper"] is True and "powershell.exe" in plano["linha_interna"] and "-Interno" in plano["linha_interna"]
        assert "pwsh" not in plano["linha_interna"].lower()

    def test_com_lista_do_pg_o_comando_real_aparece(self, tmp_path):
        lista = tmp_path / "pg.txt"
        lista.write_text("tests/test_x.py\n", encoding="utf-8")
        plano = json.loads(_funil(tmp_path, None, "-Simular", "-ListaPg", str(lista), "-PartesPg", "3").stdout)
        pg = [e for e in plano["etapas"] if e["chave"] == "pg"][0]
        assert pg["status_previsto"] == "roda" and "pg-rapido.py" in pg["comandos"][0] and "--partes 3" in pg["comandos"][0]
        real = {e["chave"]: e["comandos"] for e in plano["etapas"]}
        assert "-n 4" in real["scripts"][0] and "-n 6" in real["sqlite"][0]

    def test_sem_teto_nao_relanca_sob_o_wrapper(self, tmp_path):
        plano = json.loads(_funil(tmp_path, TODAS_OK, "-Simular", "-SemTeto").stdout)
        assert plano["relanca_sob_wrapper"] is False and plano["teto"] == "sem"

    @pytest.mark.parametrize("args", [["-Etapas", "0,9"], ["-TetoPorEtapa", "xx=40"], ["-TetoPorEtapa", "pg=0"], ["-TetoPorEtapa", "pg"],
                                      ["-ListaPg", 'a"b'], ["-Teto", "101"]])
    def test_entrada_invalida_e_recusada(self, tmp_path, args):
        assert _funil(tmp_path, TODAS_OK, "-Simular", *args).returncode != 0


@precisa_ps51
class TestEncadeamento:
    def test_run_txt_padronizado_com_contagens_status_e_codigo_de_saida(self, tmp_path):
        comandos = dict(TODAS_OK)
        comandos["sqlite"] = [imprime("3 passed, 1 failed in 2s", rc=1)]
        r = _funil(tmp_path, comandos, "-SemTeto")
        assert r.returncode == 1, r.stdout + r.stderr
        reg = _registros(tmp_path)
        assert reg[0]["tipo"] == "FUNIL" and reg[0]["teto"] == "sem" and reg[0]["hospedeiro"].startswith("5.1")
        etapas = {e["chave"]: e for e in reg if e["tipo"] == "ETAPA"}
        assert etapas["scripts"]["status"] == "ok" and etapas["scripts"]["passed"] == "5" and etapas["scripts"]["skipped"] == "1"
        assert etapas["sqlite"]["status"] == "falhou" and etapas["sqlite"]["rc"] == "1" and etapas["sqlite"]["failed"] == "1"
        assert etapas["frontend"]["passed"] == "7", "'Test Files' não conta em dobro e as duas linhas do vitest somam uma vez"
        assert etapas["catracas"]["passed"] == "96", "os comandos da mesma etapa somam"
        assert etapas["pg"]["status"] == "pulado" and "ListaPg" in etapas["pg"]["motivo"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", etapas["sqlite"]["ini"])
        assert reg[-1]["tipo"] == "FUNIL" and reg[-1]["rc"] == "1" and reg[-1]["falhas"] == "1" and reg[-1]["puladas"] == "1"
        assert (tmp_path / "run.txt.2-sqlite.txt").read_text(encoding="utf-8-sig").count("1 failed") == 1, "detalhe por etapa"

    def test_tudo_verde_mas_pg_pulado_nao_e_zero(self, tmp_path):
        r = _funil(tmp_path, TODAS_OK, "-SemTeto")
        assert r.returncode == 2, "pulada nunca conta como verde"

    def test_tudo_verde_com_o_pg_vira_zero(self, tmp_path):
        lista = tmp_path / "pg.txt"
        lista.write_text("x\n", encoding="utf-8")
        comandos = dict(TODAS_OK)
        comandos["pg"] = [imprime("5127 passed, 8 skipped in 1464.51s")]
        r = _funil(tmp_path, comandos, "-SemTeto", "-ListaPg", str(lista))
        assert r.returncode == 0, r.stdout + r.stderr
        assert _registros(tmp_path)[-1]["ok"] == "6"

    def test_para_no_erro_deixa_as_seguintes_como_nao_rodou(self, tmp_path):
        comandos = dict(TODAS_OK)
        comandos["scripts"] = [imprime("1 failed in 1s", rc=1)]
        r = _funil(tmp_path, comandos, "-SemTeto", "-ParaNoErro")
        assert r.returncode == 1
        etapas = {e["chave"]: e for e in _registros(tmp_path) if e["tipo"] == "ETAPA"}
        assert etapas["scripts"]["status"] == "falhou"
        assert all(etapas[c]["status"] == "nao_rodou" for c in ("sqlite", "frontend", "catracas", "mypy", "pg"))

    def test_etapas_escolhidas(self, tmp_path):
        r = _funil(tmp_path, TODAS_OK, "-SemTeto", "-Etapas", "1,5")
        etapas = [e["chave"] for e in _registros(tmp_path) if e["tipo"] == "ETAPA"]
        assert etapas == ["scripts", "mypy"] and r.returncode == 0

    def test_comando_que_nao_existe_vira_falha_e_nao_derruba_o_funil(self, tmp_path):
        comandos = dict(TODAS_OK)
        comandos["mypy"] = [["C:\\nao\\existe.exe", "x"]]
        r = _funil(tmp_path, comandos, "-SemTeto", "-Etapas", "4,5")
        etapas = {e["chave"]: e for e in _registros(tmp_path) if e["tipo"] == "ETAPA"}
        assert etapas["mypy"]["status"] == "falhou" and etapas["catracas"]["status"] == "ok" and r.returncode == 1

    def test_cada_etapa_roda_na_raiz_pedida(self, tmp_path):
        comandos = {"scripts": [py("import os; print(os.getcwd(), '1 passed in 1s')")]}
        _funil(tmp_path, comandos, "-SemTeto", "-Etapas", "1")
        assert str(tmp_path).lower() in (tmp_path / "run.txt.1-scripts.txt").read_text(encoding="utf-8-sig").lower()


@precisa_ps51
class TestSobOTeto:
    def test_o_encadeamento_roda_dentro_do_job_com_o_teto_e_o_teto_por_etapa_vale(self, tmp_path):
        queima = "import time; t=time.time()\nwhile time.time()-t<2.5: pass\nprint('1 passed in 3s')"
        comandos = {"scripts": [py(queima)], "sqlite": [py(queima)]}
        r = _funil(tmp_path, comandos, "-Etapas", "1,2", "-Teto", "25", "-TetoPorEtapa", "sqlite=40", "-BatimentoS", "1")
        assert r.returncode == 0, r.stdout + r.stderr
        wrapper = (tmp_path / "run.txt.wrapper.txt").read_text(encoding="utf-8-sig", errors="replace")
        assert "teto 25 %" in wrapper and "RECUSADO" not in wrapper
        assert "teto agora 40 % (era 25 %)" in wrapper, wrapper
        m = re.findall(r": (\d+) s: .+ ([\d]+),(\d) s de CPU", wrapper)
        assert m and float(f"{m[-1][1]}.{m[-1][2]}") >= 2.0, "a árvore do encadeamento está DENTRO do job (CPU contada)"
        etapas = {e["chave"]: e for e in _registros(tmp_path) if e["tipo"] == "ETAPA"}
        assert etapas["scripts"]["teto"] == "25" and etapas["sqlite"]["teto"] == "40"
        assert _registros(tmp_path)[0]["teto_por_etapa"] == "sqlite=40"

    def test_o_codigo_de_saida_do_funil_atravessa_o_wrapper(self, tmp_path):
        comandos = {"scripts": [imprime("1 failed in 1s", rc=1)]}
        r = _funil(tmp_path, comandos, "-Etapas", "1")
        assert r.returncode == 1, r.stdout + r.stderr


def test_o_script_e_5_1_compativel_e_nao_chama_pwsh_nem_mexe_em_wsl_tunel_relogio():
    texto = FUNIL.read_bytes()
    assert texto.startswith(b"\xef\xbb\xbf"), "BOM: o PowerShell 5.1 lê UTF-8 sem BOM como ANSI"
    codigo = texto.decode("utf-8-sig")
    codigo = codigo[codigo.index("#>") + 2:]
    codigo = "\n".join(l for l in codigo.splitlines() if not l.lstrip().startswith("#"))
    for proibido in ("pwsh", "wslconfig", "wsl.exe", "Stop-Process", "taskkill", "w32tm", "Set-Date", " ?? ", "?.", "&&", "-AsUTC", ".env"):
        assert proibido not in codigo, proibido


@precisa_ps51
class TestTrava:
    def test_trava_existe_durante_o_funil_com_o_pid_dele_e_some_no_fim(self, tmp_path, monkeypatch):
        trava = tmp_path / "trava" / "funil-ativo.json"
        monkeypatch.setenv("FARM_FUNIL_TRAVA", str(trava))
        monkeypatch.delenv("FARM_FUNIL_RODANDO", raising=False)
        sonda = py("import os, json, sys; d = json.load(open(sys.argv[1], encoding='utf-8-sig')); "
                   "print(('%d passed' % 1) if d['pid'] and os.environ.get('FARM_FUNIL_RODANDO') == '1' else '1 failed')")
        comandos = dict(TODAS_OK)
        comandos["scripts"] = [sonda + [str(trava)]]
        r = _funil(tmp_path, comandos, "-SemTeto", "-Etapas", "1")
        assert r.returncode == 0, r.stdout + r.stderr
        etapa = [x for x in _registros(tmp_path) if x["tipo"] == "ETAPA"][0]
        assert etapa["status"] == "ok" and etapa["passed"] == "1", "a trava com pid existia e o ambiente trazia FARM_FUNIL_RODANDO"
        assert not trava.exists(), "a trava some quando o funil acaba"
