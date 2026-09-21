// @vitest-environment jsdom
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Command, CommandState } from '../../api/types';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, apiError, installBrowserStubs, json, waitFor } from '../../test/harness';
import { runInstanceAction } from './actions';

let backend: FakeBackend;

function comando(state: CommandState, reason: string | null = null): Command {
  return {
    id: 'c-teste-0001', instance_id: 'android-09', worker_id: null, verb: 'stop', state, fence: 1,
    requested_by: 'panel', reason, attempt: 0, created_at: '2026-09-21T10:00:00.000Z',
    dispatched_at: state === 'rejected' ? null : '2026-09-21T10:00:01.000Z', acked_at: null,
    started_at: state === 'rejected' ? null : '2026-09-21T10:00:02.000Z',
    finished_at: '2026-09-21T10:00:03.000Z',
  };
}

function tons(): { tone: string; title: string }[] {
  return useToastStore.getState().toasts.map((t) => ({ tone: t.tone, title: t.title }));
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  useToastStore.setState({ toasts: [] });
  backend = new FakeBackend();
  backend.install();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('runInstanceAction — a interface conta a verdade, não a aceitação', () => {
  it('aceitação é `info`; só a confirmação do aparelho vira `success`', async () => {
    backend.on('POST', /\/actions\/stop$/, () =>
      json({ command_id: 'c-teste-0001', state: 'dispatched', deduplicated: false }));
    backend.on('GET', /\/commands\/c-teste-0001$/, () => json(comando('succeeded')));

    expect(await runInstanceAction('android-09', 'stop')).toBe(true);
    // No instante do 202 a única coisa verdadeira é que o pedido foi aceito.
    expect(tons()[0]).toEqual({ tone: 'info', title: 'Parada solicitada — android-09' });

    await waitFor(() => expect(tons().some((t) => t.tone === 'success')).toBe(true));
    expect(tons().find((t) => t.tone === 'success')?.title).toBe('Parada — android-09');
  });

  it('comando recusado avisa que NADA foi executado', async () => {
    // É o caso que ficava indistinguível de sucesso: recusa no fundo, toast verde na tela.
    backend.on('POST', /\/actions\/stop$/, () =>
      apiError(409, 'rejected', 'android-09: operação não suportada.'));
    expect(await runInstanceAction('android-09', 'stop')).toBe(false);
    const erro = tons().find((t) => t.tone === 'danger');
    expect(erro?.title).toContain('Não foi possível executar');
  });

  it('desfecho `rejected` no acompanhamento diz o motivo e que o aparelho ficou intacto', async () => {
    backend.on('POST', /\/actions\/stop$/, () =>
      json({ command_id: 'c-teste-0001', state: 'dispatched', deduplicated: false }));
    backend.on('GET', /\/commands\/c-teste-0001$/, () =>
      json(comando('rejected', 'aparelho externo: o painel nunca apaga dados')));

    await runInstanceAction('android-09', 'stop');
    await waitFor(() => expect(tons().some((t) => t.tone === 'danger')).toBe(true));
    const t = useToastStore.getState().toasts.find((x) => x.tone === 'danger');
    expect(t?.title).toBe('Parar em android-09: recusado');
    expect(t?.details).toEqual(['aparelho externo: o painel nunca apaga dados']);
    expect(t?.hint).toBe('Nada foi executado no aparelho.');
  });

  it('resultado incerto é `warning` e avisa que nada será repetido sozinho', async () => {
    backend.on('POST', /\/actions\/stop$/, () =>
      json({ command_id: 'c-teste-0001', state: 'dispatched', deduplicated: false }));
    backend.on('GET', /\/commands\/c-teste-0001$/, () =>
      json(comando('uncertain', 'adb shell excedeu 30s')));

    await runInstanceAction('android-09', 'stop');
    await waitFor(() => expect(tons().some((t) => t.tone === 'warning')).toBe(true));
    const t = useToastStore.getState().toasts.find((x) => x.tone === 'warning');
    expect(t?.title).toBe('Parar em android-09: resultado desconhecido');
    expect(t?.hint).toContain('Nada será repetido automaticamente');
  });

  it('cada clique manda chave de idempotência própria', async () => {
    backend.on('POST', /\/actions\/home$/, () =>
      json({ command_id: 'c-teste-0001', state: 'dispatched', deduplicated: false }));
    backend.on('GET', /\/commands\//, () => json(comando('succeeded')));

    await runInstanceAction('android-09', 'home');
    await runInstanceAction('android-09', 'home');
    const chaves = backend.callsTo('POST', /\/actions\/home$/)
      .map((c) => (c.body as { idempotency_key?: string }).idempotency_key);
    expect(chaves).toHaveLength(2);
    expect(chaves[0]).toMatch(/^android-09:home:/);
    expect(chaves[0]).not.toBe(chaves[1]);      // cliques diferentes, comandos diferentes
  });
});
