// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { FlowsRecipesSection } from './FlowsRecipesSection';

// Fase F: a lista de habilidades versionadas só aparece com `health.features.skills`. Desligado (o padrão), a seção
// é a de sempre — nem a lista nem a chamada a `/api/skills`.

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

function comHabilidades(ligado: boolean | undefined): void {
  const health = makeSnapshot().health;
  useAppStore.setState({ ...initialDataState, health: { ...health, features: { ...health.features, skills: ligado } } });
}

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/flows$/, () => json([]));
  backend.on('GET', /\/flows\/cobertura$/, () => json([]));
  backend.on('GET', /\/recipes$/, () => json([]));
  backend.on('GET', /\/skills$/, () => json([
    { ref: 'qa-messenger.mandar_mensagem@1', skill_id: 'qa-messenger.mandar_mensagem', version: 1,
      name: 'Mandar mensagem no QA', app_id: 'qa-messenger', state: 'draft',
      command_template: 'Mandar mensagem — contato: {contato}', schema_version: 1, content_hash: 'h', intact: true,
      state_at: '2026-09-27T12:00:00.000Z' },
  ]));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
});

it('desligado (padrão): fluxos e receitas como sempre, sem habilidades nem chamada a /api/skills', async () => {
  comHabilidades(undefined);
  await act(async () => root.render(<FlowsRecipesSection />));
  await waitFor(() => expect(text()).toContain('Nenhum fluxo salvo ainda'));
  expect(text()).not.toContain('Habilidades');
  expect(backend.callsTo('GET', /\/skills$/)).toHaveLength(0);
});

it('ligado: lista as habilidades com o estado de cada versão', async () => {
  comHabilidades(true);
  await act(async () => root.render(<FlowsRecipesSection />));
  await waitFor(() => expect(text()).toContain('Mandar mensagem no QA'));
  expect(text()).toContain('Habilidades (1)');
  expect(text()).toContain('rascunho');
  expect(text()).toContain('qa-messenger.mandar_mensagem@1');
  expect(backend.callsTo('GET', /\/skills$/)).toHaveLength(1);
});

// ---- fase J: converter fluxo e desfazer; transições de versão (atrás de `features.skills`) ---------------------

function fluxo(status: 'active' | 'disabled' = 'active') {
  return { id: 'abrir-conversa', name: 'Abrir a conversa', command_template: 'abra a conversa com {username} no instagram',
    app_id: 'instagram', source_run_id: 'r-1', status, uses: 3, created_at: '2026-09-27T10:00:00Z', last_used_at: null };
}

function versao(version: number, state: string, extra: Record<string, unknown> = {}) {
  return { ref: `instagram.abrir-conversa@${version}`, skill_id: 'instagram.abrir-conversa', version,
    name: 'Abrir a conversa', app_id: 'instagram', state, command_template: 'abra a conversa com {username} no instagram',
    schema_version: version === 1 ? 0 : 1, content_hash: 'h', intact: true, state_at: '2026-09-27T12:00:00.000Z',
    legacy_flow_id: 'abrir-conversa', ...extra };
}

async function renderComDialogo(): Promise<void> {
  await act(async () => root.render(<><FlowsRecipesSection /><ConfirmHost /></>));
}

it('desligado: um fluxo ativo não ganha "converter em habilidade" nem ação de versão', async () => {
  comHabilidades(false);
  backend.on('GET', /\/flows$/, () => json([fluxo()]));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('Abrir a conversa'));
  expect(allByRole('button', /Converter o fluxo/)).toHaveLength(0);
  expect(allByRole('button', /Desfazer a conversão/)).toHaveLength(0);
  expect(backend.callsTo('GET', /\/skills$/)).toHaveLength(0);
});

it('ligado: converter o fluxo ativo pede confirmação, chama a rota e recarrega as duas listas', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([fluxo()]));
  backend.on('GET', /\/skills$/, () => json([]));
  backend.on('POST', /\/flows\/abrir-conversa\/adopt$/, () => json({
    flow_id: 'abrir-conversa', skill_id: 'instagram.abrir-conversa', published: versao(1, 'published'),
    draft: versao(2, 'draft'), warnings: [{ code: 'W_NO_VALIDATION_CASE', message: 'sem caso', path: '/spec/validation',
      severity: 'warning', origin: 'document' }] }, 201));
  await renderComDialogo();
  await waitFor(() => expect(allByRole('button', /Converter o fluxo Abrir a conversa em habilidade/)).toHaveLength(1));

  await click(byRole('button', /Converter o fluxo Abrir a conversa em habilidade/));
  await waitFor(() => expect(text()).toContain('A versão 1 da habilidade é o plano deste fluxo'));
  await setValue(byRole('textbox', /Motivo/, byRole('dialog', /Converter/)) as HTMLTextAreaElement, 'migrar');
  const listasAntes = backend.callsTo('GET', /\/flows$/).length;
  const habilidadesAntes = backend.callsTo('GET', /\/skills$/).length;
  await click(byRole('button', /^Converter$/, byRole('dialog', /Converter/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/adopt$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /\/adopt$/)[0]?.body).toEqual({ reason: 'migrar' });
  await waitFor(() => expect(backend.callsTo('GET', /\/flows$/).length).toBeGreaterThan(listasAntes));
  await waitFor(() => expect(backend.callsTo('GET', /\/skills$/).length).toBeGreaterThan(habilidadesAntes));
});

