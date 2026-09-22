// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Command } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { CommandHistory } from './CommandTrail';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;
let linhas: Command[];

const incerto: Command = {
  id: 'c-20260921172322-6f7fdc', instance_id: 'android-15', worker_id: 'worker-lan-01', verb: 'start',
  state: 'uncertain', fence: 1, requested_by: 'panel',
  reason: 'o aparelho não completou o boot em 480 s; estado desconhecido', attempt: 0,
  created_at: '2026-09-21T17:23:22.000Z', dispatched_at: '2026-09-21T17:23:22.000Z',
  acked_at: '2026-09-21T17:23:23.000Z', started_at: '2026-09-21T17:23:30.000Z',
  finished_at: '2026-09-21T17:31:25.000Z',
};

/** O mesmo comando, ainda AGINDO: é o estado em que cancelar faz sentido — e em que não havia como pedir. */
const emVoo: Command = { ...incerto, id: 'c-20260922101010-aaaa11', state: 'running', reason: null,
                         finished_at: null };

async function render(): Promise<HTMLElement> {
  await act(async () => {
    root.render(<CommandHistory instanceId="android-15" />);
  });
  return container;
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  linhas = [incerto];
  backend.on('GET', /^\/api\/commands$/, () => json(linhas));
  backend.install();
  useAppStore.setState({ ...initialDataState });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('Comandos recentes — o `uncertain` deixa de ser invisível e ganha as duas saídas', () => {
  it('mostra o comando sem desfecho, o motivo e a trilha inteira', async () => {
    const el = await render();
    await waitFor(() => expect(text(el)).toContain('Desconhecido'));
    expect(text(el)).toContain('Há 1 comando sem desfecho');
    expect(text(el)).toContain('não completou o boot');
    // A trilha: criado → enviado → recebido → iniciado → concluído.
    for (const marco of ['criado', 'enviado', 'recebido', 'iniciado', 'concluído']) {
      expect(text(el)).toContain(marco);
    }
  });

  it('“Verificar agora” pergunta ao estado real e fecha o comando quando há prova', async () => {
    backend.on('POST', /\/commands\/[^/]+\/verify$/, () =>
      json({ command: { ...incerto, state: 'succeeded', reason: "verificado pelo estado real: o aparelho está 'online'" },
             changed: true, verifiable: true }));
    const el = await render();
    await waitFor(() => expect(text(el)).toContain('Desconhecido'));
    await click(byRole('button', /Verificar agora/, el));
    await waitFor(() => expect(backend.callsTo('POST', /\/verify$/)).toHaveLength(1));
  });

  it('“Marcar como falhou” manda a decisão humana, com nota', async () => {
    backend.on('POST', /\/commands\/[^/]+\/resolve$/, () => json({ ...incerto, state: 'failed' }));
    const el = await render();
    await waitFor(() => expect(text(el)).toContain('Desconhecido'));
    await click(byRole('button', /Marcar como falhou/, el));
    await waitFor(() => expect(backend.callsTo('POST', /\/resolve$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /\/resolve$/)[0]?.body as { outcome: string; note?: string };
    expect(corpo.outcome).toBe('failed');
    expect(corpo.note).toContain('android-15');
  });

  it('comando já resolvido não oferece decisão nenhuma', async () => {
    linhas = [{ ...incerto, state: 'succeeded', reason: null }];
    const el = await render();
    await waitFor(() => expect(text(el)).toContain('Concluído'));
    expect(text(el)).not.toContain('Verificar agora');
    expect(text(el)).not.toContain('sem desfecho');
    // Comando terminal não se cancela: `cancelled` depois de `succeeded` apagaria história.
    expect(text(el)).not.toContain('Cancelar');
  });

  it('“Cancelar” pede o cancelamento do comando que ainda está agindo', async () => {
    linhas = [emVoo];
    backend.on('POST', /\/commands\/[^/]+\/cancel$/, () =>
      json({ command: { ...emVoo, state: 'cancel_requested' }, delivered: true,
             detail: 'o boot em andamento nesta máquina foi interrompido' }));
    const el = await render();
    await waitFor(() => expect(text(el)).toContain('Executando'));
    await click(byRole('button', /Cancelar/, el));
    await waitFor(() => expect(backend.callsTo('POST', /\/cancel$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /\/cancel$/)[0]?.body as { note?: string };
    expect(corpo.note).toContain('android-15');
  });

  it('pedido já feito não oferece o botão de novo — “Cancelando” já conta o que está acontecendo', async () => {
    linhas = [{ ...emVoo, state: 'cancel_requested', reason: 'cancelamento pedido por panel' }];
    const el = await render();
    await waitFor(() => expect(text(el)).toContain('Cancelando'));
    expect(allByRole('button', /^Cancelar$/, el)).toHaveLength(0);
  });
});
