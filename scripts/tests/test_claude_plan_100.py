"""Testes do livro-razão do plano-100 (scripts/claude-plan-100.py) contra um mini-repo em diretório temporário.
Nenhuma IA, nenhum subprocesso: desde 22/09 este script só registra o que o workflow da sessão devolveu — o
executor antigo (`claude -p` em subprocesso) foi aposentado, e o teste velho que o exercitava não se aplica mais.
"""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'claude-plan-100.py'
SPEC = importlib.util.spec_from_file_location('claude_plan_100', SCRIPT)
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


PLANO = (
    '# Plano\n\n'
    '| Item | O que | Achados | Tam. |\n'
    '|---|---|---|---|\n'
    '| 0.1 | Primeiro item | #1 | P |\n'
    '| 0.2 | Segundo item | #2 | M |\n'
)


def item(item_id, status='implemented', proof='real', evidence='evidência de teste', blocker=''):
    row = {'id': item_id, 'status': status, 'proof': proof}
    if evidence:
        row['evidence'] = evidence
    if blocker:
        row['blocker'] = blocker
    return row


def grupo(items, solicitados=None, modelo='sonnet', esforco='medium', conferencia=None):
    return {'grupo': 'bloco-1:' + '+'.join(i['id'] for i in items), 'modelo': modelo, 'esforco': esforco,
            'items': items, 'solicitados': solicitados or [i['id'] for i in items],
            'conferencia': conferencia or {'itens': []}}


