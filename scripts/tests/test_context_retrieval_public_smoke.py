"""Fumaça pública do retrieval (`context-retrieval-public-smoke.py`): seleção de casos, teto de chamadas e o que o resumo grava.

Sem rede, sem backend e sem chave: só as funções puras do script. A chamada real ao Jev não é coberta aqui (é `real`, à parte).
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'context-retrieval-public-smoke.py'
SPEC = importlib.util.spec_from_file_location('context_retrieval_public_smoke', SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def golden() -> dict:
    itens = []
    for prefixo, qtd in (('EXACT_SYMBOL', 3), ('SEMANTIC_BEHAVIOR', 3), ('MIXED_IDENTIFIER_ELSEWHERE', 3)):
        for n in range(qtd):
            itens.append({'id': f'{prefixo[0]}{n}', 'category': prefixo, 'question': 'q', 'expected_files': ['a.py']})
    return {'items': itens}


def pack(semantic: dict | None, fallback: bool = False):
    return SimpleNamespace(
        files=[SimpleNamespace(path='a.py'), SimpleNamespace(path='b.py')], regions=[1, 2, 3], origin='hybrid',
        metadata={'safeguard': True, 'fallback_used': fallback, 'fallback_reason': None, 'latency_ms': 12.5,
                  **({'semantic': semantic} if semantic is not None else {})},
        budget={'spent': {'input_tokens': 100, 'cost_usd': 0.001}})


class SelecaoDeCasos(unittest.TestCase):
    def setUp(self) -> None:
        self.seis = smoke.escolher(golden())

    def test_sem_cases_roda_as_seis_escolhidas_pela_regra(self) -> None:
        self.assertEqual(len(self.seis), 6)
        self.assertEqual(smoke.selecionar(self.seis, None), self.seis)

    def test_subconjunto_mantem_a_ordem_do_golden_e_so_o_pedido(self) -> None:
        ids = [p['id'] for p in self.seis]
        pedido = f'{ids[4]},{ids[0]}'
        self.assertEqual([p['id'] for p in smoke.selecionar(self.seis, pedido)], [ids[0], ids[4]])

    def test_id_fora_das_seis_escolhidas_e_recusado(self) -> None:
        # um item que existe no golden mas NÃO foi escolhido pela regra não pode entrar por --cases
        fora = next(i['id'] for i in golden()['items'] if i['id'] not in {p['id'] for p in self.seis})
        with self.assertRaises(SystemExit):
            smoke.selecionar(self.seis, fora)

    def test_vazio_e_repetido_sao_recusados(self) -> None:
        ids = [p['id'] for p in self.seis]
        for ruim in ('', ' , ', f'{ids[0]},{ids[0]}'):
            with self.assertRaises(SystemExit):
                smoke.selecionar(self.seis, ruim)


class TetoDeChamadas(unittest.TestCase):
    def test_padrao_e_o_teto_travado(self) -> None:
        self.assertEqual(smoke.teto_de_chamadas(None), smoke.MAX_CHAMADAS)
        self.assertEqual(smoke.MAX_CHAMADAS, 12)

    def test_so_baixa_e_nunca_passa_de_doze(self) -> None:
        self.assertEqual(smoke.teto_de_chamadas(4), 4)
        self.assertEqual(smoke.teto_de_chamadas(12), 12)
        for ruim in (0, -1, 13, 100):
            with self.assertRaises(SystemExit):
                smoke.teto_de_chamadas(ruim)


class LinhaDoCaso(unittest.TestCase):
    CASO = {'id': 'H01', 'group': 'EXACT', 'expected_files': ['a.py']}

    def test_grava_por_caso_o_que_o_servico_expoe(self) -> None:
        sem = {'stage_a_cache': 'miss', 'stage_b_cache': 'miss', 'calls': 2, 'files_considered': 400, 'chunks_sent': 9,
               'chunks_dropped_soft': 1, 'stage_b_reason': None, 'fallback_reason': None}
        linha = smoke.linha_do_caso(self.CASO, pack(sem), [{'status': 200, 'ms': 1.0}, {'status': 200, 'ms': 2.0}])
        self.assertEqual((linha['http_calls'], linha['service_calls'], linha['http_statuses']), (2, 2, [200, 200]))
        self.assertEqual((linha['cache_a'], linha['cache_b'], linha['chunks_sent'], linha['files_considered']),
                         ('miss', 'miss', 9, 400))
        self.assertEqual((linha['files'], linha['regions'], linha['origin'], linha['fallback_used']), (2, 3, 'hybrid', False))
        self.assertTrue(linha['hit_at_3'] and linha['hit_at_5'])

    def test_etapa_que_o_servico_nao_diz_fica_nula_e_nao_e_inventada(self) -> None:
        linha = smoke.linha_do_caso(self.CASO, pack(None), [])
        for campo in ('cache_a', 'cache_b', 'service_calls', 'chunks_sent', 'files_considered', 'stage_b_reason'):
            self.assertIsNone(linha[campo], campo)
        self.assertEqual(linha['http_calls'], 0)

    def test_falha_da_etapa_b_aparece_com_o_motivo_e_sem_chunks_enviados(self) -> None:
        sem = {'stage_a_cache': 'miss', 'stage_b_cache': 'skipped', 'calls': 1, 'stage_b_reason': 'budget_exceeded',
               'chunks_sent': 0}
        linha = smoke.linha_do_caso(self.CASO, pack(sem), [{'status': 200, 'ms': 1.0}])
        self.assertEqual((linha['stage_b_reason'], linha['chunks_sent'], linha['http_calls']), ('budget_exceeded', 0, 1))


if __name__ == '__main__':
    unittest.main()
