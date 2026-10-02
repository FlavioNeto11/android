"""`plano-100-pacotes.py --contexto`: opt-in, sem efeito algum quando desligado, e UM serviço para o lote (ADR-063).

Nenhum subprocesso, nenhuma IA e nenhum backend real: o serviço de retrieval é injetado. Prova a garantia que importa a
quem não ligou nada (pacotes byte a byte iguais, com ou sem a flag) e que o lote não reconstrói nada por item.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'plano-100-pacotes.py'
SPEC = importlib.util.spec_from_file_location('plano_100_pacotes', SCRIPT)
pacotes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pacotes)

ITENS = [{'id': '0.1', 'titulo': 'Primeiro item', 'fase': 'Fase 0', 'aceite': 'Fecha quando tudo passar',
          'tamanho': 'P', 'corpo': 'Corrigir `fetch_user` em backend/app/x.py', 'achados': ['1'],
          'arquivos': ['backend/app/x.py'], 'modelo': 'sonnet', 'esforco': 'medium'},
         {'id': '0.2', 'titulo': 'Segundo item', 'fase': 'Fase 0', 'aceite': '', 'tamanho': 'M',
          'corpo': 'Outro trabalho', 'achados': [], 'arquivos': [], 'modelo': 'sonnet', 'esforco': 'medium'}]
APENDICE = {'1': {'titulo': 'Achado um', 'corpo': 'Texto do achado.'}}


def gerar(raiz: Path, sugerir=None) -> dict[str, bytes]:
    with patch.object(pacotes, 'RAIZ', raiz):
        pacotes.escrever(ITENS, APENDICE, sugerir)
    pasta = raiz / pacotes.DESTINO
    return {p.name: p.read_bytes() for p in sorted(pasta.iterdir())}


class _Arquivo:
    def __init__(self, path: str) -> None:
        self.path = path


class _Regiao:
    def __init__(self, path: str, a: int, b: int) -> None:
        self.path, self.start_line, self.end_line = path, a, b


class _Pack:
    origin = 'local'
    revision = 'abcdef1234567890'
    files = [_Arquivo('backend/app/y.py')]
    regions = [_Regiao('backend/app/y.py', 3, 9)]


class ServicoFalso:
    """O que o `build_service` devolve; conta o que o gerador faz com ele."""

    def __init__(self, enabled: bool = True, pack=_Pack, falha: bool = False) -> None:
        self.enabled = enabled
        self.pack = pack
        self.falha = falha
        self.consultas: list[str] = []
        self.sessoes = 0

    def gather(self, pergunta: str):
        if self.falha:
            raise RuntimeError('indice quebrado')
        self.consultas.append(pergunta)
        return self.pack if self.enabled else None

    def session(self):
        servico = self

        class _Sessao:
            def __enter__(self):
                servico.sessoes += 1

            def __exit__(self, *a):
                return False
        return _Sessao()


class PacotesComContextoTest(unittest.TestCase):
    def test_desligado_ou_sem_sugestao_a_saida_e_identica(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as d:
            sem_flag = gerar(Path(a))
            desligado = gerar(Path(b), pacotes.SugestorDeContexto(servico=ServicoFalso(enabled=False)))
            vazio = gerar(Path(c), lambda item: [])
            sem_pack = gerar(Path(d), pacotes.SugestorDeContexto(servico=ServicoFalso(pack=None)))
        self.assertEqual(sem_flag, desligado)
        self.assertEqual(sem_flag, vazio)
        self.assertEqual(sem_flag, sem_pack)

    def test_ligado_acrescenta_so_a_secao_de_sugestoes(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            base = gerar(Path(a))
            com = gerar(Path(b), pacotes.SugestorDeContexto(servico=ServicoFalso()))
        texto = com['0.1.md'].decode('utf-8')
        self.assertIn('## Sugestões de contexto', texto)
        self.assertIn('`backend/app/y.py:3-9`', texto)
        antes, depois = texto.split('## Sugestões de contexto')
        resto = depois.split('## Achados, na íntegra')[1]
        self.assertEqual(antes + '## Achados, na íntegra' + resto, base['0.1.md'].decode('utf-8'))

    def test_um_servico_para_o_lote_inteiro(self) -> None:
        falso = ServicoFalso()
        montagens = []

        def fabrica():
            montagens.append(1)
            return falso
        sugestor = pacotes.SugestorDeContexto(fabrica=fabrica)
        with tempfile.TemporaryDirectory() as a, sugestor.sessao():
            gerar(Path(a), sugestor)
        self.assertEqual(len(montagens), 1)             # montado uma vez, não uma por item
        self.assertEqual(falso.sessoes, 1)              # revisão congelada durante o lote
        self.assertEqual(len(falso.consultas), len(ITENS))
        self.assertEqual(len(sugestor.tempos_ms), len(ITENS))

    def test_a_pergunta_e_titulo_mais_corpo_com_teto(self) -> None:
        falso = ServicoFalso()
        pacotes.SugestorDeContexto(servico=falso)({**ITENS[0], 'corpo': 'x' * 2000})
        self.assertTrue(falso.consultas[0].startswith('Primeiro item. xxx'))
        self.assertEqual(len(falso.consultas[0]), 600)

    def test_backend_que_nao_carrega_nao_derruba_a_geracao(self) -> None:
        def quebra():
            raise ImportError('sem pydantic')
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            base = gerar(Path(a))
            with patch('sys.stderr'):
                quebrado = gerar(Path(b), pacotes.SugestorDeContexto(fabrica=quebra))
        self.assertEqual(base, quebrado)

    def test_consulta_que_falha_nao_derruba_a_geracao(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            base = gerar(Path(a))
            with patch('sys.stderr'):
                quebrado = gerar(Path(b), pacotes.SugestorDeContexto(servico=ServicoFalso(falha=True)))
        self.assertEqual(base, quebrado)

    def test_o_modo_explicito_chega_ao_servico(self) -> None:
        chamadas = []

        def fabrica_real_falsa(self_):
            chamadas.append(self_.modo)
            return ServicoFalso(enabled=False)
        with patch.object(pacotes.SugestorDeContexto, '_fabrica_real', fabrica_real_falsa):
            self.assertEqual(pacotes.SugestorDeContexto('shadow')(ITENS[0]), [])
        self.assertEqual(chamadas, ['shadow'])


if __name__ == '__main__':
    unittest.main()
