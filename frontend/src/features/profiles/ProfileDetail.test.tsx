// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { InstagramProfile, ProfileAccount } from '../../api/types';
import { useAppStore } from '../../store/app';
import {
  FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor,
} from '../../test/harness';
import { ProfileDetail } from './ProfileDetail';

const SENHA = 'senha-secreta-9!Zk';

function perfil(over: Partial<InstagramProfile> = {}): InstagramProfile {
  return {
    id: 'ig-1', username: 'mariana.costa91182', display_name: 'Mariana Costa', first_name: 'Mariana',
    last_name: 'Costa', birth_date: null, email: null, persona_id: 'persona-1', persona_name: 'Mariana — fotografia',
    status: 'active', instance_id: 'android-02',
    locality: { worker_id: null, worker_name: 'este servidor', worker_state: 'online', known: true,
                available: true, moved: false, physical_id: null, detail: null },
    offline_policy: 'wait',
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

it('mostra as guias da persona na ordem nova e abre na visão geral', async () => {
  await abrir();
  // "Contas e acesso" funde as antigas Contas + Autenticação; "Aparelho" virou "Aparelhos"; "Imagens" é nova.
  const guias = allByRole('tab', /./).map((t) => (t.textContent ?? '').replace(/\d+$/, '').trim());
  expect(guias).toEqual(['Visão geral', 'Persona', 'Contas e acesso', 'Imagens', 'Aparelhos', 'Memória', 'Interações',
                         'Habilidades', 'Aprovações', 'Execuções', 'Configurações']);
  expect(byRole('tab', /Visão geral/i).getAttribute('aria-selected')).toBe('true');
  expect(text()).not.toContain('Autenticação');
  expect(text()).not.toContain(SENHA);
});

const SEM_SENHA = { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                   updated_at: null, last_used_at: null, consent_at: null, consent_by: null };

/** A conta do Instagram da persona, como `GET …/accounts` a devolve na v0.28 (credencial só com metadados). */
function conta(over: Partial<ProfileAccount> = {}): ProfileAccount {
  return {
    id: 'acc-1', profile_id: 'ig-1', app_id: 'instagram', app_name: 'Instagram', package: 'com.instagram.android',
    handle: 'mariana.costa91182', host: null, login_identifier: 'mariana@exemplo.com', status: 'active',
    session_status: 'unknown', session_detail: null, session_verified_at: null,
    session: { status: 'unknown', instance_id: 'android-02', observed_username: null, verified_at: null, detail: null,
               stale: false },
    session_actions: null, automated_login: true, credential_configured: true,
    credential: { configured: true, login_identifier: 'mariana@exemplo.com', status: 'active', failed_attempts: 0,
                  blocked_until: null, updated_at: '2026-09-17T10:00:00Z', last_used_at: null,
                  consent_at: '2026-09-27T10:00:00Z', consent_by: 'Ana' },
    consent_at: '2026-09-27T10:00:00Z', notes: '', created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

const CONTA_SEM_SENHA: Partial<ProfileAccount> = {
  credential_configured: false, credential: SEM_SENHA, consent_at: null, login_identifier: null,
};

async function abrirContas(contas: ProfileAccount[], onChanged: () => Promise<void> = async () => {},
                           p: InstagramProfile = perfil()): Promise<void> {
  backend.on('GET', /\/accounts$/, () => json(contas));
  backend.on('GET', /auth-attempts$/, () => json([]));
  await act(async () => {
    root.render(<ProfileDetail profile={p} onBack={() => {}} onChanged={onChanged} />);
  });
  await click(byRole('tab', /Contas e acesso/i));
  await waitFor(() => text().includes('Senha do Instagram'));
}

it('conta sem senha recebe a senha pela guia Contas e acesso: consentimento obrigatório, PUT com consent e campo que esvazia', async () => {
  let recarregado = 0;
  backend.on('PUT', /\/accounts\/acc-1\/credential$/, () => json(conta()));
  await abrirContas([conta(CONTA_SEM_SENHA)], async () => { recarregado += 1; });
  expect(byRole('button', /Conectar/i).getAttribute('aria-disabled')).toBe('true');

  const campo = container.querySelector('input[type="password"]') as HTMLInputElement;
  expect(campo.getAttribute('autocomplete')).toBe('new-password');       // o navegador não preenche sozinho
  await setValue(campo, SENHA);
  // Com a senha digitada e SEM a autorização, o botão continua bloqueado e diz por quê.
  const guardar = byRole('button', /Guardar senha/i);
  expect(guardar.getAttribute('aria-disabled')).toBe('true');
  expect(guardar.textContent).toContain('Marque a autorização');
  await click(guardar);
  expect(backend.callsTo('PUT', /\/credential$/)).toHaveLength(0);

  await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/i));
  await click(byRole('button', /Guardar senha/i));
  await waitFor(() => backend.callsTo('PUT', /\/accounts\/acc-1\/credential$/).length === 1);
  expect(backend.callsTo('PUT', /\/credential$/)[0]?.body).toEqual({ password: SENHA, consent: true });
  await waitFor(() => recarregado === 1);                                   // a persona é relida
  expect(campo.value).toBe('');                                            // a senha não fica no formulário
  expect(text()).not.toContain(SENHA);
});

it('conta que já consentiu mostra quando e por quem, e troca a senha sem remarcar', async () => {
  backend.on('PUT', /\/accounts\/acc-1\/credential$/, () => json(conta()));
  await abrirContas([conta()]);
  expect(text()).toContain('Consentimento dado em');
  expect(text()).toContain('por Ana');
  expect(container.querySelector('input[type="checkbox"]')).toBeNull();
  await setValue(container.querySelector('input[type="password"]') as HTMLInputElement, SENHA);
  await click(byRole('button', /Trocar senha/i));
  await waitFor(() => backend.callsTo('PUT', /\/credential$/).length === 1);
  // O servidor preserva o consentimento já dado; o painel não o reenvia.
  expect(backend.callsTo('PUT', /\/credential$/)[0]?.body).toEqual({ password: SENHA });
});

it('o identificador de login mostra o valor GRAVADO, nunca o handle por padrão', async () => {
  await abrirContas([conta()]);
  const login = byRole('textbox', /Identificador de login/i) as HTMLInputElement;
  expect(login.value).toBe('mariana@exemplo.com');
  expect(text()).toContain('Identificador de login gravado: mariana@exemplo.com');

  await act(async () => root.unmount());
  root = createRoot(container);
  await abrirContas([conta(CONTA_SEM_SENHA)]);
  const vazio = byRole('textbox', /Identificador de login/i) as HTMLInputElement;
  expect(vazio.value).toBe('');                                            // e não "mariana.costa91182"
  expect(text()).toContain('Identificador de login gravado: nenhum');
});

it('sem senha digitada nada é enviado — nem pelo botão, nem pelo Enter no campo', async () => {
  await abrirContas([conta(CONTA_SEM_SENHA)]);
  await click(byRole('checkbox', /Autorizo a automação/i));
  const botao = byRole('button', /Guardar senha/i);
  expect(botao.getAttribute('aria-disabled')).toBe('true');
  await click(botao);
  const form = container.querySelector('input[type="password"]')!.closest('form') as HTMLFormElement;
  await act(async () => { form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
  expect(backend.callsTo('PUT', /\/credential$/)).toHaveLength(0);
});

it('falha ao guardar também esvazia o campo e não relê a persona', async () => {
  let recarregado = 0;
  backend.on('PUT', /\/credential$/, () => apiError(503, 'secret_store_unavailable', 'O cofre está trancado.'));
  await abrirContas([conta(CONTA_SEM_SENHA)], async () => { recarregado += 1; });
  const campo = container.querySelector('input[type="password"]') as HTMLInputElement;
  await setValue(campo, SENHA);
  await click(byRole('checkbox', /Autorizo a automação/i));
  await click(byRole('button', /Guardar senha/i));
  await waitFor(() => backend.callsTo('PUT', /\/credential$/).length === 1);
  await waitFor(() => campo.value === '');
  expect(recarregado).toBe(0);
  expect(text()).not.toContain(SENHA);
});

it('senha recusada pelo Instagram: a guia explica e oferece a troca ali mesmo', async () => {
  await abrirContas([conta({ credential: { ...conta().credential!, status: 'invalid' } })]);
  expect(text()).toContain('O Instagram recusou a senha guardada');
  expect(byRole('button', /Trocar senha/i)).toBeTruthy();
  expect(text()).toContain('Nova senha');
});

it('Conectar e Verificar usam a rota DA CONTA, gateados por session_actions', async () => {
  const liberado = { allowed: true, reason: null };
  backend.on('POST', /\/accounts\/acc-1\/session\/(connect|verify)$/,
             () => json({ accepted: true, profile_id: 'ig-1', instance_id: 'android-02', account_id: 'acc-1' }, 202));
  await abrirContas([conta({
    session_actions: { phase: 'logged_out', detail: 'Deslogado neste aparelho.', connect: liberado, verify: liberado,
                       logout: { allowed: false, reason: 'Não há sessão para encerrar.' }, inspect_app: liberado },
  })]);
  expect(byRole('button', /Sair da conta/i).getAttribute('aria-disabled')).toBe('true');
  expect(text()).toContain('Não há sessão para encerrar.');
  await click(byRole('button', /^Conectar$/i));
  await waitFor(() => backend.callsTo('POST', /\/accounts\/acc-1\/session\/connect$/).length === 1);
  await click(byRole('button', /Verificar conta/i));
  await waitFor(() => backend.callsTo('POST', /\/accounts\/acc-1\/session\/verify$/).length === 1);
  // As rotas antigas por perfil (apelidos da conta âncora) não são mais chamadas daqui.
  expect(backend.callsTo('POST', /\/instagram\/profiles\/ig-1\/(connect|verify)$/)).toHaveLength(0);
});

it('conta conectada mostra a conta observada e oferece reconectar', async () => {
  await abrirContas([conta({
    session_status: 'session_ready',
    session: { status: 'session_ready', instance_id: 'android-02', observed_username: 'mariana.costa91182',
               verified_at: '2026-09-17T10:00:00Z', detail: null, stale: true },
  })]);
  expect(text()).toContain('Conectado');
  expect(text()).toContain('Conta observada na tela: @mariana.costa91182');
  expect(text()).toContain('dado velho');
  expect(byRole('button', /Reconectar/i)).toBeTruthy();
});

it('persona sem @ ganha o Instagram pela adoção: POST /instagram/profiles com persona_id', async () => {
  useAppStore.setState({ apps: [
    { id: 'instagram', name: 'Instagram', package: 'com.instagram.android', activity: null, apk_path: null,
      nav_hints: null, known_selectors: null, builtin: true },
  ] });
  backend.on('POST', /^\/api\/instagram\/profiles$/, () => json(perfil(), 201));
  const semConta = perfil({ username: '', persona_id: null, instance_id: null, locality: null });
  backend.on('GET', /\/accounts$/, () => json([]));
  await act(async () => {
    root.render(<ProfileDetail profile={semConta} abaInicial="contas" onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('ainda não tem @ de cadastro'));
  expect(text()).toContain('sem conta de cadastro');
  await click(byRole('button', /Adicionar conta/i));
  await setValue(byRole('combobox', /Aplicativo/i) as HTMLSelectElement, 'instagram');
  await setValue(byRole('textbox', /Usuário do Instagram/i) as HTMLInputElement, 'mariana.costa91182');
  await click(byRole('button', /^Adicionar$/i));
  await waitFor(() => backend.callsTo('POST', /^\/api\/instagram\/profiles$/).length === 1);
  expect(backend.callsTo('POST', /^\/api\/instagram\/profiles$/)[0]?.body)
    .toEqual({ username: 'mariana.costa91182', persona_id: 'ig-1' });
  expect(backend.callsTo('POST', /\/accounts$/)).toHaveLength(0);
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
  // Fatos agrupados por assunto (item 11.6): o cartão do assunto aparece com o nome dele.
  expect(text()).toContain('@ana');

  // "Ensinar um fato" é um botão que abre o formulário — ele não fica exposto o tempo todo.
  await click(byRole('button', /Ensinar um fato/i));
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

/** Registra as rotas mínimas de que a aba Configurações precisa: catálogo do app, capacidades e política. */
function montarConfigBackend(capabilities: unknown[], policyBody: unknown, interacoes: unknown[] = []): void {
  backend.on('GET', /app-catalog/, () => json([
    { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
      session_provider: 'instagram', needs_profile: true },
  ]));
  backend.on('GET', /capabilities/, () => json(capabilities));
  backend.on('GET', /\/policy$/, () => json(policyBody));
  backend.on('GET', /interactions/, () => json(interacoes));
}

it('agrupa as ações por natureza (sessão, navegação, leitura, efeito externo)', async () => {
  montarConfigBackend(
    [
      { key: 'LOGOUT', title: 'Sair da conta', side_effect: true, risk: 'high',
        default_policy: 'manual_only', limit_bucket: null, needs_draft: false, bindings: [] },
      { key: 'OPEN_FEED', title: 'Abrir o feed', side_effect: false, risk: 'low',
        default_policy: 'autonomous', limit_bucket: null, needs_draft: false, bindings: [] },
      { key: 'COLLECT_THREADS', title: 'Levantar as conversas', side_effect: false, risk: 'low',
        default_policy: 'autonomous', limit_bucket: null, needs_draft: false, bindings: [] },
      { key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
        default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] },
    ],
    {
      limits: { likes_per_hour: 30, cooldown_between_external_actions_s: 45 },
      capabilities: {},
      defaults: { LOGOUT: 'manual_only', OPEN_FEED: 'autonomous', COLLECT_THREADS: 'autonomous', LIKE_POST: 'autonomous' },
      loosened: [],
    },
  );
  await abrir();
  await click(byRole('tab', /Configurações/i));
  await waitFor(() => text().includes('Curtir a publicação'));

  for (const grupo of ['Sessão', 'Navegação', 'Leitura', 'Efeito externo']) {
    expect(text()).toContain(grupo);
  }
  // o resumo de uma linha no topo conta as quatro ações pelo padrão do catálogo (todas "autonomous" menos o logout)
  expect(text()).toContain('3 sozinho · 0 com aprovação · 1 só manual');
});

it('a política de cada ação é um controle segmentado de três botões, e trocar salva na hora', async () => {
  montarConfigBackend(
    [
      { key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
        default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] },
      { key: 'SEND_MESSAGE', title: 'Enviar a mensagem', side_effect: true, risk: 'high',
        default_policy: 'approval_required', limit_bucket: 'dms', needs_draft: true, bindings: ['username', 'content'] },
    ],
    {
      limits: { likes_per_hour: 30, cooldown_between_external_actions_s: 45 },
      capabilities: { LIKE_POST: 'autonomous', SEND_MESSAGE: 'approval_required' },
      defaults: { LIKE_POST: 'autonomous', SEND_MESSAGE: 'approval_required' },
      loosened: [],
    },
  );
  backend.on('PUT', /\/policy$/, () => json({
    limits: { likes_per_hour: 30, cooldown_between_external_actions_s: 45 },
    capabilities: { LIKE_POST: 'manual_only', SEND_MESSAGE: 'approval_required' },
    defaults: { LIKE_POST: 'autonomous', SEND_MESSAGE: 'approval_required' },
    loosened: [],
  }));
  await abrir();
  await click(byRole('tab', /Configurações/i));
  await waitFor(() => text().includes('Curtir a publicação'));
  expect(text()).toContain('Curtidas por hora');
  // não há mais <select>: a política é um grupo de botões de rádio, as três opções sempre visíveis
  expect(container.querySelectorAll('select[value]')).toHaveLength(0);
  expect(byRole('radiogroup', /Política de Curtir a publicação/i)).toBeTruthy();

  const grupo = byRole('radiogroup', /Política de Curtir a publicação/i);
  await click(byRole('radio', /Só manual/i, grupo));
  await waitFor(() => backend.callsTo('PUT', /\/policy$/).length === 1);
  expect(backend.calls.find((c) => c.method === 'PUT')?.body).toEqual({ capabilities: { LIKE_POST: 'manual_only' } });
  await waitFor(() => text().includes('padrão: Sozinho'));      // a diferença em relação ao catálogo fica visível
});

it('uma ação em lote de grupo muda todas as ações do grupo em um único PUT', async () => {
  montarConfigBackend(
    [
      { key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
        default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] },
      { key: 'FOLLOW', title: 'Seguir {username}', side_effect: true, risk: 'high',
        default_policy: 'approval_required', limit_bucket: 'follows', needs_draft: false, bindings: ['username'] },
    ],
    {
      limits: { likes_per_hour: 30 },
      capabilities: { LIKE_POST: 'autonomous', FOLLOW: 'approval_required' },
      defaults: { LIKE_POST: 'autonomous', FOLLOW: 'approval_required' },
      loosened: [],
    },
  );
  backend.on('PUT', /\/policy$/, () => json({
    limits: { likes_per_hour: 30 },
    capabilities: { LIKE_POST: 'approval_required', FOLLOW: 'approval_required' },
    defaults: { LIKE_POST: 'autonomous', FOLLOW: 'approval_required' },
    loosened: [],
  }));
  await abrir();
  await click(byRole('tab', /Configurações/i));
  await waitFor(() => text().includes('Curtir a publicação'));

  const botoesDoGrupo = Array.from(container.querySelectorAll('button')).filter((b) => b.textContent === 'Com aprovação');
  await click(botoesDoGrupo[0]!);
  await waitFor(() => backend.callsTo('PUT', /\/policy$/).length === 1);
  expect(backend.calls.find((c) => c.method === 'PUT')?.body).toEqual({
    capabilities: { LIKE_POST: 'approval_required', FOLLOW: 'approval_required' },
  });
});

it('marca visualmente uma ação de risco alto afrouxada abaixo do padrão do catálogo (achado #114)', async () => {
  montarConfigBackend(
    [
      { key: 'SEND_MESSAGE', title: 'Enviar a mensagem', side_effect: true, risk: 'high',
        default_policy: 'approval_required', limit_bucket: 'dms', needs_draft: true, bindings: ['username', 'content'] },
    ],
    {
      limits: { dms_per_hour: 15 },
      capabilities: { SEND_MESSAGE: 'autonomous' },        // padrão do catálogo é approval_required
      defaults: { SEND_MESSAGE: 'approval_required' },
      loosened: ['SEND_MESSAGE'],
    },
  );
  await abrir();
  await click(byRole('tab', /Configurações/i));
  await waitFor(() => text().includes('Enviar a mensagem'));
  expect(text()).toContain('mais frouxo que o padrão');
});

it('o limite mostra um medidor com o uso de hoje contado das interações confirmadas', async () => {
  montarConfigBackend(
    [
      { key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
        default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] },
    ],
    {
      limits: { likes_per_hour: 10, cooldown_between_external_actions_s: 45 },
      capabilities: { LIKE_POST: 'autonomous' },
      defaults: { LIKE_POST: 'autonomous' },
      loosened: [],
    },
    [
      // duas curtidas confirmadas hoje, uma pendente (não conta) e uma curtida de ontem (não conta)
      { id: '1', profile_id: 'ig-1', instance_id: 'android-02', run_id: null, objective_id: null, step_id: null,
        occurred_at: new Date().toISOString(), type: 'post_liked', direction: 'out', counterparty: null,
        thread_key: null, incoming_content: null, outgoing_content: null, target: 'post-1', status: 'confirmed',
        evidence: null, created_at: new Date().toISOString() },
      { id: '2', profile_id: 'ig-1', instance_id: 'android-02', run_id: null, objective_id: null, step_id: null,
        occurred_at: new Date().toISOString(), type: 'post_liked', direction: 'out', counterparty: null,
        thread_key: null, incoming_content: null, outgoing_content: null, target: 'post-2', status: 'confirmed',
        evidence: null, created_at: new Date().toISOString() },
      { id: '3', profile_id: 'ig-1', instance_id: 'android-02', run_id: null, objective_id: null, step_id: null,
        occurred_at: new Date().toISOString(), type: 'post_liked', direction: 'out', counterparty: null,
        thread_key: null, incoming_content: null, outgoing_content: null, target: 'post-3', status: 'pending',
        evidence: null, created_at: new Date().toISOString() },
      { id: '4', profile_id: 'ig-1', instance_id: 'android-02', run_id: null, objective_id: null, step_id: null,
        occurred_at: new Date(Date.now() - 86_400_000 * 2).toISOString(), type: 'post_liked', direction: 'out',
        counterparty: null, thread_key: null, incoming_content: null, outgoing_content: null, target: 'post-4',
        status: 'confirmed', evidence: null, created_at: new Date(Date.now() - 86_400_000 * 2).toISOString() },
    ],
  );
  await abrir();
  await click(byRole('tab', /Configurações/i));
  await waitFor(() => text().includes('Curtidas por hora'));
  await waitFor(() => text().includes('2/10 hoje'));
  expect(byRole('progressbar', /Uso de hoje de Curtidas por hora/i)).toBeTruthy();
  // o limite sem contrapartida em interações (o intervalo entre ações) não ganha medidor, só o rótulo
  expect(text()).toContain('sem contagem de uso');
});

it('avisa quais campos de voz faltam na persona e deixa preencher cada um', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Mariana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: { tone: 'calmo', formality: 'neutro', typical_length: 'curta', emojis: 'raro',
              personality: 'calma', humor: 'leve', interests: ['fotografia'] },
    // O backend calcula: é a MESMA lista que vai ao modelo. A tela não recalcula nada.
    voice_gaps: ['slang', 'dm_style', 'comment_style', 'with_known', 'with_strangers', 'common_phrases',
                 'forbidden_phrases', 'examples'],
    profile_id: 'ig-1', profile_username: 'mariana.costa91182',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  await abrir();
  await click(byRole('tab', /Persona/i));
  await waitFor(() => text().includes('Faltam 8 campo(s) de voz'));
  expect(text()).toContain('Estilo em mensagem direta');
  expect(text()).toContain('Expressões proibidas');
  // Aviso sem campo para preencher seria aviso morto: os oito têm de estar alcançáveis num clique (Editar).
  await click(byRole('button', /Editar a voz/i));
  const rotulos = [...container.querySelectorAll('label')].map((l) => l.textContent ?? '');
  for (const r of ['Gírias', 'Estilo em mensagem direta', 'Estilo em comentário', 'Com quem já conhece',
                   'Com desconhecidos', 'Expressões comuns', 'Expressões proibidas', 'Exemplos']) {
    expect(rotulos.some((x) => x.includes(r))).toBe(true);
  }
});

it('testar persona aceita a INTENÇÃO, não só a mensagem recebida', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Mariana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: { tone: 'calmo' }, voice_gaps: [], profile_id: 'ig-1', profile_username: 'mariana.costa91182',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  backend.on('POST', /preview/, () => json({
    content: 'boa tarde por aí!', rationale: 'cumprimento curto', refused: false,
    refusal_reason: null, memory_candidates: [],
  }));
  await abrir();
  await click(byRole('tab', /Persona/i));
  await waitFor(() => text().includes('Testar persona'));
  const tipo = [...container.querySelectorAll('select')].at(-1) as HTMLSelectElement;
  await setValue(tipo, 'dm_initiate');
  await waitFor(() => text().includes('Intenção'));
  const intencao = [...container.querySelectorAll('textarea')].at(-1) as HTMLTextAreaElement;
  await setValue(intencao, 'dar boa tarde');
  await click(byRole('button', /Testar persona/i));
  await waitFor(() => text().includes('boa tarde por aí!'));
  expect(backend.callsTo('POST', /preview/)[0]?.body)
    .toMatchObject({ kind: 'dm_initiate', brief: 'dar boa tarde', incoming: '' });
});

it('testar persona mostra o rascunho e não publica nada', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Mariana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: { tone: 'calmo' }, profile_id: 'ig-1', profile_username: 'mariana.costa91182',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
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

// ---------------------------------------------------------------- item 11.6: persona como montagem
it('a persona marca o valor certo nas réguas e mostra Diz × Nunca diz e os exemplos como balões', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Mariana — fotografia', summary: 'Fotógrafa de retratos.', persona_prompt: 'Responda com calma.',
    traits: {
      tone: 'calmo', formality: 'formal', typical_length: 'longa', emojis: 'raro', personality: 'Observadora e gentil',
      interests: ['fotografia', 'trilhas'], common_phrases: ['bom dia!'], forbidden_phrases: ['mano'],
      examples: ['Oi! Tudo bem por aí?'], with_known: 'Direta e calorosa', with_strangers: 'Educada e reservada',
    },
    voice_gaps: ['slang', 'humor', 'dm_style', 'comment_style'],
    profile_id: 'ig-1', profile_username: 'mariana.costa91182',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  await abrir();
  await click(byRole('tab', /Persona/i));
  await waitFor(() => text().includes('Observadora e gentil'));

  // As réguas marcam o valor certo — e só ele — dentro do espectro fixo.
  const ativos = [...container.querySelectorAll('[data-active="true"]')].map((el) => el.textContent ?? '');
  expect(ativos).toEqual(expect.arrayContaining(['Formal', 'Longa', 'Raro']));
  expect(ativos).not.toEqual(expect.arrayContaining(['Informal', 'Curta', 'Nunca']));

  expect(text()).toContain('fotografia');
  expect(text()).toContain('“bom dia!”');
  expect(text()).toContain('“mano”');
  expect(text()).toContain('Oi! Tudo bem por aí?');
  expect(text()).toContain('Direta e calorosa');
  // O medidor de completude aponta exatamente o que falta, pela mesma lista do backend.
  expect(text()).toContain('falta preencher');
  expect(text()).toContain('Gírias');
});

it('a memória agrupa os fatos por assunto e ordena por importância', async () => {
  backend.on('GET', /\/memory$/, () => json([
    { id: 'mem-1', profile_id: 'ig-1', subject: '@ana', content: 'Fato A — corre maratonas', source: 'interaction',
      interaction_id: null, importance: 0.9, confidence: 0.8, occurrences: 3, expires_at: null,
      created_at: '2026-09-10T10:00:00Z', updated_at: '2026-09-10T10:00:00Z', last_used_at: '2026-09-17T10:00:00Z' },
    { id: 'mem-2', profile_id: 'ig-1', subject: '@ana', content: 'Fato B — mora em Lisboa', source: 'taught',
      interaction_id: null, importance: 0.6, confidence: 0.5, occurrences: 1, expires_at: null,
      created_at: '2026-09-11T10:00:00Z', updated_at: '2026-09-11T10:00:00Z', last_used_at: null },
    { id: 'mem-3', profile_id: 'ig-1', subject: '@bruno', content: 'Fato C — gosta de futebol', source: 'taught',
      interaction_id: null, importance: 0.3, confidence: 0.4, occurrences: 1, expires_at: null,
      created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:00:00Z', last_used_at: null },
  ]));
  await abrir();
  await click(byRole('tab', /Memória/i));
  await waitFor(() => text().includes('Fato A'));

  const html = container.innerHTML;
  const anaIdx = html.indexOf('>@ana<');
  const brunoIdx = html.indexOf('>@bruno<');
  const fatoAIdx = html.indexOf('Fato A');
  const fatoBIdx = html.indexOf('Fato B');
  const fatoCIdx = html.indexOf('Fato C');
  expect(anaIdx).toBeGreaterThan(-1);
  expect(brunoIdx).toBeGreaterThan(anaIdx);            // @ana tem importância maior: vem primeiro
  expect(fatoAIdx).toBeGreaterThan(anaIdx);
  expect(fatoBIdx).toBeGreaterThan(anaIdx);
  expect(fatoAIdx).toBeLessThan(brunoIdx);
  expect(fatoBIdx).toBeLessThan(brunoIdx);              // os dois fatos de @ana ficam no mesmo cartão
  expect(fatoCIdx).toBeGreaterThan(brunoIdx);
  expect(text()).toContain('confiança 80%');
});

it('as interações agrupam por dia e o filtro por tipo esconde os outros tipos', async () => {
  const agora = new Date();
  const ontem = new Date(agora);
  ontem.setDate(agora.getDate() - 1);
  backend.on('GET', /\/interactions$/, () => json([
    { id: 'int-1', profile_id: 'ig-1', instance_id: 'android-02', run_id: null, objective_id: null, step_id: null,
      occurred_at: agora.toISOString(), type: 'dm_sent', direction: 'out', counterparty: '@carla',
      thread_key: null, incoming_content: null, outgoing_content: 'bom dia, carla!', target: null,
      status: 'confirmed', evidence: null, created_at: agora.toISOString() },
    { id: 'int-2', profile_id: 'ig-1', instance_id: 'android-02', run_id: null, objective_id: null, step_id: null,
      occurred_at: ontem.toISOString(), type: 'post_liked', direction: 'out', counterparty: '@daniel',
      thread_key: null, incoming_content: null, outgoing_content: null, target: 'post-1',
      status: 'confirmed', evidence: null, created_at: ontem.toISOString() },
  ]));
  await abrir();
  await click(byRole('tab', /Interações/i));
  await waitFor(() => text().includes('bom dia, carla!'));
  expect(text()).toContain('Hoje');
  expect(text()).toContain('Ontem');
  expect(text()).toContain('Mensagem enviada');
  expect(text()).toContain('Curtida');

  await click(byRole('button', /^Curtida$/i));
  await waitFor(() => !text().includes('bom dia, carla!'));
  expect(text()).toContain('@daniel');
});

it('o mapa de habilidades desenha a trilha de etapas e a fração sem IA', async () => {
  backend.on('GET', /capacidades/, () => json({
    profile_id: 'ig-1',
    flows: [{
      flow_id: 'enviar-mensagem', name: 'Enviar mensagem de boas-vindas', command_template: 'Abra o Instagram e mande boas-vindas',
      package: 'com.instagram.android', target_version: null, steps_total: 4, steps_with_recipe: 3, ai_cost: 'parcial',
      estimated_usd: null, times: 5, last_at: '2026-09-17T10:00:00Z',
    }],
    steps_driven_by: { recipe: 3, ai: 1 },
    recipe_share: 0.75,
    interactions: { dm_sent: 4, post_liked: 2 },
  }));
  await abrir();
  await click(byRole('tab', /Habilidades/i));
  await waitFor(() => text().includes('Enviar mensagem de boas-vindas'));
  expect(text()).toContain('5×');

  const pontos = container.querySelectorAll('[data-tone]');
  expect(pontos).toHaveLength(4);
  expect([...pontos].filter((p) => p.getAttribute('data-tone') === 'success')).toHaveLength(3);
  expect(text()).toContain('75%');

  const barras = [...container.querySelectorAll('[role="progressbar"]')];
  const valores = barras.map((b) => b.getAttribute('aria-valuenow'));
  expect(valores).toEqual(expect.arrayContaining(['100', '50']));
});

it('a visão geral mostra o cartão de identidade com números e uma mini linha do tempo de até 8 interações', async () => {
  backend.on('GET', /capacidades/, () => json({
    profile_id: 'ig-1', flows: [], steps_driven_by: { recipe: 6, ai: 2 }, recipe_share: 0.75,
    interactions: { dm_sent: 5, post_liked: 3 },
  }));
  const base = Date.now();
  const interacoes = Array.from({ length: 9 }, (_, i) => ({
    id: `int-${i + 1}`, profile_id: 'ig-1', instance_id: 'android-02', run_id: null, objective_id: null, step_id: null,
    occurred_at: new Date(base - i * 3_600_000).toISOString(), type: 'dm_sent', direction: 'out',
    counterparty: `alvo-${i + 1}`, thread_key: null, incoming_content: null, outgoing_content: 'oi',
    target: null, status: 'confirmed', evidence: null, created_at: new Date(base - i * 3_600_000).toISOString(),
  }));
  backend.on('GET', /\/interactions$/, () => json(interacoes));
  // A voz vem no próprio objeto da persona (a persona É o perfil desde a 047): nenhuma lista a mais para achá-la.
  await abrir(perfil({
    traits: { formality: 'formal', typical_length: 'longa', emojis: 'raro', interests: ['fotografia'] },
    age: 34, gender: 'feminino', biography: { home: { city: 'Curitiba' }, work: { profession: 'Fotógrafa' } },
  }));
  await waitFor(() => text().includes('fotografia'));
  expect(backend.callsTo('GET', /personas/)).toHaveLength(0);
  for (const dado of ['34 anos', 'feminino', 'Curitiba', 'Fotógrafa']) expect(text()).toContain(dado);

  expect(text()).toContain('8');            // interações confirmadas (6+2)
  expect(text()).toContain('75%');          // roda sem IA
  await waitFor(() => text().includes('alvo-1'));
  for (let i = 1; i <= 8; i++) expect(text()).toContain(`alvo-${i}`);
  expect(text()).not.toContain('alvo-9');   // a mini linha do tempo corta em 8
});

it('Memória recarrega sozinha quando o aparelho do perfil manda evento pelo WebSocket, e mostra a origem do fato', async () => {
  const { FakeWebSocket } = await import('../../test/harness');
  const { makeEvent, makeSnapshot } = await import('../../test/fixtures');
  const { startLive, stopLive } = await import('../../store/live');
  const fato = (id: string, content: string, source: string) => ({
    id, profile_id: 'ig-1', subject: '@ana', content, source, interaction_id: null, importance: 0.3, confidence: 0.7,
    occurrences: 1, expires_at: null, created_at: '2026-09-24T10:00:00Z', updated_at: '2026-09-24T10:00:00Z',
    last_used_at: null,
  });
  let memorias = [fato('m1', 'Corre maratonas aos domingos', 'operator')];
  backend.on('GET', /\/memory/, () => json(memorias));
  backend.on('GET', /^\/api\/session$/, () => json({ operator: 'Ana', token_required: false, expires_at: null }));
  backend.on('GET', /^\/api\/snapshot$/, () => json(makeSnapshot({ last_event_id: 7 })));
  await abrir();
  await click(byRole('tab', /Memória/i));
  await waitFor(() => expect(text()).toContain("Corre maratonas aos domingos"));
  expect(text()).toContain('ensinado');

  const parar = startLive();
  try {
    await waitFor(() => expect(FakeWebSocket.instances.length).toBeGreaterThan(0));
    const ws = FakeWebSocket.last;
    await act(async () => {
      ws.serverOpen();
      ws.serverSend({ type: 'hello', server_time: new Date().toISOString(), last_event_id: 7 });
    });
    const antes = backend.callsTo('GET', /\/memory/).length;
    // o perfil viu uma tela: o backend gravou o fato e o evento chega com o aparelho dele
    memorias = [fato('m2', 'Vi na tela em 24/09 (Abrir o perfil): Ana · 120 seguidores', 'observation'), ...memorias];
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(8, 'step.updated', null, { instance_id: 'android-02' }) }));
    await waitFor(() => expect(text()).toContain("120 seguidores"), 4000);
    expect(backend.callsTo('GET', /\/memory/).length).toBe(antes + 1);
    expect(text()).toContain('visto na tela');

    // evento de OUTRO aparelho e quadro efêmero não recarregam nada
    const depois = backend.callsTo('GET', /\/memory/).length;
    await act(async () => {
      ws.serverSend({ type: 'event', event: makeEvent(9, 'step.updated', null, { instance_id: 'android-07' }) });
      ws.serverSend({ type: 'event', event: makeEvent(null, 'frame', { instance_id: 'android-02' }, { instance_id: 'android-02' }) });
    });
    await new Promise((r) => setTimeout(r, 1700));
    expect(backend.callsTo('GET', /\/memory/).length).toBe(depois);
  } finally {
    parar();
    stopLive();
  }
});

