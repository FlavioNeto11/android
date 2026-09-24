// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance, Worker } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text } from '../../test/harness';
import { FocusPanel } from './FocusPanel';

// Achados #61 e #62: o foco decidia só pelo estado do aparelho — oferecia verbo que o backend recusa no
// pré-voo, oferecia campo de texto que a loja nunca aceita, e não dizia em que máquina o aparelho roda.

const LOJA_VERBS = ['start', 'stop', 'restart', 'home', 'back', 'recents'];

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

async function renderFocus(instance: Instance, workers: Worker[] = []): Promise<HTMLElement> {
  useAppStore.setState({
    instances: { [instance.id]: instance },
    instanceOrder: [instance.id],
    workers: Object.fromEntries(workers.map((w) => [w.id, w])),
  });
  await act(async () => {
    root.render(<FocusPanel instanceId={instance.id} />);
  });
  return container;
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.on('GET', /^\/api\/instances\/[^/]+\/hierarchy/, () => json({ elements: [] }));
  backend.install();
  const snap = makeSnapshot();
  useAppStore.setState({ ...initialDataState, settings: snap.settings, health: snap.health });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('FocusPanel — capacidades do aparelho (achado #62)', () => {
  it('a loja recebe o campo de texto como os outros; só a senha da conta Google vai pela janela do emulador', async () => {
    const el = await renderFocus(makeInstance(11, { kind: 'store', state: 'online', supported_verbs: LOJA_VERBS }));
    expect(el.querySelector('input[aria-label="Texto para digitar no aparelho"]')).not.toBeNull();
    expect(text(el)).toContain('senha da conta Google é digitada direto na janela do emulador');
  });

  it('verbo que o aparelho não aceita aparece indisponível COM o motivo, antes do clique', async () => {
    const el = await renderFocus(makeInstance(11, { kind: 'store', state: 'online', supported_verbs: LOJA_VERBS }));
    const t = text(el);
    // O rótulo acompanhou o verbo: ele deixou de instalar um arquivo configurado à mão e passa a instalar a
    // versão PROMOVIDA pela camada de releases (#83).
    expect(t).toContain('Instalar versão promovida');
    expect(t).toContain('Este aparelho não aceita “Instalar versão promovida”');
    expect(t).toContain('aparelho-loja');
    // O que ela aceita continua clicável: o filtro é de capacidade, não um cadeado geral.
    expect(t).not.toContain('Este aparelho não aceita “Parar”');
  });

  it('aparelho comum sem restrição segue oferecendo tudo que o estado permite', async () => {
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(text(el)).not.toContain('Este aparelho não aceita');
    expect(el.querySelector('input[aria-label="Texto para digitar no aparelho"]')).not.toBeNull();
  });
});

describe('FocusPanel — em que servidor o aparelho roda (achado #61)', () => {
  it('aparelho de worker mostra o servidor e os dados que o WORKER reporta', async () => {
    const w = worker({ devices: [{ serial: 'emulator-5554', avd_name: 'worker-01', state: 'running',
                                   adb_port: 5555, instance_id: 'android-09' }] });
    const el = await renderFocus(
      makeInstance(9, { state: 'online', kind: 'external', worker_id: 'worker-lan-01' }), [w]);
    const t = text(el);
    expect(t).toContain('Notebook da LAN');
    expect(t).toContain('worker-01');          // AVD do worker, não o `poc_avd_9` do central
    expect(t).toContain('Porta do ADB no servidor');
  });

  it('aparelho do central não ganha selo de servidor nem linha de processo', async () => {
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(text(el)).not.toContain('Processo no servidor');
  });
});
