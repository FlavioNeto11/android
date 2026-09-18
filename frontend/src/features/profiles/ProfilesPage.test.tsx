// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { InstagramProfile } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { ProfilesPage } from './ProfilesPage';

const SENHA = 'senha-secreta-9!Zk';

function perfil(over: Partial<InstagramProfile> = {}): InstagramProfile {
  return {
    id: 'ig-1', username: 'mariana.costa91182', display_name: 'Mariana Costa', first_name: 'Mariana',
    last_name: 'Costa', birth_date: null, email: null, persona_id: null, persona_name: null, status: 'active',
    instance_id: 'android-02',
    credential: { configured: true, login_identifier: 'mariana.costa91182', status: 'active', failed_attempts: 0,
                  blocked_until: null, updated_at: '2026-09-17T10:00:00Z', last_used_at: null },
    session: { status: 'unknown', instance_id: 'android-02', observed_username: null, verified_at: null,
               detail: 'Perfil recém-cadastrado; sessão ainda não verificada.' },
    last_verified_at: null, last_activity_at: null,
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  const snap = makeSnapshot();
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    hydrateCount: 1,
    instanceOrder: snap.instances.map((i) => i.id),
  });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(): Promise<void> {
  await act(async () => {
    root.render(<ProfilesPage />);
  });
  await waitFor(() => text().includes('Perfis do Instagram'));
}

describe('perfis', () => {
  it('mostra a senha apenas como máscara, nunca o valor', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    await waitFor(() => text().includes('mariana.costa91182'));
    expect(text()).toContain('••••••••••••');
    expect(text()).toContain('Não verificada');          // sessão só vale depois de observar a tela
    expect(text()).toContain('android-02');
  });

  it('avisa quando não há perfil e oferece o cadastro', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    expect(text()).toContain('Nenhum perfil cadastrado');
    expect(byRole('button', /Novo perfil/i)).toBeTruthy();
  });

  it('cadastra pelo formulário e nunca exibe a senha depois de salvar', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();

    await click(byRole('button', /Novo perfil/i));
    await waitFor(() => text().includes('Novo perfil Instagram'));

    const usuario = byRole('textbox', /Usuário do Instagram/i) as HTMLInputElement;
    await setValue(usuario, 'mariana.costa91182');
    const senha = container.ownerDocument.querySelector('input[type="password"]') as HTMLInputElement;
    await setValue(senha, SENHA);
    const aparelho = byRole('combobox', /Aparelho/i) as HTMLSelectElement;
    await setValue(aparelho, 'android-02');

    let enviado: Record<string, unknown> = {};
    backend.on('POST', /^\/api\/instagram\/profiles$/, (call) => {
      enviado = (call.body ?? {}) as Record<string, unknown>;
      return json(perfil(), 201);
    });
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));

    await click(byRole('button', /Salvar e conectar/i));
    await waitFor(() => text().includes('mariana.costa91182') && !text().includes('Novo perfil Instagram'));

    expect(enviado.username).toBe('mariana.costa91182');
    expect(enviado.password).toBe(SENHA);                 // a senha SOBE
    expect(text()).not.toContain(SENHA);                  // e nunca aparece de volta na tela
    expect(text()).toContain('••••••••••••');
  });

  it('exige usuário, senha e aparelho antes de enviar', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    await click(byRole('button', /Novo perfil/i));
    await waitFor(() => text().includes('Novo perfil Instagram'));

    await click(byRole('button', /Salvar e conectar/i));
    await waitFor(() => text().includes('Informe a senha'));
    expect(text()).toContain('Escolha o aparelho');
    expect(backend.callsTo('POST', /^\/api\/instagram\/profiles$/)).toHaveLength(0);
  });

  it('só oferece aparelhos que ainda não têm perfil', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));   // android-02 já está usado
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    await click(byRole('button', /Novo perfil/i));
    await waitFor(() => text().includes('Novo perfil Instagram'));
    const aparelho = byRole('combobox', /Aparelho/i) as HTMLSelectElement;
    const opcoes = [...aparelho.options].map((o) => o.value);
    expect(opcoes).not.toContain('android-02');
    expect(opcoes).toContain('android-01');
  });
});
