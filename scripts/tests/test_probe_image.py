"""`scripts/probe-image.ps1`: o veredito de snapshot restaurado vem do uptime do convidado, não do log.

O script é LIDO e analisado aqui, nunca executado: ele cria um AVD temporário e consome RAM do host de produção.
Só a função pura `Test-SnapshotRestored` é tirada da árvore sintática e chamada.

O defeito: `loaded_from_snapshot` saía de um regex no log do emulador lido com o processo ainda vivo. O stdout
redirecionado para arquivo desce ao disco em blocos e na saída, então a linha "Successfully loaded snapshot" ainda
não estava lá: `false` nos 4 braços do piloto de renderer de 27/09, embora os 4 `.log.wake` a tenham no fim e o
uptime tenha seguido do ponto salvo (147-248 s logo depois de um acordar de ~4 s).

Os casos são medições reais: o piloto (`docs/desempenho/bancada/renderer-20260927-a90a6e1.jsonl`) e a fase 0 de
17/09 (`data/logs/image-probe-fase0.jsonl`, fora do Git; relatório de validação §7.3), que tem um boot a frio de
verdade: o android-34 com 2560 MB acordou em 117,8 s com uptime 116,82 s, e o log não tem "Loading snapshot".
Nos dados antigos falta `wake_elapsed_at_uptime_s`; o `wake_total_s` é tomado antes da leitura do uptime, então é
um piso do tempo decorrido e não favorece o `true`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "probe-image.ps1"
PILOTO = ROOT / "docs" / "desempenho" / "bancada" / "renderer-20260927-a90a6e1.jsonl"
MARGEM = 10.0

# fase 0 de 17/09: (rótulo, uptime_after_wake_s, wake_total_s, restaurou de fato)
FASE0 = [
    ("android-30 snap", "191.59", 7.0, True),
    ("android-34 snap 2560 MB", "116.82", 117.8, False),
    ("android-30 lowram-1536-snap", "125.90", 4.8, True),
    ("android-34 lowram-1536-snap", "130.73", 4.4, True),
]


def _pwsh() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


class ProbeImage(unittest.TestCase):
    def setUp(self) -> None:
        self.texto = SCRIPT.read_text(encoding="utf-8")

    def _julgar(self, casos: list[dict[str, object]], cultura: str = "") -> list[object]:
        """Extrai `Test-SnapshotRestored` pela AST, chama com cada caso e devolve os vereditos na ordem."""
        pwsh = _pwsh()
        if not pwsh:  # pragma: no cover - máquina sem PowerShell
            self.skipTest("sem pwsh nesta máquina")
        script = (
            "$e=$null;$t=$null;"
            f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}',[ref]$t,[ref]$e);"
            "$f=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]"
            " -and $n.Name -eq 'Test-SnapshotRestored'},$true);"
            "if(-not $f){'função ausente';exit 2};"
            ". ([scriptblock]::Create($f.Extent.Text));"
            "if($env:CULTURA){[Threading.Thread]::CurrentThread.CurrentCulture=$env:CULTURA};"
            "$saida=@(foreach($c in ($env:CASOS|ConvertFrom-Json)){"
            "@{v=(Test-SnapshotRestored $c.uptime $c.elapsed $c.margin)}});"
            "ConvertTo-Json -InputObject $saida -Compress"
        )
        env = {**os.environ, "CASOS": json.dumps(casos), "CULTURA": cultura}
        r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return [item["v"] for item in json.loads(r.stdout)]

    def test_tem_sintaxe_valida(self) -> None:
        pwsh = _pwsh()
        if not pwsh:  # pragma: no cover - máquina sem PowerShell
            self.skipTest("sem pwsh nesta máquina")
        script = ("$e=$null;$t=$null;"
                  f"[System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}',[ref]$t,[ref]$e)"
                  "|Out-Null; if($e.Count){$e|%{$_.Message};exit 1}; exit 0")
        r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_os_quatro_bracos_do_piloto_restauraram(self) -> None:
        linhas = [json.loads(ln) for ln in PILOTO.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(linhas), 4)
        casos = [{"uptime": ln["snapshot"]["uptime_after_wake_s"], "elapsed": ln["snapshot"]["wake_total_s"],
                  "margin": MARGEM} for ln in linhas]
        self.assertEqual(self._julgar(casos), [True] * 4, [ln["label"] for ln in linhas])

    def test_a_fase_0_separa_o_boot_a_frio_dos_restaurados(self) -> None:
        casos = [{"uptime": u, "elapsed": w, "margin": MARGEM} for _, u, w, _ in FASE0]
        self.assertEqual(self._julgar(casos), [esperado for *_, esperado in FASE0], [r for r, *_ in FASE0])

    def test_uptime_dentro_da_margem_nao_prova_restauracao(self) -> None:
        """Latência do adb não vira restauração: só conta o que passa de decorrido + margem, estritamente."""
        casos = [{"uptime": "14.9", "elapsed": 5.0, "margin": MARGEM},
                 {"uptime": "15.0", "elapsed": 5.0, "margin": MARGEM},
                 {"uptime": "15.1", "elapsed": 5.0, "margin": MARGEM}]
        self.assertEqual(self._julgar(casos), [False, False, True])

    def test_uptime_ilegivel_nao_da_veredito(self) -> None:
        """Sem leitura é `null`, nunca `false` por omissão nem `true`: incerteza não conta como restaurado."""
        casos = [{"uptime": u, "elapsed": 4.0, "margin": MARGEM} for u in ("", "   ", "error: device offline")]
        self.assertEqual(self._julgar(casos), [None, None, None])

    def test_o_ponto_decimal_do_proc_uptime_vale_em_maquina_pt_br(self) -> None:
        casos = [{"uptime": "147.20", "elapsed": 4.3, "margin": MARGEM},
                 {"uptime": "116.82", "elapsed": 117.8, "margin": MARGEM}]
        self.assertEqual(self._julgar(casos, cultura="pt-BR"), [True, False])

    def test_o_regex_do_log_nao_decide_mais(self) -> None:
        """O log fica registrado à parte (`restored_by_log`); o veredito é o do uptime."""
        linha_do_regex = [ln for ln in self.texto.splitlines() if "Successfully loaded" in ln and "Select-String" in ln]
        self.assertEqual(len(linha_do_regex), 1)
        self.assertIn("$snap.restored_by_log =", linha_do_regex[0])
        self.assertNotIn("loaded_from_snapshot", linha_do_regex[0])
        self.assertIn("$snap.loaded_from_snapshot = $snap.restored_by_uptime", self.texto)

    def test_o_decorrido_e_medido_depois_da_leitura_do_uptime(self) -> None:
        """O teto de um boot a frio é o tempo de parede até a leitura terminar, não o `wake_total_s` de antes."""
        i_uptime = self.texto.index("$snap.uptime_after_wake_s = ")
        i_decorrido = self.texto.index("$snap.wake_elapsed_at_uptime_s = ")
        i_veredito = self.texto.index("$snap.restored_by_uptime = ")
        self.assertLess(i_uptime, i_decorrido)
        self.assertLess(i_decorrido, i_veredito)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
