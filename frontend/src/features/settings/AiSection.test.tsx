// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { AiStatus } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { AiSection } from './AiSection';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const BASE: AiStatus = {
  provider: 'anthropic', model: 'claude-opus-5', configured: true, simulated: false, sends_data_externally: true,
  notice: 'Provedor externo.', effort: 'medium',
};

async function renderSection(status: AiStatus): Promise<HTMLElement> {
  backend.on('GET', /^\/api\/ai$/, () => json(status));
  await act(async () => {
    root.render(<AiSection />);
  });
  await waitFor(() => expect(backend.callsTo('GET', /^\/api\/ai$/)).toHaveLength(1));
  return container;
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  useAppStore.setState({ ...initialDataState, settings: makeSnapshot().settings });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('AiSection — disjuntor de conta de IA (achado #90)', () => {
  it('conta configurada e sem disjuntor: Situação "Pronta para uso"', async () => {
    const el = await renderSection(BASE);
    expect(text(el)).toContain('Pronta para uso');
    expect(text(el)).not.toContain('disjuntor');
  });

  it('disjuntor acionado: Situação some de "Pronta para uso" e o motivo aparece, sem o dicionário cru do provedor', async () => {
    const el = await renderSection({
      ...BASE, account_blocked: true,
      account_blocked_reason: 'Sem crédito no provedor de IA — recarregue e retome.',
    });
    expect(text(el)).toContain('Bloqueada (disjuntor)');
    expect(text(el)).not.toContain('Pronta para uso');
    expect(text(el)).toContain('Disjuntor de conta de IA acionado');
    expect(text(el)).toContain('Sem crédito no provedor de IA — recarregue e retome.');
    expect(text(el)).not.toContain('invalid_request_error');
  });
});