it('grupo de acesso: cada ação diz de onde vem, "herdar" apaga a escolha própria e o grupo se troca no topo', async () => {
  const caps = [
    { key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
      default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] },
    { key: 'SEND_MESSAGE', title: 'Enviar a mensagem', side_effect: true, risk: 'high',
      default_policy: 'approval_required', limit_bucket: 'dms', needs_draft: true, bindings: ['username'] },
    { key: 'OPEN_FEED', title: 'Abrir o feed', side_effect: false, risk: 'low',
      default_policy: 'autonomous', limit_bucket: null, needs_draft: false, bindings: [] },
  ];
  const politica = {
    limits: { likes_per_hour: 5, dms_per_hour: 15 },
    capabilities: { LIKE_POST: 'approval_required', SEND_MESSAGE: 'autonomous', OPEN_FEED: 'autonomous' },
    defaults: { LIKE_POST: 'autonomous', SEND_MESSAGE: 'approval_required', OPEN_FEED: 'autonomous' },
    loosened: ['SEND_MESSAGE'],
    group_id: 'grp-1', group_name: 'Cautelosos',
    own: { SEND_MESSAGE: 'autonomous' }, group: { LIKE_POST: 'approval_required', SEND_MESSAGE: 'manual_only' },
    origin: { LIKE_POST: 'group', SEND_MESSAGE: 'own', OPEN_FEED: 'default' },
    own_limits: {}, group_limits: { likes_per_hour: 5 }, limits_origin: { likes_per_hour: 'group', dms_per_hour: 'default' },
  };
  montarConfigBackend(caps, politica);
  backend.on('GET', /policy-groups$/, () => json([
    { id: 'grp-1', name: 'Cautelosos', description: '', capabilities: {}, limits: {}, loosened: [], members: [{ id: 'ig-1', username: 'mariana.costa91182' }], created_at: '', updated_at: '' },
    { id: 'grp-2', name: 'Soltos', description: '', capabilities: {}, limits: {}, loosened: [], members: [], created_at: '', updated_at: '' },
  ]));
  backend.on('PUT', /\/policy$/, () => json({ ...politica, own: {}, origin: { ...politica.origin, SEND_MESSAGE: 'group' },
                                              capabilities: { ...politica.capabilities, SEND_MESSAGE: 'manual_only' } }));
  backend.on('PATCH', /\/instagram\/profiles\/ig-1$/, () => json(perfil({ policy_group_id: 'grp-2', policy_group_name: 'Soltos' })));
  await abrir();
  await click(byRole('tab', /Configurações/i));
  await waitFor(() => expect(text()).toContain('Enviar a mensagem'));

  expect(text()).toContain('do grupo Cautelosos');                 // Curtir vem do grupo
  expect(text()).toContain('próprio · sobrepõe o grupo');          // DM foi mudada no perfil e o grupo diz outra coisa
  expect(text()).toContain('padrão');                              // Abrir o feed é o padrão do catálogo
  expect(text()).toContain('1 ação(ões) e 0 limite(s) escolhidos neste perfil — sobrepõem o grupo');

  // "herdar" manda null — nunca uma cópia do valor do grupo, que prenderia o perfil contra o grupo
  await click(byRole('button', /herdar \(Só manual\)/i));
  await waitFor(() => expect(backend.callsTo('PUT', /\/policy$/)).toHaveLength(1));
  expect(backend.callsTo('PUT', /\/policy$/)[0]!.body).toEqual({ capabilities: { SEND_MESSAGE: null } });

  // trocar o grupo é um PATCH no perfil
  const select = container.querySelector('select') as HTMLSelectElement;
  await setValue(select, 'grp-2');
  await waitFor(() => expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-1$/)).toHaveLength(1));
  expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-1$/)[0]!.body).toEqual({ policy_group_id: 'grp-2' });
});

