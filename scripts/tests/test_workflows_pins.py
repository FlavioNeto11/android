"""29.157: toda action de terceiros nos workflows e nas ações compostas está fixada por SHA de commit (40 hex) com a versão
em comentário. Prova `simulated` (lê os arquivos do repositório). Falha cita o arquivo e a linha para trocar SHA e comentário juntos."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USO = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<resto>.*)$")


def usos() -> list[tuple[Path, int, str, str]]:
    achados = []
    arquivos = [*(ROOT / ".github" / "workflows").glob("*.yml"), *(ROOT / ".github" / "actions").glob("*/action.yml")]
    for arq in sorted(arquivos):
        for n, linha in enumerate(arq.read_text(encoding="utf-8").splitlines(), 1):
            m = USO.match(linha)
            if m and not m["ref"].startswith("./"):
                achados.append((arq, n, m["ref"], m["resto"]))
    return achados


class Pins(unittest.TestCase):
    def test_ha_actions_para_conferir(self) -> None:
        self.assertGreaterEqual(len(usos()), 10)  # se cair a zero, o regex quebrou e o teste abaixo passaria à toa

    def test_toda_action_de_terceiros_tem_sha_e_versao(self) -> None:
        ruins = [f"{a.relative_to(ROOT)}:{n} {ref}" for a, n, ref, resto in usos()
                 if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref) or not re.search(r"#\s*v\d+(\.\d+)*", resto)]
        self.assertEqual(ruins, [], "fixe por SHA de commit com a versão em comentário (docs/operacao.md, 29.157)")

    def test_mesma_action_tem_o_mesmo_sha_em_todo_lugar(self) -> None:
        por_nome: dict[str, set[str]] = {}
        for _, _, ref, _ in usos():
            nome, sha = ref.split("@", 1)
            por_nome.setdefault(nome, set()).add(sha)
        self.assertEqual({k: v for k, v in por_nome.items() if len(v) > 1}, {})


if __name__ == "__main__":
    unittest.main()
