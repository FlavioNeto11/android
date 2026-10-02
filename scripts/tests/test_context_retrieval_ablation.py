"""Ablação A x A+B (`context-retrieval-ablation.py`): as contas de região que decidem a leitura da medição real.

Sem rede, sem backend e sem chave: só as funções puras do script. A execução paga é `real`, à parte.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'context-retrieval-ablation.py'
SPEC = importlib.util.spec_from_file_location('context_retrieval_ablation', SCRIPT)
abl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(abl)

ESPERADA = [['src/a.py', 100, 109]]            # 10 linhas


class SobrePosicao(unittest.TestCase):
    def test_intervalos_fechados(self) -> None:
        self.assertEqual(abl.sobrepoe((1, 10), (10, 20)), 1)       # compartilham a linha 10
        self.assertEqual(abl.sobrepoe((1, 10), (11, 20)), 0)
        self.assertEqual(abl.sobrepoe((5, 8), (1, 20)), 4)


class RegiaoEsperada(unittest.TestCase):
    def test_janela_que_contem_a_faixa_cobre_tudo(self) -> None:
        r = abl.acerto_de_regiao([('src/a.py', 80, 119)], ESPERADA)
        self.assertEqual(r, {'hit': True, 'fracao_coberta': 1.0})

    def test_cobertura_parcial_e_a_uniao_das_regioes(self) -> None:
        r = abl.acerto_de_regiao([('src/a.py', 90, 104), ('src/a.py', 103, 106)], ESPERADA)
        self.assertEqual(r, {'hit': True, 'fracao_coberta': 0.7})     # linhas 100..106

    def test_outro_arquivo_ou_faixa_distante_nao_acerta(self) -> None:
        self.assertFalse(abl.acerto_de_regiao([('src/b.py', 80, 119)], ESPERADA)['hit'])
        self.assertFalse(abl.acerto_de_regiao([('src/a.py', 1, 40)], ESPERADA)['hit'])
        self.assertEqual(abl.acerto_de_regiao([], ESPERADA), {'hit': False, 'fracao_coberta': 0.0})


class Alcancabilidade(unittest.TestCase):
    def test_regiao_fora_dos_chunks_enviados_e_inalcancavel(self) -> None:
        enviados = [('src/a.py', 1, 40), ('src/a.py', 36, 75)]
        self.assertFalse(abl.alcancavel(enviados, ESPERADA))
        self.assertTrue(abl.alcancavel(enviados + [('src/a.py', 71, 110)], ESPERADA))
        self.assertFalse(abl.alcancavel([('src/b.py', 71, 110)], ESPERADA))     # outro arquivo


class LinhasDoArquivo(unittest.TestCase):
    def test_conta_linhas_e_tolera_arquivo_ausente(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'x.py').write_text('a\nb\nc\n', encoding='utf-8')
            self.assertEqual(abl.linhas_do_arquivo(Path(d), 'x.py'), 3)
            self.assertEqual(abl.linhas_do_arquivo(Path(d), 'nao-existe.py'), 0)


if __name__ == '__main__':
    unittest.main()