// Auditoria UX 27/09, P2.11: erro de carga das abas ia só para um toast e o esqueleto ficava para sempre.

it('aba Persona com a API caída mostra o erro com "Tentar de novo" em vez de carregar para sempre', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => apiError(503, 'unavailable', 'banco indisponível'));
  await abrir();
  await click(byRole('tab', /Persona/i));
  await waitFor(() => text().includes('Não foi possível carregar a persona'));
  expect(text()).toContain('banco indisponível');

  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Mariana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: {}, voice_gaps: [], profile_id: 'ig-1', profile_username: 'mariana.costa91182',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => text().includes('Fala de fotografia'));
  expect(text()).not.toContain('Não foi possível carregar a persona');
});

it('aba Configurações com a política indisponível mostra o erro com "Tentar de novo"', async () => {
  montarConfigBackend([], { limits: {}, capabilities: {}, defaults: {}, loosened: [] });
  backend.on('GET', /\/policy$/, () => apiError(500, 'internal', 'consulta estourou o tempo'));
  await abrir();
  await click(byRole('tab', /Configurações/i));
  await waitFor(() => text().includes('Não foi possível carregar as políticas'));
  expect(text()).toContain('consulta estourou o tempo');

  backend.on('GET', /\/policy$/, () => json({ limits: {}, capabilities: {}, defaults: {}, loosened: [] }));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => text().includes('Grupo de acesso'));
  expect(text()).not.toContain('Não foi possível carregar as políticas');
});

