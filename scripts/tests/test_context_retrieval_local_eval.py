"""`context-retrieval-local-eval.py`: o conjunto de avaliação, as métricas e o anti-vazamento do índice do pai.

Sem rede e sem IA: git real numa pasta temporária e os retrievers locais de verdade (léxico e BM25), em repositório minúsculo.
"""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'context-retrieval-local-eval.py'
SPEC = importlib.util.spec_from_file_location('context_retrieval_local_eval', SCRIPT)
ev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ev)


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *args],
                       cwd=repo, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def escrever(repo: Path, nome: str, texto: str) -> None:
    caminho = repo / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding='utf-8')


def commitar(repo: Path, mensagem: str) -> str:
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', mensagem)
    return git(repo, 'rev-parse', 'HEAD')


class Classificacao(unittest.TestCase):
    def test_codigo_exclui_docs_testes_changelog_e_claude(self) -> None:
        for ok in ('backend/app/x.py', 'frontend/src/a.tsx', 'scripts/run.ps1', 'backend/migrations/001_a.sql'):
            self.assertTrue(ev.eh_codigo(ok), ok)
        for nao in ('docs/a.md', 'CHANGELOG.md', '.claude/x.py', 'backend/tests/test_a.py', 'scripts/tests/test_a.py',
                    'frontend/src/a.test.tsx', 'backend/conftest.py', 'README.md', 'config/a.yaml', 'frontend/src/__tests__/a.ts'):
            self.assertFalse(ev.eh_codigo(nao), nao)

    def test_mensagem_perde_prefixo_skip_ci_e_trailers(self) -> None:
        bruta = ('fix(devices): sessao morta e recriada [skip ci]\n\nCorpo com `simbolo`.\n\n'
                 'Co-Authored-By: Alguem <a@b.c>\n🤖 Generated with [Claude Code](x)\n')
        self.assertEqual(ev.limpar_mensagem(bruta), ('sessao morta e recriada', 'Corpo com `simbolo`.'))
        self.assertEqual(ev.limpar_mensagem('feat!: so o assunto')[0], 'so o assunto')


class Metricas(unittest.TestCase):
    def test_posicoes(self) -> None:
        m = ev.metricas(['x', 'y', 'a', 'b'], {'a'})
        self.assertEqual((m['hit3'], m['hit5'], m['rr'], m['recall5']), (True, True, 1 / 3, 1.0))
        m = ev.metricas(['x', 'y', 'z', 'w', 'a'], {'a', 'q'})
        self.assertEqual((m['hit3'], m['hit5'], m['rr'], m['recall5']), (False, True, 0.2, 0.5))

    def test_fora_dos_dez_e_ranking_vazio_nao_contam(self) -> None:
        longo = [f'f{i}' for i in range(10)] + ['a']
        self.assertEqual(ev.metricas(longo, {'a'}), {'hit3': False, 'hit5': False, 'rr': 0.0, 'recall5': 0.0})
        self.assertEqual(ev.metricas([], {'a'})['rr'], 0.0)

    def test_agregar_faz_a_media_por_modo_e_variante(self) -> None:
        def r(h3, h5, rr, rc):
            return {'hit3': h3, 'hit5': h5, 'rr': rr, 'recall5': rc, 'ms': 10.0, 'erro': None}
        linhas = [{'resultados': {'completa': {'bm25': r(True, True, 1.0, 1.0)}}},
                  {'resultados': {'completa': {'bm25': r(False, True, 0.25, 0.5)}}}]
        a = ev.agregar(linhas)['completa']['bm25']
        self.assertEqual((a['n'], a['hit3'], a['hit5'], a['mrr10'], a['recall5']), (2, 0.5, 1.0, 0.625, 0.75))


