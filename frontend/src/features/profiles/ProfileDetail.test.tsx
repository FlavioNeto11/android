// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { InstagramProfile } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { ProfileDetail } from './ProfileDetail';

const SENHA = 'senha-secreta-9!Zk';

function perfil(over: Partial<InstagramProfile> = {}): InstagramProfile {
  return {
    id: 'ig-1', username: 'mariana.costa91182', display_name: 'Mariana Costa', first_name: 'Mariana',
    last_name: 'Costa', birth_date: null, email: null, persona_id: 'persona-1', persona_name: 'Mariana — fotografia',
    status: 'active', instance_id: 'android-02',
    credential: { configured: true, login_identifier: 'mariana.costa91182', status: 'active', failed_attempts: 0,
                  blocked_until: null, updated_at: '2026-09-17T10:00:00Z', last_used_at: null },
    session: { status: 'session_ready', instance_id: 'android-02', observed_username: 'mariana.costa91182',
               verified_at: '2026-09-17T11:00:00Z', detail: '@mariana.costa91182 confirmado na tela',
               stale: false },
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
  backend.on('GET', /approvals/, () => json([]));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function abrir(p: InstagramProfile = perfil()): Promise<void> {
  await act(async () => {
    root.render(<ProfileDetail profile={p} onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('@mariana.costa91182'));
}

it('mostra as nove abas do perfil e abre na visão geral', async () => {
  await abrir();
  for (const aba of ['Visão geral', 'Persona', 'Aparelho', 'Autenticação', 'Memória', 'Interações', 'Aprovações',
                     'Execuções', 'Configurações']) {
    expect(byRole('tab', new RegExp(aba, 'i'))).toBeTruthy();
  }
  expect(text()).toContain('guardada cifrada');
  expect(text()).not.toContain(SENHA);
});

const SEM_SENHA = { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                   updated_at: null, last_used_at: null };

async function abrirAutenticacao(p: InstagramProfile, onChanged: () => Promise<void> = async () => {}): Promise<void> {
  backend.on('GET', /auth-attempts$/, () => json([]));
  await act(async () => {
    root.render(<ProfileDetail profile={p} onBack={() => {}} onChanged={onChanged} />);
  });
  await click(byRole('tab', /Autenticação/i));
  await waitFor(() => text().includes('Senha do Instagram'));
}

it('perfil cadastrado sem senha recebe a senha pela aba Autenticação, e o campo esvazia', async () => {
  let recarregado = 0;
  backend.on('PUT', /\/credential$/, () => json(perfil()));
  await abrirAutenticacao(perfil({ credential: SEM_SENHA }), async () => { recarregado += 1; });
  expect(byRole('button', /Conectar/i).getAttribute('aria-disabled')).toBe('true');

  const campo = container.querySelector('input[type="password"]') as HTMLInputElement;
  expect(campo.getAttribute('autocomplete')).toBe('new-password');       // o navegador não preenche sozinho
  await setValue(campo, SENHA);
  await click(byRole('button', /Guardar senha/i));
  await waitFor(() => backend.callsTo('PUT', /\/credential$/).length === 1);
  expect(backend.callsTo('PUT', /\/credential$/)[0]?.body).toEqual({ password: SENHA });
  await waitFor(() => recarregado === 1);                                   // o perfil é relido: Conectar destrava
  expect(campo.value).toBe('');                                            // a senha não fica no formulário
  expect(text()).not.toContain(SENHA);
});

it('sem senha digitada nada é enviado — nem pelo botão, nem pelo Enter no campo', async () => {
  await abrirAutenticacao(perfil({ credential: SEM_SENHA }));
  const botao = byRole('button', /Guardar senha/i);
  expect(botao.getAttribute('aria-disabled')).toBe('true');
  await click(botao);
  const form = container.querySelector('input[type="password"]')!.closest('form') as HTMLFormElement;
  await act(async () => { form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
  expect(backend.callsTo('PUT', /\/credential$/)).toHaveLength(0);
});

it('falha ao guardar também esvazia o campo e não relê o perfil', async () => {
  let recarregado = 0;
  backend.on('PUT', /\/credential$/, () => apiError(503, 'secret_store_unavailable', 'O cofre está trancado.'));
  await abrirAutenticacao(perfil({ credential: SEM_SENHA }), async () => { recarregado += 1; });
  const campo = container.querySelector('input[type="password"]') as HTMLInputElement;
  await setValue(campo, SENHA);
  await click(byRole('button', /Guardar senha/i));
  await waitFor(() => backend.callsTo('PUT', /\/credential$/).length === 1);
  await waitFor(() => campo.value === '');
  expect(recarregado).toBe(0);
  expect(text()).not.toContain(SENHA);
});

it('senha recusada pelo Instagram: a aba explica e oferece a troca ali mesmo', async () => {
  await abrirAutenticacao(perfil({ credential: { ...perfil().credential, status: 'invalid' } }));
  expect(text()).toContain('O Instagram recusou a senha guardada');
  expect(byRole('button', /Trocar senha/i)).toBeTruthy();
  expect(text()).toContain('Nova senha');
});

it('a memória lista o que o perfil sabe e deixa ensinar um fato novo', async () => {
  backend.on('GET', /\/memory$/, () => json([{
    id: 'mem-1', profile_id: 'ig-1', subject: '@ana', content: 'Corre maratonas aos domingos', source: 'interaction',
    interaction_id: 'int-1', importance: 0.8, confidence: 0.9, occurrences: 2, expires_at: null,
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z', last_used_at: null,
  }]));
  backend.on('POST', /\/memory$/, () => json({ id: 'mem-2' }, 201));
  await abrir();
  await click(byRole('tab', /Memória/i));
  await waitFor(() => text().includes('Corre maratonas'));
  expect(text()).toContain('visto 2x');

  await setValue(byRole('textbox', /Sobre quem/i) as HTMLInputElement, '@bruno');
  await setValue(byRole('textbox', /^Fato$/i) as HTMLTextAreaElement, 'Mudou para Lisboa');
  await click(byRole('button', /Guardar/i));
  await waitFor(() => backend.callsTo('POST', /\/memory$/).length === 1);
});

it('as aprovações mostram o texto que sairá e os três verbos', async () => {
  const pendente = {
    id: 'apr-1', profile_id: 'ig-1', run_id: 'run-1', objective_id: 'run-1:android-02', step_id: 's1',
    capability: 'SEND_MESSAGE', target: '@ana', summary: 'Enviar a mensagem para @ana',
    generated_content: 'bom dia!', approved_content: null, content: 'bom dia!', status: 'pending',
    created_at: '2026-09-17T12:00:00Z', decided_at: null, decided_note: null,
  };
  backend.on('GET', /approvals/, () => json([pendente]));
  backend.on('POST', /approvals\/.*\/decide/, () => json({ ...pendente, status: 'approved' }));
  await abrir();
  await click(byRole('tab', /Aprovações/i));
  await waitFor(() => text().includes('Enviar a mensagem para @ana'));
  expect(byRole('button', /Aprovar/i)).toBeTruthy();
  expect(byRole('button', /Rejeitar/i)).toBeTruthy();

  await click(byRole('button', /Aprovar/i));
  await waitFor(() => backend.callsTo('POST', /decide/).length === 1);
});

it('as configurações mostram a política de cada ação e salvam a mudança', async () => {
  backend.on('GET', /\/policy$/, () => json({
    limits: { likes_per_hour: 30, cooldown_between_external_actions_s: 45 },
    capabilities: { LIKE_POST: 'autonomous', SEND_MESSAGE: 'approval_required' },
    defaults: { LIKE_POST: 'autonomous', SEND_MESSAGE: 'approval_required' },
  }));
  backend.on('GET', /capabilities/, () => json([
    { key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
      default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] },
    { key: 'SEND_MESSAGE', title: 'Enviar a mensagem', side_effect: true, risk: 'high',
      default_policy: 'approval_required', limit_bucket: 'dms', needs_draft: true, bindings: ['username', 'content'] },
  ]));
  backend.on('PUT', /\/policy$/, () => json({
    limits: { likes_per_hour: 30, cooldown_between_external_actions_s: 45 },
    capabilities: { LIKE_POST: 'disabled', SEND_MESSAGE: 'approval_required' },
    defaults: { LIKE_POST: 'autonomous', SEND_MESSAGE: 'approval_required' },
  }));
  await abrir();
  await click(byRole('tab', /Configurações/i));
  await waitFor(() => text().includes('Curtir a publicação'));
  expect(text()).toContain('Curtidas por hora');

  const selects = container.querySelectorAll('select');
  await setValue(selects[0] as HTMLSelectElement, 'disabled');
  await waitFor(() => backend.callsTo('PUT', /\/policy$/).length === 1);
  await waitFor(() => text().includes('padrão: Sozinho'));      // a diferença em relação ao catálogo fica visível
});

it('testar persona mostra o rascunho e não publica nada', async () => {
  backend.on('GET', /personas/, () => json([{
    id: 'persona-1', name: 'Mariana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: { tone: 'calmo' }, profile_id: 'ig-1', profile_username: 'mariana.costa91182',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }]));
  backend.on('POST', /preview/, () => json({
    content: 'oi! tudo ótimo por aqui', rationale: 'resposta curta e calma', refused: false,
    refusal_reason: null, memory_candidates: [],
  }));
  await abrir();
  await click(byRole('tab', /Persona/i));
  await waitFor(() => text().includes('Testar persona'));
  await click(byRole('button', /Testar persona/i));
  await waitFor(() => text().includes('oi! tudo ótimo por aqui'));
  expect(backend.callsTo('POST', /interactions/)).toHaveLength(0);
});
