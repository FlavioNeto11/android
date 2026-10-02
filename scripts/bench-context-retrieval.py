#!/usr/bin/env python3
"""Mede o custo do retrieval de contexto sobre o plano-100 inteiro (modo local, sem rede, sem IA).

Fica FORA do runtime do produto: é uma régua para provar o ganho do índice BM25 persistente e do lote. Cada item do
plano vira uma pergunta (a mesma que `plano-100-pacotes.py --contexto` monta). Medições:

    ANTES  o que custava por item sem cache em disco e sem lote: um serviço novo e uma consulta fria, N amostras
    FRIO   a primeira consulta de um processo novo com o disco vazio: monta o índice e o grava
    DISCO  a primeira consulta de um processo novo com o índice já em disco (o caso da segunda geração de pacotes)
    LOTE   todos os itens num serviço só, dentro de `session()`

Uso (Python do venv do backend, a partir de qualquer pasta):

    backend/.venv/Scripts/python.exe scripts/bench-context-retrieval.py [--antes 3] [--json]

Usa uma pasta de dados TEMPORÁRIA: não toca em `data/` nem em cache de ninguém.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'backend'))

from app.config import load_config  # noqa: E402
from app.modules.context_retrieval.domain.model import RetrievalMode  # noqa: E402
from app.modules.context_retrieval.wiring import build_service  # noqa: E402


def itens_do_plano() -> list[dict]:
    spec = importlib.util.spec_from_file_location('plano_100_pacotes', RAIZ / 'scripts' / 'plano-100-pacotes.py')
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    itens, _ = modulo.montar()
    return itens


def pergunta(item: dict) -> str:
    return f"{item['titulo']}. {item['corpo']}"[:600]


def servico_novo(dados: Path, modo: RetrievalMode, *, com_cache_em_disco: bool = True):
    cfg = load_config()
    cfg.file.paths.data_dir = str(dados)
    cfg.file.context_retrieval.enabled = True
    cfg.file.context_retrieval.cache.enabled = com_cache_em_disco
    return build_service(cfg, root=RAIZ, mode=modo)


def p95(valores: list[float]) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(0.95 * len(ordenados)))]


def tamanho_do_cache(dados: Path) -> int:
    return sum(f.stat().st_size for f in (dados / 'context_retrieval' / 'bm25').glob('*.json'))


def medir(antes: int, modo: RetrievalMode) -> dict:
    itens = itens_do_plano()
    perguntas = [pergunta(i) for i in itens]
    saida: dict = {'items': len(itens), 'mode': modo.value}

    # ANTES: sem cache em disco, serviço novo por item (o que acontecia com um processo por item).
    amostras = []
    for q in perguntas[:antes]:
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.perf_counter()
            servico_novo(Path(tmp), modo, com_cache_em_disco=False).gather(q)
            amostras.append((time.perf_counter() - t0) * 1000)
    saida['BEFORE_PER_ITEM_MS'] = round(statistics.median(amostras), 1) if amostras else None

    with tempfile.TemporaryDirectory() as tmp:
        dados = Path(tmp)
        # FRIO
        t0 = time.perf_counter()
        servico = servico_novo(dados, modo)
        servico.gather(perguntas[0])
        saida['COLD_START_MS'] = round((time.perf_counter() - t0) * 1000, 1)
        bm25 = servico._local._bm25
        saida['COLD_BUILD_MS'] = round(bm25.stats['build_ms'], 1)
        saida['CACHE_SIZE_BYTES'] = tamanho_do_cache(dados)

        # DISCO: processo novo, índice em disco
        t0 = time.perf_counter()
        servico2 = servico_novo(dados, modo)
        servico2.gather(perguntas[0])
        saida['WARM_START_MS'] = round((time.perf_counter() - t0) * 1000, 1)
        bm25b = servico2._local._bm25
        saida['WARM_LOAD_MS'] = round(bm25b.stats['load_ms'], 1)
        saida['WARM_SOURCE'] = bm25b.last_source

        # LOTE: todos os itens, um serviço, uma sessão
        servico3 = servico_novo(dados, modo)
        tempos: list[float] = []
        t_lote = time.perf_counter()
        with servico3.session():
            for q in perguntas:
                t0 = time.perf_counter()
                servico3.gather(q)
                tempos.append((time.perf_counter() - t0) * 1000)
        total = time.perf_counter() - t_lote
        bm25c = servico3._local._bm25
        saida.update({
            'TOTAL_TIME_S': round(total, 2), 'FIRST_QUERY_MS': round(tempos[0], 1),
            'MEDIAN_QUERY_MS': round(statistics.median(tempos), 1), 'P95_QUERY_MS': round(p95(tempos), 1),
            'MAX_QUERY_MS': round(max(tempos), 1),
            'WARM_QUERY_MS': round(statistics.median(tempos[1:]), 1) if len(tempos) > 1 else None,
            'BM25_CACHE_HITS': int(bm25c.stats['disk_hits'] + bm25c.stats['memory_hits']),
            'BM25_CACHE_MISSES': int(bm25c.stats['misses']),
            'BM25_DISK_HITS': int(bm25c.stats['disk_hits']), 'BM25_MEMORY_HITS': int(bm25c.stats['memory_hits']),
        })
    anterior = saida['BEFORE_PER_ITEM_MS']
    if anterior:
        saida['BEFORE_ESTIMATED_TOTAL_S'] = round(anterior * len(itens) / 1000, 1)
        saida['SPEEDUP_X'] = round(anterior * len(itens) / 1000 / total, 1)
    return saida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--antes', type=int, default=3, help='amostras da linha de base (padrão 3)')
    ap.add_argument('--mode', choices=['local_only'], default='local_only')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    resultado = medir(args.antes, RetrievalMode(args.mode))
    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=1))
    else:
        for chave, valor in resultado.items():
            print(f'{chave}: {valor}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
