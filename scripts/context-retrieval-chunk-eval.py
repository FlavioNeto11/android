#!/usr/bin/env python3
"""Medição GRÁTIS (sem rede, sem chave) de como a etapa B escolhe chunks: antes e depois do rodízio (J13, ADR-063).

Contexto: no J12 a única falha de região (H25) foi do chunker: ele corta janelas de 40 linhas a partir do INÍCIO de cada arquivo, na ordem
dos candidatos, até o teto de chunks; o primeiro candidato esgota o teto e o arquivo esperado, que veio depois, não recebe chunk.
Esta medição compara, nas 30 perguntas do golden do `python-poetry/poetry` (as mesmas 6 do smoke estão entre elas), estratégias de seleção:

- `atual`        janelas do início, arquivo por arquivo (o chunker de hoje);
- `rodizio`      uma janela de cada candidato por volta (janelas do início), até o teto;
- `lexical`      arquivo por arquivo, mas as janelas de cada arquivo ordenadas por termos da pergunta que elas contêm;
- `rodizio_lexical` uma janela por candidato por volta, sempre a de maior pontuação ainda não usada (a candidata da vez).

Métrica (a mesma de região do J12): a região esperada do gabarito é ALCANÇÁVEL se alguma janela enviada a sobrepõe; e a fração das linhas
esperadas cobertas pela união das janelas. Candidatos: a linha de base local (BM25/léxico, grátis) com o arquivo esperado (a) onde o local
o pôs, (b) forçado na posição 1 e (c) forçado na posição 3 — a A real do Jev pôs o esperado em 1º em 5 de 6 e em 2º ou 3º no 6º caso.
Também mede o tamanho do payload (bytes e tokens estimados por `len//4`) porque o teto de tokens por pedido é o que limita a B.

    backend/.venv/Scripts/python.exe scripts/context-retrieval-chunk-eval.py --checkout <poetry no SHA fixado> --golden <golden.json>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'backend'))

ESCOPO = ('src/poetry/',)
STOP = {'the', 'and', 'for', 'that', 'this', 'with', 'from', 'what', 'where', 'which', 'when', 'how', 'does', 'are', 'was',
        'into', 'not', 'its', 'has', 'have', 'can', 'any', 'all', 'one', 'out', 'use', 'used', 'uses', 'file', 'function'}


def termos(texto: str) -> list[str]:
    """Identificadores e palavras da pergunta, em minúsculas, quebrando snake_case e CamelCase, sem palavras vazias."""
    saida: list[str] = []
    for tok in re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', texto):
        partes = [p for p in re.split(r'_+|(?<=[a-z0-9])(?=[A-Z])', tok) if len(p) >= 3]
        saida += [p.lower() for p in partes] + ([tok.lower()] if len(partes) > 1 else [])
    return [t for t in dict.fromkeys(saida) if t not in STOP]


def pontua(janela_texto: str, consulta: list[str]) -> int:
    minusculo = janela_texto.lower()
    return sum(min(minusculo.count(t), 3) for t in consulta)


def sobrepoe(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)


def selecionar(estrategia: str, candidatos: list[str], janelas: dict[str, list], consulta: list[str], max_chunks: int,
               max_bytes: int) -> list:
    """Devolve as janelas escolhidas respeitando os dois tetos duros (quantidade e bytes), como o chunker faz."""
    if estrategia in ('lexical', 'rodizio_lexical', 'rodizio_lexical_1o_dobro'):
        ordenadas = {c: sorted(janelas[c], key=lambda w: (-pontua(w.text, consulta), w.start_line)) for c in candidatos}
    else:
        ordenadas = {c: list(janelas[c]) for c in candidatos}
    if estrategia in ('atual', 'lexical'):
        fila = [w for c in candidatos for w in ordenadas[c]]
    else:                                                    # rodízio: uma por candidato, volta a volta
        fila = []
        # `_1o_dobro`: o 1º candidato (o que a A pôs em primeiro) leva duas janelas por volta e os demais uma
        pesos = [2] + [1] * (len(candidatos) - 1) if estrategia == 'rodizio_lexical_1o_dobro' else [1] * len(candidatos)
        proximo = {c: 0 for c in candidatos}
        while any(proximo[c] < len(ordenadas[c]) for c in candidatos):
            for c, peso in zip(candidatos, pesos):
                for _ in range(peso):
                    if proximo[c] < len(ordenadas[c]):
                        fila.append(ordenadas[c][proximo[c]])
                        proximo[c] += 1
    saida, usados = [], 0
    for w in fila:
        tam = len(w.text.encode('utf-8'))
        if len(saida) >= max_chunks or usados + tam > max_bytes:
            break                                           # o chunker de produção também para ANTES de estourar o teto
        saida.append(w)
        usados += tam
    return saida


def acerto(escolhidas: list, esperadas: list[list]) -> tuple[bool, float]:
    esperado = sum(e[2] - e[1] + 1 for e in esperadas)
    coberto = 0
    for caminho, ini, fim in esperadas:
        linhas: set[int] = set()
        for w in escolhidas:
            if w.path == caminho:
                linhas |= set(range(max(ini, w.start_line), min(fim, w.end_line) + 1))
        coberto += len(linhas)
    return coberto > 0, (coberto / esperado if esperado else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--checkout', type=Path, required=True)
    ap.add_argument('--golden', type=Path, required=True)
    ap.add_argument('--max-chunks', type=int, default=16)
    ap.add_argument('--max-bytes', type=int, default=48_000)
    ap.add_argument('--candidatos', type=int, default=5)
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()

    from app.config import load_config
    from app.modules.context_retrieval.domain.model import RetrievalMode
    from app.modules.context_retrieval.infrastructure.chunker import Chunker
    from app.modules.context_retrieval.infrastructure.workspace import Workspace
    from app.modules.context_retrieval.wiring import build_service
    import tempfile

    golden = json.loads(args.golden.read_text(encoding='utf-8'))['items']
    cfg = load_config()
    estrategias = ('atual', 'rodizio', 'lexical', 'rodizio_lexical', 'rodizio_lexical_1o_dobro')
    with tempfile.TemporaryDirectory() as dados:
        cfg.file.paths.data_dir = dados
        cfg.file.context_retrieval.enabled = True
        cfg.file.context_retrieval.top_k = args.candidatos
        local = build_service(cfg, root=args.checkout, mode=RetrievalMode.LOCAL_ONLY)
        chunker = Chunker(Workspace(args.checkout.resolve()))
        cache: dict[str, list] = {}

        def janelas_de(caminho: str) -> list:
            if caminho not in cache:
                cache[caminho] = chunker.chunks_for([caminho], max_chunks=10**6, max_bytes=10**9)
            return cache[caminho]

        linhas: list[dict] = []
        for item in golden:
            esperado = item['expected_files'][0]
            consulta = termos(item['question'])
            pack = local.gather(item['question'], scope=ESCOPO)
            local_top = [f.path for f in pack.files][:args.candidatos]
            outros = [p for p in local_top if p != esperado]
            regimes = {
                'local': local_top,
                'esperado_em_1o': [esperado] + outros[:args.candidatos - 1],
                'esperado_em_2o': (outros[:1] + [esperado] + outros[1:])[:args.candidatos],
                'esperado_em_3o': (outros[:2] + [esperado] + outros[2:])[:args.candidatos],
            }
            for regime, cands in regimes.items():
                if esperado not in cands:
                    continue                                   # a A nem trouxe o arquivo: nenhuma seleção de chunk resolve
                janelas = {c: janelas_de(c) for c in cands}
                for est in estrategias:
                    esc = selecionar(est, cands, janelas, consulta, args.max_chunks, args.max_bytes)
                    hit, frac = acerto(esc, item['expected_regions'])
                    payload = sum(len(w.text.encode('utf-8')) for w in esc)
                    linhas.append({'id': item['id'], 'grupo': item['category'], 'regime': regime, 'estrategia': est,
                                   'alcancavel': hit, 'fracao': round(frac, 3), 'chunks': len(esc), 'bytes': payload,
                                   'tokens_estimados': payload // 4})

    resumo: dict[str, dict] = {}
    for regime in ('local', 'esperado_em_1o', 'esperado_em_2o', 'esperado_em_3o'):
        for est in estrategias:
            sel = [l for l in linhas if l['regime'] == regime and l['estrategia'] == est]
            if not sel:
                continue
            resumo[f'{regime}/{est}'] = {
                'perguntas': len(sel), 'alcancavel': sum(l['alcancavel'] for l in sel),
                'fracao_media': round(sum(l['fracao'] for l in sel) / len(sel), 3),
                'tokens_estimados_max': max(l['tokens_estimados'] for l in sel),
                'tokens_estimados_medio': round(sum(l['tokens_estimados'] for l in sel) / len(sel))}
    saida = {'max_chunks': args.max_chunks, 'max_bytes': args.max_bytes, 'candidatos': args.candidatos, 'resumo': resumo, 'linhas': linhas}
    texto = json.dumps(saida, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(texto + '\n', encoding='utf-8')
    for k, v in resumo.items():
        print(f'{k:40} {v["alcancavel"]:>2}/{v["perguntas"]:<2} fração {v["fracao_media"]:.2f}  tokens est. médio {v["tokens_estimados_medio"]:>5} máx {v["tokens_estimados_max"]:>5}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
