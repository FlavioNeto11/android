"""Quais testes rodar para o que mudou: o ciclo rápido do desenvolvimento (segundos, não os ~35 min da suíte).

Mapeia os arquivos alterados aos testes que os importam, direta ou indiretamente (grafo de imports do backend),
e sempre inclui os guardas de arquitetura e do pacote do agente. Só stdlib; não chama IA nem toca aparelho.

Uso (a partir da raiz do repositório):

    python scripts/testes-afetados.py                 # lista (base origin/main + árvore de trabalho)
    python scripts/testes-afetados.py --run           # lista e roda com o pytest do venv do backend
    python scripts/testes-afetados.py --run --ocioso  # idem, em prioridade ociosa (não disputa CPU com os emuladores)
    python scripts/testes-afetados.py --base HEAD~3   # contra outra base
    python scripts/testes-afetados.py --arquivos backend/app/devices/rede.py   # sem git, arquivos dados

Limites, ditos de frente: o grafo vê `import` e `from ... import`, não importação dinâmica nem arquivo de dados
lido em tempo de execução; mudança em módulo muito central (`state.py`, `models.py`, `api.py`) afeta quase tudo e o
script avisa ("amplo"). Ele acelera o laço de trabalho; não substitui a suíte inteira antes de uma entrega que
mexa em contrato compartilhado ou migração.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GUARDAS = ("test_arquitetura.py", "test_pacote_do_agente.py")
LIMITE_AMPLO = 0.5  # fração dos testes a partir da qual o resultado é "amplo"
PROFUNDIDADE = 3
LIMITE_HUB = 12  # módulo importado por tantos outros que propagar a partir dele não distingue nada
GENERICOS = {"app", "main", "state", "api", "models", "service", "services", "config", "utils", "core", "common", "base",
             "types", "client", "tools", "helpers", "domain", "application", "infrastructure", "modules", "social",
             "devices", "taskqueue", "planning", "security", "workers", "worker", "commands", "automation"}


def _modulo_de(caminho: Path, pacote_raiz: Path) -> str:
    rel = caminho.relative_to(pacote_raiz.parent).with_suffix("")
    partes = list(rel.parts)
    if partes[-1] == "__init__":
        partes.pop()
    return ".".join(partes)


def _imports(arvore: ast.AST, modulo_atual: str, eh_pacote: bool) -> set[str]:
    saida: set[str] = set()
    base = modulo_atual.split(".") if eh_pacote else modulo_atual.split(".")[:-1]
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for a in no.names:
                saida.add(a.name)
        elif isinstance(no, ast.ImportFrom):
            if no.level:
                ancora = base[: len(base) - (no.level - 1)] if no.level > 1 else base
                prefixo = ".".join(ancora + ([no.module] if no.module else []))
            else:
                prefixo = no.module or ""
            if prefixo:
                saida.add(prefixo)
                for a in no.names:  # `from app.x import y` pode ser o submódulo app.x.y
                    saida.add(f"{prefixo}.{a.name}")
    return saida


def _le(caminho: Path) -> ast.AST | None:
    try:
        return ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def grafo_backend(raiz: Path) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Retorna (quem_importa[modulo] = módulos que o importam, arquivo_do_modulo)."""
    app = raiz / "backend" / "app"
    modulos: dict[str, str] = {}
    importa: dict[str, set[str]] = {}
    for f in app.rglob("*.py"):
        nome = _modulo_de(f, app)
        modulos[nome] = f.relative_to(raiz).as_posix()
    for nome, rel in modulos.items():
        arv = _le(raiz / rel)
        if arv is None:
            continue
        importa[nome] = {i for i in _imports(arv, nome, rel.endswith("__init__.py")) if i in modulos}
    quem: dict[str, set[str]] = {m: set() for m in modulos}
    for m, deps in importa.items():
        for d in deps:
            quem[d].add(m)
    return quem, modulos


def testes_do_backend(raiz: Path) -> dict[str, set[str]]:
    """Retorna teste (caminho relativo) -> módulos app.* que ele importa."""
    pasta = raiz / "backend" / "tests"
    saida: dict[str, set[str]] = {}
    for f in pasta.rglob("test_*.py"):
        arv = _le(f)
        if arv is None:
            continue
        saida[f.relative_to(raiz).as_posix()] = {i for i in _imports(arv, "tests." + f.stem, False) if i.startswith("app")}
    return saida


