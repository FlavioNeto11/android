// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { makePersona } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { usePersonas } from './usePersonas';

/**
 * Polimento (e) do deploy 2: várias telas montadas juntas pediam `GET /personas` cada uma. A leitura em andamento é
 * uma só; depois que ela volta, uma nova montagem lê de novo. Prova `simulated`.
 */
let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.on('GET', /^\/api\/personas$/, () => json([makePersona('p1', 'Ana Lima')]));
  backend.install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

function Quem({ nome }: { nome: string }) {
  const pessoas = usePersonas();
  return <p>{nome}: {pessoas ? pessoas.map((p) => p.name).join(',') : '…'}</p>;
}

describe('usePersonas', () => {
  it('três usos montados juntos fazem uma leitura só e todos recebem a lista', async () => {
    await act(async () => { root.render(<><Quem nome="barra" /><Quem nome="tela" /><Quem nome="comando" /></>); });
    await waitFor(() => expect(text(container)).toContain('comando: Ana Lima'));
    expect(text(container)).toContain('barra: Ana Lima');
    expect(text(container)).toContain('tela: Ana Lima');
    expect(backend.callsTo('GET', /^\/api\/personas$/)).toHaveLength(1);
  });

  it('depois que a leitura voltou, outra montagem lê de novo (a lista pode ter mudado)', async () => {
    await act(async () => { root.render(<Quem nome="a" />); });
    await waitFor(() => expect(text(container)).toContain('a: Ana Lima'));
    await act(async () => { root.render(<><Quem nome="a" /><Quem nome="b" /></>); });
    await waitFor(() => expect(text(container)).toContain('b: Ana Lima'));
    expect(backend.callsTo('GET', /^\/api\/personas$/)).toHaveLength(2);
  });
});