def resultado(*grupos):
    return {'resultados': list(grupos)}


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write(self.root / 'docs/plano-100.md', PLANO)
        write(self.root / '.claude/plano-100.json', json.dumps({
            'plan': 'docs/plano-100.md',
            'batches': [{'id': 'bloco-1', 'items': ['0.1', '0.2']}]}))
        write(self.root / '.claude/plano-100/pacotes/indice.json', json.dumps({
            '0.1': {'modelo': 'sonnet'}, '0.2': {'modelo': 'opus'}}))
        self.raiz_patch = patch.object(plan, 'RAIZ', self.root)
        self.raiz_patch.start()
        self.addCleanup(self.raiz_patch.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            codigo = plan.main(list(args))
        return codigo, out.getvalue(), err.getvalue()

    def write_result(self, payload) -> Path:
        caminho = self.root / 'resultado.json'
        caminho.write_text(json.dumps(payload), encoding='utf-8')
        return caminho

    def state(self):
        return json.loads((self.root / plan.ESTADO).read_text(encoding='utf-8'))

    # --- carregar(): o mapa tem de casar com o plano e com o índice de pacotes ---

    def test_carregar_recusa_mapa_que_nao_casa_com_o_plano(self):
        write(self.root / '.claude/plano-100.json', json.dumps({
            'plan': 'docs/plano-100.md',
            'batches': [{'id': 'bloco-1', 'items': ['0.1']}]}))  # falta 0.2
        with self.assertRaises(plan.ErroDoPlano) as ctx:
            plan.carregar()
        self.assertIn('faltando', str(ctx.exception))

    def test_carregar_recusa_indice_de_pacotes_desatualizado(self):
        write(self.root / '.claude/plano-100/pacotes/indice.json', json.dumps({'0.1': {'modelo': 'sonnet'}}))
        with self.assertRaises(plan.ErroDoPlano):
            plan.carregar()

    def test_carregar_recusa_id_repetido_no_plano(self):
        write(self.root / 'docs/plano-100.md', PLANO + '| 0.1 | Repetido | #3 | P |\n')
        with self.assertRaises(plan.ErroDoPlano) as ctx:
            plan.carregar()
        self.assertIn('repetido', str(ctx.exception))

    def test_carregar_ok_devolve_config_indice_e_ids_na_ordem_do_plano(self):
        config, indice, ids = plan.carregar()
        self.assertEqual(ids, ['0.1', '0.2'])
        self.assertEqual(set(indice), {'0.1', '0.2'})
        self.assertEqual(config['plan'], 'docs/plano-100.md')

    # --- validar(): o que entra no livro tem de estar completo ---

    def test_validar_recusa_resultado_sem_linha_por_id_solicitado(self):
        payload = resultado(grupo([item('0.1')], solicitados=['0.1', '0.2']))
        with self.assertRaises(plan.ErroDoPlano) as ctx:
            plan.validar(payload, {'0.1', '0.2'})
        self.assertIn('0.2', str(ctx.exception))

    def test_validar_recusa_id_desconhecido(self):
        payload = resultado(grupo([item('9.9')]))
        with self.assertRaises(plan.ErroDoPlano):
            plan.validar(payload, {'0.1', '0.2'})

    def test_validar_recusa_status_fora_do_vocabulario(self):
        payload = resultado(grupo([item('0.1', status='done')]))
        with self.assertRaises(plan.ErroDoPlano):
            plan.validar(payload, {'0.1', '0.2'})

    def test_validar_recusa_proof_fora_do_vocabulario(self):
        payload = resultado(grupo([item('0.1', proof='manual')]))
        with self.assertRaisesRegex(plan.ErroDoPlano, 'estado ou prova inválidos'):
            plan.validar(payload, {'0.1', '0.2'})

    def test_validar_diz_que_tests_e_unit_sao_simulated(self):
        # B6 (31.316): a mensagem ensina o que registrar, em vez de só dizer "inválido".
        for valor in ('tests', 'unit'):
            with self.subTest(proof=valor):
                payload = resultado(grupo([item('0.1', proof=valor)]))
                with self.assertRaisesRegex(plan.ErroDoPlano, r'tests/unit → simulated.*arquivo::teste'):
                    plan.validar(payload, {'0.1', '0.2'})

    def test_validar_aceita_simulated_com_evidencia_de_teste(self):
        payload = resultado(grupo([item('0.1', proof='simulated', evidence='tests/test_x.py::test_y')]))
        linhas = plan.validar(payload, {'0.1', '0.2'})
        self.assertEqual(linhas[0]['proof'], 'simulated')

    def test_validar_recusa_implemented_sem_evidencia(self):
        payload = resultado(grupo([item('0.1', evidence='')]))
        with self.assertRaises(plan.ErroDoPlano) as ctx:
            plan.validar(payload, {'0.1', '0.2'})
        self.assertIn('sem evidência', str(ctx.exception))

    def test_validar_recusa_blocked_sem_motivo(self):
        payload = resultado(grupo([item('0.1', status='blocked', proof='not_run', evidence='', blocker='')]))
        with self.assertRaises(plan.ErroDoPlano) as ctx:
            plan.validar(payload, {'0.1', '0.2'})
        self.assertIn('bloqueado sem motivo', str(ctx.exception))

    def test_validar_aceita_blocked_com_motivo_e_sem_evidencia(self):
        payload = resultado(grupo([item('0.1', status='blocked', proof='not_run', evidence='',
                                         blocker='depende do dono')], solicitados=['0.1']))
        linhas = plan.validar(payload, {'0.1', '0.2'})
        self.assertEqual(linhas[0]['status'], 'blocked')

    def test_validar_recusa_prova_diferente_de_not_run_sem_evidencia(self):
        payload = resultado(grupo([item('0.1', status='partial', proof='simulated', evidence='')],
                                   solicitados=['0.1']))
        with self.assertRaises(plan.ErroDoPlano) as ctx:
            plan.validar(payload, {'0.1', '0.2'})
        self.assertIn('prova sem evidência', str(ctx.exception))

    def test_validar_ignora_grupo_com_erro_e_segue_os_demais(self):
        payload = {'resultados': [
            {'grupo': 'x', 'erro': 'agente recusou a tarefa'},
            grupo([item('0.1')]),
        ]}
        linhas = plan.validar(payload, {'0.1', '0.2'})
        self.assertEqual([linha['id'] for linha in linhas], ['0.1'])

    def test_validar_recusa_resultado_sem_nenhum_item_aplicavel(self):
        payload = {'resultados': [{'grupo': 'x', 'erro': 'nada'}]}
        with self.assertRaises(plan.ErroDoPlano):
            plan.validar(payload, {'0.1', '0.2'})

    def test_validar_marca_conferencia_questionada(self):
        payload = resultado(grupo([item('0.1')], conferencia={
            'itens': [{'id': '0.1', 'confere': False, 'motivo': 'evidência não bate com o diff'}]}))
        linhas = plan.validar(payload, {'0.1', '0.2'})
        self.assertFalse(linhas[0]['conferido'])
        self.assertEqual(linhas[0]['conferencia'], 'evidência não bate com o diff')

    def test_validar_sem_registro_de_conferencia_fica_none(self):
        payload = resultado(grupo([item('0.1')], conferencia={'itens': []}))
        linhas = plan.validar(payload, {'0.1', '0.2'})
        self.assertIsNone(linhas[0]['conferido'])

    # --- aplicar(): grava o estado e regrava o relatório ---

    def test_aplicar_grava_estado_e_rodada(self):
        config, indice, ids = plan.carregar()
        caminho = self.write_result(resultado(grupo([item('0.1'), item('0.2', status='blocked', proof='not_run',
                                                                        evidence='', blocker='falta autorização')])))
        codigo = plan.aplicar(caminho, indice, ids)
        self.assertEqual(codigo, 0)
        estado = self.state()
        self.assertEqual(estado['itens']['0.1']['status'], 'implemented')
        self.assertEqual(estado['itens']['0.2']['status'], 'blocked')
        self.assertEqual(len(estado['rodadas']), 1)
        self.assertEqual(estado['rodadas'][0]['itens'], ['0.1', '0.2'])
        self.assertTrue((self.root / plan.RELATORIO).is_file())

    def test_aplicar_devolve_2_quando_a_conferencia_questiona(self):
        config, indice, ids = plan.carregar()
        caminho = self.write_result(resultado(grupo([item('0.1')], conferencia={
            'itens': [{'id': '0.1', 'confere': False, 'motivo': 'suspeito'}]})))
        codigo = plan.aplicar(caminho, indice, ids)
        self.assertEqual(codigo, 2)

    def test_aplicar_devolve_0_quando_a_conferencia_confirma(self):
        config, indice, ids = plan.carregar()
        caminho = self.write_result(resultado(grupo([item('0.1')], conferencia={
            'itens': [{'id': '0.1', 'confere': True, 'motivo': 'diff bate'}]})))
        codigo = plan.aplicar(caminho, indice, ids)
        self.assertEqual(codigo, 0)

    def test_aplicar_preserva_itens_anteriores_nao_tocados_pela_rodada(self):
        config, indice, ids = plan.carregar()
        plan.aplicar(self.write_result(resultado(grupo([item('0.1')], solicitados=['0.1']))), indice, ids)
        plan.aplicar(self.write_result(resultado(grupo([item('0.2')], solicitados=['0.2']))), indice, ids)
        estado = self.state()
        self.assertEqual(estado['itens']['0.1']['status'], 'implemented')
        self.assertEqual(estado['itens']['0.2']['status'], 'implemented')
        self.assertEqual(len(estado['rodadas']), 2)

    # --- relatorio(): documento regravado a partir do estado ---

    def test_relatorio_conta_implementados_sobre_o_total_do_plano(self):
        config, indice, ids = plan.carregar()
        estado = {'versao': 1, 'itens': {'0.1': {'status': 'implemented', 'proof': 'real', 'evidence': 'e'}},
                  'rodadas': []}
        plan.relatorio(indice, ids, estado)
        texto = (self.root / plan.RELATORIO).read_text(encoding='utf-8')
        self.assertIn('1 de 2 itens implementados.', texto)
        self.assertIn('| 0.1 |', texto)
        self.assertIn('| 0.2 |', texto)  # pendente também aparece na tabela
        self.assertIn('Pendentes (1): 0.2', texto)

    def test_relatorio_marca_conferencia_questionada_em_negrito(self):
        config, indice, ids = plan.carregar()
        estado = {'versao': 1, 'itens': {
            '0.1': {'status': 'implemented', 'proof': 'real', 'evidence': 'e', 'conferido': False}}, 'rodadas': []}
        plan.relatorio(indice, ids, estado)
        texto = (self.root / plan.RELATORIO).read_text(encoding='utf-8')
        self.assertIn('**questionada**', texto)

    # --- check(): resumo sem tocar em IA ---

    def test_check_lista_pendentes_por_modelo(self):
        config, indice, ids = plan.carregar()
        codigo = plan.check(config, indice, ids)
        self.assertEqual(codigo, 0)

    def test_check_com_estado_parcial_conta_pendentes_certo(self):
        write(self.root / plan.ESTADO, json.dumps({'versao': 1, 'rodadas': [],
              'itens': {'0.1': {'status': 'implemented', 'proof': 'real', 'evidence': 'e'}}}))
        config, indice, ids = plan.carregar()
        out = io.StringIO()
        with redirect_stdout(out):
            plan.check(config, indice, ids)
        self.assertIn('Implementados: 1. Pendentes: 1.', out.getvalue())

    # --- main(): roteamento de linha de comando ---

    def test_main_aplicar_sem_arquivo_falha(self):
        codigo, _, err = self.run_cli('aplicar')
        self.assertEqual(codigo, 1)
        self.assertIn('Informe o arquivo', err)

    def test_main_aplicar_com_resultado_valido(self):
        caminho = self.write_result(resultado(grupo([item('0.1'), item('0.2')])))
        codigo, out, _ = self.run_cli('aplicar', str(caminho))
        self.assertEqual(codigo, 0)
        self.assertIn('item(ns) registrado(s)', out)

    def test_main_relatorio_regrava_sem_exigir_resultado_novo(self):
        codigo, out, _ = self.run_cli('relatorio')
        self.assertEqual(codigo, 0)
        self.assertIn('Relatório regravado', out)
        self.assertTrue((self.root / plan.RELATORIO).is_file())

    def test_main_check_e_o_padrao_sem_argumento(self):
        codigo, out, _ = self.run_cli()
        self.assertEqual(codigo, 0)
        self.assertIn('itens no plano', out)

    def test_main_run_explica_e_sai_1_sem_tocar_no_estado(self):
        codigo, _, err = self.run_cli('run')
        self.assertEqual(codigo, 1)
        self.assertIn('claude -p', err)
        self.assertFalse((self.root / plan.ESTADO).is_file())

    def test_main_propaga_erro_do_plano_como_saida_1(self):
        write(self.root / '.claude/plano-100.json', json.dumps({
            'plan': 'docs/plano-100.md', 'batches': [{'id': 'bloco-1', 'items': ['0.1']}]}))
        codigo, _, err = self.run_cli('check')
        self.assertEqual(codigo, 1)
        self.assertIn('Interrompido', err)


if __name__ == '__main__':
    unittest.main()
