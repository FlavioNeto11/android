// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { TeachingPanel } from './TeachingPanel';

// Fase L (usabilidade) do quadro de ensino v2: a carga inicial nunca oferece "Gerar" antes de saber se a gravação
// já tem ensino (P1.5), "Descartar" existe e confirma (P1.2), e os estados seguem os padrões do painel (P2.2).

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const DOC = {
  apiVersion: 'automation/v1alpha1', kind: 'Skill',
  metadata: { id: 'qa-messenger.mandar_mensagem', name: 'Mandar mensagem', app: 'qa-messenger' },
  spec: { invocation: { command_template: 'Mandar mensagem — contato: {contato}' },
          nodes: [{ id: 'abrir', goal: { title: 'Abrir a conversa', goal: 'abrir' } }] },
};
const ANOT = {
  evidence: {}, discarded: [], assumptions: [], preconditions: [], postconditions: [], suggested_proofs: [],
  parameters: [], effects: [], risks: ['Etapa “abrir” conferida pelo modelo.'],
};
function candidata(seq: number, status = 'proposed') {
  return { id: `cand-${seq}`, teaching_id: 'ens-1', seq, status, validation_status: 'none', generated_by: 'ai:simulado',
           content_hash: 'h', version_id: null, document: DOC, annotations: ANOT, created_at: '', updated_at: '' };
}
function ensino(status: string, extra: Record<string, unknown> = {}) {
  return { id: 'ens-1', instruction: 'Mandar mensagem', skill_id: null, base_version: null, app_id: 'qa-messenger',
           profile_id: null, status, validation_status: 'none', result_version_id: null, operator: null,
           created_at: '', updated_at: '', closed_at: null, source: 'hybrid', demonstrations: [], turns: [],
           candidates: [], current_candidate: null, open_questions: [], errors: [], ...extra };
}
const RESUMO = { id: 'ens-1', status: 'asking' };

async function render(): Promise<void> {
  await act(async () => root.render(<><TeachingPanel trainingSessionId="trn-1" intent="Mandar mensagem" appId="qa-messenger" /><ConfirmHost /></>));
}

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it('P1.5: enquanto procura o ensino da gravação, "Gerar" não aparece; se a busca falha, há erro com "Tentar de novo"', async () => {
  let responder: ((r: Response) => void) | null = null;
  backend.on('GET', /\/teaching-sessions$/, () => new Promise<Response>((r) => { responder = r; }));
  await render();
  await waitFor(() => expect(responder).not.toBeNull());
  expect(text()).toContain('Procurando o ensino desta gravação');
  expect(allByRole('button', /Gerar candidata/)).toHaveLength(0);

  responder!(apiError(503, 'db_unavailable', 'banco fora do ar'));
  await waitFor(() => expect(text()).toContain('Não foi possível saber se esta gravação já tem ensino'));
  expect(text()).toContain('banco fora do ar');
  expect(allByRole('button', /Gerar candidata/)).toHaveLength(0);
  expect(backend.callsTo('POST', /\/teaching-sessions$/)).toHaveLength(0);

  backend.on('GET', /\/teaching-sessions$/, () => json([]));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => expect(allByRole('button', /Gerar candidata de habilidade/)).toHaveLength(1));
  expect(backend.callsTo('GET', /\/teaching-sessions$/)).toHaveLength(2);
});

it('P1.5: com um ensino já ligado à gravação, o quadro o reencontra e não oferece "Gerar" (que criaria um segundo)', async () => {
  backend.on('GET', /\/teaching-sessions$/, () => json([RESUMO]));
  backend.on('GET', /\/teaching-sessions\/ens-1$/, () => json(ensino('validating', { current_candidate: candidata(1) })));
  await render();
  await waitFor(() => expect(text()).toContain('Salvar como rascunho'));
  expect(allByRole('button', /Gerar candidata/)).toHaveLength(0);
  expect(text()).toContain('a validar');
  expect(text()).toMatch(/candidata 1 .*proposta/);
  expect(text()).not.toContain('proposed');
});