it('guia Contas e acesso com a API caída mostra o erro com "Tentar de novo", não "Carregando…" nem lista vazia', async () => {
  backend.on('GET', /\/accounts$/, () => apiError(503, 'unavailable', 'banco indisponível'));
  await abrir();
  await click(byRole('tab', /^Contas e acesso/i));
  await waitFor(() => text().includes('Não foi possível carregar as contas da persona'));
  expect(text()).not.toContain('Carregando…');

  backend.on('GET', /\/accounts$/, () => json([{
    id: 'acc-1', profile_id: 'ig-1', app_id: 'instagram', app_name: 'Instagram', handle: 'mariana.costa91182',
    login_identifier: null, automated_login: true, credential_configured: true, session_status: 'logged_out',
    session_verified_at: null, session_detail: null, created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }]));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => text().includes('Senha do Instagram'));
  // O status da sessão sai traduzido, nunca como enum.
  expect(text()).toContain('Fora da conta');
  expect(text()).not.toContain('logged_out');
});

// ---------------------------------------------------------------- evolução 2, onda E1: biografia por seção
const PESSOA = {
  id: 'ig-1', name: 'Mariana Costa', summary: 'Fotógrafa de retratos.', persona_prompt: '', username: 'mariana.costa91182',
  traits: { tone: 'calmo' }, voice_gaps: [],
  biography: {
    schema_version: 1,
    home: { city: 'Curitiba', state: 'PR' },
    work: { profession: 'Fotógrafa', employer: 'Estúdio Luz', education: ['Artes Visuais'] },
    life: { marital_status: 'solteira', history: ['Mudou para Curitiba em 2015'] },
    beliefs: { religion: 'católica', politics: null },
    tastes: { hobbies: ['trilhas'] },
  },
};

