"""Testes de scripts/docs-check.py contra um mini-repo em diretório temporário. Nenhuma IA, nenhum arquivo real
do projeto é lido ou alterado."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'docs-check.py'
SPEC = importlib.util.spec_from_file_location('docs_check', SCRIPT)
docs_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs_check)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


PLANO_MINIMO = (
    '# Plano\n\n'
    '| Item | O que | Achados | Tam. |\n'
    '|---|---|---|---|\n'
    '| 0.1 | Primeiro item | #1 | P |\n'
    '| T.1 | Item transversal | #2 | M |\n'
)


class DocsCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def base_plan(self) -> None:
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO)
        write(self.root / '.claude/plano-100.json', json.dumps({
            'plan': 'docs/plano-100.md', 'batches': [{'id': 'bloco-1', 'items': ['0.1', 'T.1']}]}))

    def run_check(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            codigo = docs_check.rodar(self.root)
        return codigo, buffer.getvalue()

    # --- CLAUDE.md ---

    def test_claude_md_ausente_e_erro(self):
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('ERRO: CLAUDE.md não existe.', saida)

    def test_claude_md_curto_nao_gera_erro_de_tamanho(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n\nCurto.\n')
        _, saida = self.run_check()
        self.assertNotIn('CLAUDE.md tem', saida)

    def test_claude_md_longo_e_erro(self):
        write(self.root / 'CLAUDE.md', '\n'.join(f'linha {i}' for i in range(201)))
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('ERRO: CLAUDE.md tem 201 linhas', saida)

    # --- links ---

    def test_link_bom_e_link_quebrado(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'README.md', '[claude](CLAUDE.md) e [some](docs/nao-existe.md)\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('README.md:1: link quebrado para "docs/nao-existe.md"', saida)
        self.assertNotIn('link quebrado para "CLAUDE.md"', saida)

    def _repositorio_git(self) -> None:
        import subprocess
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        (self.root / '.git' / 'info').mkdir(exist_ok=True)

    def test_link_para_arquivo_local_ignorado_pelo_git_nao_e_quebrado(self):
        """O handoff é local (`.git/info/exclude`): num worktree novo ele não existe e o link continua valendo."""
        self._repositorio_git()
        with open(self.root / '.git' / 'info' / 'exclude', 'a', encoding='utf-8') as f:
            f.write('.claude/handoff-current.md\n')
        write(self.root / 'CLAUDE.md', '# Projeto\n\n[handoff](.claude/handoff-current.md)\n')
        codigo, saida = self.run_check()
        self.assertNotIn('link quebrado', saida)

    def test_link_para_arquivo_ausente_e_nao_ignorado_continua_quebrado(self):
        self._repositorio_git()
        write(self.root / 'CLAUDE.md', '# Projeto\n\n[handoff](.claude/outro.md)\n')
        codigo, saida = self.run_check()
        self.assertIn('link quebrado para ".claude/outro.md"', saida)

    def test_link_em_bloco_historico_e_so_aviso(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/auditoria-2026-09-21/README.md', '[x](sem-arquivo.md)\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 0)  # link em pasta histórica é só AVISO, nunca ERRO
        self.assertIn('AVISO: docs/auditoria-2026-09-21/README.md:1: link quebrado', saida)
        self.assertNotIn('ERRO: docs/auditoria-2026-09-21/README.md', saida)

    def test_ignora_ancora_pura_e_link_http(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'README.md', '[topo](#topo) e [site](https://example.com/x) e [mail](mailto:a@b.com)\n')
        codigo, _ = self.run_check()
        self.assertEqual(codigo, 0)

    def test_ignora_link_dentro_de_bloco_cercado_e_code_span(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'README.md', '```\n[quebrado](nao-existe.md)\n```\n\ninline `[quebrado](nao-existe.md)` fim\n')
        codigo, _ = self.run_check()
        self.assertEqual(codigo, 0)

    def test_ancora_e_numero_de_linha_sao_removidos_do_alvo(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/x.md', '# X\n')
        write(self.root / 'README.md', '[x](docs/x.md#secao) e [y](docs/x.md:12)\n')
        codigo, _ = self.run_check()
        self.assertEqual(codigo, 0)

    def test_link_absoluto_a_partir_da_raiz(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/x.md', '# X\n')
        write(self.root / 'docs/sub/README.md', '[x](/docs/x.md)\n')
        codigo, _ = self.run_check()
        self.assertEqual(codigo, 0)

    # --- mapa do plano-100 ---

    def test_mapa_do_plano_casando_nao_gera_erro(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        codigo, _ = self.run_check()
        self.assertEqual(codigo, 0)

    def test_mapa_faltando_id_e_erro(self):
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO)
        write(self.root / '.claude/plano-100.json', json.dumps({
            'plan': 'docs/plano-100.md', 'batches': [{'id': 'bloco-1', 'items': ['0.1']}]}))
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('não cobre: T.1', saida)

    def test_mapa_com_id_a_mais_e_erro(self):
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO)
        write(self.root / '.claude/plano-100.json', json.dumps({
            'plan': 'docs/plano-100.md', 'batches': [{'id': 'bloco-1', 'items': ['0.1', 'T.1', '9.9']}]}))
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('cita ID que não está no plano: 9.9', saida)

    def test_id_repetido_no_proprio_plano_e_erro(self):
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO + '| 0.1 | Repetido no plano | #3 | P |\n')
        write(self.root / '.claude/plano-100.json', json.dumps({
            'plan': 'docs/plano-100.md', 'batches': [{'id': 'bloco-1', 'items': ['0.1', 'T.1']}]}))
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('docs/plano-100.md repete ID(s): 0.1', saida)

    def test_mapa_com_id_repetido_e_erro(self):
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO)
        write(self.root / '.claude/plano-100.json', json.dumps({
            'plan': 'docs/plano-100.md', 'batches': [{'id': 'bloco-1', 'items': ['0.1', '0.1', 'T.1']}]}))
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('repete ID(s): 0.1', saida)

    # --- roadmap ---

    def test_roadmap_com_id_valido_nao_gera_erro(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/roadmap.md', '| ID | O que |\n|---|---|\n| 0.1 | Primeiro |\n')
        codigo, _ = self.run_check()
        self.assertEqual(codigo, 0)

    def test_roadmap_com_id_invalido_e_erro(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/roadmap.md', '| ID | O que |\n|---|---|\n| 9.9 | Inexistente |\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('cita ID fora do plano: 9.9', saida)

    # --- estado.json ---

    def test_estado_com_vocabulario_valido_nao_gera_aviso(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / '.claude/plano-100/estado.json', json.dumps({'itens': {
            '0.1': {'status': 'implemented', 'proof': 'real'}}}))
        _, saida = self.run_check()
        self.assertNotIn('fora do vocabulário', saida)

    def test_estado_com_status_invalido_e_aviso(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / '.claude/plano-100/estado.json', json.dumps({'itens': {
            '0.1': {'status': 'done', 'proof': 'unit'}}}))
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 0)  # é só AVISO
        self.assertIn('AVISO: .claude/plano-100/estado.json com status/proof fora do vocabulário: 0.1', saida)

    def test_estado_com_valor_nao_dict_nao_quebra_e_vira_aviso(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / '.claude/plano-100/estado.json', json.dumps({'itens': {'0.1': 'não é um objeto'}}))
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 0)
        self.assertIn('AVISO: .claude/plano-100/estado.json com status/proof fora do vocabulário: 0.1', saida)

    def test_estado_com_id_fora_do_plano_e_aviso(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / '.claude/plano-100/estado.json', json.dumps({'itens': {
            '9.9': {'status': 'implemented', 'proof': 'real'}}}))
        _, saida = self.run_check()
        self.assertIn('AVISO: .claude/plano-100/estado.json cita ID que não está no plano: 9.9', saida)

    # --- relatório de execução ---

    def test_contagem_do_relatorio_batendo_nao_gera_aviso(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / '.claude/plano-100/estado.json', json.dumps({'itens': {
            '0.1': {'status': 'implemented', 'proof': 'real'}}}))
        write(self.root / 'docs/execucao-plano-100-runner.md', '# X\n\n1 de 2 itens implementados.\n')
        _, saida = self.run_check()
        self.assertNotIn('Rode: python scripts/claude-plan-100.py relatorio', saida)

    def test_contagem_do_relatorio_desatualizada_e_aviso(self):
        self.base_plan()
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / '.claude/plano-100/estado.json', json.dumps({'itens': {
            '0.1': {'status': 'implemented', 'proof': 'real'},
            'T.1': {'status': 'implemented', 'proof': 'real'}}}))
        write(self.root / 'docs/execucao-plano-100-runner.md', '# X\n\n1 de 2 itens implementados.\n')
        _, saida = self.run_check()
        self.assertIn('diz "1 de 2 itens", mas o estado atual é "2 de 2"', saida)

    # --- banco.md ---

    def test_banco_citando_todas_as_migracoes_nao_gera_aviso(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'backend/migrations/001_init.sql', '-- init\n')
        write(self.root / 'backend/migrations/002_seguinte.sql', '-- seguinte\n')
        write(self.root / 'docs/banco.md', 'Migrações 001 e 002 aplicadas.\n')
        _, saida = self.run_check()
        self.assertNotIn('não cita a(s) migração', saida)

    def test_banco_sem_citar_migracao_e_aviso(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'backend/migrations/001_init.sql', '-- init\n')
        write(self.root / 'backend/migrations/002_seguinte.sql', '-- seguinte\n')
        write(self.root / 'docs/banco.md', 'Só a 001 está documentada.\n')
        _, saida = self.run_check()
        self.assertIn('AVISO: docs/banco.md não cita a(s) migração(ões): 002_seguinte.sql', saida)

    # --- IDs únicos: decisões e aprendizados ---

    def test_adr_unico_nao_gera_erro(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/decisoes.md', '## ADR-001 — Primeira\n\ntexto\n\n## ADR-002 — Segunda\n\ntexto\n')
        codigo, _ = self.run_check()
        self.assertEqual(codigo, 0)

    def test_adr_duplicado_e_erro(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/decisoes.md', '## ADR-001 — Primeira\n\ntexto\n\n## ADR-001 — Repetida\n\ntexto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('docs/decisoes.md repete ID(s): ADR-001', saida)

    def test_k_duplicado_e_erro(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/conhecimento/aprendizados.md',
              '### K-001 — Primeiro\n\ntexto\n\n### K-001 — Repetido\n\ntexto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('docs/conhecimento/aprendizados.md repete ID(s): K-001', saida)

    # --- CLI ---

    def test_main_aceita_raiz_e_devolve_codigo_de_saida(self):
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            codigo = docs_check.main(['--raiz', str(self.root)])
        self.assertEqual(codigo, 0)
        self.assertIn('docs-check: 0 erros, 0 avisos', buffer.getvalue())



def _plano_json(**mudancas) -> str:
    """O mapa de blocos válido do mini-repo (PLANO_MINIMO), com as mudanças pedidas (valor `None` remove a chave)."""
    dados = {'model': 'claude-opus-5', 'plan': 'docs/plano-100.md', 'prompt': 'docs/prompt.md',
             'batches': [{'id': 'bloco-1', 'name': 'Primeiro', 'effort': 'high', 'items': ['0.1', 'T.1']}],
             'modelos': {'0.1': 'opus'}}
    for chave, valor in mudancas.items():
        if valor is None:
            dados.pop(chave, None)
        else:
            dados[chave] = valor
    return json.dumps(dados)


class EsquemaDoPlanoJsonTests(unittest.TestCase):
    """29.160: o FORMATO de `.claude/plano-100.json`, com o caminho da chave no erro. Só biblioteca padrão."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO)
        write(self.root / 'docs/prompt.md', '# Prompt\n')

    def run_check(self, texto: str):
        write(self.root / '.claude/plano-100.json', texto)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            codigo = docs_check.rodar(self.root)
        return codigo, buffer.getvalue()

    def test_exemplo_bom_completo_passa(self):
        codigo, saida = self.run_check(_plano_json())
        self.assertEqual(codigo, 0, saida)

    def test_exemplo_bom_so_com_o_minimo_passa(self):
        """Só `plan` e `batches[].id/items` são lidos pelos scripts; o resto é opcional."""
        texto = json.dumps({'plan': 'docs/plano-100.md', 'batches': [{'id': 'b', 'items': ['0.1', 'T.1']}]})
        codigo, saida = self.run_check(texto)
        self.assertEqual(codigo, 0, saida)

    def test_json_invalido_diz_a_linha(self):
        codigo, saida = self.run_check('{\n  "plan": "docs/plano-100.md",\n  "batches": [\n')
        self.assertEqual(codigo, 1)
        self.assertRegex(saida, r'\.claude/plano-100\.json: linha \d+, coluna \d+: JSON inválido')

    def test_raiz_que_nao_e_objeto(self):
        codigo, saida = self.run_check('["0.1"]')
        self.assertEqual(codigo, 1)
        self.assertIn('o arquivo deve ser um objeto, veio list', saida)

    def test_chave_desconhecida_na_raiz_sugere_a_certa(self):
        codigo, saida = self.run_check(_plano_json(batchs=[{'id': 'b', 'items': ['0.1']}]))
        self.assertEqual(codigo, 1)
        self.assertIn('.claude/plano-100.json: batchs: chave desconhecida (nenhum script a lê) (quis dizer `batches`?)', saida)

    def test_plan_ausente_e_arquivo_inexistente(self):
        _, saida = self.run_check(_plano_json(plan=None))
        self.assertIn('plan: chave obrigatória ausente', saida)
        _, saida = self.run_check(_plano_json(plan='docs/nao-existe.md'))
        self.assertIn('plan: o arquivo "docs/nao-existe.md" não existe', saida)
        _, saida = self.run_check(_plano_json(prompt='docs/sumiu.md'))
        self.assertIn('prompt: o arquivo "docs/sumiu.md" não existe', saida)

    def test_batches_ausente_ou_do_tipo_errado_nao_gera_traceback(self):
        for valor in ('texto', {}, [], 7):
            codigo, saida = self.run_check(_plano_json(batches=valor))
            self.assertEqual(codigo, 1, valor)
            self.assertIn('batches: deve ser uma lista não vazia de blocos', saida)
        codigo, saida = self.run_check(_plano_json(batches=None))
        self.assertEqual(codigo, 1)
        self.assertIn('batches: chave obrigatória ausente', saida)

    def test_bloco_com_chave_errada_diz_o_indice_e_o_id(self):
        bloco = {'id': 'bloco-x', 'item': ['0.1', 'T.1']}
        codigo, saida = self.run_check(_plano_json(batches=[{'id': 'primeiro', 'items': ['0.1']}, bloco]))
        self.assertEqual(codigo, 1)
        self.assertIn('batches[1].item: chave desconhecida (nenhum script a lê) (quis dizer `items`?)', saida)
        self.assertIn('batches[1] ("bloco-x").items: obrigatório, lista não vazia de IDs do plano', saida)

    def test_item_como_numero_ou_fora_do_formato(self):
        codigo, saida = self.run_check(_plano_json(batches=[{'id': 'b', 'items': ['0.1', 0.2, 'T-1']}]))
        self.assertEqual(codigo, 1)
        self.assertIn('batches[0] ("b").items[1]: ID fora do formato', saida)
        self.assertIn('batches[0] ("b").items[2]: ID fora do formato', saida)
        self.assertNotIn('items[0]', saida)

    def test_esforco_fora_da_lista(self):
        codigo, saida = self.run_check(_plano_json(batches=[{'id': 'b', 'effort': 'altissimo', 'items': ['0.1', 'T.1']}]))
        self.assertEqual(codigo, 1)
        self.assertIn('batches[0] ("b").effort: esforço "altissimo" fora de', saida)

    def test_id_de_bloco_repetido(self):
        blocos = [{'id': 'b', 'items': ['0.1']}, {'id': 'b', 'items': ['T.1']}]
        codigo, saida = self.run_check(_plano_json(batches=blocos))
        self.assertEqual(codigo, 1)
        self.assertIn('batches[1].id: id de bloco repetido: "b"', saida)

    def test_modelos_com_id_fora_do_mapa_e_modelo_invalido(self):
        codigo, saida = self.run_check(_plano_json(modelos={'9.9': 'opus', '0.1': 'gpt', 'xx': 'sonnet'}))
        self.assertEqual(codigo, 1)
        self.assertIn('modelos.9.9: esse ID não está em nenhum bloco', saida)
        self.assertIn('modelos.0.1: modelo "gpt" fora de', saida)
        self.assertIn('modelos.xx: a chave deve ser um ID do plano', saida)

    def test_formato_errado_nao_deixa_o_mapa_de_ids_estourar(self):
        """Com `batches` quebrado a conferência de ID não roda (e não vira traceback); o erro de formato basta."""
        codigo, saida = self.run_check(_plano_json(batches=[{'id': 'b', 'items': 'nao-e-lista'}]))
        self.assertEqual(codigo, 1)
        self.assertNotIn('Traceback', saida)
        self.assertNotIn('não cobre', saida)

    def test_o_mapa_real_do_repositorio_esta_no_formato(self):
        """O arquivo versionado de verdade passa pelo mesmo esquema (o mini-repo só traz o plano e o prompt reais)."""
        raiz = SCRIPT.parents[1]
        rel = docs_check.Relatorio()
        self.assertTrue(docs_check.checar_plano_json(raiz, rel), rel.erros)
        self.assertEqual(rel.erros, [])


