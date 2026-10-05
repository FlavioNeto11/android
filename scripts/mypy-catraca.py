"""Catraca do mypy no código novo (`app.contracts`, `app.modules`, `app.shared`): a contagem de erros só desce (29.102).

O job `backend-tipos` do CI nasceu bloqueante com zero erro nesses pacotes e derivou até 254 (cron de 05/10/2026):
reprovava toda noite, e um vermelho permanente não é rede. Este script roda o mesmo mypy, lê a contagem do resumo e
compara com o teto em `backend/mypy-teto.txt`:

- acima do teto: reprova, com os dois números;
- igual: passa;
- abaixo: passa e diz para baixar o teto no mesmo commit (a catraca só vale se acompanhar o que melhorou).

Saída do mypy sem resumo (o mypy quebrou, não achou o pacote) reprova: contagem desconhecida nunca passa.

Uso (de qualquer pasta): `python scripts/mypy-catraca.py`. Só stdlib; não chama IA nem toca aparelho.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TETO = RAIZ / "backend" / "mypy-teto.txt"
PACOTES = ("app.contracts", "app.modules", "app.shared")
_RESUMO_ERRO = re.compile(r"^Found (\d+) errors? in \d+ files?", re.M)
_RESUMO_OK = re.compile(r"^Success: no issues found", re.M)


def contar(saida: str) -> int | None:
    """Erros no resumo do mypy; `None` se não há resumo (o mypy não chegou ao fim)."""
    if m := _RESUMO_ERRO.search(saida):
        return int(m.group(1))
    return 0 if _RESUMO_OK.search(saida) else None


def ler_teto(caminho: Path = TETO) -> int:
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            return int(linha)
    raise ValueError(f"{caminho} sem número")


def veredito(atual: int | None, teto: int) -> tuple[int, str]:
    if atual is None:
        return 1, "mypy sem resumo: a contagem é desconhecida (o mypy quebrou?); veja a saída acima"
    if atual > teto:
        return 1, f"mypy no código novo SUBIU: {atual} erros, teto {teto}. Corrija os novos; o teto não sobe."
    if atual < teto:
        return 0, f"mypy no código novo desceu: {atual} erros, teto {teto}. Baixe `backend/mypy-teto.txt` para {atual}."
    return 0, f"mypy no código novo: {atual} erros, no teto ({teto})."


def main() -> int:
    r = subprocess.run([sys.executable, "-m", "mypy", *(a for p in PACOTES for a in ("-p", p))],
                       cwd=RAIZ / "backend", capture_output=True, text=True, check=False)
    saida = r.stdout + r.stderr
    print(saida, end="" if saida.endswith("\n") else "\n")
    if "No module named mypy" in saida:
        print(f"o Python que roda a catraca ({sys.executable}) não tem o mypy: instale o requirements-dev.txt nele")
        return 2
    rc, msg = veredito(contar(saida), ler_teto())
    print(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
