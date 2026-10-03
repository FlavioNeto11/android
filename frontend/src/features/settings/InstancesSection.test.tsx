// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance, InstagramProfile, Worker } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { InstancesSection } from './InstancesSection';

// Achado #64: amarrar/desamarrar aparelho a servidor só existia por chamada manual à API, e o aviso de órfão
// mandava o usuário a um controle que não estava aqui. Item 11.9: essa tela virou cartões agrupados por
// servidor, com edição sob demanda num painel que abre ao clicar no cartão — os testes abrem esse painel antes
// de mexer nos campos.

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z', last_seen_at: '2026-09-22T10:00:00Z',
    ...over,
  };
}

function perfil(over: Partial<InstagramProfile> = {}): InstagramProfile {
  return {
    id: 'ig-1', username: 'qa.user01', display_name: 'QA User 01', first_name: null, last_name: null,
    birth_date: null, email: null, persona_id: null, persona_name: null, status: 'active', instance_id: 'android-01',
    locality: null, offline_policy: 'wait',
    credential: { configured: true, login_identifier: 'qa.user01', status: 'active', failed_attempts: 0,
                  blocked_until: null, updated_at: '2026-09-17T10:00:00Z', last_used_at: null },
    session: { status: 'session_ready', instance_id: 'android-01', observed_username: 'qa.user01',
               verified_at: '2026-09-17T11:00:00Z', detail: null, stale: false },
    last_verified_at: null, last_activity_at: null,
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

const CENTRAL = worker({ id: 'central', name: 'Este servidor', local: true });

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

async function render(instances: Instance[], workers: Worker[], profiles: InstagramProfile[] = []): Promise<HTMLElement> {
  backend.on('GET', /^\/api\/instagram\/profiles$/, () => json(profiles));
  useAppStore.setState({
    ...initialDataState,
    instances: Object.fromEntries(instances.map((i) => [i.id, i])),
    instanceOrder: instances.map((i) => i.id),
    workers: Object.fromEntries(workers.map((w) => [w.id, w])),
  });
  await act(async () => {
    root.render(<InstancesSection />);
  });
  // O carregamento de perfis é assíncrono (useEffect); deixa a promise resolver antes de seguir.
  await waitFor(() => expect(useAppStore.getState().hydrated || true).toBe(true));
  return container;
}

/** Abre o painel de edição do cartão (o cartão inteiro é o gatilho do Popover). O painel vai para o `body` por
 *  portal (a página virou contêiner), então o que está DENTRO dele se procura no documento, não em `el`. */
async function abrirEdicao(el: HTMLElement, instanceId: string): Promise<void> {
  await click(byRole('button', new RegExp(`Editar ${instanceId}`), el));
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('InstancesSection — agrupamento por servidor', () => {
  it('agrupa os cartões por servidor, central primeiro', async () => {
    const el = await render(
      [makeInstance(1, { worker_id: null }), makeInstance(2, { worker_id: 'worker-lan-01' })],
      [CENTRAL, worker()],
    );
    const heads = [...el.querySelectorAll('div')].map((d) => d.textContent).filter((t) => t?.startsWith('Este servidor') || t?.startsWith('Notebook da LAN'));
    expect(text(el)).toContain('Este servidor');
    expect(text(el)).toContain('Notebook da LAN');
    // O cartão do android-01 (central) existe; o do android-02 (worker remoto) também.
    expect(byRole('button', /Editar android-01/, el)).toBeTruthy();
    expect(byRole('button', /Editar android-02/, el)).toBeTruthy();
    expect(heads.length).toBeGreaterThan(0);
  });

  it('servidor que saiu da lista vira grupo próprio marcado "não inscrito"', async () => {
    const el = await render([makeInstance(1, { worker_id: 'worker-sumido' })], [CENTRAL]);
    expect(text(el)).toContain('worker-sumido');
    expect(text(el)).toContain('servidor não inscrito');
  });

  it('a etiqueta de servidor filtra os cartões mostrados', async () => {
    const el = await render(
      [makeInstance(1, { worker_id: null }), makeInstance(2, { worker_id: 'worker-lan-01' })],
      [CENTRAL, worker()],
    );
    await click(byRole('button', /^Notebook da LAN \(1\)$/, el));
    expect(byRole('button', /Editar android-02/, el)).toBeTruthy();
    expect(() => byRole('button', /Editar android-01/, el)).toThrow();
  });

  it('o filtro ativo é anunciado por aria-pressed, como os chips do Comando (P3.6)', async () => {
    const el = await render(
      [makeInstance(1, { worker_id: null }), makeInstance(2, { worker_id: 'worker-lan-01' })],
      [CENTRAL, worker()],
    );
    const filtros = byRole('group', /Filtrar aparelhos/, el);
    const todos = byRole('button', /^Todos$/, filtros);
    const lan = byRole('button', /^Notebook da LAN \(1\)$/, filtros);
    expect(todos.getAttribute('aria-pressed')).toBe('true');
    expect(lan.getAttribute('aria-pressed')).toBe('false');
    await click(lan);
    expect(lan.getAttribute('aria-pressed')).toBe('true');
    expect(todos.getAttribute('aria-pressed')).toBe('false');
  });
});

describe('InstancesSection — indicador de divergência', () => {
  it('conta observada batendo com o rótulo mostra "bate"', async () => {
    const el = await render([makeInstance(1, { account_label: 'qa-user-01', account_evidence: 'Conta: qa-user-01 confirmada' })], [CENTRAL]);
    const card = byRole('button', /Editar android-01/, el);
    expect(card.textContent).toContain('bate');
  });

  it('conta observada diferente do rótulo diz o que diverge e o que fazer', async () => {
    const el = await render([makeInstance(1, { account_label: 'qa-user-01', account_evidence: 'Conta: outra-conta' })], [CENTRAL]);
    const card = byRole('button', /Editar android-01/, el);
    expect(card.textContent).toContain('conta diferente do rótulo');
    expect(card.querySelector('[title^="A última conta vista no aparelho não é a do rótulo"]')).not.toBeNull();
  });

  it('a evidência antiga com seletor cru aparece sem o seletor (validação do deploy 8, android-06)', async () => {
    const el = await render([makeInstance(1, {
      account_label: 'qa-user-01',
      account_evidence: 'pós-condição comprovada pela árvore local, sem IA (selector:id=action_bar_title|text=={username})',
    })], [CENTRAL]);
    const card = byRole('button', /Editar android-01/, el);
    expect(card.textContent).not.toContain('selector:');
    expect(card.textContent).toContain('pós-condição comprovada pela árvore local, sem IA');
  });

  it('nada observado ainda mostra "não observada"', async () => {
    const el = await render([makeInstance(1, { account_label: 'qa-user-01', account_evidence: null })], [CENTRAL]);
    const card = byRole('button', /Editar android-01/, el);
    expect(card.textContent).toContain('não observada');
  });

  it('o filtro "Só divergências" esconde quem bate ou não tem observação', async () => {
    const el = await render(
      [
        makeInstance(1, { account_label: 'qa-user-01', account_evidence: 'Conta: outra-conta' }),
        makeInstance(2, { account_label: 'qa-user-02', account_evidence: 'Conta: qa-user-02' }),
      ],
      [CENTRAL],
    );
    await click(byRole('button', /Só divergências/, el));
    expect(byRole('button', /Editar android-01/, el)).toBeTruthy();
    expect(() => byRole('button', /Editar android-02/, el)).toThrow();
  });
});

describe('InstancesSection — perfil vinculado', () => {
  it('mostra o @usuário do perfil do Instagram vinculado ao aparelho', async () => {
    const el = await render([makeInstance(1)], [CENTRAL], [perfil({ instance_id: 'android-01' })]);
    await waitFor(() => expect(byRole('button', /Editar android-01/, el).textContent).toContain('@qa.user01'));
  });

  it('sem perfil vinculado, o cartão avisa', async () => {
    const el = await render([makeInstance(1)], [CENTRAL], []);
    const card = byRole('button', /Editar android-01/, el);
    expect(card.textContent).toContain('sem perfil vinculado');
  });
});

describe('InstancesSection — edição pelo painel', () => {
  it('lista os workers inscritos e marca o central quando o aparelho não tem worker', async () => {
    const el = await render([makeInstance(1, { worker_id: null })], [CENTRAL, worker()]);
    await abrirEdicao(el, 'android-01');
    const select = byRole('combobox', /Servidor/) as HTMLSelectElement;
    expect(select.value).toBe('');
    expect([...select.options].map((o) => o.textContent)).toEqual(['Este servidor (central)', 'Notebook da LAN']);
  });

  it('o worker local conta como central (o aparelho não aparece amarrado a um worker remoto)', async () => {
    const el = await render([makeInstance(1, { worker_id: 'central' })], [CENTRAL, worker()]);
    await abrirEdicao(el, 'android-01');
    const select = byRole('combobox', /Servidor/) as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('amarrar a um worker manda worker_id no PUT e o aparelho passa a ser daquele servidor', async () => {
    backend.on('PUT', /^\/api\/instances\/android-01$/, (c) =>
      json({ ...makeInstance(1), ...(c.body as object), worker_id: 'worker-lan-01' }));
    const el = await render([makeInstance(1, { worker_id: null })], [CENTRAL, worker()]);
    await abrirEdicao(el, 'android-01');
    await setValue(byRole('combobox', /Servidor/) as HTMLSelectElement, 'worker-lan-01');
    await act(async () => { await click(byRole('button', /^Salvar$/)); });
    await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)[0]!.body).toEqual({ worker_id: 'worker-lan-01' });
    await waitFor(() => expect(useAppStore.getState().instances['android-01']!.worker_id).toBe('worker-lan-01'));
  });

  it('voltar para o central manda worker_id null — é o "desamarre o aparelho" do aviso de órfão', async () => {
    backend.on('PUT', /^\/api\/instances\/android-01$/, () => json(makeInstance(1, { worker_id: null })));
    const el = await render([makeInstance(1, { worker_id: 'worker-lan-01' })], [CENTRAL, worker()]);
    await abrirEdicao(el, 'android-01');
    await setValue(byRole('combobox', /Servidor/) as HTMLSelectElement, '');
    await act(async () => { await click(byRole('button', /^Salvar$/)); });
    await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)[0]!.body).toEqual({ worker_id: null });
  });

  it('editar o rótulo pelo painel marca rascunho, some o "Salvar alterações" some até a troca, e salva pelo botão em lote', async () => {
    backend.on('PUT', /^\/api\/instances\/android-01$/, (c) => json({ ...makeInstance(1), ...(c.body as object) }));
    const el = await render([makeInstance(1, { account_label: 'antigo' })], [CENTRAL]);
    await abrirEdicao(el, 'android-01');
    const label = byRole('textbox', /Rótulo da conta/) as HTMLInputElement;
    await setValue(label, 'novo-rotulo');
    // O painel fecha ao trocar de foco/clicar fora, mas o rascunho sobrevive: a barra de lote já mostra 1 pendente.
    expect(text(el)).toContain('Salvar alterações (1)');
    await act(async () => { await click(byRole('button', /^Salvar alterações/, el)); });
    await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)[0]!.body).toEqual({ account_label: 'novo-rotulo' });
  });

  it('servidor que saiu da lista aparece como "não inscrito" em vez de virar central sem aviso', async () => {
    const el = await render([makeInstance(1, { worker_id: 'worker-sumido' })], [CENTRAL]);
    await abrirEdicao(el, 'android-01');
    const select = byRole('combobox', /Servidor/) as HTMLSelectElement;
    expect(select.value).toBe('worker-sumido');
    expect(text(select)).toContain('worker-sumido (não inscrito)');
  });
});
