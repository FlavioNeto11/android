// @vitest-environment jsdom
// 29.143 (adendo v1.67): o controle manual tem dono. Pedir o controle que outra pessoa tem devolve 409
// `controlled_by_other` com `dono` e `desde`; o painel diz quem e desde quando, e só toma com a confirmação.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfirmHost } from '../components/Confirm';
import { formatClock } from '../lib/time';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../test/harness';
import { useControlStore } from './control';
import { useToastStore } from './toasts';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;
const DESDE = '2026-10-05T18:00:00Z';

function recusa(desde: string | null): Response {
  return json({ detail: { code: 'controlled_by_other', message: 'texto do backend', dono: 'Operadora B', desde } }, 409);
}

function titulos(): string[] {
  return useToastStore.getState().toasts.map((t) => t.title);
}

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  useControlStore.setState({ leases: {}, busy: {} });
  useToastStore.setState({ toasts: [] });
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('controle com dono (29.143)', () => {
  it('409 controlled_by_other: a confirmação diz quem e desde quando; "Tomar o controle" manda {tomar: true}', async () => {
    backend.on('POST', /control\/take$/, (c) => (c.body ? json({ status: 'granted', lease_id: 'lease-novo' }) : recusa(DESDE)));
    await act(async () => root.render(<ConfirmHost />));
    let pedido!: Promise<void>;
    await act(async () => { pedido = useControlStore.getState().take('android-04'); });
    await waitFor(() => expect(text()).toContain('Operadora B está no controle de android-04'));
    expect(text()).toContain(`Desde ${formatClock(DESDE)}.`);
    expect(text()).toContain('a gravação é encerrada e fica em "Para revisar" (não é descartada)');
    expect(useControlStore.getState().leases['android-04']).toBeUndefined();

    await click(byRole('button', /^Tomar o controle$/));
    await act(async () => { await pedido; });
    const chamadas = backend.callsTo('POST', /control\/take$/);
    expect(chamadas).toHaveLength(2);
    expect(chamadas[0]?.body).toBeUndefined();
    expect(chamadas[1]?.body).toEqual({ tomar: true });
    expect(useControlStore.getState().leases['android-04']?.leaseId).toBe('lease-novo');
  });

  it('cancelar a confirmação não toma: um pedido só, sem lease e sem toast de erro genérico', async () => {
    backend.on('POST', /control\/take$/, () => recusa(DESDE));
    await act(async () => root.render(<ConfirmHost />));
    let pedido!: Promise<void>;
    await act(async () => { pedido = useControlStore.getState().take('android-04'); });
    await waitFor(() => expect(text()).toContain('Operadora B está no controle de android-04'));
    await click(byRole('button', /^Cancelar$/));
    await act(async () => { await pedido; });
    expect(backend.callsTo('POST', /control\/take$/)).toHaveLength(1);
    expect(useControlStore.getState().leases['android-04']).toBeUndefined();
    expect(titulos().some((t) => t.startsWith('Não foi possível'))).toBe(false);
  });

  it('pedido pendente de outra pessoa (desde nulo): só explica, sem oferecer a tomada que o backend recusaria', async () => {
    backend.on('POST', /control\/take$/, () => recusa(null));
    await act(async () => root.render(<ConfirmHost />));
    await act(async () => { await useControlStore.getState().take('android-04'); });
    expect(titulos()).toContain('Operadora B já pediu o controle de android-04');
    expect(text()).not.toContain('Tomar o controle');
    expect(backend.callsTo('POST', /control\/take$/)).toHaveLength(1);
  });

  it('quem perde o controle numa tomada: o lease cai e o aviso diz quem tomou; a tomada desta aba não derruba o próprio lease', async () => {
    // Aparelho próprio: a tomada desta aba em outro teste segue na janela de 3 s do módulo.
    useControlStore.setState({ leases: { 'android-07': { leaseId: 'lease-velho', status: 'granted', acquiredAt: Date.now() - 60_000 } } });
    useControlStore.getState().tomado('android-07', 'Operadora B');
    expect(useControlStore.getState().leases['android-07']).toBeUndefined();
    expect(titulos()).toContain('Operadora B tomou o controle de android-07');

    // A aba que tomou recebe o mesmo evento logo depois da resposta: o lease novo fica.
    backend.on('POST', /control\/take$/, () => json({ status: 'granted', lease_id: 'lease-meu' }));
    await act(async () => { await useControlStore.getState().take('android-05', true); });
    useControlStore.getState().tomado('android-05', 'Operador A');
    expect(useControlStore.getState().leases['android-05']?.leaseId).toBe('lease-meu');
  });

  it('tomada lenta (resposta depois de 3 s): o evento da própria tomada, logo depois da resposta, não derruba o lease novo', async () => {
    let responder!: (r: Response) => void;
    backend.on('POST', /control\/take$/, () => new Promise<Response>((ok) => { responder = ok; }));
    const inicio = Date.now();
    const relogio = vi.spyOn(Date, 'now').mockReturnValue(inicio);
    try {
      let pedido!: Promise<void>;
      await act(async () => { pedido = useControlStore.getState().take('android-08', true); });
      await waitFor(() => expect(responder).toBeTypeOf('function'));
      relogio.mockReturnValue(inicio + 5000);
      responder(json({ status: 'granted', lease_id: 'lease-lento' }));
      await act(async () => { await pedido; });
      relogio.mockReturnValue(inicio + 5200);
      useControlStore.getState().tomado('android-08', 'Operador A');
      expect(useControlStore.getState().leases['android-08']?.leaseId).toBe('lease-lento');
    } finally {
      relogio.mockRestore();
    }
  });
});
