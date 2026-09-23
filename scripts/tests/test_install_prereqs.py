"""Achado #153: `scripts/install-prereqs.ps1` abortava em PowerShell 5.1 por stderr de programa NATIVO.

O script é LIDO e analisado aqui, nunca executado: ele baixa o Android SDK, aceita licenças em nome do dono e
instala imagens de sistema. O mesmo tratamento que o instalador do worker já recebe com `bash -n`.

O defeito: `$ErrorActionPreference = 'Stop'` + `& $sdkmanager` faz do stderr do sdkmanager (que existe mesmo
quando tudo dá certo) um `ErrorRecord` terminante, e a instalação da máquina nova morre no meio sem nada ter
falhado. A correção é `2>&1` e decidir pelo `$LASTEXITCODE`.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "install-prereqs.ps1"


class InstallPrereqs(unittest.TestCase):
    def setUp(self) -> None:
        self.texto = SCRIPT.read_text(encoding="utf-8")

    def test_tem_sintaxe_valida(self) -> None:
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:  # pragma: no cover - máquina sem PowerShell
            self.skipTest("sem pwsh nesta máquina")
        script = ("$e=$null;$t=$null;"
                  f"[System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}',[ref]$t,[ref]$e)"
                  "|Out-Null; if($e.Count){$e|%{$_.Message};exit 1}; exit 0")
        r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_nenhuma_chamada_ao_sdkmanager_escapa_do_tratamento_de_stderr(self) -> None:
        chamadas = [ln.strip() for ln in self.texto.splitlines() if "& $sdkmanager" in ln]
        self.assertTrue(chamadas, "o script precisa chamar o sdkmanager em algum lugar")
        for ln in chamadas:
            self.assertIn("2>&1", ln, f"stderr sem tratamento aborta em PS 5.1: {ln}")

    def test_o_desfecho_vem_do_codigo_de_saida_e_o_preference_volta_ao_que_era(self) -> None:
        self.assertIn("$LASTEXITCODE -ne 0", self.texto)
        self.assertIn("$ErrorActionPreference = 'Continue'", self.texto)
        self.assertIn("finally { $ErrorActionPreference = $anterior }", self.texto)

    def test_o_script_continua_parando_no_primeiro_erro_de_verdade(self) -> None:
        """A tolerância é só ao stderr do nativo: o `Stop` do script inteiro continua valendo."""
        self.assertIn("$ErrorActionPreference = 'Stop'", self.texto)

    def test_a_imagem_da_loja_nao_entra_por_padrao_e_o_texto_diz_como_pedi_la(self) -> None:
        """Quem precisa da Play Store tem de pedir — é o que o `config.example.yaml` avisa no bloco da loja."""
        self.assertIn("@('google_apis')", self.texto)
        self.assertIn("google_apis_playstore",
                      (ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
