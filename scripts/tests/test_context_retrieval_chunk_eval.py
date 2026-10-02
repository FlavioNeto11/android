"""Medição grátis de seleção de chunks (`context-retrieval-chunk-eval.py`): a estratégia medida é a MESMA que o produto usa.

Sem rede e sem chave. O que se guarda aqui é que o número do doc (rodizio_lexical_1o_dobro) descreve o chunker de produção, e que as
contas de região da medição estão certas.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'backend'))
SPEC = importlib.util.spec_from_file_location('context_retrieval_chunk_eval', RAIZ / 'scripts' / 'context-retrieval-chunk-eval.py')
medicao = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(medicao)

from app.modules.context_retrieval.infrastructure.chunker import Chunker, termos_da_pergunta  # noqa: E402
from app.modules.context_retrieval.infrastructure.workspace import Workspace  # noqa: E402


class Equivalencia(unittest.TestCase):
    def test_termos_iguais_aos_do_produto(self) -> None:
        for q in ('Where is `LazyWheelOverHTTP` defined?', 'how does solve_version pick the best candidate_file'):
            self.assertEqual(medicao.termos(q), termos_da_pergunta(q))

    def test_a_estrategia_medida_escolhe_as_mesmas_janelas_que_o_chunker_de_producao(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d)
            for nome, n in (('a.py', 300), ('b.py', 250), ('c.py', 120)):
                linhas = [f'linha{i}' for i in range(1, n + 1)]
                linhas[99] = 'def resolver_versao(): pass   # solve version'
                (raiz / nome).write_text('\n'.join(linhas) + '\n', encoding='utf-8')
            chunker = Chunker(Workspace(raiz))
            pergunta = 'where is resolver_versao solve version'
            cands = ['a.py', 'b.py', 'c.py']
            janelas = {c: chunker.chunks_for([c], max_chunks=10**6, max_bytes=10**9) for c in cands}
            medidas = medicao.selecionar('rodizio_lexical_1o_dobro', cands, janelas, medicao.termos(pergunta), 8, 48_000)
            producao = chunker.chunks_for(cands, max_chunks=8, max_bytes=48_000, query=pergunta)
            self.assertEqual([(w.path, w.start_line) for w in medidas], [(w.path, w.start_line) for w in producao])


class ContasDeRegiao(unittest.TestCase):
    def test_sobreposicao_e_fracao_coberta(self) -> None:
        self.assertEqual(medicao.sobrepoe((1, 10), (10, 20)), 1)
        W = type('W', (), {})
        a = W(); a.path, a.start_line, a.end_line = 'f.py', 90, 104
        b = W(); b.path, b.start_line, b.end_line = 'f.py', 103, 106
        hit, frac = medicao.acerto([a, b], [['f.py', 100, 109]])
        self.assertTrue(hit)
        self.assertAlmostEqual(frac, 0.7)
        self.assertEqual(medicao.acerto([a], [['g.py', 100, 109]]), (False, 0.0))


if __name__ == '__main__':
    unittest.main()
