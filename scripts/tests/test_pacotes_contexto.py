"""`plano-100-pacotes.py --contexto`: opt-in, sem efeito algum quando desligado (ADR-063).

Nenhum subprocesso real e nenhuma IA: a chamada à CLI do retrieval é injetada. O que se prova é a garantia que importa
para quem não ligou nada: os pacotes saem byte a byte iguais, com ou sem a flag, enquanto o retrieval está desligado.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'plano-100-pacotes.py'
SPEC = importlib.util.spec_from_file_location('plano_100_pacotes', SCRIPT)
pacotes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pacotes)

ITENS = [{'id': '0.1', 'titulo': 'Primeiro item', 'fase': 'Fase 0', 'aceite': 'Fecha quando tudo passar',
          'tamanho': 'P', 'corpo': 'Corrigir `fetch_user` em backend/app/x.py', 'achados': ['1'],
          'arquivos': ['backend/app/x.py'], 'modelo': 'sonnet', 'esforco': 'medium'}]
APENDICE = {'1': {'titulo': 'Achado um', 'corpo': 'Texto do achado.'}}


def gerar(raiz: Path, sugerir=None) -> dict[str, bytes]:
    with patch.object(pacotes, 'RAIZ', raiz):
        pacotes.escrever(ITENS, APENDICE, sugerir)
    pasta = raiz / pacotes.DESTINO
    return {p.name: p.read_bytes() for p in sorted(pasta.iterdir())}


def resposta(saida: dict):
    def executar(*_a, **_k):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(saida), stderr='')
    return executar


class PacotesComContextoTest(unittest.TestCase):
    def test_sem_a_flag_e_com_o_retrieval_desligado_a_saida_e_identica(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as c:
            sem_flag = gerar(Path(a))
            desligado = gerar(Path(b), pacotes.sugerir_contexto_por_retrieval(executar=resposta({'enabled': False})))
            vazio = gerar(Path(c), lambda item: [])
        self.assertEqual(sem_flag, desligado)
        self.assertEqual(sem_flag, vazio)

    def test_ligado_acrescenta_so_a_secao_de_sugestoes(self) -> None:
        saida = {'origin': 'local', 'revision': 'abcdef1234567890', 'files': [{'path': 'backend/app/y.py'}],
                 'regions': [{'path': 'backend/app/y.py', 'start_line': 3, 'end_line': 9}]}
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            base = gerar(Path(a))
            com = gerar(Path(b), pacotes.sugerir_contexto_por_retrieval('local_only', executar=resposta(saida)))
        texto = com['0.1.md'].decode('utf-8')
        self.assertIn('## Sugestões de contexto', texto)
        self.assertIn('`backend/app/y.py:3-9`', texto)
        self.assertEqual(com['indice.json'].decode().count('"0.1"'), base['indice.json'].decode().count('"0.1"'))
        sem_secao = texto.split('## Sugestões de contexto')[0] + '## Achados, na íntegra' + \
            texto.split('## Achados, na íntegra')[1]
        self.assertTrue(sem_secao.startswith(base['0.1.md'].decode('utf-8')[:200]))

    def test_cli_do_retrieval_com_defeito_nao_derruba_a_geracao(self) -> None:
        def quebra(*_a, **_k):
            raise OSError('sem python')
        for executor in (quebra, lambda *a, **k: subprocess.CompletedProcess([], 1, stdout='nao e json', stderr='')):
            with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
                base = gerar(Path(a))
                with patch('sys.stderr'):
                    quebrado = gerar(Path(b), pacotes.sugerir_contexto_por_retrieval(executar=executor))
            self.assertEqual(base, quebrado)

    def test_o_modo_explicito_vai_para_a_cli(self) -> None:
        chamadas = []

        def executar(comando, **_k):
            chamadas.append(comando)
            return subprocess.CompletedProcess([], 0, stdout=json.dumps({'enabled': False}), stderr='')
        pacotes.sugerir_contexto_por_retrieval('shadow', executar=executar)(ITENS[0])
        self.assertIn('--mode', chamadas[0])
        self.assertEqual(chamadas[0][chamadas[0].index('--mode') + 1], 'shadow')
        self.assertIn('app.modules.context_retrieval.presentation.cli', chamadas[0])


if __name__ == '__main__':
    unittest.main()