it('ligado: fluxo adotado mostra a habilidade, trava o interruptor e desfaz pela rota', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([fluxo('disabled')]));
  backend.on('GET', /\/skills$/, () => json([versao(1, 'published'), versao(2, 'draft')]));
  backend.on('POST', /\/flows\/abrir-conversa\/release$/, () => json({ flow_id: 'abrir-conversa',
    skill_id: 'instagram.abrir-conversa', flow_status: 'active', discarded_drafts: ['instagram.abrir-conversa@2'],
    versions: [versao(1, 'disabled')] }));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('habilidade instagram.abrir-conversa@1'));
  expect(allByRole('button', /Converter o fluxo/)).toHaveLength(0);
  const interruptor = byRole('switch', /Abrir a conversa/);
  expect(interruptor.hasAttribute('disabled') || interruptor.getAttribute('aria-disabled') === 'true').toBe(true);
  expect(text()).toContain('convertida do fluxo abrir-conversa');

  await click(byRole('button', /Desfazer a conversão do fluxo Abrir a conversa/));
  await waitFor(() => expect(text()).toContain('volta a valer exatamente como era'));
  await click(byRole('button', /^Desfazer$/, byRole('dialog', /Desfazer/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/release$/)).toHaveLength(1));
});

it('ligado: a recusa da conversão aparece na linha do fluxo, com o código e os erros da ida e volta', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([fluxo()]));
  backend.on('GET', /\/skills$/, () => json([]));
  backend.on('POST', /\/adopt$/, () => json({ detail: { code: 'invalid_document',
    message: 'O fluxo abrir-conversa não se converte sem mudar o plano.',
    errors: ['E_ROUNDTRIP /steps/1/postcondition/value: abrir_conversa: postcondition/value muda'] } }, 422));
  await renderComDialogo();
  await waitFor(() => expect(allByRole('button', /Converter o fluxo/)).toHaveLength(1));
  await click(byRole('button', /Converter o fluxo/));
  await waitFor(() => expect(text()).toContain('A versão 1 da habilidade'));
  await click(byRole('button', /^Converter$/, byRole('dialog', /Converter/)));
  await waitFor(() => expect(text()).toContain('invalid_document'));
  expect(text()).toContain('não se converte sem mudar o plano');
  expect(text()).toContain('E_ROUNDTRIP /steps/1/postcondition/value');
});

it('ligado: cada versão tem as transições do seu estado; validar com motivo é a validação manual', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([]));
  backend.on('GET', /\/skills$/, () => json([versao(2, 'candidate'), versao(3, 'draft', { legacy_flow_id: null })]));
  backend.on('POST', /\/skills\/instagram\.abrir-conversa\/versions\/2\/status$/, () => json({
    ...versao(2, 'validated'), parent_version: 1, state_detail: 'validação manual', history: [] }));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('instagram.abrir-conversa@3'));
  expect(allByRole('button', /^Submeter instagram\.abrir-conversa@3$/)).toHaveLength(1);
  expect(allByRole('button', /^Validar instagram\.abrir-conversa@2$/)).toHaveLength(1);
  expect(allByRole('button', /^Desabilitar instagram\.abrir-conversa@2$/)).toHaveLength(1);
  expect(allByRole('button', /^Publicar /)).toHaveLength(0);

  await click(byRole('button', /^Validar instagram\.abrir-conversa@2$/));
  await waitFor(() => expect(text()).toContain('validação manual do dono'));
  await setValue(byRole('textbox', /Motivo/, byRole('dialog', /Validar/)) as HTMLTextAreaElement, 'conversão comprovada');
  await click(byRole('button', /^Validar$/, byRole('dialog', /Validar/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/status$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /\/status$/)[0]?.body).toEqual({ to: 'validated', reason: 'conversão comprovada', manual: true });
});

it('ligado: a recusa da transição mostra o código e as pendências do domínio na linha da versão', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([]));
  backend.on('GET', /\/skills$/, () => json([versao(2, 'candidate')]));
  backend.on('POST', /\/status$/, () => json({ detail: { code: 'validation_pending',
    message: 'instagram.abrir-conversa@2 ainda não tem a prova que `validated` exige.',
    pending: ['caso x: sem observação'] } }, 409));
  await renderComDialogo();
  await waitFor(() => expect(allByRole('button', /^Validar /)).toHaveLength(1));
  await click(byRole('button', /^Validar /));
  await waitFor(() => expect(text()).toContain('validação manual do dono'));
  await click(byRole('button', /^Validar$/, byRole('dialog', /Validar/)));
  await waitFor(() => expect(text()).toContain('validation_pending'));
  expect(backend.callsTo('POST', /\/status$/)[0]?.body).toEqual({ to: 'validated', reason: '', manual: false });
  expect(text()).toContain('caso x: sem observação');
});
