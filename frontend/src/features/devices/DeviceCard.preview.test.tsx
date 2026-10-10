// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FrameInfo, Instance, StreamInfo } from '../../api/types';
import { useAppStore } from '../../store/app';
import { usePreviewStore } from '../../store/preview';
import { initialDataState } from '../../store/reducer';
import { makeInstance, makeSnapshot } from '../../test/fixtures';
import { installBrowserStubs, text } from '../../test/harness';
import { AI_FRAME_MAX_AGE_MS, DeviceCard, frameStaleLimitMs, isFrameStale } from './DeviceCard';
import { isPreviewPaused } from './streamState';

/**
 * Prévia sob demanda no cartão (adendo v0.20, C2/C3): a miniatura só busca imagem quando está na tela, e "prévia
 * suspensa" é um estado neutro — não é desatualizado nem falha. Prova `simulated` (jsdom, sem backend).
 */

/** Dublê controlável: o jsdom não tem IntersectionObserver, e aqui o teste decide o que está na tela. */
class FakeIntersectionObserver {
  static all: FakeIntersectionObserver[] = [];
  readonly targets = new Set<Element>();
  private readonly cb: IntersectionObserverCallback;

  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb;
    FakeIntersectionObserver.all.push(this);
  }

  observe(el: Element): void {
    this.targets.add(el);
  }

  unobserve(el: Element): void {
    this.targets.delete(el);
  }

  disconnect(): void {
    this.targets.clear();
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  /** O "navegador" avisa que o elemento entrou ou saiu da viewport. */
  static async report(el: Element, isIntersecting: boolean): Promise<void> {
    await act(async () => {
      for (const o of FakeIntersectionObserver.all) {
        if (o.targets.has(el)) {
          o.cb([{ target: el, isIntersecting } as IntersectionObserverEntry], o as unknown as IntersectionObserver);
        }
      }
    });
  }
}

const noop = () => undefined;
let root: Root;
let container: HTMLElement;

const OLD_TS = '2026-09-17T12:00:09.000Z';

function frame(id: string, ts = new Date().toISOString(), stale = false): FrameInfo {
  return { id, ts, width: 1080, height: 2400, orientation: 'portrait', stale };
}

function stream(status: StreamInfo['status'], over: Partial<StreamInfo> = {}): StreamInfo {
  return { status, detail: '', last_frame_at: null, frame_age_s: null, last_capture_error: null, last_capture_error_at: null,
           consecutive_capture_failures: 0, ...over };
}

function online(over: Partial<Instance> = {}): Instance {
  return makeInstance(3, { state: 'online', automation: { state: 'ready', detail: null }, ...over });
}

async function render(instance: Instance): Promise<void> {
  await act(async () => {
    root.render(<DeviceCard instance={instance} appName="QA Messenger" selected={false} focused={false} onToggle={noop} onRange={noop} onOpen={noop} />);
  });
}

function card(): HTMLElement {
  const el = container.querySelector('article');
  if (!el) throw new Error('cartão não renderizado');
  return el;
}

