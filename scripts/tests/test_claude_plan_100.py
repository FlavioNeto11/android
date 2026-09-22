"""Testes locais do orquestrador; nenhum modelo ou aparelho é acionado."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
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
            'modelUsage': {'claude-opus-5': {'inputTokens': 10}}, 'total_cost_usd': 0,
            'structured_output': {'items': [item(i, status) for i in ids],
                                  'summary': 'Resultado do CLI substituto.', 'progress': progress,
                                  'next_action': 'continue', 'reason': 'Investigação local e evidências registradas.'}}


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
        patch.dict(os.environ, {'CLAUDECODE': '', 'CLAUDE_CODE_SKIP_PROMPT_HISTORY': '',
                               'CLAUDE_CODE_DISABLE_WORKFLOWS': '',
                               'CLAUDE_CONFIG_DIR': str(self.root / 'user-config')}).start()
        patch.object(plan, 'check_cli', return_value=plan.CliInfo('/fake/claude', frozenset(plan.OVERRIDE_EFFORTS))).start()
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
        self.assertEqual([call[1] for call in self.calls[:3]], ['xhigh', 'medium', 'xhigh'])
        for command, effort, *_ in self.calls:
            self.assertEqual(command[command.index('--effort') + 1], effort)
            self.assertEqual(command[command.index('--model') + 1], 'claude-opus-5')
            settings = json.loads(command[command.index('--settings') + 1])
            self.assertFalse(settings['ultracode'])
            self.assertTrue(settings['disableWorkflows'])
            self.assertEqual(command[command.index('--disallowedTools') + 1:
                                     command.index('--disallowedTools') + 3], ['Agent', 'Task'])
            self.assertNotIn('--dangerously-skip-permissions', command)
        self.assertEqual(self.main('run'), 0)
        self.assertEqual(len(self.calls), 19, 'retomar não deve repetir itens implementados')
        report = (self.root / plan.REPORT).read_text()
        self.assertIn('simulated', report)
        self.assertNotIn('| real |', report)

    def test_max_is_scoped_and_default_resume_preserves_progress(self):
        self.assertEqual(self.main('run', '--block', '0-protecao', '--effort', 'max'), 0)
        self.assertEqual(len(self.calls), 1)
        command, effort, ids, session, _ = self.calls[0]
        self.assertEqual(effort, 'max')
        self.assertEqual(ids, self.config['batches'][0]['items'])
        self.assertIn('--disallowedTools', command)
        self.assertEqual(self.main('run'), 0)
        self.assertEqual(len(self.calls), 19)
        self.assertEqual([call[1] for call in self.calls[:3]], ['max', 'medium', 'xhigh'])
        self.assertTrue(all(call[3] == session for call in self.calls))
        self.assertEqual(plan.read_json(self.root / plan.CONFIG), self.config)
        self.assertEqual(self.state()['history'][0]['requested_reasoning_effort'], 'max')

    def test_ultracode_is_explicit_and_next_run_disables_workflows(self):
        with patch.dict(os.environ, {'CLAUDE_CODE_EFFORT_LEVEL': 'max',
                                     'CLAUDE_CODE_SUBAGENT_MODEL': 'claude-sonnet-5',
                                     'CLAUDE_CODE_DISABLE_WORKFLOWS': '1'}):
            environment = plan.child_environment('ultracode')
            self.assertEqual(environment['CLAUDE_CODE_EFFORT_LEVEL'], 'xhigh')
            self.assertEqual(environment['CLAUDE_CODE_SUBAGENT_MODEL'], 'claude-opus-5')
            self.assertEqual(environment['CLAUDE_CODE_DISABLE_WORKFLOWS'], '1')
            self.assertEqual(os.environ['CLAUDE_CODE_EFFORT_LEVEL'], 'max')
            self.assertEqual(os.environ['CLAUDE_CODE_SUBAGENT_MODEL'], 'claude-sonnet-5')
        self.assertEqual(self.main('run', '--block', '0-protecao', '--effort', 'ultracode'), 0)
        command, effort, ids, session, prompt = self.calls[0]
        self.assertEqual(effort, 'ultracode')
        self.assertNotIn('--disallowedTools', command)
        settings = json.loads(command[command.index('--settings') + 1])
        self.assertTrue(settings['ultracode'])
        self.assertNotIn('disableWorkflows', settings, 'não deve reabilitar workflows desativados pelo usuário')
        record = self.state()['history'][0]
        self.assertTrue(record['ultracode'])
        self.assertEqual(record['requested_reasoning_effort'], 'xhigh')
        self.assertEqual(self.main('run'), 0)
        command = self.calls[1][0]
        settings = json.loads(command[command.index('--settings') + 1])
        self.assertFalse(settings['ultracode'])
        self.assertTrue(settings['disableWorkflows'])
        self.assertEqual(self.calls[1][1], 'medium')
        self.assertEqual(self.calls[1][3], session)
        self.assertEqual(plan.read_json(self.root / plan.CONFIG), self.config)

    def test_invalid_block_or_unscoped_override_never_starts_cli(self):
        for args in [('run', '--effort', 'max'),
                     ('run', '--effort', 'ultracode'),
                     ('run', '--block', 'bloco-inexistente')]:
            with self.subTest(args=args):
                self.assertEqual(self.main(*args), 1)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / plan.STATE_DIR).exists())

    def test_override_dry_run_does_not_change_map_or_create_state(self):
        self.assertEqual(self.main('run', '--block', '1-comandos', '--effort', 'ultracode', '--dry-run'), 0)
        self.assertIn('1-comandos: ultracode', self.silent.getvalue())
        self.assertEqual(plan.read_json(self.root / plan.CONFIG), self.config)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / plan.STATE_DIR).exists())

    def write_legacy_state(self, digest):
        state = {'version': 1, 'digest': digest, 'session_id': 'original-session',
                 'session_started': True, 'calls': 1,
                 'items': {i: item(i) for i in self.config['batches'][0]['items']},
                 'history': [{'block': '0-protecao', 'requested_model': 'claude-sonnet-5',
                              'requested_effort': 'high', 'status': 'reported'}]}
        plan.atomic_json(self.root / plan.STATE_DIR / 'state.json', state)
        return state

    def test_legacy_migration_preserves_completed_items_session_and_history(self):
        for digest in sorted(plan.LEGACY_SONNET_DIGESTS):
            with self.subTest(digest=digest):
                self.calls.clear()
                before = self.write_legacy_state(digest)
                self.assertEqual(self.main('run'), 0)
                after = self.state()
                self.assertEqual(len(self.calls), 18)
                self.assertTrue(all(call[3] == before['session_id'] for call in self.calls))
                self.assertIn('--resume', self.calls[0][0])
                for item_id, value in before['items'].items():
                    self.assertEqual(after['items'][item_id], value)
                self.assertEqual(after['history'][0], before['history'][0])
                self.assertEqual(after['version'], 3)
                self.assertEqual(after['profile_updates'][0]['from_digest'], digest)
                self.assertEqual(after['profile_updates'][0]['to_digest'], self.digest)
                self.assertEqual(self.main('run'), 0)
                self.assertEqual(len(self.state()['profile_updates']), 1)

    def test_legacy_migration_does_not_accept_unpublished_plan_map_or_prompt(self):
        for path in ('docs/plano-100.md', str(plan.CONFIG), self.config['prompt']):
            with self.subTest(path=path):
                before = self.write_legacy_state(sorted(plan.LEGACY_SONNET_DIGESTS)[0])
                target = self.root / path
                original = target.read_text(encoding='utf-8')
                if path == str(plan.CONFIG):
                    edited = json.loads(original)
                    edited['batches'][0]['effort'] = 'medium'
                    target.write_text(json.dumps(edited), encoding='utf-8')
                else:
                    target.write_text(original + '\nInstrução de trabalho diferente.\n', encoding='utf-8')
                try:
                    self.assertEqual(self.main('run'), 1)
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.state(), before)
                finally:
                    target.write_text(original, encoding='utf-8')

    def test_unknown_checkpoint_digest_is_not_migrated(self):
        before = self.write_legacy_state('unknown-digest')
        self.assertEqual(self.main('run'), 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), before)

    def test_terminal_newlines_do_not_invalidate_published_profile(self):
        before = self.write_legacy_state(sorted(plan.LEGACY_SONNET_DIGESTS)[0])
        for path in (self.config['plan'], self.config['prompt']):
            target = self.root / path
            target.write_text(target.read_text(encoding='utf-8').rstrip('\n') + '\n\n\n', encoding='utf-8')
        self.assertEqual(self.main('run', '--block', '0-ajustes'), 0)
        self.assertEqual(self.state()['session_id'], before['session_id'])
        self.assertEqual(self.state()['digest'], self.digest)

    def test_cli_without_requested_effort_fails_before_any_model_call(self):
        for effort in ('xhigh', 'max', 'ultracode'):
            with self.subTest(effort=effort):
                help_result = subprocess.CompletedProcess(
                    ['claude', '--help'], 0,
                    '--effort low medium high --json-schema --session-id --resume --max-turns', '')
                with patch.object(plan, 'check_cli', REAL_CHECK_CLI), \
                        patch.object(plan.shutil, 'which', return_value='/fake/claude'), \
                        patch.object(plan.subprocess, 'run', return_value=help_result):
                    self.assertEqual(self.main('run', '--block', '0-protecao', '--effort', effort), 1)
                self.assertEqual(self.calls, [])
                self.assertFalse((self.root / plan.STATE_DIR).exists())

    def test_opus_manual_checkpoint_migrates_without_repeating_completed_blocks(self):
        before = self.write_legacy_state(plan.OPUS_PROFILE_DIGEST)
        before['version'] = 2
        plan.atomic_json(self.root / plan.STATE_DIR / 'state.json', before)
        self.assertEqual(self.main(), 0)
        self.assertEqual(len(self.calls), 18)
        after = self.state()
        self.assertEqual(after['version'], 3)
        self.assertEqual(after['session_id'], before['session_id'])
        self.assertEqual(after['profile_updates'][0]['from_digest'], plan.OPUS_PROFILE_DIGEST)

    def test_unresolved_technical_block_does_not_prevent_other_blocks(self):
        def stuck(result):
            if result['structured_output']['items'][0]['id'] == '0.1':
                for entry in result['structured_output']['items']:
                    entry['status'] = 'partial'
                result['structured_output']['progress'] = False
            return result
        self.mutate = stuck
        self.assertEqual(self.main(), 2)
        self.assertEqual([call[1] for call in self.calls[:3]], ['xhigh', 'max', 'medium'])
        self.assertEqual(len(self.calls), 20)
        self.assertEqual(sum(i['status'] == 'implemented' for i in self.state()['items'].values()), 63)
        self.assertNotIn('ultracode', [call[1] for call in self.calls])

    def test_workflow_is_not_repeated_when_diagnosis_does_not_progress(self):
        def reorganize(result):
            for entry in result['structured_output']['items']:
                entry['status'] = 'partial'
            result['structured_output'].update(progress=False, next_action='workflow')
            return result
        self.mutate = reorganize
        self.assertEqual(self.main('--block', '0-protecao'), 2)
        self.assertEqual([call[1] for call in self.calls], ['xhigh', 'ultracode', 'max'])
        self.assertEqual(self.main('--block', '0-protecao'), 2)
        self.assertEqual([call[1] for call in self.calls].count('ultracode'), 1)

    def test_disabled_workflows_fall_back_to_reasoning_without_changing_settings(self):
        path = self.root / '.claude/settings.local.json'
        path.write_text('{"disableWorkflows": true}', encoding='utf-8')
        def request_workflow(result):
            if len(self.calls) == 1:
                for entry in result['structured_output']['items']:
                    entry['status'] = 'partial'
                result['structured_output'].update(progress=False, next_action='workflow')
            return result
        self.mutate = request_workflow
        self.assertEqual(self.main('--block', '0-protecao'), 0)
        self.assertEqual([call[1] for call in self.calls], ['xhigh', 'max'])
        self.assertEqual(path.read_text(), '{"disableWorkflows": true}')
        self.assertTrue(all('--disallowedTools' in call[0] for call in self.calls))

    def test_missing_optional_mode_does_not_require_user_to_choose_another(self):
        self.mutate = lambda result: {
            **result, 'structured_output': {
                **result['structured_output'], 'items': [item(i['id'], 'partial') for i in result['structured_output']['items']],
                'progress': False, 'next_action': 'workflow'}}
        with patch.object(plan, 'check_cli', return_value=plan.CliInfo('/fake/claude', frozenset({'medium', 'xhigh'}))):
            self.assertEqual(self.main('--block', '0-protecao'), 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.state()['controls']['0-protecao']['status'], 'needs_attention')

    def test_interruption_preserves_automatically_selected_effort_for_same_command(self):
        def deepen_then_interrupt(result):
            if len(self.calls) == 1:
                for entry in result['structured_output']['items']:
                    entry['status'] = 'partial'
                result['structured_output'].update(progress=False, next_action='reasoning')
            if len(self.calls) == 2:
                self.return_code = 17
            return result
        self.mutate = deepen_then_interrupt
        self.assertEqual(self.main(), 1)
        session = self.state()['session_id']
        self.assertEqual(self.state()['controls']['0-protecao']['next_effort'], 'max')
        self.return_code = 0
        self.mutate = lambda result: result
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.calls[2][1], 'max')
        self.assertEqual(self.calls[2][3], session)

    def test_real_blocker_never_causes_automatic_escalation(self):
        def blocked(result):
            result['structured_output'].update(
                items=[item(i['id'], 'blocked', 'not_run') for i in result['structured_output']['items']],
                progress=False, next_action='workflow', reason='Falta autorização de produção.')
            return result
        self.mutate = blocked
        self.assertEqual(self.main('--block', '0-protecao'), 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], 'xhigh')

    def test_invalid_routing_cannot_trigger_more_spend(self):
        self.mutate = lambda result: {
            **result, 'structured_output': {**result['structured_output'], 'next_action': 'arbitrary-shell-command'}}
        self.assertEqual(self.main(), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.state()['items'], {})

    def test_missing_session_recovers_once_and_keeps_completed_items(self):
        self.assertEqual(self.main('--block', '0-protecao'), 0)
        before = self.state()
        def missing_once(result):
            if len(self.calls) == 2:
                return {'subtype': 'error_during_execution', 'is_error': True,
                        'errors': ['No conversation found with session ID: ' + result['session_id']]}
            return result
        self.mutate = missing_once
        self.assertEqual(self.main(), 0)
        after = self.state()
        self.assertNotEqual(before['session_id'], after['session_id'])
        self.assertEqual(len(after['session_recoveries']), 1)
        self.assertIn('--session-id', self.calls[2][0])
        self.assertEqual(self.calls[1][2], self.calls[2][2])
        for key, value in before['items'].items():
            self.assertEqual(after['items'][key], value)

    def test_missing_session_loop_is_bounded(self):
        self.assertEqual(self.main('--block', '0-protecao'), 0)
        self.mutate = lambda result: {'subtype': 'error_during_execution', 'is_error': True,
            'errors': ['No conversation found with session ID: ' + result['session_id']]}
        self.assertEqual(self.main(), 1)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(self.state()['session_recoveries']), 1)

    def test_turn_limit_resumes_with_same_effort_and_session(self):
        def turn_limit_once(result):
            if len(self.calls) == 1:
                self.return_code = 1
                return {**result, 'subtype': 'error_max_turns', 'is_error': True, 'structured_output': None}
            self.return_code = 0
            return result
        self.mutate = turn_limit_once
        self.assertEqual(self.main('--block', '0-protecao'), 0)
        self.assertEqual([call[1] for call in self.calls], ['xhigh', 'xhigh'])
        self.assertEqual(self.calls[0][3], self.calls[1][3])
        self.assertIn('--resume', self.calls[1][0])

    def test_turn_limit_recovery_is_bounded(self):
        self.return_code = 1
        self.mutate = lambda result: {**result, 'subtype': 'error_max_turns', 'is_error': True, 'structured_output': None}
        self.assertEqual(self.main(), 1)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual({call[1] for call in self.calls}, {'xhigh'})

    def test_budget_auth_quota_and_permission_errors_are_not_escalated_or_retried(self):
        for subtype in ('error_max_budget_usd', 'error_during_execution', 'error_rate_limit', 'error_max_turns'):
            with self.subTest(subtype=subtype):
                self.calls.clear()
                self.return_code = 1
                self.mutate = lambda result: {**result, 'subtype': subtype, 'is_error': True,
                    'permission_denials': [{'tool_name': 'Bash'}] if subtype == 'error_max_turns' else [],
                    'errors': ['authentication, quota or permission unavailable'], 'structured_output': None}
                self.assertEqual(self.main(), 1)
                self.assertEqual(len(self.calls), 1)
                self.assertNotIn('session_recoveries', self.state())
                self.assertEqual(self.state()['items'], {})

    def test_historical_sonnet_totals_do_not_look_like_new_model_usage(self):
        before = self.write_legacy_state(sorted(plan.LEGACY_SONNET_DIGESTS)[0])
        before['history'][0]['model_usage'] = {'claude-sonnet-5': {'inputTokens': 20, 'outputTokens': 5}}
        plan.atomic_json(self.root / plan.STATE_DIR / 'state.json', before)
        def cumulative(result):
            result['modelUsage']['claude-sonnet-5'] = {'inputTokens': 20, 'outputTokens': 5}
            return result
        self.mutate = cumulative
        self.assertEqual(self.main(), 0)
        self.assertEqual(len(self.calls), 18)
        self.assertEqual(self.state()['session_id'], before['session_id'])

    def test_new_usage_of_historical_model_stops_execution(self):
        before = self.write_legacy_state(sorted(plan.LEGACY_SONNET_DIGESTS)[0])
        before['history'][0]['model_usage'] = {'claude-sonnet-5': {'inputTokens': 20}}
        plan.atomic_json(self.root / plan.STATE_DIR / 'state.json', before)
        def cumulative(result):
            result['modelUsage']['claude-sonnet-5'] = {'inputTokens': 21}
            return result
        self.mutate = cumulative
        self.assertEqual(self.main(), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.state()['items'], before['items'])

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

    def test_same_command_rechecks_blocked_items_once_and_preserves_completed_work(self):
        def block_first(result):
            if result['structured_output']['items'][0]['id'] == '0.1':
                result['structured_output']['items'][0] = item('0.1', 'blocked', 'not_run')
            return result
        self.mutate = block_first
        self.assertEqual(self.main('run'), 2)
        self.assertEqual(self.main('run'), 2)
        self.assertEqual(len(self.calls), 20)
        self.assertEqual(self.calls[-1][2], ['0.1'])
        self.mutate = lambda result: result
        self.assertEqual(self.main(), 0)
        self.assertEqual(len(self.calls), 21)
        self.assertEqual(self.calls[-1][2], ['0.1'])

    def test_partial_without_progress_stops_expensive_loop(self):
        def partial(result):
            result['structured_output']['items'] = [item(i['id'], 'partial') for i in result['structured_output']['items']]
            result['structured_output']['progress'] = False
            return result
        self.mutate = partial
        self.assertEqual(self.main('--block', '0-ajustes'), 2)
        self.assertEqual([call[1] for call in self.calls], ['medium', 'xhigh', 'max'])
        self.assertEqual(self.state()['controls']['0-ajustes']['status'], 'needs_attention')

    def test_partial_progress_is_bounded(self):
        def partial(result):
            result['structured_output']['items'] = [item(i['id'], 'partial') for i in result['structured_output']['items']]
            return result
        self.mutate = partial
        self.assertEqual(self.main('--block', '0-ajustes', '--max-rounds', '2'), 2)
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
            result['modelUsage'] = {'claude-sonnet-5': {'inputTokens': 10}}
            return result
        self.mutate = substituted
        self.assertEqual(self.main('run'), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.state()['items'], {})

    def write_cli_substitute(self):
        simulator = self.root / 'fake-claude'
        simulator.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, sys
args = sys.argv[1:]
if '--help' in args:
    print('--effort low medium high xhigh max ultracode --json-schema --session-id --resume --max-turns')
    sys.exit(0)
schema = json.loads(args[args.index('--json-schema') + 1])
ids = schema['properties']['items']['items']['properties']['id']['enum']
flag = '--resume' if '--resume' in args else '--session-id'
session = args[args.index(flag) + 1]
effort = args[args.index('--effort') + 1]
model = args[args.index('--model') + 1]
settings = json.loads(args[args.index('--settings') + 1])
assert model == 'claude-opus-5'
assert os.environ['CLAUDE_CODE_EFFORT_LEVEL'] == ('xhigh' if effort == 'ultracode' else effort)
if effort == 'ultracode':
    assert os.environ['CLAUDE_CODE_SUBAGENT_MODEL'] == model
    assert settings['ultracode'] is True
    assert '--disallowedTools' not in args
else:
    assert settings['ultracode'] is False
    assert settings['disableWorkflows'] is True
    assert args[args.index('--disallowedTools') + 1:args.index('--disallowedTools') + 3] == ['Agent', 'Task']
assert 'MODO RUNNER' in sys.stdin.read()
trace = pathlib.Path('transport.jsonl')
previous = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
status, progress, next_action = 'implemented', True, 'continue'
if os.environ.get('FAKE_CLAUDE_ADAPTIVE') == '1' and ids[0] == '0.1':
    attempt = sum(call.get('first_id') == '0.1' for call in previous)
    if attempt < 3:
        status, progress, next_action = [('partial', False, 'reasoning'),
                                         ('partial', False, 'workflow'),
                                         ('partial', True, 'continue')][attempt]
with pathlib.Path('transport.jsonl').open('a') as stream:
    stream.write(json.dumps({'session': session, 'flag': flag, 'effort': effort, 'model': model, 'first_id': ids[0]}) + '\\n')
items = [dict(id=i, status=status, proof='simulated', evidence='CLI local substituto', blocker='') for i in ids]
print(json.dumps(dict(subtype='success', is_error=False, session_id=session,
    modelUsage={'claude-opus-5': {}}, structured_output=dict(items=items, progress=progress, summary='OK simulado',
        next_action=next_action, reason='Evidência local do cenário de continuidade.'))))
''', encoding='utf-8')
        simulator.chmod(0o700)
        return simulator

    @unittest.skipIf(os.name == 'nt', 'simulador executável usa shebang POSIX')
    def test_real_subprocess_transport_with_local_cli_substitute(self):
        simulator = self.write_cli_substitute()
        with patch.object(plan, 'invoke', REAL_INVOKE), patch.object(plan, 'check_cli', REAL_CHECK_CLI):
            self.assertEqual(self.main('run', '--claude', str(simulator)), 0)
        calls = [json.loads(line) for line in (self.root / 'transport.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 19)
        self.assertEqual(len({call['session'] for call in calls}), 1)
        self.assertEqual(calls[0]['flag'], '--session-id')
        self.assertTrue(all(call['flag'] == '--resume' for call in calls[1:]))
        self.assertEqual([call['effort'] for call in calls[:3]], ['xhigh', 'medium', 'xhigh'])

    @unittest.skipIf(os.name == 'nt', 'simulador executável usa shebang POSIX')
    def test_real_subprocess_ultracode_then_max_then_default(self):
        simulator = self.write_cli_substitute()
        with patch.object(plan, 'invoke', REAL_INVOKE), patch.object(plan, 'check_cli', REAL_CHECK_CLI):
            self.assertEqual(self.main('run', '--claude', str(simulator), '--block', '0-protecao', '--effort', 'ultracode'), 0)
            self.assertEqual(self.main('run', '--claude', str(simulator), '--block', '0-ajustes', '--effort', 'max'), 0)
            self.assertEqual(self.main('run', '--claude', str(simulator)), 0)
        calls = [json.loads(line) for line in (self.root / 'transport.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 19)
        self.assertEqual(len({call['session'] for call in calls}), 1)
        self.assertEqual([call['effort'] for call in calls[:3]], ['ultracode', 'max', 'xhigh'])
        self.assertTrue(all(call['flag'] == '--resume' for call in calls[1:]))

    @unittest.skipIf(os.name == 'nt', 'simulador executável usa shebang POSIX')
    def test_one_command_automatically_routes_all_modes_with_real_subprocess(self):
        simulator = self.write_cli_substitute()
        with patch.object(plan, 'invoke', REAL_INVOKE), patch.object(plan, 'check_cli', REAL_CHECK_CLI), \
                patch.dict(os.environ, {'FAKE_CLAUDE_ADAPTIVE': '1'}):
            self.assertEqual(self.main('--claude', str(simulator)), 0)
        calls = [json.loads(line) for line in (self.root / 'transport.jsonl').read_text().splitlines()]
        self.assertEqual([call['effort'] for call in calls[:5]], ['xhigh', 'max', 'ultracode', 'xhigh', 'medium'])
        self.assertEqual(len(calls), 22)
        self.assertEqual(len({call['session'] for call in calls}), 1)
        self.assertEqual(len(self.state()['items']), 67)
        self.assertTrue(all(record['automatic'] for record in self.state()['history']))


class NativeSkillTests(unittest.TestCase):
    def test_project_skills_have_explicit_native_effort_and_no_delegation(self):
        for name, effort in [('plano-100', 'medium'), ('plano-100-medium', 'medium'), ('plano-100-high', 'xhigh'), ('plano-100-xhigh', 'xhigh'), ('plano-100-max', 'max'), ('plano-100-ultracode', 'medium')]:
            text = (ROOT / '.claude/skills' / name / 'SKILL.md').read_text()
            self.assertTrue(text.startswith('---\n'))
            header = text.split('---', 2)[1]
            self.assertIn(f'effort: {effort}', header)
            self.assertIn('model: claude-opus-5', header)
            self.assertIn('disable-model-invocation: true', header)
            self.assertNotIn('context: fork', header)


if __name__ == '__main__':
    unittest.main()