it('P1.2: "Descartar" pede confirmação e chama a rota; descartado é terminal e não volta a oferecer "Gerar"', async () => {
  backend.on('GET', /\/teaching-sessions$/, () => json([RESUMO]));
  backend.on('GET', /\/teaching-sessions\/ens-1$/, () => json(ensino('open', { current_candidate: candidata(1, 'superseded') })));
  backend.on('POST', /\/teaching-sessions\/ens-1\/discard$/, () => json(ensino('discarded', { closed_at: 'x' })));
  await render();
  await waitFor(() => expect(allByRole('button', /^Descartar/)).toHaveLength(1));

  await click(byRole('button', /^Descartar/));
  await waitFor(() => expect(text()).toContain('Descartar este ensino?'));
  await click(byRole('button', /^Cancelar$/, byRole('dialog', /Descartar este ensino/)));
  await waitFor(() => expect(allByRole('dialog', /Descartar este ensino/)).toHaveLength(0));
  expect(backend.callsTo('POST', /discard$/)).toHaveLength(0);

  await click(byRole('button', /^Descartar/));
  await waitFor(() => expect(text()).toContain('Descartar este ensino?'));
  await click(byRole('button', /^Descartar ensino$/, byRole('dialog', /Descartar este ensino/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/teaching-sessions\/ens-1\/discard$/)).toHaveLength(1));
  await waitFor(() => expect(text()).toContain('Ensino descartado'));
  expect(text()).toContain('descartado');
  expect(allByRole('button', /^Descartar/)).toHaveLength(0);
  expect(allByRole('button', /Gerar candidata/)).toHaveLength(0);
});

it('P2.2: a validação que não compila mostra a recusa num aviso; riscos ficam num aviso; pergunta sem texto tem fallback', async () => {
  const semTexto = { id: 9, kind: 'ambiguity', key: 'ai:abc123def456', origin: 'ai', text: null, target: null, candidate_id: 'cand-2' };
  backend.on('GET', /\/teaching-sessions$/, () => json([RESUMO]));
  backend.on('GET', /\/teaching-sessions\/ens-1$/, () => json(ensino('validating', { current_candidate: candidata(2) })));
  backend.on('POST', /\/skill-candidates\/cand-2\/validate$/, () => json(ensino('open', {
    current_candidate: candidata(2, 'rejected'),
    turns: [{ id: 5, kind: 'note', author: 'compiler', reply_to: null, candidate_id: 'cand-2', created_by: null, created_at: '',
              body: 'A candidata não compila:\nE_CAPABILITY_REQUIRED /spec/nodes/0: abrir precisa de capability' }],
  })));
  backend.on('POST', /\/teaching-sessions\/ens-1\/candidates$/, () => json(ensino('asking', { current_candidate: candidata(3), open_questions: [semTexto] })));
  await render();
  await waitFor(() => expect(text()).toContain('Salvar como rascunho'));
  expect(byRole('note', /Riscos/).textContent).toContain('conferida pelo modelo');

  await click(byRole('button', /Salvar como rascunho/));
  await waitFor(() => expect(text()).toContain('Candidata 2 recusada'));
  const aviso = byRole('alert', /Candidata 2 recusada/);
  expect(aviso.textContent).toContain('E_CAPABILITY_REQUIRED /spec/nodes/0');
  expect(text()).toContain('recusada');
  expect(backend.callsTo('POST', /publish$/)).toHaveLength(0);           // reprovada: nada vira rascunho
  expect(allByRole('button', /Salvar como rascunho/)).toHaveLength(0);

  await click(byRole('button', /Pedir outra candidata/));
  await waitFor(() => expect(text()).toContain('Pergunta sem texto (ai:abc123def456)'));
  const resposta = byRole('textbox', /Resposta à pergunta: Pergunta sem texto/) as HTMLInputElement;
  await setValue(resposta, 'É a conversa do cliente.');
  await click(byRole('button', /^Responder/));
  await waitFor(() => expect(backend.callsTo('POST', /answers$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /answers$/)[0]!.body).toEqual({ question_id: 9, body: 'É a conversa do cliente.' });
});