function thumbSrc(): string | null {
  return container.querySelector('img[alt^="Tela atual"]')?.getAttribute('src') ?? null;
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
  FakeIntersectionObserver.all = [];
  usePreviewStore.setState({ visible: {} });
  useAppStore.setState({ ...initialDataState, settings: makeSnapshot().settings });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

afterAll(() => {
  vi.unstubAllGlobals();
});

describe('miniatura sob demanda', () => {
  it('fora da viewport não mostra imagem nem pede frame — e não entra no watch.grid', async () => {
    await render(online({ frame: frame('f1') }));
    // antes da primeira resposta do observador, nada é buscado
    expect(thumbSrc()).toBeNull();
    await FakeIntersectionObserver.report(card(), false);
    expect(thumbSrc()).toBeNull();
    expect(usePreviewStore.getState().visible).toEqual({});

    // chega um evento `frame` novo para o aparelho: fora da tela, continua sem buscar
    await render(online({ frame: frame('f2') }));
    expect(thumbSrc()).toBeNull();
    expect(container.querySelectorAll('img')).toHaveLength(0);
  });

  it('na tela busca o frame atual; saindo, congela no último visto até voltar', async () => {
    await render(online({ frame: frame('f1') }));
    await FakeIntersectionObserver.report(card(), true);
    expect(thumbSrc()).toBe('/api/instances/android-03/frame?mode=thumb&f=f1');
    expect(usePreviewStore.getState().visible).toEqual({ 'android-03': true });

    await render(online({ frame: frame('f2') }));
    expect(thumbSrc()).toBe('/api/instances/android-03/frame?mode=thumb&f=f2');

    await FakeIntersectionObserver.report(card(), false);
    expect(usePreviewStore.getState().visible).toEqual({});
    await render(online({ frame: frame('f3') }));
    await render(online({ frame: frame('f4') }));
    // a imagem antiga fica (sem piscar ao voltar), mas nenhum `src` novo — nenhum GET /frame
    expect(thumbSrc()).toBe('/api/instances/android-03/frame?mode=thumb&f=f2');

    await FakeIntersectionObserver.report(card(), true);
    expect(thumbSrc()).toBe('/api/instances/android-03/frame?mode=thumb&f=f4');
  });

  it('desmontar tira o aparelho do conjunto visível', async () => {
    await render(online({ frame: frame('f1') }));
    await FakeIntersectionObserver.report(card(), true);
    expect(usePreviewStore.getState().visible).toEqual({ 'android-03': true });
    await act(async () => root.unmount());
    expect(usePreviewStore.getState().visible).toEqual({});
    root = createRoot(container); // o afterEach desmonta de novo
  });
});

describe('prévia suspensa (stream.status = paused)', () => {
  it('mostra "Prévia suspensa" com a hora do frame antigo, sem selo de desatualizado', async () => {
    // O backend marca `frame.stale` num frame velho mesmo com a prévia suspensa: não pode virar "Desatualizado".
    await render(online({ frame: frame('f1', OLD_TS, true), stream: stream('paused', { last_frame_at: OLD_TS }) }));
    await FakeIntersectionObserver.report(card(), true);
    const t = text(card());
    expect(t).toContain('Prévia suspensa');
    expect(t).toContain('última imagem');
    expect(t).not.toContain('Desatualizado');
    expect(t).not.toContain('Sem imagem nova');
    // a imagem antiga continua lá
    expect(thumbSrc()).toBe('/api/instances/android-03/frame?mode=thumb&f=f1');
    expect(container.querySelector('[title^="Ninguém estava olhando"]')).not.toBeNull();
  });

  it('suspensa sem frame nenhum não diz "Aguardando a primeira imagem" nem "Desatualizado"', async () => {
    await render(online({ frame: null, stream: stream('paused') }));
    await FakeIntersectionObserver.report(card(), true);
    const t = text(card());
    expect(t).toContain('Prévia suspensa');
    expect(t).not.toContain('Aguardando a primeira imagem');
    expect(t).not.toContain('Desatualizado');
  });

  it('frame novo depois da pausa: o selo sai sem esperar o instance.updated', async () => {
    const recente = new Date().toISOString();
    await render(online({ frame: frame('f2', recente), stream: stream('paused', { last_frame_at: OLD_TS }) }));
    await FakeIntersectionObserver.report(card(), true);
    expect(text(card())).not.toContain('Prévia suspensa');
    expect(text(card())).not.toContain('Desatualizado');
  });

  it('cada situação da tela tem o próprio texto (suspensa × aguardando × falha × servidor fora × desligado)', async () => {
    const casos: [Instance, string, string[]][] = [
      [online({ frame: frame('p', OLD_TS, true), stream: stream('paused', { last_frame_at: OLD_TS }) }), 'Prévia suspensa',
       ['Desatualizado', 'Captura falhando', 'Servidor desconectado']],
      [online({ frame: null, stream: stream('no_frame') }), 'Aguardando a primeira imagem', ['Prévia suspensa']],
      [online({ frame: frame('e', OLD_TS, true), stream: stream('capture_error', { consecutive_capture_failures: 4 }) }),
       'Captura falhando', ['Prévia suspensa']],
      [online({ frame: frame('w', OLD_TS, true), stream: stream('worker_offline') }), 'Servidor desconectado', ['Prévia suspensa']],
      [makeInstance(3, { state: 'stopped', stream: stream('device_offline') }), 'Emulador desligado', ['Prévia suspensa', 'Desatualizado']],
      [makeInstance(3, { state: 'hibernated', stream: stream('device_hibernated') }), 'Hibernado', ['Prévia suspensa', 'Desatualizado']],
    ];
    for (const [inst, esperado, ausentes] of casos) {
      await render(inst);
      await FakeIntersectionObserver.report(card(), true);
      const t = text(card());
      expect(t, `${inst.stream?.status}`).toContain(esperado);
      for (const a of ausentes) expect(t, `${inst.stream?.status} não deveria dizer ${a}`).not.toContain(a);
    }
  });
});

describe('regras puras', () => {
  const now = Date.parse('2026-09-26T12:00:00.000Z');

  it('isFrameStale: suspensa nunca é desatualizada, nem com frame.stale nem sem frame', () => {
    const paused = stream('paused', { last_frame_at: OLD_TS });
    expect(isFrameStale({ state: 'online', frame: frame('a', OLD_TS, true), stream: paused }, now, 5000)).toBe(false);
    expect(isFrameStale({ state: 'online', frame: null, stream: stream('paused') }, now, 5000)).toBe(false);
    // o resto continua como antes
    expect(isFrameStale({ state: 'online', frame: frame('a', OLD_TS, true), stream: stream('stale') }, now, 5000)).toBe(true);
    expect(isFrameStale({ state: 'online', frame: null }, now, 5000)).toBe(true);
    expect(isFrameStale({ state: 'online', frame: frame('a', OLD_TS) }, now, 5000)).toBe(true);
  });

  it('IA no controle: o frame chega a cada observação dela, e o prazo é o do ritmo dela (r-20260928195344-02ee9e)', () => {
    const settings = makeSnapshot().settings;
    const base = frameStaleLimitMs(settings, true);
    expect(frameStaleLimitMs(settings, true, true)).toBe(Math.max(base, AI_FRAME_MAX_AGE_MS));
    expect(AI_FRAME_MAX_AGE_MS).toBeGreaterThan(base);
    const dezSegundos = new Date(now - 10_000).toISOString();
    const tela = { state: 'online' as const, frame: frame('a', dezSegundos), stream: stream('live') };
    // um ciclo da IA (árvore, modelo, ação, assentamento) não deixa a tela cinza
    expect(isFrameStale(tela, now, frameStaleLimitMs(settings, true, true))).toBe(false);
    // o mesmo frame sem a IA segue a regra de sempre
    expect(isFrameStale(tela, now, frameStaleLimitMs(settings, true))).toBe(true);
    // o backend marcou `stale` (a IA passou do prazo dela): vale
    expect(isFrameStale({ ...tela, frame: frame('a', dezSegundos, true) }, now, frameStaleLimitMs(settings, true, true)))
      .toBe(true);
  });

  it('isPreviewPaused: só online, e só até chegar frame mais novo que o conhecido pela pausa', () => {
    const f = frame('a', OLD_TS);
    expect(isPreviewPaused({ state: 'online', frame: f, stream: stream('paused', { last_frame_at: OLD_TS }) })).toBe(true);
    expect(isPreviewPaused({ state: 'online', frame: null, stream: stream('paused') })).toBe(true);
    expect(isPreviewPaused({ state: 'stopped', frame: f, stream: stream('paused', { last_frame_at: OLD_TS }) })).toBe(false);
    expect(isPreviewPaused({ state: 'online', frame: f, stream: stream('live', { last_frame_at: OLD_TS }) })).toBe(false);
    expect(isPreviewPaused({ state: 'online', frame: frame('b', '2026-09-17T12:05:00.000Z'),
                             stream: stream('paused', { last_frame_at: OLD_TS }) })).toBe(false);
    // pausou sem frame; depois chegou um: a prévia voltou
    expect(isPreviewPaused({ state: 'online', frame: f, stream: stream('paused') })).toBe(false);
  });
});
