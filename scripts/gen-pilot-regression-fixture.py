#!/usr/bin/env python3
"""Congela os resultados PÚBLICOS do piloto (holdout Poetry 2.5.1) como fixture da regressão da regra híbrida v1.

Fora do runtime e fora da suíte: roda uma vez, a mão, onde existam os dados do piloto (`data/jev-pilot/holdout-real/
results.json` e `holdout_bench/golden.json` da branch `claude/jev-pilot`, que NÃO é mergeada). O resultado, pequeno e
sem código, vai para `backend/tests/fixtures/context_retrieval/pilot_regression.json` e é o que o teste lê; a suíte não
depende da branch do piloto nem de rede.

Cada caso guarda o que o piloto MEDIU: a saída do ripgrep (arquivos e janelas), a do `jev_map` (arquivos e regiões) e o que o
híbrido do piloto entregou a partir das duas. A regressão alimenta a implementação nova com as duas primeiras e confere que
chega ao que o piloto entregou (ver `test_context_retrieval_pilot_regression.py` para o que é igual e o que diverge de propósito).

    python scripts/gen-pilot-regression-fixture.py --results <results.json> --golden <golden.json> --hybrid-py <experiments/jev/hybrid.py>

`--hybrid-py` é o módulo da regra do piloto (só stdlib): é ele que calcula os identificadores, a salvaguarda e a saída PURA da
regra, para a regressão comparar a regra nova com a regra velha e não com um número que alguém copiou.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

DESTINO = Path(__file__).resolve().parents[1] / 'backend' / 'tests' / 'fixtures' / 'context_retrieval' / 'pilot_regression.json'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--results', type=Path, required=True)
    ap.add_argument('--golden', type=Path, required=True)
    ap.add_argument('--hybrid-py', type=Path, required=True)
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('pilot_hybrid', args.hybrid_py)
    piloto = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(piloto)
    resultados = json.loads(args.results.read_text(encoding='utf-8'))
    golden = json.loads(args.golden.read_text(encoding='utf-8'))
    perguntas = {i['id']: i for i in golden['items']}
    por_item: dict[str, dict[str, dict]] = {}
    for linha in resultados['rows']:
        por_item.setdefault(linha['id'], {})[linha['strategy']] = linha
    casos = []
    for item_id in sorted(por_item):
        rg, jev, hib = (por_item[item_id][k] for k in ('ripgrep', 'jev_map', 'hybrid'))
        pergunta = perguntas[item_id]['question']
        ativa = piloto.safeguard_active(pergunta, rg['selected_files'])
        topo_lexical = [r for r in rg['selected_regions'] if rg['selected_files'] and r[0] == rg['selected_files'][0]]
        grupo = 'MIXED' if hib.get('mixed') else hib['group']
        casos.append({
            'id': item_id, 'group': grupo, 'category': hib['category'], 'question': pergunta,
            'identifiers': piloto.explicit_identifiers(pergunta), 'safeguard': ativa,
            'rule_files': piloto.merge_files(ativa, rg['selected_files'], jev['ranked_files_top10']),
            'rule_regions': piloto.merge_regions(ativa, topo_lexical if ativa else [], jev['selected_regions']),
            'rg_files': rg['selected_files'], 'rg_regions': rg['selected_regions'],
            'jev_files': jev['ranked_files_top10'], 'jev_regions': jev['selected_regions'],
            'pilot_files': hib['ranked_files_top10'], 'pilot_regions': hib['selected_regions'],
            'pilot_fallback': bool(hib.get('fallback')),
        })
    saida = {'source': 'claude/jev-pilot, holdout Poetry 2.5.1 (SHA 94b6e35b9091991887aa54feeb3771a86d3bd692)',
             'hybrid_rule_version': resultados['meta'].get('hybrid_rule', {}).get('version', '1'),
             'cases': casos}
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(json.dumps(saida, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'{len(casos)} casos -> {DESTINO}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
