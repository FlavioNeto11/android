// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
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