async function abrirPersona(): Promise<void> {
  backend.on('GET', /\/personas\/ig-1$/, () => json(PESSOA));
  await abrir();
  await click(byRole('tab', /^Persona$/i));
  await waitFor(() => text().includes('Origem e casa'));
}

it('salvar uma seção da biografia manda SÓ aquela seção no PATCH', async () => {
  backend.on('PATCH', /\/personas\/ig-1$/, (c) => json({ ...PESSOA, biography: { ...PESSOA.biography, ...(c.body as { biography: object }).biography } }));
  await abrirPersona();
  // Nada mudou: salvar fica bloqueado com o motivo.
  expect(byRole('button', /Salvar trabalho/i).getAttribute('aria-disabled')).toBe('true');
  await setValue(byRole('textbox', /^Profissão/i) as HTMLInputElement, 'Fotógrafa de casamentos');
  await click(byRole('button', /Salvar trabalho/i));
  await waitFor(() => backend.callsTo('PATCH', /\/personas\/ig-1$/).length === 1);
  expect(backend.callsTo('PATCH', /\/personas\/ig-1$/)[0]?.body).toEqual({
    biography: { work: { profession: 'Fotógrafa de casamentos', employer: 'Estúdio Luz', education: ['Artes Visuais'] } },
  });
});

