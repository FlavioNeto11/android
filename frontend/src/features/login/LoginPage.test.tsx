// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { api } from '../../api/client';
import { LiveSocket } from '../../api/ws';
import { useSessionStore } from '../../store/session';
import { FakeBackend, FakeWebSocket, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { LoginPage } from './LoginPage';

/**
 * O item 9.1 em três perguntas: a tela pede a chave quando (e só quando) esta origem exige? o que ela manda
 * chega ao backend? e quando a sessão cai, o painel volta para cá sozinho?
 */

const backend = new FakeBackend();
let root: Root;
let container: HTMLDivElement;

async function montar(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<LoginPage />);
  });
}

beforeEach(() => {
  installBrowserStubs();
  backend.calls = [];
  backend.install();
  window.localStorage.clear();
  useSessionStore.setState({ checked: true, operator: null, tokenRequired: false, busy: false, error: null,
                             lastName: '' });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('tela de login', () => {
  it('no loopback pede só o nome, e o nome vira a identidade da sessão', async () => {
    backend.on('POST', /^\/api\/login$/, (c) => json({ operator: (c.body as { operator: string }).operator,
                                                       token_required: false, expires_at: null }));
    await montar();
    expect(text(container)).toContain('não é preciso chave de acesso');

    await act(async () => {
      const campo = byRole('textbox', /Seu nome/, container) as HTMLInputElement;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(campo, 'Ana Ribeiro');
      campo.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(byRole('button', /Entrar/, container));

    await waitFor(() => expect(useSessionStore.getState().operator).toBe('Ana Ribeiro'));
    const chamadas = backend.callsTo('POST', /^\/api\/login$/);
    expect(chamadas).toHaveLength(1);
    expect(chamadas[0]?.body).toEqual({ operator: 'Ana Ribeiro' });   // sem chave: o loopback não a exige
  });

  it('de fora, pede a chave de acesso e a manda junto — é o que o WebSocket e as <img> não conseguem fazer', async () => {
    useSessionStore.setState({ tokenRequired: true });
    backend.on('POST', /^\/api\/login$/, () => json({ operator: 'Ana', token_required: true, expires_at: null }));
    await montar();
    expect(text(container)).toContain('API_TOKEN');

    for (const [rotulo, valor] of [[/Seu nome/, 'Ana'], [/Chave de acesso/, 'tk-de-teste']] as const) {
      await act(async () => {
        const campo = byRole('textbox', rotulo, container) as HTMLInputElement;
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(campo, valor);
        campo.dispatchEvent(new Event('input', { bubbles: true }));
      });
    }
    await click(byRole('button', /Entrar/, container));

    await waitFor(() => expect(useSessionStore.getState().operator).toBe('Ana'));
    expect(backend.callsTo('POST', /^\/api\/login$/)[0]?.body).toEqual({ operator: 'Ana', token: 'tk-de-teste' });
  });

  it('chave errada explica o que fazer em vez de "tente novamente"', async () => {
    useSessionStore.setState({ tokenRequired: true });
    backend.on('POST', /^\/api\/login$/, () => apiError(401, 'invalid_credentials', 'Credencial inválida.'));
    await montar();
    await act(async () => {
      const campo = byRole('textbox', /Seu nome/, container) as HTMLInputElement;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(campo, 'Ana');
      campo.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(byRole('button', /Entrar/, container));

    await waitFor(() => expect(text(container)).toContain('Não foi possível entrar'));
    expect(text(container)).toContain('API_TOKEN');
    expect(useSessionStore.getState().operator).toBeNull();
  });

  it('nome curto demais nem chega ao backend', async () => {
    await montar();
    await click(byRole('button', /Entrar/, container));
    expect(backend.callsTo('POST', /^\/api\/login$/)).toHaveLength(0);
    expect(text(container)).toContain('pelo menos 2 caracteres');
  });
});

describe('a sessão caindo', () => {
  it('um 401 em qualquer chamada derruba a sessão — o painel não fica repetindo "tente novamente"', async () => {
    useSessionStore.setState({ operator: 'Ana', checked: true });
    backend.on('GET', /^\/api\/snapshot$/, () => apiError(401, 'unauthorized', 'Credencial ausente ou inválida.'));
    await montar();
    await expect(api.snapshot()).rejects.toThrow();
    expect(useSessionStore.getState().operator).toBeNull();
    expect(useSessionStore.getState().tokenRequired).toBe(true);
  });

  it('depois de "Sair", a tela volta pedindo a chave de novo — e não um login que o backend recusa', async () => {
    // O defeito que isto tranca: `token_required` respondia sobre ESTA requisição, então com a sessão aberta
    // virava `false`; ao sair, o campo da chave sumia e o login seguinte era recusado sem saída visível.
    useSessionStore.setState({ operator: 'Ana', tokenRequired: true, checked: true });
    backend
      .on('POST', /^\/api\/logout$/, () => json({ ended: true }))
      .on('GET', /^\/api\/session$/, () => json({ operator: null, token_required: true, expires_at: null }));

    await act(async () => { await useSessionStore.getState().signOut(); });
    expect(useSessionStore.getState().operator).toBeNull();
    expect(useSessionStore.getState().tokenRequired).toBe(true);

    await montar();
    expect(text(container)).toContain('API_TOKEN');
  });

  it('o WebSocket fechado com 4401 chega ao painel COM o código — sem ele, backoff eterno', async () => {
    await montar();
    const vistos: Array<[string, number | undefined]> = [];
    const socket = new LiveSocket(0, { onHello: () => undefined, onEvent: () => undefined,
                                       onResync: () => undefined,
                                       onClose: (reason, code) => vistos.push([reason, code]) }, null);
    FakeWebSocket.last.serverClose(4401, '');
    expect(vistos).toEqual([['Conexão encerrada (código 4401)', 4401]]);
    socket.close();
  });
});
