"""Testes locais do orquestrador; nenhum modelo ou aparelho é acionado."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'claude-plan-100.py'
SPEC = importlib.util.spec_from_file_location('claude_plan', SCRIPT)
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)
ROOT = SCRIPT.parents[1]
REAL_INVOKE = plan.invoke
REAL_CHECK_CLI = plan.check_cli


def item(item_id, status='implemented', proof='simulated'):
    return {'id': item_id, 'status': status, 'proof': proof,
            'evidence': 'teste local: contrato preservado',
            'blocker': 'depende de autorização' if status == 'blocked' else ''}


def envelope(ids, session, status='implemented', progress=True):
    return {'subtype': 'success', 'session_id': session, 'is_error': False,
            'modelUsage': {'claude-sonnet-5': {'inputTokens': 10}}, 'total_cost_usd': 0,
            'structured_output': {'items': [item(i, status) for i in ids],
                                  'summary': 'Resultado do CLI substituto.', 'progress': progress}}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for path in ('docs/plano-100.md', 'docs/prompt-executar-plano-100-claude.md', '.claude/plano-100.json'):
            destination = self.root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, destination)
        self.config, self.ids, self.digest = plan.load_config(self.root)
        self.calls = []
        self.mutate = lambda result: result
        self.return_code = 0
        self.silent = io.StringIO()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'CLAUDECODE': '', 'CLAUDE_CODE_SKIP_PROMPT_HISTORY': ''}).start()
        patch.object(plan, 'check_cli', return_value='/fake/claude').start()
        patch.object(plan, 'invoke', side_effect=self.fake_cli).start()

    def fake_cli(self, command, prompt, root, effort, prefix):
        schema = json.loads(command[command.index('--json-schema') + 1])
        ids = schema['properties']['items']['items']['properties']['id']['enum']
        session_flag = '--resume' if '--resume' in command else '--session-id'
        session = command[command.index(session_flag) + 1]
        self.calls.append((command, effort, ids, session, prompt))
        result = self.mutate(envelope(ids, session))
        prefix.with_suffix('.json').write_text(json.dumps(result), encoding='utf-8')
        return self.return_code

    def main(self, *args):
        with redirect_stdout(self.silent), redirect_stderr(self.silent):
            return plan.main(list(args), root=self.root)

    def state(self):
        return plan.read_json(self.root / plan.STATE_DIR / 'state.json')

    def test_full_plan_switches_effort_and_resumes_same_session(self):
        self.assertEqual(self.main('run'), 0)
        self.assertEqual(len(self.calls), 19)
        self.assertEqual(len(self.state()['items']), 67)
        sessions = {call[3] for call in self.calls}
        self.assertEqual(len(sessions), 1)
        self.assertIn('--session-id', self.calls[0][0])
        for command, effort, ids, session, prompt in self.calls[1:]:
            self.assertIn('--resume', command)
            self.assertNotIn('--session-id', command)
        self.assertEqual([call[1] for call in self.calls[:3]], ['high', 'medium', 'high'])
        for command, effort, *_ in self.calls:
            self.assertEqual(command[command.index('--effort') + 1], effort)
            self.assertNotIn('--dangerously-skip-permissions', command)
        self.assertEqual(self.main('run'), 0)
        self.assertEqual(len(self.calls), 19, 'retomar não deve repetir itens implementados')
        report = (self.root / plan.REPORT).read_text()
        self.assertIn('simulated', report)
        self.assertNotIn('| real |', report)

    def test_dry_run_never_calls_cli_or_writes_state(self):
        self.assertEqual(self.main('run', '--dry-run'), 0)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / plan.STATE_DIR).exists())

    def test_plan_drift_requires_explicit_mapping_update(self):
        path = self.root / 'docs/plano-100.md'
        path.write_text(path.read_text() + '\n| 11.1 | nova tarefa |\n')
        self.assertEqual(self.main('run', '--dry-run'), 1)
        self.assertEqual(self.calls, [])

    def test_omitted_id_cannot_be_marked_complete(self):
        def corrupt(result):
            result['structured_output']['items'].pop()
            return result
        self.mutate = corrupt
        self.assertEqual(self.main('run'), 1)
        self.assertEqual(self.state()['items'], {})
        self.assertEqual(len(self.calls), 1)

    def test_denied_permission_stops_without_retries_or_grant_changes(self):
        def deny(result):
            result['permission_denials'] = [{'tool_name': 'Bash'}]
            return result
        self.mutate = deny
        self.assertEqual(self.main('run'), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.state()['items'], {})

    def test_blocked_items_do_not_stop_independent_blocks_and_require_explicit_retry(self):
        def block_first(result):
            if len(self.calls) == 1:
                result['structured_output']['items'][0] = item('0.1', 'blocked', 'not_run')
            return result
        self.mutate = block_first
        self.assertEqual(self.main('run'), 2)
        self.assertEqual(self.main('run'), 2)
        self.assertEqual(len(self.calls), 19)
        self.mutate = lambda result: result
        self.assertEqual(self.main('run', '--retry-blocked'), 0)
        self.assertEqual(self.calls[-1][2], ['0.1'])

    def test_partial_without_progress_stops_expensive_loop(self):
        def partial(result):
            result['structured_output']['items'] = [item(i['id'], 'partial') for i in result['structured_output']['items']]
            result['structured_output']['progress'] = False
            return result
        self.mutate = partial
        self.assertEqual(self.main('run'), 1)
        self.assertEqual(len(self.calls), 1)

    def test_partial_progress_is_bounded(self):
        def partial(result):
            result['structured_output']['items'] = [item(i['id'], 'partial') for i in result['structured_output']['items']]
            return result
        self.mutate = partial
        self.assertEqual(self.main('run', '--max-rounds', '2'), 1)
        self.assertEqual(len(self.calls), 2)

    def test_failed_cli_keeps_resume_id_and_pending_work(self):
        self.return_code = 17
        self.assertEqual(self.main('run'), 1)
        previous_session = self.state()['session_id']
        self.assertEqual(self.state()['items'], {})
        self.return_code = 0
        self.assertEqual(self.main('run'), 0)
        self.assertEqual(self.calls[1][3], previous_session)
        self.assertIn('--resume', self.calls[1][0])

    def test_fresh_session_preserves_completed_items(self):
        self.assertEqual(self.main('run'), 0)
        before = self.state()
        self.assertEqual(self.main('run', '--fresh-session'), 0)
        after = self.state()
        self.assertNotEqual(before['session_id'], after['session_id'])
        self.assertEqual(before['items'], after['items'])
        self.assertEqual(len(self.calls), 19)

    def test_lock_prevents_two_executors(self):
        with plan.execution_lock(self.root / plan.STATE_DIR):
            self.assertEqual(self.main('run'), 1)
        self.assertEqual(self.calls, [])

    def test_nested_claude_is_rejected(self):
        with patch.dict(os.environ, {'CLAUDECODE': '1'}):
            self.assertEqual(self.main('run'), 1)
        self.assertEqual(self.calls, [])

    def test_effort_overrides_only_child_environment(self):
        with patch.dict(os.environ, {'CLAUDE_CODE_EFFORT_LEVEL': 'max'}):
            self.assertEqual(plan.child_environment('medium')['CLAUDE_CODE_EFFORT_LEVEL'], 'medium')
            self.assertEqual(os.environ['CLAUDE_CODE_EFFORT_LEVEL'], 'max')

    def test_report_without_evidence_is_rejected(self):
        result = envelope(['1.3'], 'session')
        result['structured_output']['items'][0]['evidence'] = ''
        with self.assertRaises(plan.PlanError):
            plan.validate_result(result, ['1.3'], 'session')

    def test_wrong_session_result_is_rejected(self):
        with self.assertRaises(plan.PlanError):
            plan.validate_result(envelope(['1.3'], 'other'), ['1.3'], 'session')

    def test_unexpected_model_stops_further_spend(self):
        def substituted(result):
            result['modelUsage'] = {'claude-opus-5': {'inputTokens': 10}}
            return result
        self.mutate = substituted
        self.assertEqual(self.main('run'), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.state()['items'], {})

    @unittest.skipIf(os.name == 'nt', 'simulador executável usa shebang POSIX')
    def test_real_subprocess_transport_with_local_cli_substitute(self):
        simulator = self.root / 'fake-claude'
        simulator.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, sys
args = sys.argv[1:]
if '--help' in args:
    print('--effort --json-schema --session-id --resume --max-turns')
    sys.exit(0)
schema = json.loads(args[args.index('--json-schema') + 1])
ids = schema['properties']['items']['items']['properties']['id']['enum']
flag = '--resume' if '--resume' in args else '--session-id'
session = args[args.index(flag) + 1]
effort = args[args.index('--effort') + 1]
assert os.environ['CLAUDE_CODE_EFFORT_LEVEL'] == effort
assert 'MODO RUNNER' in sys.stdin.read()
with pathlib.Path('transport.jsonl').open('a') as stream:
    stream.write(json.dumps({'session': session, 'flag': flag, 'effort': effort}) + '\\n')
items = [dict(id=i, status='implemented', proof='simulated', evidence='CLI local substituto', blocker='') for i in ids]
print(json.dumps(dict(subtype='success', is_error=False, session_id=session,
    modelUsage={'claude-sonnet-5': {}}, structured_output=dict(items=items, progress=True, summary='OK simulado'))))
''', encoding='utf-8')
        simulator.chmod(0o700)
        with patch.object(plan, 'invoke', REAL_INVOKE), patch.object(plan, 'check_cli', REAL_CHECK_CLI):
            self.assertEqual(self.main('run', '--claude', str(simulator)), 0)
        calls = [json.loads(line) for line in (self.root / 'transport.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 19)
        self.assertEqual(len({call['session'] for call in calls}), 1)
        self.assertEqual(calls[0]['flag'], '--session-id')
        self.assertTrue(all(call['flag'] == '--resume' for call in calls[1:]))
        self.assertEqual([call['effort'] for call in calls[:3]], ['high', 'medium', 'high'])


class NativeSkillTests(unittest.TestCase):
    def test_project_skills_have_explicit_native_effort_and_no_delegation(self):
        for name, effort in [('plano-100', 'medium'), ('plano-100-medium', 'medium'), ('plano-100-high', 'high')]:
            text = (ROOT / '.claude/skills' / name / 'SKILL.md').read_text()
            self.assertTrue(text.startswith('---\n'))
            header = text.split('---', 2)[1]
            self.assertIn(f'effort: {effort}', header)
            self.assertIn('model: claude-sonnet-5', header)
            self.assertIn('disable-model-invocation: true', header)
            self.assertNotIn('context: fork', header)


if __name__ == '__main__':
    unittest.main()
