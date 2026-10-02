#!/usr/bin/env python3
"""Medição LOCAL e gratuita da qualidade do retrieval de contexto NESTE repositório (ADR-063). Zero rede, zero chamada paga.

Conjunto de avaliação tirado do histórico git: commits `feat`/`fix` recentes, sem merge, de escopo claro. A PERGUNTA é o
assunto (sem o prefixo `tipo(escopo):`) mais o corpo do commit, sem os trailers; o GABARITO são os arquivos de código
que o commit MODIFICOU ou APAGOU (existem no pai). Documentação, testes, `CHANGELOG`, `.claude/` e arquivo novo ficam de fora do
gabarito (arquivo novo não existe no índice do pai: ninguém o acharia).

Anti-vazamento: o índice é o do estado do PAI do commit (um worktree descartável em `--detach <pai>`), nunca o do commit.

Modos medidos, todos locais: `lexical` (ripgrep ou Python), `bm25` e `hybrid_local` (a fusão do `LocalRetriever`, o
`local_only` do produto). Variantes da pergunta: `completa` (assunto+corpo, repositório inteiro), `assunto` (só o assunto) e
`codigo` (assunto+corpo restrito a `backend/app`, `frontend/src` e `scripts`). Métricas: hit@3, hit@5 (algum arquivo do
gabarito entre os k primeiros), MRR@10 (1/posição do primeiro acerto, 0 se fora dos 10) e recall@5 (fração do gabarito nos 5).

    backend/.venv/Scripts/python.exe scripts/context-retrieval-local-eval.py [--ate <commit>] [--n 40] [--out resumo.json]
    ... --listar        # só mostra o conjunto (sem rodar o retrieval)
    ... --consumidor    # só o modo e a pergunta de `plano-100-pacotes.py --contexto`: hybrid_local, pergunta cortada em 600 caracteres,
                        # sem escopo (`completa`, o antes) e com o escopo de código (`codigo`, o padrão do consumidor)

Só mede: não altera ranking nem política. Limite assumido: a pergunta é a mensagem de commit, que costuma citar o símbolo ou o
arquivo (mais fácil que uma pergunta livre), então os números são um TETO para o léxico e não um benchmark de perguntas abertas.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'backend'))

TOP_K = 10
MAX_ARQUIVOS_DO_GABARITO = 8
MIN_CARACTERES_DA_PERGUNTA = 40
EXTENSOES_DE_CODIGO = {'.py', '.ts', '.tsx', '.js', '.ps1', '.sh', '.sql'}
ESCOPO_CODIGO = ('backend/app/', 'frontend/src/', 'scripts/')
MODOS = ('lexical', 'bm25', 'hybrid_local')
VARIANTES = ('completa', 'assunto', 'codigo')
MODOS_DO_CONSUMIDOR = ('hybrid_local',)
VARIANTES_DO_CONSUMIDOR = ('completa', 'codigo')
LIMITE_DA_PERGUNTA_DO_CONSUMIDOR = 600
_TIPO = re.compile(r'^(feat|fix)(\([^)]*\))?!?:\s*', re.IGNORECASE)
_RUIDO = re.compile(r'\[skip ci\]', re.IGNORECASE)


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True, check=check)
    return r.stdout.decode('utf-8', errors='replace')


def eh_teste(caminho: str) -> bool:
    partes = caminho.split('/')
    nome = partes[-1]
    return ('tests' in partes[:-1] or '__tests__' in partes[:-1] or nome.startswith('test_') or '.test.' in nome
            or '.spec.' in nome or nome == 'conftest.py')


def eh_codigo(caminho: str) -> bool:
    """Código de produção ou de operação: extensão de código, fora de docs, `.claude`, testes e changelog."""
    if caminho.startswith(('docs/', '.claude/')) or caminho.upper().startswith('CHANGELOG'):
        return False
    return Path(caminho).suffix.lower() in EXTENSOES_DE_CODIGO and not eh_teste(caminho)


def limpar_mensagem(bruta: str) -> tuple[str, str]:
    """(assunto sem `tipo(escopo):`, corpo), ambos sem `[skip ci]` e sem trailers (`Co-Authored-By`, `Generated with`)."""
    linhas = bruta.replace('\r\n', '\n').split('\n')
    assunto = _TIPO.sub('', _RUIDO.sub('', linhas[0])).strip()
    corpo: list[str] = []
    for linha in linhas[1:]:
        baixa = linha.strip().lower()
        if baixa.startswith(('co-authored-by:', 'generated with', '🤖')):
            continue
        corpo.append(_RUIDO.sub('', linha).rstrip())
    return assunto, '\n'.join(corpo).strip()


def metricas(ranking: list[str], gabarito: set[str]) -> dict:
    """Do ranking (os `TOP_K` primeiros) contra o gabarito."""
    posicoes = [i for i, p in enumerate(ranking[:TOP_K], 1) if p in gabarito]
    primeiro = posicoes[0] if posicoes else None
    return {'hit3': bool(primeiro and primeiro <= 3), 'hit5': bool(primeiro and primeiro <= 5),
            'rr': (1.0 / primeiro) if primeiro else 0.0,
            'recall5': len(gabarito & set(ranking[:5])) / len(gabarito) if gabarito else 0.0}


def agregar(linhas: list[dict], modos: tuple[str, ...] = MODOS, variantes: tuple[str, ...] = VARIANTES) -> dict:
    """Médias por modo e variante, a partir das linhas por commit (`linha['resultados'][variante][modo]`)."""
    saida: dict = {}
    for variante in variantes:
        saida[variante] = {}
        for modo in modos:
            itens = [c['resultados'][variante][modo] for c in linhas if modo in c['resultados'].get(variante, {})]
            n = len(itens)
            if not n:
                continue
            lat = sorted(i['ms'] for i in itens)
            saida[variante][modo] = {
                'n': n, 'hit3': round(sum(i['hit3'] for i in itens) / n, 4), 'hit5': round(sum(i['hit5'] for i in itens) / n, 4),
                'mrr10': round(sum(i['rr'] for i in itens) / n, 4), 'recall5': round(sum(i['recall5'] for i in itens) / n, 4),
                'latencia_mediana_ms': round(statistics.median(lat), 1),
                'latencia_p95_ms': round(lat[min(n - 1, int(0.95 * n))], 1), 'erros': sum(1 for i in itens if i.get('erro'))}
    return saida


def escolher_commits(repo: Path, ate: str, n: int) -> list[dict]:
    """Os `n` commits `feat`/`fix` mais recentes até `ate` (sem merge) com pergunta e gabarito aproveitáveis."""
    # todos os commits sem merge alcançáveis de `ate` (os de PR e os diretos na main), do mais novo ao mais antigo
    shas = git(repo, 'rev-list', '--no-merges', ate).split()
    escolhidos: list[dict] = []
    for sha in shas:
        if len(escolhidos) >= n:
            break
        bruta = git(repo, 'show', '-s', '--format=%B', sha)
        if not _TIPO.match(bruta.splitlines()[0] if bruta.strip() else ''):
            continue
        pais = git(repo, 'show', '-s', '--format=%P', sha).split()
        if len(pais) != 1:
            continue
        alterados = [l.split('\t') for l in git(repo, 'diff-tree', '--no-commit-id', '--name-status', '-r', '--no-renames', sha).splitlines()]
        gabarito = sorted({a[1] for a in alterados if len(a) == 2 and a[0] in ('M', 'D') and eh_codigo(a[1])})
        adicionados = sum(1 for a in alterados if len(a) == 2 and a[0] == 'A' and eh_codigo(a[1]))
        assunto, corpo = limpar_mensagem(bruta)
        pergunta = (assunto + '\n\n' + corpo).strip()
        if not gabarito or len(gabarito) > MAX_ARQUIVOS_DO_GABARITO or len(pergunta) < MIN_CARACTERES_DA_PERGUNTA:
            continue
        escolhidos.append({'sha': sha, 'pai': pais[0], 'assunto': assunto, 'pergunta': pergunta, 'gabarito': gabarito,
                           'arquivos_novos_fora_do_gabarito': adicionados})
    return escolhidos


def _recuperadores(raiz: Path, cache: Path):
    from app.modules.context_retrieval.application.local import LocalRetriever
    from app.modules.context_retrieval.infrastructure.bm25 import BM25Retriever
    from app.modules.context_retrieval.infrastructure.lexical import LexicalRetriever
    from app.modules.context_retrieval.infrastructure.workspace import Workspace

    ws = Workspace(raiz)
    lex = LexicalRetriever(ws, use_ripgrep=True, window_lines=7, ripgrep_path=None)
    bm = BM25Retriever(ws, k1=1.2, b=0.75, cache_dir=cache)
    return ws, lex, bm, LocalRetriever(lex, bm)


def avaliar(repo: Path, commits: list[dict], scratch: Path, modos: tuple[str, ...] = MODOS,
            variantes_pedidas: tuple[str, ...] = VARIANTES, limite_da_pergunta: int | None = None) -> list[dict]:
    """Roda os modos locais por commit, com o índice no estado do pai. `scratch` é uma pasta que ainda não existe."""
    from app.modules.context_retrieval.domain.model import RetrievalRequest

    git(repo, 'worktree', 'add', '--detach', '-q', str(scratch), commits[0]['pai'])
    linhas: list[dict] = []
    try:
        for c in commits:
            git(scratch, 'checkout', '-q', '--detach', c['pai'])
            git(scratch, 'clean', '-fdxq')
            with tempfile.TemporaryDirectory() as cache:
                ws, lex, bm, local = _recuperadores(scratch, Path(cache))
                resultados: dict = {v: {} for v in variantes_pedidas}
                gab = set(c['gabarito'])
                assunto_apenas = c['assunto']
                completa = c['pergunta'][:limite_da_pergunta] if limite_da_pergunta else c['pergunta']
                todas = {'completa': (completa, ()), 'assunto': (assunto_apenas, ()), 'codigo': (completa, ESCOPO_CODIGO)}
                variantes = {v: todas[v] for v in variantes_pedidas}
                with ws.pinned() as rev:
                    arquivos = len(ws.files())
                    primeira_bm25_ms = None
                    for variante, (pergunta, escopo) in variantes.items():
                        req = RetrievalRequest(query=pergunta, root=scratch, revision=rev, scope=escopo, top_k=TOP_K)
                        for modo in modos:
                            t0 = time.perf_counter()
                            erro = None
                            try:
                                if modo == 'lexical':
                                    sel = lex.retrieve(req)
                                elif modo == 'bm25':
                                    sel = bm.retrieve(req)
                                else:
                                    sel = local.retrieve(req)
                                ranking = [f.path for f in sel.selected_files]
                            except Exception as exc:  # noqa: BLE001  falha do retrieval conta como erro, nunca como acerto
                                ranking, erro = [], type(exc).__name__
                            ms = (time.perf_counter() - t0) * 1000
                            if modo == 'bm25' and primeira_bm25_ms is None:
                                primeira_bm25_ms = ms             # a primeira consulta paga a construção do índice
                            resultados[variante][modo] = {**metricas(ranking, gab), 'ms': round(ms, 1), 'erro': erro,
                                                          'top3': ranking[:3]}
                linhas.append({'sha': c['sha'][:12], 'assunto': c['assunto'][:90], 'gabarito': c['gabarito'], 'arquivos_no_indice': arquivos,
                               'primeira_consulta_bm25_ms': round(primeira_bm25_ms or 0.0, 1), 'resultados': resultados})
    finally:
        git(repo, 'worktree', 'remove', '--force', str(scratch), check=False)
    return linhas


def tabela(agregado: dict, modos: tuple[str, ...] = MODOS, variantes: tuple[str, ...] = VARIANTES) -> str:
    saida = ['| Variante | Modo | n | hit@3 | hit@5 | MRR@10 | recall@5 | latência mediana | p95 |', '|---|---|---|---|---|---|---|---|---|']
    for variante in variantes:
        for modo in modos:
            a = agregado.get(variante, {}).get(modo)
            if a:
                saida.append(f"| {variante} | {modo} | {a['n']} | {a['hit3']:.1%} | {a['hit5']:.1%} | {a['mrr10']:.3f} | {a['recall5']:.1%} "
                             f"| {a['latencia_mediana_ms']} ms | {a['latencia_p95_ms']} ms |")
    return '\n'.join(saida)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--repo', type=Path, default=RAIZ)
    ap.add_argument('--ate', default='HEAD', help='último commit a considerar (o resumo grava o SHA)')
    ap.add_argument('--n', type=int, default=40)
    ap.add_argument('--listar', action='store_true', help='só mostra o conjunto de avaliação')
    ap.add_argument('--consumidor', action='store_true',
                    help='só o modo e a pergunta de plano-100-pacotes.py --contexto (hybrid_local, 600 caracteres), sem escopo x escopo de código')
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()

    ate = git(args.repo, 'rev-parse', args.ate).strip()
    commits = escolher_commits(args.repo, ate, args.n)
    if args.listar or not commits:
        for c in commits:
            print(c['sha'][:12], len(c['gabarito']), c['assunto'][:100])
        print(f'{len(commits)} commits')
        return 0
    with tempfile.TemporaryDirectory() as pasta:
        t0 = time.perf_counter()
        modos, variantes = (MODOS_DO_CONSUMIDOR, VARIANTES_DO_CONSUMIDOR) if args.consumidor else (MODOS, VARIANTES)
        linhas = avaliar(args.repo, commits, Path(pasta) / 'indice-do-pai', modos, variantes,
                         LIMITE_DA_PERGUNTA_DO_CONSUMIDOR if args.consumidor else None)
        total_s = time.perf_counter() - t0
    agregado = agregar(linhas, modos, variantes)
    primeiras = sorted(l['primeira_consulta_bm25_ms'] for l in linhas)
    resumo = {'ate': ate, 'commits': len(linhas), 'top_k': TOP_K, 'agregado': agregado,
              'indice_bm25_primeira_consulta_ms': {'mediana': round(statistics.median(primeiras), 1), 'max': primeiras[-1]},
              'arquivos_no_indice_mediana': int(statistics.median(l['arquivos_no_indice'] for l in linhas)),
              'duracao_total_s': round(total_s, 1), 'por_commit': linhas}
    if args.out:
        args.out.write_text(json.dumps(resumo, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(tabela(agregado, modos, variantes))
    indice = '' if args.consumidor else f" indice(1a consulta bm25) mediana={resumo['indice_bm25_primeira_consulta_ms']['mediana']} ms"
    print(f"commits={len(linhas)} ate={ate[:12]}{indice} arquivos={resumo['arquivos_no_indice_mediana']} total={resumo['duracao_total_s']} s")
    return 0


if __name__ == '__main__':
    sys.exit(main())
