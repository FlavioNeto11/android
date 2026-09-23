// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { InstagramProfile } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { ProfilesPage } from './ProfilesPage';

const SENHA = 'senha-secreta-9!Zk';

function perfil(over: Partial<InstagramProfile> = {}): InstagramProfile {
  return {
    id: 'ig-1', username: 'mariana.costa91182', display_name: 'Mariana Costa', first_name: 'Mariana',
    last_name: 'Costa', birth_date: null, email: null, persona_id: null, persona_name: null, status: 'active',
    instance_id: 'android-02',
    locality: { worker_id: null, worker_name: 'este servidor', worker_state: 'online', known: true,
                available: true, moved: false, physical_id: null, detail: null },
    offline_policy: 'wait',
    credential: { configured: true, login_identifier: 'mariana.costa91182', status: 'active', failed_attempts: 0,
                  blocked_until: null, updated_at: '2026-09-17T10:00:00Z', last_used_at: null },
    session: { status: 'unknown', instance_id: 'android-02', observed_username: null, verified_at: null,
               detail: 'Perfil recém-cadastrado; sessão ainda não verificada.', stale: false },
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
    // Os dois juntos, como o snapshot entrega no app de verdade: a lista de aparelhos de tarefa é derivada do MAPA
    // (é nele que está o `kind`), não só da ordem.
    instances: Object.fromEntries(snap.instances.map((i) => [i.id, i])),
    instanceOrder: snap.instances.map((i) => i.id),
  });
  useUiStore.setState({ focusInstanceId: null });
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

/** Com o host do diálogo, como em App.tsx — sem ele a confirmação nunca aparece e o fluxo de remover nem roda. */
async function renderComDialogo(): Promise<void> {
  await act(async () => {
    root.render(<><ProfilesPage /><ConfirmHost /></>);
  });
  await waitFor(() => text().includes('Perfis do Instagram'));
}

describe('remover perfil', () => {
  it('desistir no diálogo NÃO apaga o perfil', async () => {
    // `confirm` devolve um objeto `{confirmed, note}`, sempre verdadeiro. Testar o objeto em vez de `confirmed`
    // fazia "Voltar" apagar o perfil e a credencial do mesmo jeito — e nenhum teste renderizava o diálogo para ver.
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('DELETE', /^\/api\/instagram\/profiles\//, () => json(null, 204));
    await renderComDialogo();
    await waitFor(() => text().includes('mariana.costa91182'));

    await click(byRole('button', /Remover perfil/i));
    await waitFor(() => text().includes('A credencial guardada no cofre também é apagada'));
    await click(byRole('button', /^Voltar$/i, byRole('dialog', /Remover/)));
    await waitFor(() => !text().includes('A credencial guardada no cofre também é apagada'));
    expect(backend.callsTo('DELETE', /profiles/)).toHaveLength(0);
    expect(text()).toContain('mariana.costa91182');
  });

  it('confirmar no diálogo apaga', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('DELETE', /^\/api\/instagram\/profiles\//, () => json(null, 204));
    await renderComDialogo();
    await waitFor(() => text().includes('mariana.costa91182'));

    await click(byRole('button', /Remover perfil/i));
    await waitFor(() => text().includes('A credencial guardada no cofre também é apagada'));
    await click(byRole('button', /^Remover$/i, byRole('dialog', /Remover/)));
    await waitFor(() => backend.callsTo('DELETE', /profiles\/ig-1$/).length === 1);
  });
});

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

  it('Salvar e conectar já dispara a conexão com o Instagram', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('POST', /^\/api\/instagram\/profiles$/, () => json(perfil(), 201));
    backend.on('POST', /connect$/, () => json({ accepted: true, profile_id: 'ig-1', instance_id: 'android-02' }, 202));
    await render();
    await click(byRole('button', /Novo perfil/i));
    await waitFor(() => text().includes('Novo perfil Instagram'));
    await setValue(byRole('textbox', /Usuário do Instagram/i) as HTMLInputElement, 'mariana.costa91182');
    await setValue(container.ownerDocument.querySelector('input[type="password"]') as HTMLInputElement, SENHA);
    await setValue(byRole('combobox', /Aparelho/i) as HTMLSelectElement, 'android-02');

    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));
    await click(byRole('button', /Salvar e conectar/i));
    await waitFor(() => backend.callsTo('POST', /connect$/).length === 1);
    expect(text()).not.toContain(SENHA);
  });

  it('o botão conectar fica bloqueado sem senha ou sem aparelho, e explica o motivo', async () => {
    const semCredencial = perfil({
      credential: { configured: false, login_identifier: null, status: null, failed_attempts: 0,
                    blocked_until: null, updated_at: null, last_used_at: null },
    });
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([semCredencial]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    await waitFor(() => text().includes('não configurada'));
    const conectar = byRole('button', /Conectar/i);
    expect(conectar.getAttribute('aria-disabled')).toBe('true');
    expect(text()).toContain('Abra o perfil e guarde a senha na aba Autenticação antes de conectar.');
    await click(conectar);
    expect(backend.callsTo('POST', /connect$/)).toHaveLength(0);
  });

  it('com senha e aparelho, o botão conectar DISPARA — não basta ele existir', async () => {
    // O teste acima só provava o caso bloqueado. Sem este, um botão permanentemente inerte passava despercebido:
    // ele aparecia habilitado, exibia um motivo falso e o clique não fazia nada.
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('POST', /connect$/, () => json({ accepted: true, profile_id: 'ig-1', instance_id: 'android-02' }, 202));
    await render();
    await waitFor(() => text().includes('mariana.costa91182'));
    const conectar = byRole('button', /Conectar/i);
    expect(conectar.getAttribute('aria-disabled')).toBeNull();
    expect(text()).not.toContain('Vincule um aparelho');
    await click(conectar);
    await waitFor(() => backend.callsTo('POST', /connect$/).length === 1);
  });

  it('com aparelho vinculado, verificar conta também dispara', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('POST', /verify$/, () => json({ accepted: true }, 202));
    await render();
    await waitFor(() => text().includes('mariana.costa91182'));
    await click(byRole('button', /Verificar conta/i));
    await waitFor(() => backend.callsTo('POST', /verify$/).length === 1);
  });

  it('perfil conectado mostra a conta observada e oferece reconectar', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil({
      session: { status: 'session_ready', instance_id: 'android-02', observed_username: 'mariana.costa91182',
                 verified_at: '2026-09-17T10:00:00Z', detail: '@mariana.costa91182 confirmado na tela',
                 stale: false },
    })]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    await waitFor(() => text().includes('Conectado'));
    expect(text()).toContain('Conta observada');
    expect(byRole('button', /Reconectar/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------- fila "Aguardando intervenção" (achado #106)
describe('fila de intervenção', () => {
  it('lista perfil, aparelho e motivo de quem está preso, e ignora quem não está', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([
      perfil({
        id: 'ig-1', username: 'mariana.costa91182',
        session: { status: 'auth_challenge', instance_id: 'android-02', observed_username: null,
                   verified_at: '2026-09-23T09:00:00Z', detail: 'O Instagram exige confirmação adicional.',
                   stale: false },
      }),
      perfil({ id: 'ig-2', username: 'lucas.almeida9484', instance_id: 'android-01' }),  // session_ready: fora da fila
    ]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    await waitFor(() => text().includes('Aguardando intervenção'));

    expect(text()).toContain('mariana.costa91182');
    expect(text()).toContain('android-02');
    expect(text()).toContain('O Instagram exige confirmação adicional.');
    // "lucas.almeida9484" está `unknown` (padrão do fixture), não `session_ready` — mas o que importa aqui é que
    // ele NÃO aparece na fila, que só existe uma vez (o card do perfil também mostra o @ dele).
    expect(text().match(/lucas\.almeida9484/g)?.length ?? 0).toBe(1);
  });

  it('sem ninguém preso, a fila não aparece', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil()]));   // status padrão: unknown
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    await waitFor(() => text().includes('mariana.costa91182'));
    expect(text()).not.toContain('Aguardando intervenção');
  });

  it('assumir controle na fila pede o lease e abre o painel de foco do aparelho certo', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil({
      session: { status: 'wrong_account', instance_id: 'android-02', observed_username: 'outra.conta',
                 verified_at: '2026-09-23T09:00:00Z', detail: 'a conta aberta é @outra.conta', stale: false },
    })]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('POST', /\/instances\/android-02\/control\/take$/, () => json({ status: 'granted', lease_id: 'lease-1' }));
    await render();
    await waitFor(() => text().includes('Aguardando intervenção'));

    await click(byRole('button', /Assumir controle/i));
    await waitFor(() => backend.callsTo('POST', /control\/take$/).length === 1);
    // Abre o painel de Foco do aparelho certo — é ele quem mostra a tela para a pessoa resolver, local ou
    // remoto (o painel em si é testado em `FocusPanel.test.tsx`; aqui importa que a fila manda para lá).
    await waitFor(() => useUiStore.getState().focusInstanceId === 'android-02');
  });
});