it('a biografia marca o que vai ao modelo, e Crenças diz que ficam guardadas e não vão', async () => {
  await abrirPersona();
  expect(text()).toContain('guardadas, não vão ao modelo');
  // A marca acompanha só os campos da lista do backend (cidade, profissão, formação, hobbies), e o nome.
  const doCampo = (rotulo: RegExp) => (byRole('textbox', rotulo).parentElement?.textContent ?? '');
  expect(doCampo(/^Cidade onde mora/)).toContain('vai ao modelo');
  expect(doCampo(/^Profissão/)).toContain('vai ao modelo');
  expect(doCampo(/^Religião/)).not.toContain('vai ao modelo');
  expect(doCampo(/^Onde trabalha/)).not.toContain('vai ao modelo');
});

// ---------------------------------------------------------------- guia Aparelhos
it('Aparelhos sem vínculo diz onde vincular (Configuração → Instâncias e contas)', async () => {
  await act(async () => {
    root.render(<ProfileDetail profile={perfil({ instance_id: null, locality: null })} abaInicial="aparelhos"
                               onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('Configuração → Instâncias e contas'));
});

it('Aparelhos mostra o aparelho com os apps e abre o Foco nele', async () => {
  const { useUiStore } = await import('../../store/ui');
  useUiStore.setState({ focusInstanceId: null });
  backend.on('GET', /\/app-state$/, () => json([
    { instance_id: 'android-02', package_name: 'com.instagram.android', desired_release_id: null, installed_release_id: null,
      observed_version_name: '447.0', observed_version_code: 447, observed_splits: [], expected_splits: [],
      first_install_time: null, last_update_time: null, state: 'ready', pending_op: null, verified_at: null,
      drift_kind: null, detail: null, previous_release_id: null, last_operation: null },
    { instance_id: 'android-07', package_name: 'com.outro.app', desired_release_id: null, installed_release_id: null,
      observed_version_name: '1.0', observed_version_code: 1, observed_splits: [], expected_splits: [],
      first_install_time: null, last_update_time: null, state: 'ready', pending_op: null, verified_at: null,
      drift_kind: null, detail: null, previous_release_id: null, last_operation: null },
  ]));
  await act(async () => {
    root.render(<ProfileDetail profile={perfil()} abaInicial="aparelhos" onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('com.instagram.android'));
  expect(text()).toContain('447.0');
  expect(text()).not.toContain('com.outro.app');            // só os apps DESTE aparelho
  expect(text()).toContain('Onde esta persona vive');
  await click(byRole('button', /Abrir no Foco/i));
  expect(useUiStore.getState().focusInstanceId).toBe('android-02');
});
