// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { FrameInfo, Instance, Worker } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeInstance, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
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
    expect(t).toContain('Instalar app');
    expect(t).toContain('Este aparelho não aceita “Instalar app”');
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

describe('FocusPanel — tela sensível (contrato C4)', () => {
  const agora = () => new Date().toISOString();
  const frameComum = (id: string): FrameInfo => ({ id, ts: agora(), width: 1080, height: 2400, orientation: 'portrait', stale: false });
  const marcador = (id: string): FrameInfo => ({ ...frameComum(id), sensitive: true });
  const buscasDeFrame = () => backend.callsTo('GET', /\/frame$/);

  function imagemOk(id: string): Response {
    return new Response(new TextEncoder().encode('jpeg'), {
      status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'X-Frame-Id': id, 'X-Frame-Ts': agora(), 'X-Frame-Width': '1080',
                 'X-Frame-Height': '2400', 'X-Frame-Orientation': 'portrait' },
    });
  }

  it('marcador: não busca /frame, mostra o estado neutro e guarda id e tamanho para o controle manual', async () => {
    const el = await renderFocus(makeInstance(1, { state: 'online', frame: marcador('m1') }));
    await waitFor(() => expect(text(el)).toContain('Tela sensível — prévia oculta'));
    expect(buscasDeFrame()).toHaveLength(0);
    expect(el.querySelector('img')).toBeNull();
    const t = text(el);
    expect(t).not.toContain('Não foi possível carregar a tela');
    expect(t).not.toContain('Desatualizado');
    // "Frame exibido" é o que vai como `frame_id` na entrada manual: o marcador, com o tamanho da tela
    expect(t).toContain('m1 (1080×2400)');
    expect(el.querySelector('[role="img"]')?.getAttribute('aria-label')).toMatch(/^Tela sensível de android-01/);
  });

  it('404 sensitive_screen na busca: mesmo estado neutro, sem erro vermelho nem imagem quebrada', async () => {
    backend.on('GET', /\/frame$/, () => apiError(404, 'sensitive_screen', 'A tela atual deste aparelho é sensível.'));
    const el = await renderFocus(makeInstance(1, { state: 'online', frame: frameComum('f1') }));
    await waitFor(() => expect(text(el)).toContain('Tela sensível — prévia oculta'));
    expect(buscasDeFrame()).toHaveLength(1);
    const t = text(el);
    expect(t).not.toContain('Não foi possível carregar a tela');
    expect(t).not.toContain('Ainda não há frame');
    expect(el.querySelector('img')).toBeNull();
    // o tamanho do último frame conhecido segue valendo para o controle manual até o marcador chegar
    expect(t).toContain('f1 (1080×2400)');
  });

  it('frame comum depois do marcador: volta a buscar e mostrar a imagem', async () => {
    backend.on('GET', /\/frame$/, () => imagemOk('f2'));
    const el = await renderFocus(makeInstance(1, { state: 'online', frame: marcador('m1') }));
    await waitFor(() => expect(text(el)).toContain('Tela sensível — prévia oculta'));
    expect(buscasDeFrame()).toHaveLength(0);

    await act(async () => {
      useAppStore.setState((s) => ({ instances: { ...s.instances, 'android-01': { ...s.instances['android-01']!, frame: frameComum('f2') } } }));
    });
    await waitFor(() => expect(el.querySelector('img')).not.toBeNull());
    expect(buscasDeFrame()).toHaveLength(1);
    expect(text(el)).not.toContain('Tela sensível');
    expect(text(el)).toContain('f2 (1080×2400)');

    // e ficou sensível de novo: a imagem sai na hora, sem nova busca
    await act(async () => {
      useAppStore.setState((s) => ({ instances: { ...s.instances, 'android-01': { ...s.instances['android-01']!, frame: marcador('m3') } } }));
    });
    await waitFor(() => expect(text(el)).toContain('Tela sensível — prévia oculta'));
    expect(el.querySelector('img')).toBeNull();
    expect(buscasDeFrame()).toHaveLength(1);
  });
});

describe('FocusPanel — celular (P1.1 da auditoria UX de 27/09)', () => {
  // O jsdom não tem `matchMedia`. O dublê responde à consulta pelo `max-width` que ela declara, como o navegador
  // faria numa janela dessa largura — assim o teste não depende do limiar exato escrito no componente.
  function fingirLarguraDaJanela(px: number): void {
    const matchMedia = (query: string): MediaQueryList => {
      const m = /max-width:\s*(\d+)px/.exec(query);
      return {
        matches: m ? px <= Number(m[1]) : false,
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      };
    };
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: matchMedia });
  }

  afterEach(() => {
    delete (window as { matchMedia?: unknown }).matchMedia;
    useUiStore.setState({ focusInstanceId: null });
  });

  it('em 390 px o cabeçalho tem "Voltar", que fecha o foco (o "Fechar" do desktop some)', async () => {
    fingirLarguraDaJanela(390);
    useUiStore.setState({ focusInstanceId: 'android-01' });
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(allByRole('button', 'Fechar', el)).toHaveLength(0);
    await click(byRole('button', 'Voltar', el));
    expect(useUiStore.getState().focusInstanceId).toBeNull();
  });

  it('no desktop não há "Voltar": o painel fica ao lado e fecha por "Fechar"', async () => {
    fingirLarguraDaJanela(1440);
    useUiStore.setState({ focusInstanceId: 'android-01' });
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(allByRole('button', 'Voltar', el)).toHaveLength(0);
    await click(byRole('button', 'Fechar', el));
    expect(useUiStore.getState().focusInstanceId).toBeNull();
  });

  it('aparelho que sumiu do backend também tem "Voltar" em tela estreita', async () => {
    fingirLarguraDaJanela(390);
    useUiStore.setState({ focusInstanceId: 'android-99' });
    useAppStore.setState({ instances: {}, instanceOrder: [] });
    await act(async () => {
      root.render(<FocusPanel instanceId="android-99" />);
    });
    await click(byRole('button', 'Voltar', container));
    expect(useUiStore.getState().focusInstanceId).toBeNull();
  });
});
