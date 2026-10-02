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
            'batches': [{'id': 'bloco-1', 'items': ['0.1', 'T.1']}]}))

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
            'batches': [{'id': 'bloco-1', 'items': ['0.1']}]}))
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('não cobre: T.1', saida)

    def test_mapa_com_id_a_mais_e_erro(self):
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO)
        write(self.root / '.claude/plano-100.json', json.dumps({
            'batches': [{'id': 'bloco-1', 'items': ['0.1', 'T.1', '9.9']}]}))
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('cita ID que não está no plano: 9.9', saida)

    def test_id_repetido_no_proprio_plano_e_erro(self):
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO + '| 0.1 | Repetido no plano | #3 | P |\n')
        write(self.root / '.claude/plano-100.json', json.dumps({
            'batches': [{'id': 'bloco-1', 'items': ['0.1', 'T.1']}]}))
        write(self.root / 'CLAUDE.md', '# Projeto\n')
        codigo, saida = self.run_check()
        self.assertEqual(codigo, 1)
        self.assertIn('docs/plano-100.md repete ID(s): 0.1', saida)

    def test_mapa_com_id_repetido_e_erro(self):
        write(self.root / 'docs/plano-100.md', PLANO_MINIMO)
        write(self.root / '.claude/plano-100.json', json.dumps({
            'batches': [{'id': 'bloco-1', 'items': ['0.1', '0.1', 'T.1']}]}))
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


if __name__ == '__main__':
    unittest.main()
