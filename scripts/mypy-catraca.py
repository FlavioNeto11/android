"""Catraca do mypy no código novo (`app.contracts`, `app.modules`, `app.shared`): a contagem de erros só desce (29.102).

O job `backend-tipos` do CI nasceu bloqueante com zero erro nesses pacotes e derivou até 254 (cron de 05/10/2026):
reprovava toda noite, e um vermelho permanente não é rede. Este script roda o mesmo mypy, lê a contagem do resumo e
compara com o teto em `backend/mypy-teto.txt`:

- acima do teto: reprova, com os dois números;
- igual: passa;
- abaixo: passa e diz para baixar o teto no mesmo commit (a catraca só vale se acompanhar o que melhorou).

Saída do mypy sem resumo (o mypy quebrou, não achou o pacote) reprova: contagem desconhecida nunca passa.

Uso (de qualquer pasta): `python scripts/mypy-catraca.py`. Só stdlib; não chama IA nem toca aparelho.

29.144: o mypy pode morar num Python À PARTE (`--python <python.exe>` ou a variável `MYPY_PYTHON`), num caminho fixo
fora do Git (no central, `C:\\farm\\ferramentas\\mypy`), em vez de no venv do backend, que nenhuma sessão instala. Aí o
mypy analisa o venv do backend por `--python-executable`, e o site-packages do backend entra no `PYTHONPATH` porque o
plugin `pydantic.mypy` (do `backend/mypy.ini`) é importado pelo Python do próprio mypy. Os dois Pythons precisam ter a
mesma versão: com versões diferentes o import quebra, a saída fica sem resumo e a catraca reprova.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TETO = RAIZ / "backend" / "mypy-teto.txt"
VARIAVEL = "MYPY_PYTHON"
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


def _python_do_venv(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _site_packages(venv: Path) -> Path | None:
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    return next(iter(sorted(venv.glob("lib/python*/site-packages"))), None)


def comando(python_do_mypy: Path | None, backend: Path = RAIZ / "backend",
            env: dict[str, str] | None = None) -> tuple[list[str], dict[str, str]]:
    """O comando do mypy e o ambiente dele. Sem Python à parte, o de sempre (o mypy no Python que roda a catraca)."""
    env = dict(os.environ if env is None else env)
    pacotes = [a for p in PACOTES for a in ("-p", p)]
    if python_do_mypy is None:
        return [sys.executable, "-m", "mypy", *pacotes], env
    venv = backend / ".venv"
    sp = _site_packages(venv)
    if sp is not None:
        env["PYTHONPATH"] = os.pathsep.join(x for x in (str(sp), env.get("PYTHONPATH", "")) if x)
    return [str(python_do_mypy), "-m", "mypy", "--python-executable", str(_python_do_venv(venv)), *pacotes], env


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python scripts/mypy-catraca.py")
    ap.add_argument("--python", type=Path, default=None,
                    help=f"Python com o mypy, à parte do venv do backend (padrão: a variável {VARIAVEL}, se houver)")
    args = ap.parse_args([] if argv is None else argv)
    python_do_mypy = args.python or (Path(os.environ[VARIAVEL]) if os.environ.get(VARIAVEL) else None)
    cmd, env = comando(python_do_mypy)
    r = subprocess.run(cmd, cwd=RAIZ / "backend", env=env, capture_output=True, text=True, check=False)
    saida = r.stdout + r.stderr
    print(saida, end="" if saida.endswith("\n") else "\n")
    if "No module named mypy" in saida:
        quem = python_do_mypy or sys.executable
        print(f"o Python do mypy ({quem}) não tem o mypy: instale nele os pinos do mypy do backend/requirements-dev.txt, "
              f"ou aponte {VARIAVEL} para um que tenha")
        return 2
    rc, msg = veredito(contar(saida), ler_teto())
    print(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
