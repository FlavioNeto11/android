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

describe('AiSection — hub de IA (itens 7.1 e 7.2)', () => {
  const PAPEL = {
    role: 'decide', provider: 'local', kind: 'openai', model: 'qwen-vl', endpoint: '127.0.0.1:8001',
    sends_data_externally: false, configured: true, priced: false, vision: true, tools: true,
    refusal_fallback: false, fallback_provider: null, timeout_s: 45, concurrency: 8, effort: 'low',
  } as const;
  const PLANEJADOR = {
    role: 'plan', provider: 'anthropic', kind: 'anthropic', model: 'claude-opus-5', endpoint: 'api.anthropic.com',
    sends_data_externally: true, configured: true, priced: true, vision: true, tools: true,
    refusal_fallback: true, fallback_provider: null, timeout_s: 120, concurrency: 4, effort: 'medium',
  } as const;

  it('mostra provedor, endpoint e "os dados saem?" POR função', async () => {
    const el = await renderSection({ ...BASE, roles: [PAPEL, PLANEJADOR] });
    expect(text(el)).toContain('Por função');
    expect(text(el)).toContain('127.0.0.1:8001');
    expect(text(el)).toContain('api.anthropic.com');
    // O modelo local não tem preço cadastrado: a tela diz isso em vez de deixar somar zero escondido.
    expect(text(el)).toContain('modelo sem preço cadastrado');
  });

  it('função sem fallback declarado diz que o erro sobe — e a que tem diz para onde cai', async () => {
    const el = await renderSection({
      ...BASE, roles: [PAPEL, { ...PLANEJADOR, fallback_provider: 'anthropic' }],
    });
    expect(text(el)).toContain('o erro sobe (sem fallback pago)');
    expect(text(el)).toContain('cai para');
  });

  it('a aba IA passa a dizer que o fallback pago de recusa está ligado, e qual é o alvo', async () => {
    const semHub = await renderSection(BASE);
    expect(text(semHub)).not.toContain('Fallback pago de recusa');
    await act(async () => root.unmount());
    root = createRoot(container);
    backend = new FakeBackend();
    backend.install();
    const el = await renderSection({
      ...BASE, refusal_fallback: true,
      refusal_fallback_target: 'definido pelo provedor (documentado: claude-opus-4-8)',
      spend_today_usd: 8.88, spend_limit_day_usd: 25,
    });
    expect(text(el)).toContain('Fallback pago de recusa está ligado');
    expect(text(el)).toContain('claude-opus-4-8');
    expect(text(el)).toContain('US$ 8.88 de US$ 25.00');
  });
});