class ConjuntoEAvaliacao(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        escrever(self.repo, 'app/billing.py',
                 'def calcular_imposto(fatura):\n    """imposto da fatura de cobranca"""\n    return fatura * 0.1\n')
        escrever(self.repo, 'app/auth.py', 'def entrar(usuario):\n    """login da sessao do usuario"""\n    return usuario\n')
        escrever(self.repo, 'docs/guia.md', 'guia\n')
        self.base = commitar(self.repo, 'chore: base')
        # entra no conjunto: modifica billing.py; a mensagem cita um termo que SÓ existe depois do commit
        escrever(self.repo, 'app/billing.py',
                 'def calcular_imposto(fatura):\n    """imposto da fatura de cobranca"""\n    return fatura * 0.1\n\n'
                 'def zzqunico_arredondar(valor):\n    return round(valor, 2)\n')
        escrever(self.repo, 'CHANGELOG.md', 'mudou\n')
        self.alvo = commitar(self.repo, 'fix(billing): corrige o imposto da fatura de cobranca com zzqunico_arredondar [skip ci]\n\n'
                                        'O calculo do imposto na fatura arredondava errado.\n\nCo-Authored-By: X <x@y.z>')
        escrever(self.repo, 'docs/guia.md', 'guia novo\n')
        commitar(self.repo, 'docs: so documentacao, fora do conjunto por nao ter codigo no gabarito')
        escrever(self.repo, 'app/novo.py', 'def novo():\n    return 1\n')
        commitar(self.repo, 'feat(novo): arquivo novo, sem gabarito existente no pai, nao entra no conjunto')
        escrever(self.repo, 'app/auth.py', 'def entrar(usuario):\n    """login da sessao do usuario"""\n    return usuario\n# +\n')
        self.auth = commitar(self.repo, 'fix(auth): ajusta o login da sessao do usuario na entrada do sistema')

    def test_conjunto_so_tem_feat_fix_com_gabarito_de_codigo_existente(self) -> None:
        cs = ev.escolher_commits(self.repo, 'HEAD', 10)
        self.assertEqual([c['sha'] for c in cs], [self.auth, self.alvo])      # do mais novo ao mais antigo
        alvo = cs[1]
        self.assertEqual(alvo['gabarito'], ['app/billing.py'])                # CHANGELOG fica de fora
        self.assertEqual(alvo['pai'], self.base)
        self.assertNotIn('skip ci', alvo['pergunta'])
        self.assertNotIn('Co-Authored', alvo['pergunta'])
        self.assertTrue(alvo['pergunta'].startswith('corrige o imposto'))
        self.assertEqual(ev.escolher_commits(self.repo, 'HEAD', 1)[0]['sha'], self.auth)   # --n corta

    def test_avaliacao_usa_o_indice_do_pai_e_nao_vaza_a_resposta(self) -> None:
        cs = [c for c in ev.escolher_commits(self.repo, 'HEAD', 10) if c['sha'] == self.alvo]
        scratch = Path(self._tmp.name) / 'pai'
        linhas = ev.avaliar(self.repo, cs, scratch)
        self.assertFalse(scratch.exists(), 'o worktree descartável tem de sumir')
        self.assertEqual(git(self.repo, 'worktree', 'list').count('\n'), 0)
        r = linhas[0]['resultados']
        # BM25 acha billing.py pelas palavras que JÁ existiam (imposto, fatura, cobranca)
        self.assertTrue(r['completa']['bm25']['hit5'] and r['completa']['hybrid_local']['hit5'])
        # o termo zzqunico_arredondar só existe DEPOIS do commit: o léxico no índice do pai não pode achá-lo
        self.assertEqual(r['assunto']['lexical']['top3'].count('app/billing.py') + r['completa']['lexical']['top3'].count('app/billing.py'), 0)
        for variante in ev.VARIANTES:
            for modo in ev.MODOS:
                self.assertIsNone(r[variante][modo]['erro'], (variante, modo))
        self.assertEqual(linhas[0]['arquivos_no_indice'], 3)                   # billing, auth, guia: o estado do pai, sem novo.py

    def test_modo_consumidor_roda_so_o_hibrido_local_sem_e_com_escopo_de_codigo(self) -> None:
        cs = [c for c in ev.escolher_commits(self.repo, 'HEAD', 10) if c['sha'] == self.alvo]
        linhas = ev.avaliar(self.repo, cs, Path(self._tmp.name) / 'pai2', ev.MODOS_DO_CONSUMIDOR, ev.VARIANTES_DO_CONSUMIDOR,
                            ev.LIMITE_DA_PERGUNTA_DO_CONSUMIDOR)
        r = linhas[0]['resultados']
        self.assertEqual({v: set(m) for v, m in r.items()}, {'completa': {'hybrid_local'}, 'codigo': {'hybrid_local'}})
        self.assertTrue(r['completa']['hybrid_local']['hit5'])        # sem escopo acha billing.py
        self.assertFalse(r['codigo']['hybrid_local']['hit5'])         # o repositório de teste não tem backend/app/: o escopo é aplicado
        self.assertEqual(r['codigo']['hybrid_local']['top3'], [])
        a = ev.agregar(linhas, ev.MODOS_DO_CONSUMIDOR, ev.VARIANTES_DO_CONSUMIDOR)
        self.assertEqual(set(a), {'completa', 'codigo'})
        self.assertEqual(ev.tabela(a, ev.MODOS_DO_CONSUMIDOR, ev.VARIANTES_DO_CONSUMIDOR).count('hybrid_local'), 2)

    def test_falha_do_retriever_conta_como_erro_e_nunca_como_acerto(self) -> None:
        m = ev.metricas([], {'app/billing.py'})
        self.assertFalse(m['hit3'] or m['hit5'])


if __name__ == '__main__':
    unittest.main()
