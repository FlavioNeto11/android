// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { TrainingReview } from './TrainingReview';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const SESSAO = {
  id: 'trn-1', instance_id: 'android-01', profile_id: 'ig-1', app_id: 'qa-messenger', intent: 'Mandar mensagem',
  status: 'recorded', operator: null, proposal: null, flow_id: null, created_at: '', finished_at: '', updated_at: '',
  inputs: [
    { session_id: 'trn-1', seq: 1, ts: '', type: 'tap', x: 10, y: 10, x2: null, y2: null, key_name: null, text: null,
      has_text: false, text_len: null, package: 'com.pocqa.messenger', app_id: null,
      target: { text: 'QA-001', resource_id: 'x:id/conversation_name', unique: ['rid+text'] }, screen_title: 'Conversas',
      screen_lines: ['QA-001'], sensitive: false },
    { session_id: 'trn-1', seq: 2, ts: '', type: 'text', x: null, y: null, x2: null, y2: null, key_name: null, text: null,
      has_text: true, text_len: 12, package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null,
      screen_lines: [], sensitive: false },
  ],
};

const PROPOSTA = {
  summary: 'Mandar mensagem', command_template: 'mande para {contato}', app_id: 'qa-messenger',
  parameters: [{ name: 'contato', example: 'QA-001', description: '' }], discarded: [], questions: ['O texto muda?'],
  steps: [{ key: 'abrir', title: 'Abrir a conversa com {contato}', goal: 'abrir', inputs: [1], side_effect: false,
            capability: null, bindings: [], app_id: null,
            postcondition: { kind: 'text_visible', value: '{contato}', description: 'aberta' } }],
};

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training\/trn-1$/, () => json(SESSAO));
  backend.on('GET', /\/instagram\/profiles$/, () => json([{ id: 'ig-1', username: 'aluno.um' }, { id: 'ig-2', username: 'aluno.dois' }]));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('POST', /\/training\/trn-1\/propose$/, () => json({ ...SESSAO, status: 'proposed', proposal: PROPOSTA }));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem',
    steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }],
  }));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it('mostra a gravação (texto sigiloso sem conteúdo), pede a proposta, e salva com o escopo escolhido', async () => {
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('QA-001'));
  expect(text()).toContain('(sigiloso)');
  await click(byRole('button', /Pedir proposta à IA/i));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  expect(text()).toContain('{contato} = QA-001');
  await click(byRole('checkbox', /@aluno.dois/i));
  await click(byRole('button', /Salvar habilidade/i));
  await waitFor(() => expect(text()).toContain('mandar-mensagem'));
  const corpo = backend.callsTo('POST', /\/save$/)[0]!.body as { profile_ids: string[] };
  expect(corpo.profile_ids.sort()).toEqual(['ig-1', 'ig-2']);           // o perfil do aparelho já vem marcado
  expect(text()).toContain('sem IA');
});