// ---------------------------------------------------------------- localidade (E9, item 4.4)
describe('onde o perfil vive', () => {
  it('o cartão diz em que servidor os dados vivem, e avisa quando ele mudou', async () => {
    // Antes o cartão mostrava só `instance_id`: um perfil cujo servidor mudou (ou caiu) aparecia igual aos
    // demais, e a pessoa só descobria quando a tarefa falhava no aparelho errado.
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([perfil({
      locality: { worker_id: 'worker-lan-02', worker_name: 'Notebook da sala', worker_state: 'offline',
                  known: true, available: false, moved: true, physical_id: null,
                  detail: 'os dados deste perfil vivem em Notebook da sala, mas android-02 aponta hoje para este servidor' },
    })]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    await waitFor(() => text().includes('mariana.costa91182'));
    expect(text()).toContain('Servidor');
    expect(text()).toContain('Notebook da sala');
    expect(text()).toContain('mudou de servidor');
    expect(text()).toContain('indisponível');
    expect(text()).toContain('aponta hoje para este servidor');
  });

  it('o select de criação agrupa os aparelhos por servidor', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/workers$/, () => json([
      { id: 'w-local', name: 'Servidor central', appium_mode: 'central', max_slots: 4, verbs: [],
        state: 'online', observed_state: 'online', maintenance: false, connected: true, local: true,
        resources: {}, devices: [], enrolled_at: '2026-09-20T10:00:00Z' },
    ]));
    await render();
    await click(byRole('button', /Novo perfil/i));
    await waitFor(() => text().includes('Um perfil por aparelho'));
    const grupos = [...container.querySelectorAll('optgroup')].map((g) => g.getAttribute('label'));
    expect(grupos.length).toBeGreaterThan(0);
    // O snapshot de teste não carimba `worker_id` nos aparelhos: eles caem no rótulo do servidor local.
    expect(grupos[0]).toBeTruthy();
    expect(container.querySelectorAll('optgroup option').length).toBeGreaterThan(0);
  });
});
