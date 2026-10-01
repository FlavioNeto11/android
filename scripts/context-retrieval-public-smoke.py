#!/usr/bin/env python3
"""Fumaça REAL e pública do pipeline novo de retrieval contra o Jev (System One). Não é um benchmark.

Valida o ADAPTADOR e o PIPELINE novos de ponta a ponta: implementação nova -> API real -> resposta válida -> uso/custo ->
cache -> resultado híbrido. Só código PÚBLICO, no checkout fixado do piloto (python-poetry/poetry, SHA abaixo). NUNCA o
repositório deste projeto: a política de envio bloqueia código privado de qualquer forma (`PRIVATE_CODE_SEND_APPROVED = False`)
e este script ainda recusa rodar se a raiz não for o checkout público íntegro.

Limites (travados no código, não em argumento): 6 perguntas, 12 chamadas, sem nova tentativa. As perguntas são escolhidas por regra
ANTES de qualquer execução e vêm intactas do holdout do piloto: as 2 primeiras de cada grupo, na ordem do `golden.json`.

A chave `TYPESAFE_API_KEY` vem só do ambiente/`.env` pelo `EnvSettings`; este script nunca a lê, imprime ou grava. Sem chave:
`REAL_PUBLIC_SMOKE = NOT_RUN_KEY_MISSING`, sem erro (e isso não bloqueia nada).

    backend/.venv/Scripts/python.exe scripts/context-retrieval-public-smoke.py --checkout <poetry> --golden <golden.json> [--run]

Sem `--run` só mostra o plano (perguntas, limites, estado da chave) e não chama nada.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'backend'))

SHA = '94b6e35b9091991887aa54feeb3771a86d3bd692'
MAX_PERGUNTAS = 6
MAX_CHAMADAS = 12
POR_GRUPO = {'EXACT': 2, 'SEMANTIC': 2, 'MIXED': 2}


def escolher(golden: dict) -> list[dict]:
    """As duas primeiras perguntas de cada grupo, na ordem do golden. Regra fixa, sem olhar resultado nenhum."""
    itens = golden['items']
    saida: list[dict] = []
    for grupo, n in POR_GRUPO.items():
        if grupo == 'EXACT':
            candidatos = [i for i in itens if i['category'].startswith('EXACT_')]
        elif grupo == 'SEMANTIC':
            candidatos = [i for i in itens if i['category'].startswith('SEMANTIC_')]
        else:
            candidatos = [i for i in itens if i['category'].startswith('MIXED_')]
        saida += [{**i, 'group': grupo} for i in candidatos[:n]]
    assert len(saida) == MAX_PERGUNTAS
    return saida


def git(checkout: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=checkout, capture_output=True, text=True, check=True).stdout.strip()


def verificar_checkout(checkout: Path) -> None:
    if git(checkout, 'rev-parse', 'HEAD') != SHA:
        raise SystemExit(f'checkout fora do SHA fixado {SHA}')
    if git(checkout, 'status', '--porcelain'):
        raise SystemExit('checkout sujo: recusado')
    if 'python-poetry/poetry' not in git(checkout, 'remote', 'get-url', 'origin'):
        raise SystemExit('origem inesperada: recusado')
    if 'Permission is hereby granted' not in (checkout / 'LICENSE').read_text(encoding='utf-8'):
        raise SystemExit('licença MIT não conferida: recusado')
    if checkout.resolve() == RAIZ.resolve() or RAIZ.resolve() in checkout.resolve().parents and \
            (checkout / 'backend' / 'app').exists():
        raise SystemExit('este é o repositório do projeto: recusado')


def esperado_em(arquivos: list[str], esperados: list[str], k: int) -> bool:
    return any(e in arquivos[:k] for e in esperados)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--checkout', type=Path, required=True)
    ap.add_argument('--golden', type=Path, required=True)
    ap.add_argument('--run', action='store_true', help='faz as chamadas reais (sem isto, só mostra o plano)')
    ap.add_argument('--out', type=Path, default=None, help='grava o resumo (JSON) aqui')
    args = ap.parse_args()

    golden = json.loads(args.golden.read_text(encoding='utf-8'))
    perguntas = escolher(golden)
    verificar_checkout(args.checkout)

    from app.config import EnvSettings, load_config
    from app.modules.context_retrieval.domain.model import RetrievalMode
    from app.modules.context_retrieval.adapters.jev import JevSemanticProvider
    from app.modules.context_retrieval.wiring import ambiente_do_provedor, build_service
    import httpx

    cfg = load_config()
    tem_chave = cfg.env.typesafe_api_key is not None            # só o fato; o valor nunca é lido aqui
    plano = {'checkout_sha': SHA, 'questions': [{'id': p['id'], 'group': p['group'], 'category': p['category']}
                                                 for p in perguntas],
             'max_calls': MAX_CHAMADAS, 'max_retries': 0, 'key_configured': tem_chave}
    if not args.run:
        print(json.dumps({**plano, 'REAL_PUBLIC_SMOKE': 'PLAN_ONLY'}, ensure_ascii=False, indent=1))
        return 0
    if not tem_chave:
        print(json.dumps({**plano, 'REAL_PUBLIC_SMOKE': 'NOT_RUN_KEY_MISSING'}, ensure_ascii=False, indent=1))
        return 0

    chamadas: list[dict] = []

    class Contador(httpx.BaseTransport):
        """Conta e limita as requisições REAIS que saem; a 13ª nem parte."""

        def __init__(self) -> None:
            self._real = httpx.HTTPTransport()

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if len(chamadas) >= MAX_CHAMADAS:
                raise httpx.ConnectError('limite de chamadas do smoke')
            t0 = time.perf_counter()
            resposta = self._real.handle_request(request)
            chamadas.append({'status': resposta.status_code, 'ms': round((time.perf_counter() - t0) * 1000, 1)})
            return resposta

    with tempfile.TemporaryDirectory() as dados:
        cfg.file.paths.data_dir = dados
        c = cfg.file.context_retrieval
        c.enabled = True
        c.top_k = 5
        c.semantic.provider = 'jev'
        c.semantic.repository_class = 'public'
        c.semantic.allow_public = True
        c.semantic.max_calls = 2
        c.semantic.max_calls_per_session = MAX_CHAMADAS
        c.semantic.max_cost_usd = 0.05
        c.semantic.max_map_files = 400
        provedor = JevSemanticProvider(model=c.semantic.model, env=ambiente_do_provedor(cfg), transport=Contador())
        servico = build_service(cfg, root=args.checkout, mode=RetrievalMode.HYBRID, provider=provedor)
        linhas: list[dict] = []
        custo = 0.0
        with servico.session():
            for p in perguntas:
                pack = servico.gather(p['question'], scope=('src/poetry/',))
                assert pack is not None
                arquivos = [f.path for f in pack.files]
                gasto = pack.budget['spent']
                custo += gasto['cost_usd']
                linhas.append({
                    'id': p['id'], 'group': p['group'], 'origin': pack.origin,
                    'safeguard': pack.metadata.get('safeguard'), 'fallback_used': pack.metadata['fallback_used'],
                    'fallback_reason': pack.metadata['fallback_reason'],
                    'hit_at_3': esperado_em(arquivos, p['expected_files'], 3),
                    'hit_at_5': esperado_em(arquivos, p['expected_files'], 5),
                    'files': len(arquivos), 'regions': len(pack.regions),
                    'input_tokens': gasto['input_tokens'], 'cost_usd': gasto['cost_usd'],
                    'latency_ms': pack.metadata['latency_ms'],
                    'cache_a': pack.metadata.get('semantic', {}).get('stage_a_cache'),
                    'cache_b': pack.metadata.get('semantic', {}).get('stage_b_cache')})
            # a mesma pergunta de novo: tem de sair do cache, sem chamada nova
            antes = len(chamadas)
            repetida = servico.gather(perguntas[0]['question'], scope=('src/poetry/',))
            assert repetida is not None
        resumo = {**plano, 'REAL_PUBLIC_SMOKE': 'EXECUTED', 'network_calls': len(chamadas),
                  'cache_repeat_new_calls': len(chamadas) - antes,
                  'cache_repeat_a': repetida.metadata.get('semantic', {}).get('stage_a_cache'),
                  'statuses': sorted({c['status'] for c in chamadas}),
                  'cost_usd_total': round(custo, 6), 'cases': linhas}
    texto = json.dumps(resumo, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(texto + '\n', encoding='utf-8')
    print(texto)
    return 0


if __name__ == '__main__':
    sys.exit(main())