def _dependencias_do_modelo() -> bool:
    return docs_check._carregar_modelo_de_config()[0] is not None


EXEMPLO_REAL = SCRIPT.parents[1] / 'config' / 'config.example.yaml'


@unittest.skipUnless(_dependencias_do_modelo(), 'precisa de PyYAML e pydantic (use o Python do venv do backend)')
class EsquemaDoConfigExemploTests(unittest.TestCase):
    """29.160: `config/config.example.yaml` contra o modelo do backend (`AppConfigFile`). O mini-repo traz o exemplo
    (bom ou ruim); o modelo é o desta árvore. O `config.yaml` da instalação nunca entra."""

    def setUp(self) -> None:
        import yaml
        self.yaml = yaml
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        self.bom = yaml.safe_load(EXEMPLO_REAL.read_text(encoding='utf-8'))

    def run_check(self, dados=None, texto: str | None = None):
        write(self.root / 'config/config.example.yaml',
              texto if texto is not None else self.yaml.safe_dump(dados, allow_unicode=True))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            codigo = docs_check.rodar(self.root)
        return codigo, buffer.getvalue()

    def test_o_exemplo_real_do_repositorio_passa(self):
        codigo, saida = self.run_check(texto=EXEMPLO_REAL.read_text(encoding='utf-8'))
        self.assertEqual(codigo, 0, saida)
        self.assertNotIn('NÃO conferido', saida)

    def test_chave_desconhecida_num_bloco_diz_o_caminho_e_sugere(self):
        ruim = {**self.bom, 'instances': {**self.bom['instances'], 'countt': 5}}
        codigo, saida = self.run_check(ruim)
        self.assertEqual(codigo, 1)
        self.assertIn('config/config.example.yaml: instances.countt: chave desconhecida '
                      '(o backend a ignoraria em silêncio) (quis dizer `count`?)', saida)

    def test_bloco_desconhecido_na_raiz(self):
        codigo, saida = self.run_check({**self.bom, 'telemetria': {'ligada': True}})
        self.assertEqual(codigo, 1)
        self.assertIn('config/config.example.yaml: telemetria: chave desconhecida', saida)

    def test_chave_desconhecida_dentro_de_item_de_lista(self):
        apps = [dict(self.bom['apps'][0], pacote_errado='x'), *self.bom['apps'][1:]]
        codigo, saida = self.run_check({**self.bom, 'apps': apps})
        self.assertEqual(codigo, 1)
        self.assertIn('config/config.example.yaml: apps[0].pacote_errado: chave desconhecida', saida)

    def test_tipo_errado_diz_o_caminho_sem_ecoar_o_valor(self):
        valor = 'VALOR-QUE-NAO-PODE-APARECER-123'
        ruim = {**self.bom, 'instances': {**self.bom['instances'], 'count': valor}}
        codigo, saida = self.run_check(ruim)
        self.assertEqual(codigo, 1)
        self.assertIn('config/config.example.yaml: instances.count: ', saida)
        self.assertNotIn(valor, saida)

    def test_bloco_com_formato_errado(self):
        """Um bloco que devia ser mapa virando lista, e uma lista virando mapa."""
        codigo, saida = self.run_check({**self.bom, 'server': ['127.0.0.1'], 'apps': {'id': 'x'}})
        self.assertEqual(codigo, 1)
        self.assertIn('config/config.example.yaml: server: ', saida)
        self.assertIn('config/config.example.yaml: apps: ', saida)

    def test_bloco_que_saiu_do_config_e_recusado(self):
        codigo, saida = self.run_check({**self.bom, 'instagram': {}})
        self.assertEqual(codigo, 1)
        self.assertIn('o bloco `instagram:` do config.yaml saiu', saida)

    def test_yaml_invalido_diz_a_linha(self):
        codigo, saida = self.run_check(texto='server:\n  host: "127.0.0.1\n  port: 8000\n')
        self.assertEqual(codigo, 1)
        self.assertRegex(saida, r'config/config\.example\.yaml: linha \d+, coluna \d+: YAML inválido')

    def test_raiz_que_nao_e_mapa(self):
        codigo, saida = self.run_check(texto='- server\n- paths\n')
        self.assertEqual(codigo, 1)
        self.assertIn('o arquivo deve ser um mapa de blocos, veio list', saida)

    def test_o_config_da_instalacao_nunca_e_aberto(self):
        """Um `config.yaml` ao lado, com lixo e um valor marcado: nem erro, nem eco, nem leitura."""
        write(self.root / 'config/config.yaml', 'server: {{{ lixo\nsegredo: MARCA-DA-INSTALACAO-999\n')
        lidos: list[str] = []
        original = docs_check.ler

        def espiao(caminho):
            lidos.append(Path(caminho).name)
            return original(caminho)

        docs_check.ler = espiao
        try:
            codigo, saida = self.run_check(texto=EXEMPLO_REAL.read_text(encoding='utf-8'))
        finally:
            docs_check.ler = original
        self.assertEqual(codigo, 0, saida)
        self.assertNotIn('MARCA-DA-INSTALACAO-999', saida)
        self.assertNotIn('config.yaml', lidos)
        self.assertIn('config.example.yaml', lidos)

    def test_sem_o_exemplo_no_repositorio_nao_ha_o_que_conferir(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            codigo = docs_check.rodar(self.root)
        self.assertEqual(codigo, 0, buffer.getvalue())


class SemDependenciasDoModeloTests(unittest.TestCase):
    """Sem PyYAML/pydantic (o job de documentação do CI só instala o pytest) a conferência AVISA, nunca falha nem
    finge ter passado."""

    def test_sem_dependencias_avisa_e_nao_da_erro(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d)
            write(raiz / 'CLAUDE.md', '# Projeto\n')
            write(raiz / 'config/config.example.yaml', 'chave_que_nao_existe: 1\n')
            original = docs_check._carregar_modelo_de_config
            docs_check._carregar_modelo_de_config = lambda: (None, None, 'falta o pacote yaml')
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    codigo = docs_check.rodar(raiz)
            finally:
                docs_check._carregar_modelo_de_config = original
        saida = buffer.getvalue()
        self.assertEqual(codigo, 0, saida)
        self.assertIn('AVISO: config/config.example.yaml: formato NÃO conferido (falta o pacote yaml)', saida)
        self.assertIn('backend\\.venv\\Scripts\\python.exe scripts\\docs-check.py', saida)


@unittest.skipUnless(_dependencias_do_modelo(), 'precisa de PyYAML e pydantic')
class PercursoDoModeloTests(unittest.TestCase):
    """O percurso de chaves desconhecidas, com modelos próprios: mapa de modelos, lista, opcional, alias e `extra`."""

    def setUp(self) -> None:
        from typing import Optional
        from pydantic import BaseModel, ConfigDict, Field

        class Folha(BaseModel):
            valor: int = 0

        class Livre(BaseModel):
            model_config = ConfigDict(extra='allow')
            a: int = 0

        class Raiz(BaseModel):
            mapa: dict[str, Folha] = {}
            lista: list[Folha] = []
            talvez: Optional[Folha] = None
            livre: Livre = Livre()
            apelido: int = Field(0, alias='nome_no_yaml')
            solto: dict[str, int] = {}

        self.Raiz, self.Base = Raiz, BaseModel

    def achados(self, dados):
        saida: list[tuple[str, str]] = []
        docs_check._chaves_desconhecidas(dados, self.Raiz, self.Base, '', saida)
        return {onde for onde, _ in saida}

    def test_tudo_certo_nao_acha_nada(self):
        dados = {'mapa': {'x': {'valor': 1}}, 'lista': [{'valor': 2}], 'talvez': {'valor': 3},
                 'livre': {'qualquer': 1}, 'nome_no_yaml': 4, 'solto': {'qualquer': 5}}
        self.assertEqual(self.achados(dados), set())

    def test_acha_em_cada_forma_de_aninhamento(self):
        dados = {'mapa': {'x': {'valor': 1, 'ruim1': 0}}, 'lista': [{'valor': 1}, {'ruim2': 0}],
                 'talvez': {'ruim3': 0}, 'raiz_ruim': 1}
        self.assertEqual(self.achados(dados), {'mapa.x.ruim1', 'lista[1].ruim2', 'talvez.ruim3', 'raiz_ruim'})

    def test_modelo_com_extra_allow_aceita_chave_nova(self):
        self.assertEqual(self.achados({'livre': {'nova': 1}}), set())

    def test_o_alias_conta_como_conhecido_e_o_nome_do_campo_tambem(self):
        self.assertEqual(self.achados({'nome_no_yaml': 1}), set())
        self.assertEqual(self.achados({'apelido': 1}), set())


class CloneLimpo(unittest.TestCase):
    """29.102: o handoff local estava só no `.git/info/exclude`, que não vem num clone, e o docs-check do CI acusava o
    link do CLAUDE.md como quebrado toda noite. O `.gitignore` VERSIONADO tem de ignorá-lo: num repositório novo,
    só com ele, o alvo é ignorado."""

    def test_o_gitignore_versionado_ignora_o_handoff_local(self):
        import subprocess
        gitignore = (SCRIPT.parents[1] / '.gitignore').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(['git', 'init', '-q'], cwd=d, check=True)
            (Path(d) / '.gitignore').write_text(gitignore, encoding='utf-8')
            r = subprocess.run(['git', 'check-ignore', '-q', '--', '.claude/handoff-current.md'], cwd=d, check=False)
        self.assertEqual(r.returncode, 0, 'o .gitignore versionado não ignora .claude/handoff-current.md')


if __name__ == '__main__':
    unittest.main()
