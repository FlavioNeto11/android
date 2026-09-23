"""Achado #153: script de medição não inventa mais o parque por número.

`'android-{0:d2}' -f $i` era uma mentira desde o dia em que `android-09/10` e `android-12…15` passaram a ser
aparelhos de OUTRA máquina e `android-11` virou a loja: `scale-test -StopAtEnd` mandava `stop` aos remotos pelo
worker enquanto media só a RSS local, `rotation-test -Accounts 10` arrastava dois remotos para um teste de vagas
de RAM deste host, e `start.ps1 -StartInstances 10` tentava ligar aparelho que não é desta máquina.

Os três scripts são LIDOS e analisados aqui, nunca executados: eles ligam emuladores, sobem o backend e
despacham comandos ao parque de verdade.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ("scale-test.ps1", "rotation-test.ps1", "start.ps1")


def _texto(nome: str) -> str:
    return (ROOT / "scripts" / nome).read_text(encoding="utf-8")


class AlvosDoParque(unittest.TestCase):
    def test_nenhum_script_gera_id_de_aparelho_por_numero(self) -> None:
        for nome in SCRIPTS:
            linhas = [ln.strip() for ln in _texto(nome).splitlines()
                      if "'android-{0:d2}' -f" in ln and not ln.strip().startswith("#")]
            self.assertEqual(linhas, [], f"{nome} ainda inventa id de aparelho: {linhas}")

    def test_os_tres_perguntam_o_parque_ao_backend(self) -> None:
        for nome in SCRIPTS:
            self.assertIn("/api/instances", _texto(nome), nome)

    def test_a_loja_nunca_entra_num_teste_de_parque(self) -> None:
        for nome in ("scale-test.ps1", "rotation-test.ps1"):
            self.assertIn("$_.kind -ne 'store'", _texto(nome), nome)

    def test_start_liga_so_emulador_desta_maquina(self) -> None:
        self.assertIn("$_.kind -eq 'emulator'", _texto("start.ps1"))

    def test_onde_aceita_local_worker_e_todos_e_recusa_o_resto(self) -> None:
        for nome in ("scale-test.ps1", "rotation-test.ps1"):
            texto = _texto(nome)
            self.assertIn("[string]$Onde", texto, nome)
            self.assertIn("'^worker:(.+)$'", texto, nome)
            self.assertIn("-Onde aceita 'local', 'todos' ou 'worker:<id>'", texto, nome)

    def test_o_rodizio_mede_o_host_por_padrao(self) -> None:
        """O teste de vagas de RAM é sobre ESTA máquina: o padrão tem de ser `local`, não o parque inteiro."""
        self.assertIn("[string]$Onde = 'local'", _texto("rotation-test.ps1"))

    def test_os_scripts_tem_sintaxe_valida(self) -> None:
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:  # pragma: no cover - máquina sem PowerShell
            self.skipTest("sem pwsh nesta máquina")
        for nome in SCRIPTS:
            caminho = ROOT / "scripts" / nome
            script = ("$e=$null;$t=$null;"
                      f"[System.Management.Automation.Language.Parser]::ParseFile('{caminho}',[ref]$t,[ref]$e)"
                      "|Out-Null; if($e.Count){$e|%{$_.Message};exit 1}; exit 0")
            r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, f"{nome}: {r.stdout}{r.stderr}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
