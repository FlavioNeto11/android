// @vitest-environment jsdom
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Command, CommandState } from '../../api/types';
import { useAppStore } from '../../store/app';
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

describe('acompanhamento por evento — sem teto e sem desistir na primeira falha de rede', () => {
  function comandoUpdated(cmd: Command) {
    useAppStore.getState().applyEvent({
      id: null, ts: '2026-09-21T10:09:00.000Z', kind: 'command.updated', level: 'info', run_id: null,
      instance_id: cmd.instance_id, objective_id: null, step_id: null, attempt_id: null,
      message: 'comando', data: { command: cmd },
    });
  }

  beforeEach(() => {
    useAppStore.setState({ lastCommand: {} });
  });

  it('desfecho que chega DEPOIS do antigo teto de 210 s ainda é relatado', async () => {
    // O defeito: o painel parava de acompanhar aos 210 s enquanto o backend tinha até 600 s de prazo. O `start`
    // remoto real de 21/09 fechou 483 s depois de criado — 273 s além do teto — e ninguém soube o desfecho.
    // Aqui a sondagem NUNCA devolve desfecho: quem conta é o evento, venha quando vier.
    backend.on('POST', /\/actions\/start$/, () =>
      json({ command_id: 'c-lento-0001', state: 'dispatched', deduplicated: false }));
    backend.on('GET', /\/commands\/c-lento-0001$/, () => json({ ...comando('running'), id: 'c-lento-0001' }));

    await runInstanceAction('android-09', 'start');
    expect(tons().some((t) => t.tone === 'success')).toBe(false);

    comandoUpdated({ ...comando('succeeded'), id: 'c-lento-0001', verb: 'start' });
    await waitFor(() => expect(tons().some((t) => t.tone === 'success')).toBe(true));
    expect(tons().find((t) => t.tone === 'success')?.title).toBe('Iniciada — android-09');
  });

  it('falha transitória no GET não encerra o acompanhamento em silêncio', async () => {
    // Era `catch { return }` dentro do laço: erro em QUALQUER sondagem apagava o acompanhamento sem avisar.
    backend.on('POST', /\/actions\/stop$/, () =>
      json({ command_id: 'c-rede-0001', state: 'dispatched', deduplicated: false }));
    backend.on('GET', /\/commands\/c-rede-0001$/, () => apiError(503, 'unavailable', 'a rede piscou'));

    await runInstanceAction('android-09', 'stop');
    await waitFor(() => expect(backend.callsTo('GET', /\/commands\/c-rede-0001$/).length).toBeGreaterThan(0));
    expect(tons().some((t) => t.tone === 'success')).toBe(false);

    comandoUpdated({ ...comando('succeeded'), id: 'c-rede-0001' });
    await waitFor(() => expect(tons().some((t) => t.tone === 'success')).toBe(true));
  });

  it('comando de OUTRO aparelho não fecha o que esta aba acompanha', async () => {
    backend.on('POST', /\/actions\/stop$/, () =>
      json({ command_id: 'c-teste-0001', state: 'dispatched', deduplicated: false }));
    backend.on('GET', /\/commands\/c-teste-0001$/, () => json(comando('running')));

    await runInstanceAction('android-09', 'stop');
    comandoUpdated({ ...comando('succeeded'), id: 'c-outro-0001', instance_id: 'android-10' });
    await new Promise((r) => setTimeout(r, 30));
    expect(tons().some((t) => t.tone === 'success')).toBe(false);
  });
});
