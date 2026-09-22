#!/usr/bin/env python3
"""Quanto custou cada agente do workflow, lido dos transcritos locais. Determinístico: nenhuma chamada de IA.

Existe porque a conta do plano-100 não é intuitiva. Na primeira rodada real, um item de porte médio custou
US$ 8,29 — e **78% disso foi cache lido**: 32,3 milhões de tokens relidos ao longo dos turnos do agente, contra
65 mil tokens de saída. Ou seja, o que encarece não é o que o agente escreve, é quantas vezes ele relê o que já
tem. Comando longo, saída de teste grande e arquivo reaberto viram dinheiro de um jeito que não aparece na
sensação de "foi rápido".

    python scripts/plano-100-custo.py            # todas as rodadas desta sessão
    python scripts/plano-100-custo.py --total    # só a linha final
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path
import sys

RAIZ = Path(__file__).resolve().parents[1]
#: US$ por milhão de tokens: [entrada, cache lido, cache gravado, saída]. Espelha `ai.prices` do config.yaml —
#: se um preço mudar lá, mude aqui; são as duas contas do projeto e elas têm de concordar.
PRECOS = {
    'opus': (5.0, 0.5, 6.25, 25.0),
    'sonnet': (2.0, 0.2, 2.5, 10.0),
    'haiku': (1.0, 0.1, 1.25, 5.0),
}
CAMPOS = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')


def familia(modelo: str | None) -> str:
    for nome in PRECOS:
        if modelo and nome in modelo:
            return nome
    return 'sonnet'


def transcritos() -> list[Path]:
    """Todas as sessões deste projeto, não só a última: o que interessa é o gasto do plano, e ele atravessa sessões.

    O nome do diretório é o caminho do repositório com `:` e separadores virando `-` — `C:\\git\\android` vira
    `C--git-android`.
    """
    base = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'
    projeto = str(RAIZ).replace(':', '-').replace('\\', '-').replace('/', '-')
    sessoes = sorted((base / projeto).glob('*/subagents/workflows'), key=lambda p: p.stat().st_mtime)
    if not sessoes:
        raise SystemExit(f'Nenhum transcrito de workflow em {base / projeto}.')
    return sessoes


def agentes(raizes: list[Path]):
    for run in sorted((r for raiz in raizes for r in raiz.glob('wf_*')), key=lambda p: p.stat().st_mtime):
        for meta in sorted(run.glob('agent-*.meta.json')):
            uso, modelo = collections.Counter(), None
            diario = meta.with_name(meta.name.replace('.meta.json', '.jsonl'))
            if not diario.is_file():
                continue
            for linha in diario.read_text(encoding='utf-8', errors='replace').splitlines():
                try:
                    dado = json.loads(linha)
                except ValueError:
                    continue
                mensagem = dado.get('message') if isinstance(dado.get('message'), dict) else {}
                consumo = mensagem.get('usage')
                if consumo:
                    for campo in CAMPOS:
                        uso[campo] += consumo.get(campo, 0) or 0
                    modelo = mensagem.get('model') or modelo
            if not uso:
                continue
            cabecalho = json.loads(meta.read_text(encoding='utf-8'))
            # `description` é onde o rótulo do agente vai parar; `model` do meta é mais confiável que o da
            # mensagem, porque é o que foi pedido e não o que respondeu.
            rotulo = cabecalho.get('description') or cabecalho.get('label') or meta.stem
            yield run.name, rotulo, familia(cabecalho.get('model') or modelo), uso


def custo(modelo: str, uso: collections.Counter) -> float:
    return sum(uso[campo] * preco for campo, preco in zip(CAMPOS, PRECOS[modelo])) / 1e6


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--total', action='store_true', help='Só a linha final.')
    parser.add_argument('--tudo', action='store_true',
                        help='Incluir workflows que não são do plano-100 (a auditoria, por exemplo).')
    args = parser.parse_args(argv)
    total, geral, rodada_atual = 0.0, collections.Counter(), None
    fora = collections.Counter()
    fora_usd = 0.0
    for rodada, rotulo, modelo, uso in agentes(transcritos()):
        # Só os agentes deste plano. O diretório guarda o histórico do projeto inteiro, e somar a auditoria aqui
        # daria um número que não responde à pergunta "quanto está custando executar o plano".
        if not args.tudo and not rotulo.startswith(('implementar:', 'conferir:')):
            fora.update(uso)
            fora_usd += custo(modelo, uso)
            continue
        total += custo(modelo, uso)
        geral.update(uso)
        if args.total:
            continue
        if rodada != rodada_atual:
            rodada_atual = rodada
            print(f'--- {rodada}')
        print(f'  {rotulo:<36} {modelo:<7} cache-lido {uso["cache_read_input_tokens"]:>11,} '
              f'saida {uso["output_tokens"]:>7,} | US$ {custo(modelo, uso):>6.2f}')
    lido = geral['cache_read_input_tokens']
    soma = sum(geral[campo] for campo in CAMPOS) or 1
    print(f'\nPlano-100: US$ {total:.2f} · {soma:,} tokens, dos quais {lido:,} ({lido * 100 // soma}%) '
          f'são cache lido.')
    if fora_usd:
        print(f'Outros workflows deste projeto (auditoria etc.), fora da conta acima: US$ {fora_usd:.2f} '
              f'em {sum(fora[campo] for campo in CAMPOS):,} tokens. Use --tudo para detalhar.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
