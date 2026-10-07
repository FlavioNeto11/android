"""29.199: ONDE o servidor PostgreSQL gasta CPU numa corrida de testes (pg_stat_statements num contêiner descartável próprio).

A medida do 29.197 mostrou o contêiner do PG a 400-500 % de 600 % (VM do Docker de 6 vCPUs) durante a série `-n 8`: o servidor é o limite. Este
script responde "em quê": sobe um contêiner `farm-pg-diag` (porta 55440, tmpfs 2 GB, a MESMA configuração do `farm-pg-rapido` mais
`shared_preload_libraries=pg_stat_statements`), roda a fatia de testes contra ele e devolve a tabela por comando normalizado, com a % do tempo total e a
soma por CATEGORIA (esvaziamento do esquema do worker, aplicação sob teste etc.). Não toca o `farm-pg-rapido` nem o banco do ambiente central.

    python scripts/pg-diagnostico.py --lista fatia.txt --simular            # só diz o que faria (sem docker)
    python scripts/pg-diagnostico.py --lista fatia.txt --workers 8          # sobe, roda, lê, para o contêiner
    python scripts/pg-diagnostico.py --ler --saida tabela.md                # só lê o contêiner de pé

Veredito (decide o 29.199): a soma das categorias do HARNESS (`truncate`, `impressao`, `reseed`, `sequencia`, `terminate`, `migracoes`) em % do total:
>= 35 % vale o código (H1/H2 da leitura); < 15 % o item morre; no meio, é zona cinzenta e se olha comando a comando.

Uma rodada por vez na máquina (a mesma trava do `pg-rapido.py`); pytest em prioridade ociosa dentro de um Job Object; só stdlib.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

AQUI = Path(__file__).resolve().parent
NOME = "farm-pg-diag"
PORTA = 55440
TMPFS_MB = 2048
LIMITE_VALE = 35.0
LIMITE_MORRE = 15.0
CATEGORIAS_DO_HARNESS = ("truncate", "impressao", "reseed", "sequencia", "terminate", "migracoes")
EXTRAS_DO_SERVIDOR = ("shared_preload_libraries=pg_stat_statements", "pg_stat_statements.track=all", "pg_stat_statements.max=10000")

#: Consulta que lê o pg_stat_statements do banco `farm` (sem a do próprio diagnóstico) como UM JSON, para não depender de separador.
SQL_LEITURA = (
    "SELECT coalesce(json_agg(t), '[]'::json) FROM ("
    " SELECT calls, total_exec_time AS total_ms, mean_exec_time AS media_ms, rows, query"
    " FROM pg_stat_statements s JOIN pg_database d ON d.oid = s.dbid"
    " WHERE d.datname = 'farm' AND s.query NOT ILIKE '%pg_stat_statements%'"
    " ORDER BY total_exec_time DESC LIMIT 600) t")


def _carregar_pg_rapido():
    spec = importlib.util.spec_from_file_location("pg_rapido", AQUI / "pg-rapido.py")
    mod = importlib.util.module_from_spec(spec)           # type: ignore[arg-type]
    sys.modules["pg_rapido"] = mod                        # o `@dataclass` procura o módulo em `sys.modules`
    spec.loader.exec_module(mod)                          # type: ignore[union-attr]
    return mod


# ------------------------------------------------------------------------------------------------------ classificação (pura)
_REGRAS: list[tuple[str, "re.Pattern[str]"]] = [
    ("truncate", re.compile(r"^\s*truncate\b", re.I)),
    ("impressao", re.compile(r"information_schema\.columns|string_agg\(x|pg_get_constraintdef|pg_views\b", re.I)),
    ("reseed", re.compile(r"overriding\s+system\s+value", re.I)),
    ("sequencia", re.compile(r"\bsetval\b|alter\s+sequence|pg_sequences", re.I)),
    ("terminate", re.compile(r"pg_terminate_backend|pg_stat_activity", re.I)),
    ("migracoes", re.compile(r"schema_migrations", re.I)),
    ("ddl", re.compile(r"^\s*(create|alter|drop)\s+(schema|table|index|unique|view|function|trigger|extension|type)\b", re.I)),
    ("sessao", re.compile(r"^\s*(set|reset|show|begin|commit|rollback|savepoint|release|deallocate|discard)\b", re.I)),
]


def classificar(query: str) -> str:
    """A categoria de um comando normalizado do pg_stat_statements. Só o HARNESS dos testes tem categoria própria; o resto é `aplicacao`."""
    for nome, padrao in _REGRAS:
        if padrao.search(query):
            return nome
    return "aplicacao"


def resumir(linhas: Sequence[dict]) -> dict:
    """{total_ms, categorias: {nome: {ms, pct, calls}}, harness_pct, veredito, topo: [...]} a partir das linhas do pg_stat_statements."""
    total = sum(float(x["total_ms"]) for x in linhas)
    cat: dict[str, dict] = {}
    topo = []
    for x in linhas:
        c = classificar(str(x["query"]))
        ms = float(x["total_ms"])
        e = cat.setdefault(c, {"ms": 0.0, "calls": 0})
        e["ms"] += ms
        e["calls"] += int(x["calls"])
        topo.append({"categoria": c, "calls": int(x["calls"]), "total_ms": ms, "media_ms": float(x["media_ms"]), "rows": int(x["rows"]),
                     "pct": (100.0 * ms / total) if total else 0.0, "query": " ".join(str(x["query"]).split())})
    for e in cat.values():
        e["pct"] = (100.0 * e["ms"] / total) if total else 0.0
    harness = sum(cat.get(c, {"pct": 0.0})["pct"] for c in CATEGORIAS_DO_HARNESS)
    return {"total_ms": total, "categorias": cat, "harness_pct": harness, "veredito": veredito(harness, total), "topo": topo}


def veredito(harness_pct: float, total_ms: float) -> str:
    if total_ms <= 0:
        return "sem dados: o pg_stat_statements voltou vazio (a corrida não rodou ou a extensão não carregou); nada se conclui"
    if harness_pct >= LIMITE_VALE:
        return f"VALE o código: o harness (esvaziamento do esquema) é {harness_pct:.1f} % do tempo do servidor (limite {LIMITE_VALE:.0f} %): H1/H2 da leitura"
    if harness_pct < LIMITE_MORRE:
        return f"O ITEM MORRE: o harness é só {harness_pct:.1f} % do tempo do servidor (abaixo de {LIMITE_MORRE:.0f} %); o gargalo é a aplicação sob teste"
    return f"ZONA CINZENTA: o harness é {harness_pct:.1f} % (entre {LIMITE_MORRE:.0f} e {LIMITE_VALE:.0f} %): olhar comando a comando antes de decidir"


def tabela_md(r: dict, quantos: int = 25) -> str:
    """A tabela "onde o servidor gasta CPU": categorias e os `quantos` comandos de maior tempo, em Markdown."""
    saida = [f"# Onde o servidor PG gasta CPU (pg_stat_statements, banco farm)", "",
             f"Tempo total de execução no servidor: {r['total_ms'] / 1000:.1f} s. **Veredito:** {r['veredito']}", "",
             "| Categoria | Tempo (s) | % do total | Chamadas |", "|---|---:|---:|---:|"]
    for nome, e in sorted(r["categorias"].items(), key=lambda kv: -kv[1]["ms"]):
        marca = " (harness)" if nome in CATEGORIAS_DO_HARNESS else ""
        saida.append(f"| {nome}{marca} | {e['ms'] / 1000:.1f} | {e['pct']:.1f} | {e['calls']} |")
    saida += ["", f"## Os {quantos} comandos de maior tempo", "", "| % | Tempo (s) | Chamadas | Média (ms) | Categoria | Comando normalizado |",
              "|---:|---:|---:|---:|---|---|"]
    for t in r["topo"][:quantos]:
        q = t["query"][:140].replace("|", "\\|")
        saida.append(f"| {t['pct']:.1f} | {t['total_ms'] / 1000:.1f} | {t['calls']} | {t['media_ms']:.1f} | {t['categoria']} | `{q}` |")
    return "\n".join(saida) + "\n"


# ---------------------------------------------------------------------------------------------------------------- contêiner
def comando_docker_run(pg) -> list[str]:
    """O `docker run` do diagnóstico: o MESMO do farm-pg-rapido (mesma imagem, configuração e tmpfs de 2 GB) com nome, porta e a extensão carregada."""
    inst = pg.Instancia(NOME, PORTA, TMPFS_MB)
    cmd = pg.comando_docker_run(inst)
    for c in EXTRAS_DO_SERVIDOR:
        cmd += ["-c", c]
    return cmd


def subir(pg, executar: Callable, dormir: Callable[[float], None], relatar: Callable[[str], None], prazo_s: float = 240) -> bool:
    executar(["docker", "rm", "-f", NOME])
    run = executar(comando_docker_run(pg))
    if run.returncode != 0:
        relatar(f"docker run saiu com rc={run.returncode}: {' '.join((run.stderr or run.stdout or '').split())[:300] or 'sem mensagem'}")
        return False
    inicio = time.monotonic()
    while time.monotonic() - inicio < prazo_s:
        if executar(["docker", "exec", NOME, "pg_isready", "-h", "127.0.0.1", "-p", "5432", "-U", "postgres", "-d", "farm"]).returncode == 0:
            break
        dormir(2)
    else:
        relatar("o contêiner não aceitou conexão no prazo")
        return False
    ext = executar(["docker", "exec", NOME, "psql", "-U", "postgres", "-d", "farm", "-c", "CREATE EXTENSION IF NOT EXISTS pg_stat_statements"])
    if ext.returncode != 0:
        relatar(f"CREATE EXTENSION pg_stat_statements falhou: {' '.join((ext.stderr or ext.stdout or '').split())[:300]}")
        return False
    executar(["docker", "exec", NOME, "psql", "-U", "postgres", "-d", "farm", "-c", "SELECT pg_stat_statements_reset()"])
    return True


def ler(executar: Callable) -> list[dict]:
    r = executar(["docker", "exec", NOME, "psql", "-U", "postgres", "-d", "farm", "-tA", "-c", SQL_LEITURA])
    if r.returncode != 0:
        raise RuntimeError(f"a leitura do pg_stat_statements falhou: {' '.join((r.stderr or r.stdout or '').split())[:300]}")
    return json.loads(r.stdout.strip() or "[]")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lista", type=Path, help="arquivo com um teste por linha, relativo a backend/ (a fatia da corrida)")
    ap.add_argument("--workers", type=int, default=8, help="`-n` do pytest (padrão 8)")
    ap.add_argument("--saida", type=Path, default=None, help="grava a tabela em Markdown aqui (e o JSON ao lado, .json)")
    ap.add_argument("--ler", action="store_true", help="só lê o pg_stat_statements do contêiner de pé e imprime a tabela")
    ap.add_argument("--manter", action="store_true", help="não para o contêiner no fim (para ler de novo com --ler)")
    ap.add_argument("--simular", action="store_true", help="só diz o que faria, sem docker")
    args = ap.parse_args(argv)
    pg = _carregar_pg_rapido()

    def relatar(linha: str) -> None:
        print(linha, flush=True)

    def entregar(linhas: list[dict]) -> int:
        r = resumir(linhas)
        texto = tabela_md(r)
        print(texto)
        if args.saida:
            args.saida.parent.mkdir(parents=True, exist_ok=True)
            args.saida.write_text(texto, encoding="utf-8")
            args.saida.with_suffix(".json").write_text(json.dumps({k: r[k] for k in ("total_ms", "categorias", "harness_pct", "veredito")}, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
        return 0 if r["total_ms"] > 0 else 2

    if args.ler:
        return entregar(ler(pg._executar))
    if not args.lista:
        ap.error("--lista é obrigatória (ou --ler)")
    backend = pg.RAIZ / "backend"
    arquivos = [ln.strip() for ln in args.lista.read_text(encoding="utf-8").splitlines()
                if ln.strip() and "conftest" not in ln and (backend / ln.strip()).exists()]
    if not arquivos:
        relatar("NÃO RODOU: a lista não tem nenhum teste que exista em backend/")
        return 2
    if args.simular:
        relatar("docker: " + " ".join(comando_docker_run(pg)))
        relatar(f"depois: CREATE EXTENSION pg_stat_statements + pg_stat_statements_reset(); pytest -n {args.workers} em {len(arquivos)} arquivos "
                f"({arquivos[0]} … {arquivos[-1]}) contra postgresql://…:{PORTA}/farm; ler, imprimir a tabela e "
                + ("manter" if args.manter else "parar") + f" o contêiner {NOME}")
        return 0
    if not pg.tentar_travar():
        relatar("NÃO RODOU: outra rodada do pg-rapido/diagnóstico está em curso; nenhum contêiner foi tocado")
        return 10
    livre = pg.ram_livre_gb()
    if livre is not None and livre < pg.RAM_MINIMA_GB:
        relatar(f"NÃO RODOU: {livre} GB livres, abaixo de {pg.RAM_MINIMA_GB}")
        pg.soltar_trava()
        return 9
    subiu = False
    try:
        subiu = True
        if not subir(pg, pg._executar, time.sleep, relatar):
            return 8
        saida_pytest = (args.saida.parent if args.saida else Path.cwd()) / "pg_diagnostico_pytest.txt"
        dsn = f"postgresql://postgres:teste@127.0.0.1:{PORTA}/farm"
        relatar(f"pytest -n {args.workers} em {len(arquivos)} arquivos, início {pg.agora()}")
        proc = pg._lancar_pytest(arquivos, saida_pytest, dsn, args.workers)
        rc = proc.wait()
        pg.fechar_job(proc)
        relatar(f"pytest rc={rc} fim {pg.agora()} | {pg.ultima_contagem(saida_pytest)}")
        saida_leitura = entregar(ler(pg._executar))
        return rc or saida_leitura
    finally:
        if subiu and not args.manter:
            try:
                pg._executar(["docker", "stop", NOME])
            except BaseException as exc:  # noqa: BLE001
                print(f"ATENÇÃO: o docker stop {NOME} não terminou ({type(exc).__name__}: {exc}); confira com `docker ps`", file=sys.stderr, flush=True)
        pg.soltar_trava()


if __name__ == "__main__":
    sys.exit(main())
