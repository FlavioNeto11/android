#!/usr/bin/env python3
"""Execute os blocos do plano com esforço explícito; somente biblioteca padrão."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path('.claude/plano-100.json')
STATE_DIR = Path('.claude/plano-100')
REPORT = Path('docs/execucao-plano-100-runner.md')
EFFORTS = {'medium', 'high'}
STATUSES = {'implemented', 'partial', 'blocked'}
PROOFS = {'real', 'simulated', 'not_run'}


class PlanError(Exception):
    pass


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def load_config(root):
    config = read_json(root / CONFIG)
    plan = (root / config['plan']).read_text(encoding='utf-8')
    ids = re.findall(r'^\|\s*((?:\d+|T)\.\d+)\s*\|', plan, re.M)
    assigned = []
    names = set()
    for batch in config['batches']:
        if batch['effort'] not in EFFORTS or not re.fullmatch(r'[\w-]+', batch['id']):
            raise PlanError('Bloco ou esforço inválido na configuração.')
        if batch['id'] in names or not batch['items']:
            raise PlanError('Bloco duplicado ou vazio.')
        names.add(batch['id'])
        assigned.extend(batch['items'])
    if not ids or len(ids) != len(set(ids)) or len(assigned) != len(set(assigned)) or set(ids) != set(assigned):
        raise PlanError('O mapa de blocos não cobre exatamente os IDs atuais do plano; revise a configuração.')
    if not (root / config['prompt']).is_file():
        raise PlanError('Prompt de execução não encontrado.')
    prompt = (root / config['prompt']).read_text(encoding='utf-8')
    digest = hashlib.sha256((json.dumps(config, sort_keys=True) + plan + prompt).encode()).hexdigest()
    return config, ids, digest


def result_schema(ids):
    fields = {
        'id': {'type': 'string', 'enum': ids},
        'status': {'type': 'string', 'enum': sorted(STATUSES)},
        'proof': {'type': 'string', 'enum': sorted(PROOFS)},
        'evidence': {'type': 'string'},
        'blocker': {'type': 'string'},
    }
    return {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'items': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                      'properties': fields, 'required': list(fields)}},
            'summary': {'type': 'string'},
            'progress': {'type': 'boolean'},
        }, 'required': ['items', 'summary', 'progress'],
    }


def validate_result(envelope, ids, session_id):
    if not isinstance(envelope, dict) or envelope.get('is_error') or envelope.get('subtype') != 'success':
        raise PlanError('Claude não concluiu a chamada com sucesso; confira os logs locais.')
    if envelope.get('permission_denials'):
        raise PlanError('Há permissões recusadas. Resolva-as na sessão interativa e retome; nenhuma permissão foi ampliada.')
    if envelope.get('session_id') != session_id:
        raise PlanError('O resultado pertence a outra sessão; progresso não aplicado.')
    result = envelope.get('structured_output')
    if not isinstance(result, dict) or not isinstance(result.get('items'), list):
        raise PlanError('Resultado estruturado ausente; progresso não aplicado.')
    if not isinstance(result.get('progress'), bool) or not isinstance(result.get('summary'), str):
        raise PlanError('Resumo/progresso inválido.')
    seen = []
    for item in result['items']:
        if not isinstance(item, dict) or item.get('status') not in STATUSES or item.get('proof') not in PROOFS:
            raise PlanError('Estado inválido de item.')
        if not isinstance(item.get('id'), str):
            raise PlanError('ID inválido.')
        seen.append(item['id'])
        if not isinstance(item.get('evidence'), str) or not isinstance(item.get('blocker'), str):
            raise PlanError('Evidência/bloqueio inválido.')
        if item['status'] == 'implemented' and not item['evidence'].strip():
            raise PlanError('Implementação sem evidência.')
        if item['status'] == 'blocked' and not item['blocker'].strip():
            raise PlanError('Bloqueio sem motivo.')
        if item['proof'] != 'not_run' and not item['evidence'].strip():
            raise PlanError('Prova sem evidência.')
    if len(seen) != len(set(seen)) or set(seen) != set(ids):
        raise PlanError('Resultado omite, duplica ou acrescenta IDs; progresso não aplicado.')
    return result


def build_command(cli, config, batch, ids, state, args):
    command = [cli, '-p', '--model', config['model'], '--effort', batch['effort'],
               '--output-format', 'json', '--json-schema', json.dumps(result_schema(ids)),
               '--max-turns', str(args.max_turns), '--permission-mode', args.permission_mode,
               '--settings', json.dumps({'ultracode': False, 'switchModelsOnFlag': False}),
               '--disallowedTools', 'Agent', 'Task']
    if state['session_started']:
        command += ['--resume', state['session_id']]
    else:
        command += ['--session-id', state['session_id']]
    if args.max_budget_usd_per_call is not None:
        command += ['--max-budget-usd', str(args.max_budget_usd_per_call)]
    return command


def child_environment(effort):
    environment = os.environ.copy()
    # Coerência com --effort mesmo se o terminal herdou um esforço fixo.
    # Não alterar o ambiente pai, controles de permissão ou limites administrados.
    environment['CLAUDE_CODE_EFFORT_LEVEL'] = effort
    return environment


def make_prompt(config, batch, ids):
    return f'''Execute o bloco {batch['id']} do plano, somente os IDs: {', '.join(ids)}.
Modelo solicitado: {config['model']}; esforço solicitado pelo CLI: {batch['effort']}.
Siga {config['prompt']} no MODO RUNNER. Leia o plano inteiro apenas se ainda não
estiver no contexto; depois use só achados/arquivos relevantes. Consulte o checkpoint.
Confira dependências antes de implementar; bloqueio operacional não dispensa código
e testes isolados. T.1–T.3 acompanham as entregas sem ampliar os IDs desta resposta.
Não invoque outras skills, subagentes ou sessões Claude. Não altere o mapa, este
executor, seu estado ou relatório gerado para conseguir passar um gate.
Ao terminar este bloco, retorne o resultado estruturado e encerre a chamada:
o executor inicia o bloco seguinte com o esforço correspondente. Para cada ID,
use implemented apenas com implementação pronta e evidência verificável; partial
para trabalho restante; blocked com motivo concreto. proof distingue real,
simulated e not_run. Real exige máquina/data/IDs ou referência ao registro real.
Informe progress=true somente se houve avanço concreto. Aponte o que mudou,
testes realmente executados e pendências em summary. Não declare fase encerrada
se faltar prova real. Preserve decisões, credenciais e limites do plano.
'''


def invoke(command, prompt, root, effort, log_prefix):
    with log_prefix.with_suffix('.json').open('w', encoding='utf-8') as output, \
            log_prefix.with_suffix('.stderr.log').open('w', encoding='utf-8') as error:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=output,
                                   stderr=error, text=True, encoding='utf-8', env=child_environment(effort))
        try:
            process.stdin.write(prompt)
            process.stdin.close()
            while True:
                try:
                    return process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    print('  Claude continua trabalhando; saída completa nos logs locais.', flush=True)
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise


@contextmanager
def execution_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'run.lock'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PlanError(f'Outro executor pode estar ativo. Confira {path}; remova o lock apenas após confirmar que terminou.') from exc
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def pending_ids(batch, state, retry_blocked=False):
    skipped = {'implemented'} if retry_blocked else {'implemented', 'blocked'}
    return [item for item in batch['items'] if state['items'].get(item, {}).get('status') not in skipped]


def render_report(root, ids, state):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    lines = ['# Execução do plano pelo CLI', '',
             'Gerado a partir do resultado informado pelo executor; evidências em `relatorio-validacao.md`.',
             'Implementação pronta não significa aceite real concluído.', '',
             '| Item | Implementação | Prova | Evidência | Bloqueio |', '|---|---|---|---|---|']
    for item_id in ids:
        item = state['items'].get(item_id, {})
        values = [item_id, item.get('status', 'pending'), item.get('proof', 'not_run'),
                  item.get('evidence', ''), item.get('blocker', '')]
        lines.append('| ' + ' | '.join(map(cell, values)) + ' |')
    lines += ['', 'Último resumo: ' + cell(state.get('summary', 'Ainda não executado.')), '']
    path = root / REPORT
    temporary = path.with_suffix('.tmp')
    temporary.write_text('\n'.join(lines), encoding='utf-8')
    os.replace(temporary, path)


def check_cli(cli):
    found = shutil.which(cli)
    if not found:
        raise PlanError('Claude Code não encontrado. Instale/atualize, autentique com claude e tente novamente.')
    check = subprocess.run([found, '--help'], capture_output=True, text=True, encoding='utf-8', timeout=30)
    required = ('--effort', '--json-schema', '--session-id', '--resume', '--max-turns')
    if check.returncode or any(flag not in check.stdout for flag in required):
        raise PlanError('Este Claude Code não oferece as opções necessárias. Atualize com claude update.')
    return found


def run(root, config, ids, digest, args):
    if os.environ.get('CLAUDECODE'):
        raise PlanError('Execute em um terminal externo ao agente Claude; sessões aninhadas não são iniciadas.')
    if os.environ.get('CLAUDE_CODE_SKIP_PROMPT_HISTORY', '').lower() in {'1', 'true', 'yes'}:
        raise PlanError('A persistência de sessão está desativada; habilite-a antes de usar a retomada.')
    cli = check_cli(args.claude)
    directory = root / STATE_DIR
    with execution_lock(directory):
        state_path = directory / 'state.json'
        if state_path.exists():
            state = read_json(state_path)
            if state.get('digest') != digest:
                raise PlanError('Plano/mapa/prompt mudou desde a execução. Reconcilie os checkpoints antes de criar um estado novo.')
        else:
            state = {'version': 1, 'digest': digest, 'session_id': str(uuid.uuid4()),
                     'session_started': False, 'items': {}, 'calls': 0, 'history': []}
        if args.fresh_session:
            state['session_id'] = str(uuid.uuid4())
            state['session_started'] = False
        atomic_json(state_path, state)
        for batch in config['batches']:
            targets = pending_ids(batch, state, args.retry_blocked)
            if not targets:
                continue
            for attempt in range(args.max_rounds):
                state['calls'] += 1
                stamp = f"{state['calls']:04d}-{batch['id']}"
                prefix = directory / stamp
                command = build_command(cli, config, batch, targets, state, args)
                record = {'block': batch['id'], 'items': targets, 'requested_model': config['model'],
                          'requested_effort': batch['effort'], 'status': 'running', 'log': stamp,
                          'started_at': datetime.now(timezone.utc).isoformat()}
                state['history'].append(record)
                # Reserve o ID antes do processo: uma interrupção pode deixar trabalho e sessão reais.
                state['session_started'] = True
                atomic_json(state_path, state)
                print(f"[{batch['id']}] {config['model']} / {batch['effort']} / {', '.join(targets)}", flush=True)
                try:
                    code = invoke(command, make_prompt(config, batch, targets), root, batch['effort'], prefix)
                    if code:
                        raise PlanError(f'Claude terminou com código {code}. Confira {prefix.name}; o bloco permanece pendente.')
                    envelope = read_json(prefix.with_suffix('.json'))
                    result = validate_result(envelope, targets, state['session_id'])
                    usage = envelope.get('modelUsage')
                    if not isinstance(usage, dict) or not usage:
                        raise PlanError('CLI não informou utilização por modelo; confira a sessão antes de continuar.')
                    if any(model != config['model'] and not model.startswith(config['model'] + '-') for model in usage):
                        raise PlanError('CLI informou modelo diferente do solicitado. Confira a configuração; não haverá outra chamada automática.')
                    record.update(status='reported', model_usage=envelope.get('modelUsage', {}),
                                  reported_cost_usd=envelope.get('total_cost_usd'))
                    state['items'].update({item['id']: item for item in result['items']})
                    state['summary'] = result['summary']
                    atomic_json(state_path, state)
                    render_report(root, ids, state)
                    print(result['summary'], flush=True)
                except BaseException:
                    record['status'] = 'interrupted_or_error'
                    atomic_json(state_path, state)
                    raise
                targets = pending_ids(batch, state)
                if not targets:
                    break
                if not result['progress']:
                    raise PlanError('O bloco não avançou. Checkpoint preservado; diagnostique antes de gastar outra chamada.')
            else:
                raise PlanError('Limite de rodadas deste bloco atingido. Retome o mesmo comando após revisar o checkpoint.')
        blocked = [key for key, value in state['items'].items() if value['status'] == 'blocked']
        print('Blocos processados. Confira implementação e provas em ' + str(REPORT))
        if blocked:
            print('Itens bloqueados: ' + ', '.join(blocked) + '. Após resolver, use --retry-blocked.')
        return 2 if blocked else 0


def positive_int(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError('Informe um inteiro positivo.')
    return result


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'run'))
    parser.add_argument('--dry-run', action='store_true', help='Mostrar blocos sem chamar Claude nem escrever estado.')
    parser.add_argument('--claude', default='claude', help='Executável local do Claude Code.')
    parser.add_argument('--permission-mode', choices=('default', 'acceptEdits', 'auto'), default='acceptEdits')
    parser.add_argument('--max-turns', type=positive_int, default=80)
    parser.add_argument('--max-rounds', type=positive_int, default=3)
    parser.add_argument('--max-budget-usd-per-call', type=float, help='Teto do CLI por chamada; não é orçamento total do plano.')
    parser.add_argument('--retry-blocked', action='store_true')
    parser.add_argument('--fresh-session', action='store_true', help='Nova conversa mantendo o progresso; use só para recuperar sessão indisponível.')
    args = parser.parse_args(argv)
    try:
        if args.max_budget_usd_per_call is not None and not (0 < args.max_budget_usd_per_call < float('inf')):
            raise PlanError('Orçamento por chamada deve ser positivo e finito.')
        config, ids, digest = load_config(root)
        if args.dry_run or args.action == 'check':
            print(f"{len(ids)} itens; {len(config['batches'])} blocos; modelo {config['model']}.")
            for batch in config['batches']:
                print(f"{batch['id']}: {batch['effort']} -> {', '.join(batch['items'])}")
            print('Somente validação local; nenhuma chamada à IA.')
            return 0
        return run(root, config, ids, digest, args)
    except KeyboardInterrupt:
        print('Interrompido. Confira o checkpoint e retome com o mesmo comando.', file=sys.stderr)
        return 130
    except (PlanError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print('Execução interrompida: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
