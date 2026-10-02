// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { ContextRetrievalStatus } from '../../api/types';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { ContextRetrievalSection } from './ContextRetrievalSection';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const ROTA = /^\/api\/context-retrieval\/status$/;

const DESLIGADO: ContextRetrievalStatus = {
  enabled: false, mode: 'disabled', top_k: 5,
  provider: { name: 'none', model: '', available: false, unavailable_reason: 'no_provider' },
  external_send: {
    allowed: false, reason: 'private_repository', repository_class: 'private', configured_for_remote: false,
    visibility: 'not_applicable', visibility_verified: false, remote_visibility_verified: false, head_public_verified: false,
    worktree_clean: null,
  },
  budget: { timeout_ms: 5000, max_calls: 2, max_cost_usd: 0.05 },
  summary: {
    requests: 0, by_mode: {}, cache: { hit: 0, miss: 0 }, latency_ms: { p50: null, p95: null, n: 0 },
    cost_usd: 0, input_tokens: 0, fallbacks: {}, privacy_blocks: {},
  },
};

const HIBRIDO_BLOQUEADO: ContextRetrievalStatus = {
  ...DESLIGADO, enabled: true, mode: 'hybrid',
  provider: { name: 'jev', model: 'jev-1', available: true, unavailable_reason: null },
  external_send: {
    ...DESLIGADO.external_send, allowed: false, reason: 'repository_worktree_dirty', repository_class: 'public',
    configured_for_remote: true, visibility: 'public', visibility_verified: true, remote_visibility_verified: true,
    head_public_verified: true, worktree_clean: false,
  },
  summary: {
    requests: 12, by_mode: { hybrid: 12 }, cache: { hit: 3, miss: 1 }, latency_ms: { p50: 40, p95: 900, n: 12 },
    cost_usd: 0.004266, input_tokens: 23283, fallbacks: { budget_exceeded: 2, timeout: 1 },
    privacy_blocks: { repository_worktree_dirty: 4 },
  },
};

async function render(status: ContextRetrievalStatus): Promise<HTMLElement> {
  backend.on('GET', ROTA, () => json(status));
  await act(async () => { root.render(<ContextRetrievalSection />); });
  await waitFor(() => expect(backend.callsTo('GET', ROTA)).toHaveLength(1));
  await waitFor(() => expect(text(container)).not.toContain('Consultando'));
  return container;
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('ContextRetrievalSection — só leitura do estado (ADR-063)', () => {
  it('desligado: diz que nada é consultado nem sai, e que não há pedidos', async () => {
    const el = await render(DESLIGADO);
    await waitFor(() => expect(text(el)).toContain('Desligado'));
    expect(text(el)).toContain('nada é consultado e nada sai da máquina');
    expect(text(el)).toContain('Nenhum pedido registrado ainda');
    expect(text(el)).toContain('As provas de proveniência só contam quando o modo usa um provedor remoto');
  });

  it('híbrido com worktree sujo: envio bloqueado, o motivo e as três provas', async () => {
    const el = await render(HIBRIDO_BLOQUEADO);
    await waitFor(() => expect(text(el)).toContain('Envio externo bloqueado'));
    expect(text(el)).toContain('Há alteração local não commitada');
    const provas = el.querySelector('[aria-label="Provas exigidas para enviar código público"]');
    expect(provas).not.toBeNull();
    expect(text(provas!)).toMatch(/Remoto público provado\s*Sim/);
    expect(text(provas!)).toMatch(/Commit atual público provado\s*Sim/);
    expect(text(provas!)).toMatch(/Worktree limpo\s*Não/);
  });

  it('mostra métricas, cache, custo e as razões de volta ao local e de bloqueio', async () => {
    const el = await render(HIBRIDO_BLOQUEADO);
    await waitFor(() => expect(text(el)).toContain('Acerto do cache'));
    expect(text(el)).toContain('75%');
    expect(text(el)).toContain('US$ 0,004266');
    expect(text(el)).toContain('Orçamento estourado');
    expect(text(el)).toContain('Tempo esgotado');
    expect(text(el)).toContain('Bloqueios de privacidade');
  });

  it('git sem resposta (worktree_clean nulo) aparece como "Sem resposta", não como Sim nem Não', async () => {
    const el = await render({ ...HIBRIDO_BLOQUEADO, external_send: { ...HIBRIDO_BLOQUEADO.external_send, worktree_clean: null } });
    await waitFor(() => expect(text(el)).toMatch(/Worktree limpo\s*Sem resposta/));
  });

  it('é só leitura: um botão (Atualizar), nenhuma chamada além de GET e nenhum controle que ligue o remoto', async () => {
    const el = await render(HIBRIDO_BLOQUEADO);
    const botoes = [...el.querySelectorAll('button')].map((b) => text(b).trim());
    expect(botoes).toEqual(['Atualizar']);
    expect(el.querySelectorAll('input, select, textarea, [role="switch"]')).toHaveLength(0);
    expect(backend.calls.filter((c) => c.method !== 'GET')).toHaveLength(0);
    expect(text(el)).toContain('não liga o envio a provedor externo');
  });

  it('Atualizar consulta de novo, e só a leitura', async () => {
    const el = await render(DESLIGADO);
    const botao = [...el.querySelectorAll('button')].find((b) => text(b).includes('Atualizar'))!;
    await act(async () => { botao.click(); });
    await waitFor(() => expect(backend.callsTo('GET', ROTA)).toHaveLength(2));
    expect(backend.calls.filter((c) => c.method !== 'GET')).toHaveLength(0);
  });

  it('503 not_ready: aviso com a mensagem, sem derrubar a tela', async () => {
    backend.on('GET', ROTA, () => json({ code: 'not_ready', message: 'A configuração ainda não foi composta.' }, 503));
    await act(async () => { root.render(<ContextRetrievalSection />); });
    await waitFor(() => expect(text(container)).toContain('Não foi possível consultar o retrieval de contexto'));
    expect(text(container)).toContain('Retrieval de contexto');
  });
});
