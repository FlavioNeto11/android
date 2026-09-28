// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { documentoVisivel, intervaloVisivel } from './polling';

// P3.4: releitura periódica só com a aba visível; ao voltar, relê na hora e retoma o intervalo.

let visibility: DocumentVisibilityState = 'visible';

function mudarVisibilidade(v: DocumentVisibilityState): void {
  visibility = v;
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vi.useFakeTimers();
  visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
});

afterEach(() => {
  vi.useRealTimers();
  delete (document as { visibilityState?: unknown }).visibilityState;
});

describe('intervaloVisivel', () => {
  it('anda com a aba visível e para quando ela fica oculta', () => {
    const tick = vi.fn();
    const desligar = intervaloVisivel(tick, 1000);
    vi.advanceTimersByTime(3000);
    expect(tick).toHaveBeenCalledTimes(3);

    mudarVisibilidade('hidden');
    vi.advanceTimersByTime(5000);
    expect(tick).toHaveBeenCalledTimes(3); // nada em segundo plano
    desligar();
  });

  it('ao voltar do segundo plano relê na hora e retoma o intervalo', () => {
    const tick = vi.fn();
    const desligar = intervaloVisivel(tick, 1000);
    mudarVisibilidade('hidden');
    vi.advanceTimersByTime(2500);
    expect(tick).not.toHaveBeenCalled();

    mudarVisibilidade('visible');
    expect(tick).toHaveBeenCalledTimes(1); // quem volta quer o dado de agora
    vi.advanceTimersByTime(2000);
    expect(tick).toHaveBeenCalledTimes(3);
    desligar();
  });

  it('aberto com a aba oculta não dispara nada até ela voltar', () => {
    visibility = 'hidden';
    const tick = vi.fn();
    const desligar = intervaloVisivel(tick, 1000);
    vi.advanceTimersByTime(3000);
    expect(tick).not.toHaveBeenCalled();
    mudarVisibilidade('visible');
    expect(tick).toHaveBeenCalledTimes(1);
    desligar();
  });

  it('desligar para o intervalo e deixa de ouvir a visibilidade', () => {
    const tick = vi.fn();
    const desligar = intervaloVisivel(tick, 1000);
    desligar();
    vi.advanceTimersByTime(3000);
    mudarVisibilidade('hidden');
    mudarVisibilidade('visible');
    expect(tick).not.toHaveBeenCalled();
  });

  it('documentoVisivel só considera oculta a aba explicitamente `hidden`', () => {
    expect(documentoVisivel()).toBe(true);
    visibility = 'hidden';
    expect(documentoVisivel()).toBe(false);
  });
});
