#!/usr/bin/env python3
"""Mede o custo de reconstruir o índice BM25 no repositório REAL, antes e depois do índice incremental (ADR-063). Zero rede.

Trabalha num worktree descartável do `--repo` (nunca edita o checkout): constrói o índice a frio e depois mede a primeira consulta
de cada cenário em que a revisão mudou. Roda o MESMO script contra o código antigo e o novo com `--backend <pasta backend>`:

    backend/.venv/Scripts/python.exe scripts/context-retrieval-incremental-bench.py [--backend <backend>] [--repo .] [--voltar 10]

Cenários (tempo da 1ª consulta depois da mudança): `frio` (sem nenhum índice), `uma_edicao` (um arquivo editado, mesmo processo),
`processo_novo` (outra edição, retriever novo com o índice só em disco) e `trocar_commit` (checkout de `HEAD~N`, mesmo processo).
Com o código antigo os contadores de reaproveitamento não existem e saem `null`.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PERGUNTA = 'onde o retrieval constroi o indice e calcula a pontuacao dos arquivos'


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True, check=True)
    return r.stdout.decode('utf-8', errors='replace').strip()


def principais(repo: Path, n: int) -> list[str]:
    """Os `n` primeiros arquivos `.py` rastreados de `backend/app` (estáveis e pequenos o bastante para editar)."""
    arquivos = [f for f in git(repo, 'ls-files', 'backend/app').splitlines() if f.endswith('.py')]
    return arquivos[:n]


def editar(scratch: Path, rel: str) -> None:
    p = scratch / rel
    p.write_text(p.read_text(encoding='utf-8') + '\n# edicao do bench: termo_inedito_do_bench\n', encoding='utf-8')


def consultar(retriever, scratch: Path) -> tuple[float, dict]:
    from app.modules.context_retrieval.domain.model import RetrievalRequest
    rev = retriever._ws.revision()
    t0 = time.perf_counter()
    retriever.retrieve(RetrievalRequest(query=PERGUNTA, root=scratch, revision=rev, top_k=5))
    ms = (time.perf_counter() - t0) * 1000
    lb = getattr(retriever, 'last_build', None) or {}
    return round(ms, 1), {'reaproveitados': lb.get('reused'), 'analisados': lb.get('analyzed'), 'semente': lb.get('seed'),
                          'fonte': retriever.last_source}


def medir(repo: Path, voltar: int, repeticoes: int) -> dict:
    from app.modules.context_retrieval.infrastructure.bm25 import BM25Retriever
    from app.modules.context_retrieval.infrastructure.workspace import Workspace

    resultados: dict[str, list] = {'frio': [], 'uma_edicao': [], 'processo_novo': [], 'trocar_commit': []}
    arquivos = 0
    for _ in range(repeticoes):
        with tempfile.TemporaryDirectory() as pasta:
            scratch = Path(pasta) / 'wt'
            cache = Path(pasta) / 'cache'
            git(repo, 'worktree', 'add', '--detach', '-q', str(scratch), 'HEAD')
            try:
                alvos = principais(scratch, 4)
                r = BM25Retriever(Workspace(scratch), cache_dir=cache)
                ms, info = consultar(r, scratch)
                resultados['frio'].append((ms, info))
                arquivos = len(r._indice.caminhos)
                editar(scratch, alvos[0])
                resultados['uma_edicao'].append(consultar(r, scratch))
                editar(scratch, alvos[1])
                novo = BM25Retriever(Workspace(scratch), cache_dir=cache)
                resultados['processo_novo'].append(consultar(novo, scratch))
                git(scratch, 'checkout', '-q', '--', '.')
                git(scratch, 'checkout', '-q', '--detach', f'HEAD~{voltar}')
                resultados['trocar_commit'].append(consultar(novo, scratch))
            finally:
                git(repo, 'worktree', 'remove', '--force', str(scratch))
    saida: dict = {'arquivos_no_indice': arquivos, 'repeticoes': repeticoes}
    for cenario, itens in resultados.items():
        tempos = [t for t, _ in itens]
        saida[cenario] = {'ms_mediana': round(statistics.median(tempos), 1), 'ms': tempos, 'detalhe': itens[-1][1]}
    return saida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--backend', type=Path, default=RAIZ / 'backend', help='pasta backend cujo código será medido')
    ap.add_argument('--repo', type=Path, default=RAIZ)
    ap.add_argument('--voltar', type=int, default=10, help='commits a voltar no cenário trocar_commit')
    ap.add_argument('--repeticoes', type=int, default=3)
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()
    sys.path.insert(0, str(args.backend.resolve()))
    resumo = {'backend': str(args.backend.resolve()), 'commit': git(args.repo, 'rev-parse', 'HEAD')[:12],
              **medir(args.repo, args.voltar, args.repeticoes)}
    texto = json.dumps(resumo, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(texto + '\n', encoding='utf-8')
    print(texto)
    return 0


if __name__ == '__main__':
    sys.exit(main())