def afetados(raiz: Path, arquivos: list[str]) -> dict:
    quem, modulos = grafo_backend(raiz)
    por_arquivo = {v: k for k, v in modulos.items()}
    testes = testes_do_backend(raiz)
    escolhidos: set[str] = set()
    motivos: dict[str, str] = {}
    mudados: set[str] = set()
    sugestoes: list[str] = []

    def marca(teste: str, motivo: str) -> None:
        if teste in testes or (raiz / teste).exists():
            escolhidos.add(teste)
            motivos.setdefault(teste, motivo)

    for a in arquivos:
        a = a.replace("\\", "/")
        if a in por_arquivo:
            mudados.add(por_arquivo[a])
        elif a.startswith("backend/tests/") and a.endswith(".py") and Path(a).name.startswith("test_"):
            marca(a, "o próprio teste mudou")
        elif a.startswith("backend/migrations/"):
            for t in testes:
                if any(k in Path(t).name for k in ("migra", "banco", "db", "schema")):
                    marca(t, f"migração {Path(a).name}")
        elif a.startswith("scripts/") and a.endswith(".py"):
            cand = f"scripts/tests/test_{Path(a).stem.replace('-', '_')}.py"
            if (raiz / cand).exists():
                marca(cand, f"script {Path(a).name}")
        elif a.startswith("frontend/src/"):
            sugestoes.append(f"cd frontend && npx vitest related {a} --run")
        elif a.startswith("backend/app/conhecimento/") or a.startswith("contracts/"):
            for t in testes:
                if any(k in Path(t).name for k in ("pacote", "conhecimento", "skill", "capabil", "contrato")):
                    marca(t, f"dado declarado {a}")

    # fecho reverso dos módulos mudados, com profundidade limitada e SEM atravessar os "hubs" (main, state, api...):
    # quase tudo importa um hub, e atravessá-lo transformaria qualquer mudança em "a suíte inteira".
    hubs = {m for m, qs in quem.items() if len(qs) >= LIMITE_HUB} | {"app.main", "app.state", "app.api", "app.config", "app.models", "app.db"}
    alcance = set(mudados)
    fronteira = set(mudados)
    for _ in range(PROFUNDIDADE):
        proximos: set[str] = set()
        for m in fronteira:
            if m in hubs and m not in mudados:
                continue
            proximos |= quem.get(m, set())
        fronteira = proximos - alcance
        alcance |= proximos
    alcance_util = {m for m in alcance if m not in hubs or m in mudados}
    for t, mods in testes.items():
        batem = mods & alcance_util
        if batem:
            marca(t, "importa " + sorted(batem, key=len)[0])
    # teste homônimo ou do mesmo assunto (nome do arquivo), mesmo sem import direto: cobre os testes de integração
    # que só importam `app.main` e exercitam o módulo por HTTP
    for m in mudados:
        partes = m.split(".")
        fichas = {p for seg in partes[-2:] for p in seg.split("_") if len(p) >= 4 and p not in GENERICOS}
        base = partes[-1]
        for t in testes:
            nome = Path(t).name
            if nome.startswith(f"test_{base}"):
                marca(t, f"homônimo de {m}")
            elif fichas & set(Path(t).stem.removeprefix("test_").split("_")):
                marca(t, f"mesmo assunto de {m}")
    for g in GUARDAS:
        marca(f"backend/tests/{g}", "guarda permanente")

    total = len(testes)
    amplo = total > 0 and len([t for t in escolhidos if t in testes]) / total >= LIMITE_AMPLO
    return {"testes": sorted(escolhidos), "motivos": motivos, "mudados": sorted(mudados), "total_backend": total,
            "amplo": amplo, "sugestoes": sugestoes}


def arquivos_mudados(raiz: Path, base: str) -> list[str]:
    def git(*args: str) -> list[str]:
        r = subprocess.run(["git", *args], cwd=raiz, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return [x for x in r.stdout.splitlines() if x.strip()]

    vistos: dict[str, None] = {}
    for lista in (git("diff", "--name-only", f"{base}...HEAD"), git("diff", "--name-only"), git("diff", "--name-only", "--cached"),
                  git("ls-files", "--others", "--exclude-standard")):
        for x in lista:
            vistos[x] = None
    return list(vistos)


def python_do_backend(raiz: Path) -> str:
    for cand in (raiz / "backend/.venv/Scripts/python.exe", raiz / "backend/.venv/bin/python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--arquivos", nargs="*", help="em vez do git: estes arquivos (caminhos relativos à raiz)")
    ap.add_argument("--run", action="store_true", help="roda o pytest com os testes escolhidos")
    ap.add_argument("--ocioso", action="store_true", help="prioridade ociosa (Windows) ao rodar")
    args = ap.parse_args()

    arquivos = args.arquivos if args.arquivos is not None else arquivos_mudados(RAIZ, args.base)
    r = afetados(RAIZ, arquivos)
    print(f"{len(arquivos)} arquivo(s) mudado(s); {len(r['mudados'])} módulo(s) do backend; "
          f"{len(r['testes'])} de {r['total_backend']} arquivos de teste do backend.")
    for t in r["testes"]:
        print(f"  {t}   ({r['motivos'].get(t, '')})")
    for s in r["sugestoes"]:
        print(f"  front: {s}")
    if r["amplo"]:
        print("AMPLO: a mudança atinge metade ou mais dos testes; considere a suíte inteira (uma vez, em segundo plano).")
    if not args.run:
        return 0
    if not r["testes"]:
        print("nada a rodar")
        return 0
    cmd = [python_do_backend(RAIZ), "-m", "pytest", "-q", *[t.removeprefix("backend/") for t in r["testes"] if t.startswith("backend/")]]
    flags = getattr(subprocess, "IDLE_PRIORITY_CLASS", 0) if args.ocioso else 0
    print("rodando:", " ".join(cmd[:4]), "...")
    return subprocess.run(cmd, cwd=RAIZ / "backend", creationflags=flags).returncode


if __name__ == "__main__":
    sys.exit(main())
