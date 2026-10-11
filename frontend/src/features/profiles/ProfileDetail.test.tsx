// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { InstagramProfile, ProfileAccount } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { useUiStore } from '../../store/ui';
import { APPS, SETTINGS, makeBinding, makeInstance, makeSession } from '../../test/fixtures';
import {
  FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor,
} from '../../test/harness';
import { ProfileDetail } from './ProfileDetail';

/** Rótulo de cada guia por seção (a seção é o primeiro nível; a guia, a aba dentro dela). */
const GUIAS_POR_SECAO: [RegExp, string[]][] = [
  [/^Perfil/, ['Persona', 'Imagens', 'Memória']],
  [/^Contas e aparelhos/, ['Contas e acesso', 'Aparelhos']],
  [/^Atividade/, ['Interações', 'Execuções', 'Aprovações']],
  [/^Avançado/, ['Habilidades', 'Configurações']],
];

/** Abre uma guia como a pessoa faz: escolhe a seção que a contém (se ainda não é a aberta) e clica na aba. */
async function irParaGuia(nome: RegExp): Promise<void> {
  if (allByRole('tab', nome).length === 0) {
    const secao = GUIAS_POR_SECAO.find(([, guias]) => guias.some((g) => nome.test(g)));
    if (!secao) throw new Error(`Nenhuma seção tem a guia ${String(nome)}`);
    await click(byRole('button', secao[0], byRole('navigation', /Seções da persona/)));
  }
  await click(byRole('tab', nome));
}

const SENHA = 'senha-secreta-9!Zk';

function perfil(over: Partial<InstagramProfile> = {}): InstagramProfile {
  return {
    id: 'ig-1', username: 'luciana.bastos73519', display_name: 'Luciana Bastos', first_name: 'Luciana',
    last_name: 'Bastos', birth_date: null, email: null, persona_id: 'persona-1', persona_name: 'Luciana — fotografia',
    status: 'active', instance_id: 'android-02',
    locality: { worker_id: null, worker_name: 'este servidor', worker_state: 'online', known: true,
                available: true, moved: false, physical_id: null, detail: null },
    offline_policy: 'wait',
    credential: { configured: true, login_identifier: 'luciana.bastos73519', status: 'active', failed_attempts: 0,
                  blocked_until: null, updated_at: '2026-09-17T10:00:00Z', last_used_at: null },
    session: { status: 'session_ready', instance_id: 'android-02', observed_username: 'luciana.bastos73519',
               verified_at: '2026-09-17T11:00:00Z', detail: '@luciana.bastos73519 confirmado na tela',
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
  await waitFor(() => text().includes('@luciana.bastos73519'));
}

const SECOES_NA_TELA = ['Visão geral', 'Perfil', 'Contas e aparelhos', 'Atividade', 'Avançado'];

function rotulosDe(botoes: HTMLElement[]): string[] {
  return botoes.map((t) => (t.textContent ?? '').replace(/\d+$/, '').trim());
}

it('mostra 5 seções no primeiro nível e abre na Visão geral, sem abas soltas', async () => {
  await abrir();
  const nav = byRole('navigation', /Seções da persona/);
  expect(rotulosDe(allByRole('button', /./, nav))).toEqual(SECOES_NA_TELA);
  expect(byRole('button', /^Visão geral/, nav).getAttribute('aria-current')).toBe('page');
  expect(allByRole('button', /./, nav).filter((b) => b.getAttribute('aria-current') === 'page')).toHaveLength(1);
  // Visão geral é uma guia só: não há faixa de abas (o rótulo da região diz onde se está).
  expect(allByRole('tab', /./)).toHaveLength(0);
  expect(byRole('region', /^Visão geral$/)).toBeTruthy();
  expect(text()).not.toContain('Autenticação');
  expect(text()).not.toContain(SENHA);
});

it('em tela estreita as seções são uma lista suspensa com as mesmas 5 opções', async () => {
  await abrir();
  const lista = byRole('combobox', /Seção da persona/) as HTMLSelectElement;
  expect(Array.from(lista.options).map((o) => o.text)).toEqual(SECOES_NA_TELA);
  expect(lista.value).toBe('visao');
  await setValue(lista, 'avancado');
  expect(byRole('button', /^Avançado/).getAttribute('aria-current')).toBe('page');
  expect(rotulosDe(allByRole('tab', /./))).toEqual(['Habilidades', 'Configurações']);
});

it('cada seção abre a sua primeira guia e mostra só as guias dela: as 11 guias continuam alcançáveis', async () => {
  await abrir();
  const esperado: [RegExp, string[]][] = [
    [/^Perfil/, ['Persona', 'Imagens', 'Memória']],
    [/^Contas e aparelhos/, ['Contas e acesso', 'Aparelhos']],
    [/^Atividade/, ['Interações', 'Execuções', 'Aprovações']],
    [/^Avançado/, ['Habilidades', 'Configurações']],
  ];
  const vistas = ['Visão geral'];
  for (const [secao, guias] of esperado) {
    await click(byRole('button', secao, byRole('navigation', /Seções da persona/)));
    expect(byRole('button', secao).getAttribute('aria-current')).toBe('page');
    expect(rotulosDe(allByRole('tab', /./))).toEqual(guias);
    expect(allByRole('tab', /./)[0]?.getAttribute('aria-selected')).toBe('true');   // abre a primeira
    vistas.push(...guias);
  }
  expect(vistas.sort()).toEqual(['Visão geral', 'Persona', 'Contas e acesso', 'Imagens', 'Aparelhos', 'Memória', 'Interações',
                                 'Habilidades', 'Aprovações', 'Execuções', 'Configurações'].sort());
});

it('guia vinda do link abre dentro da seção certa (…/memoria → Perfil > Memória)', async () => {
  await act(async () => {
    root.render(<ProfileDetail profile={perfil()} aba="memoria" onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => byRole('tab', /Memória/).getAttribute('aria-selected') === 'true');
  expect(byRole('button', /^Perfil/).getAttribute('aria-current')).toBe('page');
  expect(rotulosDe(allByRole('tab', /./))).toEqual(['Persona', 'Imagens', 'Memória']);
});

it('trocar de seção avisa a tela pela guia (a URL guarda a guia, não a seção)', async () => {
  const pedidos: string[] = [];
  await act(async () => {
    root.render(<ProfileDetail profile={perfil()} aba="visao" onAbaChange={(a) => pedidos.push(a)} onBack={() => {}}
                               onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('@luciana.bastos73519'));
  await click(byRole('button', /^Atividade/));
  await click(byRole('button', /^Avançado/));
  expect(pedidos).toEqual(['interacoes', 'habilidades']);
});

it('o cabeçalho tem a identidade UMA vez: nome e @ não se repetem na Visão geral', async () => {
  await abrir();
  const t = text();
  expect((t.match(/Luciana Bastos/g) ?? []).length).toBe(1);
  expect((t.match(/@luciana\.bastos73519/g) ?? []).length).toBe(1);
  expect(container.querySelectorAll('h1')).toHaveLength(1);
  expect(container.querySelector('h1')?.textContent).toBe('Luciana Bastos');
  // O cartão "Identidade" ficou só com atributos.
  expect(text()).toContain('Idade');
  expect(text()).toContain('Cidade');
});

it('estado "Não verificada" é um botão que leva à guia Contas e acesso (e não dispara nada)', async () => {
  await abrir(perfil({ session: { ...perfil().session, status: 'unknown', verified_at: null, detail: null } }));
  const antes = backend.calls.filter((c) => c.method !== 'GET').length;
  await click(byRole('button', /Não verificada.*Verificar conta/));
  await waitFor(() => byRole('tab', /Contas e acesso/).getAttribute('aria-selected') === 'true');
  expect(backend.calls.filter((c) => c.method !== 'GET').length).toBe(antes);
});

it('estado "Conectado" não é botão e diz há quanto tempo a conta foi confirmada', async () => {
  await abrir();
  expect(text()).toMatch(/Conectado · confirmada há /);
  expect(() => byRole('button', /Conectado/)).toThrow();
});

it('"Conectado" sem data de confirmação mostra só o estado', async () => {
  await abrir(perfil({ session: { ...perfil().session, status: 'session_ready', verified_at: null } }));
  expect(text()).toContain('Conectado');
  expect(text()).not.toMatch(/Conectado · confirmada/);
});

it('"Abrir no aparelho" abre o Foco do aparelho principal', async () => {
  await abrir();
  await click(byRole('button', /^Abrir no aparelho/));
  expect(useUiStore.getState().focusInstanceId).toBe('android-02');
  await act(async () => { useUiStore.getState().closeFocus(); });
});

it('sem aparelho vinculado, "Abrir no aparelho" fica indisponível e explica por quê', async () => {
  await abrir(perfil({ instance_id: null, locality: null, session: { ...perfil().session, instance_id: null } }));
  const botao = byRole('button', /Abrir no aparelho/);
  expect(botao.getAttribute('aria-disabled')).toBe('true');
  expect(botao.textContent).toContain('Vincule um aparelho');
  expect(text()).toContain('Sem aparelho vinculado');
});

it('religião e política ficam num bloco recolhido: fechado por padrão, com aria-expanded, abre e fecha', async () => {
  backend.on('GET', /capacidades/, () => json({ profile_id: 'ig-1', flows: [], steps_driven_by: {}, recipe_share: null, interactions: {} }));
  backend.on('GET', /\/interactions$/, () => json([]));
  await abrir(perfil({ biography: { beliefs: { religion: RELIGIAO, politics: POLITICA } } as InstagramProfile['biography'] }));
  const botao = byRole('button', /Atributos de personalidade/);
  expect(botao.getAttribute('aria-expanded')).toBe('false');
  expect(botao.tagName).toBe('BUTTON');                       // alcançável e acionável pelo teclado (Enter/Espaço)
  expect(text()).not.toContain('católica');
  expect(text()).not.toContain('centro-esquerda');
  expect(text()).not.toContain('Religião');
  await click(botao);
  expect(botao.getAttribute('aria-expanded')).toBe('true');
  expect(text()).toContain('católica · pratica às vezes');
  expect(text()).toContain('centro-esquerda · engajamento baixo');
  await click(botao);
  expect(botao.getAttribute('aria-expanded')).toBe('false');
  expect(text()).not.toContain('católica');
});

it('o escopo por app só aparece onde filtra (Memória e Interações) e diz "Mostrando: …" e o que ele filtra', async () => {
  backend.on('GET', /\/accounts$/, () => json([conta()]));
  backend.on('GET', /\/memory/, () => json([]));
  await abrir();
  await waitFor(() => backend.callsTo('GET', /\/accounts$/).length > 0);
  expect(() => byRole('group', /Filtrar por app/)).toThrow();     // Visão geral: nada a filtrar
  await irParaGuia(/Memória/i);
  const grupo = await waitFor(() => byRole('group', /Filtrar por app/));
  expect(grupo.textContent).toContain('Mostrando: todos os apps');
  expect(grupo.textContent).toContain('Filtra só Memória e Interações');
  await click(byRole('button', /^Instagram/, grupo));
  expect(grupo.textContent).toContain('Mostrando: Instagram');
  await irParaGuia(/Contas e acesso/i);
  expect(() => byRole('group', /Filtrar por app/)).toThrow();
});

const SEM_SENHA = { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                   updated_at: null, last_used_at: null, consent_at: null, consent_by: null };

/** A conta do Instagram da persona, como `GET …/accounts` a devolve na v0.28 (credencial só com metadados). */
function conta(over: Partial<ProfileAccount> = {}): ProfileAccount {
  return {
    id: 'acc-1', profile_id: 'ig-1', app_id: 'instagram', app_name: 'Instagram', package: 'com.instagram.android',
    handle: 'luciana.bastos73519', host: null, login_identifier: 'luciana@exemplo.com', status: 'active',
    session_status: 'unknown', session_detail: null, session_verified_at: null,
    session: { status: 'unknown', instance_id: 'android-02', observed_username: null, verified_at: null, detail: null,
               stale: false },
    session_actions: null, automated_login: true, credential_configured: true,
    credential: { configured: true, login_identifier: 'luciana@exemplo.com', status: 'active', failed_attempts: 0,
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
  // O formulário de conta espera o catálogo (a falha dele não vira "nenhum app é o âncora"): o registro de sempre.
  backend.on('GET', /app-catalog/, () => json([
    { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
      session_provider: 'instagram', needs_profile: true, profile_anchor: true },
  ]));
  await act(async () => {
    root.render(<ProfileDetail profile={p} onBack={() => {}} onChanged={onChanged} />);
  });
  await irParaGuia(/Contas e acesso/i);
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

/** A conta Outlook da persona, sem senha e sem login automático (23.9). */
const OUTLOOK: Partial<ProfileAccount> = {
  id: 'acc-2', app_id: 'outlook', app_name: 'Outlook', package: 'com.microsoft.office.outlook',
  handle: 'luciana@outlook.com', automated_login: false, ...CONTA_SEM_SENHA,
};

it('conta sem senha usa a senha de outra conta da persona: POST …/credential/clone só com o id, sem valor nem consent', async () => {
  backend.on('POST', /\/accounts\/acc-2\/credential\/clone$/, () => json(conta({ ...OUTLOOK, credential_configured: true })));
  await abrirContas([conta(), conta(OUTLOOK)]);
  // Só a conta SEM senha oferece a de outra (a do Instagram não tem de onde clonar: a do Outlook está vazia).
  const escolha = byRole('combobox', /Usar a senha de outra conta desta persona/i) as HTMLSelectElement;
  expect(allByRole('combobox', /Usar a senha de outra conta desta persona/i)).toHaveLength(1);
  expect(byRole('button', /Usar esta senha/i).getAttribute('aria-disabled')).toBe('true');
  await setValue(escolha, 'acc-1');
  expect(escolha.textContent).toContain('Instagram — luciana.bastos73519');
  await click(byRole('button', /Usar esta senha/i));
  await waitFor(() => backend.callsTo('POST', /\/credential\/clone$/).length === 1);
  expect(backend.callsTo('POST', /\/credential\/clone$/)[0]?.body).toEqual({ clonar_de: 'acc-1' });
  expect(backend.callsTo('PUT', /\/credential$/)).toHaveLength(0);
});

it('adicionar conta com a senha de outra conta: o campo de senha some e o POST leva clonar_de, sem password nem consent', async () => {
  useAppStore.setState({ apps: [
    { id: 'instagram', name: 'Instagram', package: 'com.instagram.android', activity: null, apk_path: null,
      nav_hints: null, known_selectors: null, builtin: true },
    { id: 'outlook', name: 'Outlook', package: 'com.microsoft.office.outlook', activity: null, apk_path: null,
      nav_hints: null, known_selectors: null, builtin: false },
  ] });
  backend.on('POST', /\/accounts$/, () => json(conta({ ...OUTLOOK, credential_configured: true }), 201));
  await abrirContas([conta()]);
  await click(byRole('button', /Adicionar conta/i));
  // O formulário espera o catálogo de apps: o seletor só aparece quando ele chega (29.104).
  await setValue(await waitFor(() => byRole('combobox', /Aplicativo/i)) as HTMLSelectElement, 'outlook');
  await setValue(byRole('textbox', /Usuário na conta/i) as HTMLInputElement, 'luciana@outlook.com');
  const senhas = container.querySelectorAll('input[type="password"]').length;
  await setValue(byRole('combobox', /^Usar a senha de outra conta/i) as HTMLSelectElement, 'acc-1');
  expect(container.querySelectorAll('input[type="password"]').length).toBe(senhas - 1);
  expect(text()).toContain('A autorização para a automação digitá-la não vem junto');
  await click(byRole('button', /^Adicionar$/i));
  await waitFor(() => backend.callsTo('POST', /\/accounts$/).length === 1);
  expect(backend.callsTo('POST', /\/accounts$/)[0]?.body).toEqual({
    app_id: 'outlook', handle: 'luciana@outlook.com', host: null, login_identifier: null, clonar_de: 'acc-1',
  });
});

it('o identificador de login mostra o valor GRAVADO, nunca o handle por padrão', async () => {
  await abrirContas([conta()]);
  const login = byRole('textbox', /Identificador de login/i) as HTMLInputElement;
  expect(login.value).toBe('luciana@exemplo.com');
  expect(text()).toContain('Identificador de login gravado: luciana@exemplo.com');

  await act(async () => root.unmount());
  root = createRoot(container);
  await abrirContas([conta(CONTA_SEM_SENHA)]);
  const vazio = byRole('textbox', /Identificador de login/i) as HTMLInputElement;
  expect(vazio.value).toBe('');                                            // e não "luciana.bastos73519"
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
  // Os botões da conta dividem o `loading`: Verificar só aceita o clique depois da resposta do Conectar (29.104).
  await waitFor(() => byRole('button', /Verificar conta/i).getAttribute('aria-busy') !== 'true');
  await click(byRole('button', /Verificar conta/i));
  await waitFor(() => backend.callsTo('POST', /\/accounts\/acc-1\/session\/verify$/).length === 1);
  // As rotas antigas por perfil (apelidos da conta âncora) não são mais chamadas daqui.
  expect(backend.callsTo('POST', /\/instagram\/profiles\/ig-1\/(connect|verify)$/)).toHaveLength(0);
});

it('conta conectada mostra a conta observada e oferece reconectar', async () => {
  await abrirContas([conta({
    session_status: 'session_ready',
    session: { status: 'session_ready', instance_id: 'android-02', observed_username: 'luciana.bastos73519',
               verified_at: '2026-09-17T10:00:00Z', detail: null, stale: true },
  })]);
  expect(text()).toContain('Conectado');
  expect(text()).toContain('Conta observada na tela: @luciana.bastos73519');
  expect(text()).toContain('dado velho');
  expect(byRole('button', /Reconectar/i)).toBeTruthy();
});

it('persona sem @ ganha o Instagram pela adoção: POST /instagram/profiles com persona_id', async () => {
  useAppStore.setState({ apps: [
    { id: 'instagram', name: 'Instagram', package: 'com.instagram.android', activity: null, apk_path: null,
      nav_hints: null, known_selectors: null, builtin: true },
  ] });
  // 23.10: quem decide se o app escolhido é a conta de cadastro é o catálogo (`profile_anchor`), não o nome dele.
  backend.on('GET', /app-catalog/, () => json([
    { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
      session_provider: 'instagram', needs_profile: true, profile_anchor: true },
  ]));
  backend.on('POST', /^\/api\/instagram\/profiles$/, () => json(perfil(), 201));
  const semConta = perfil({ username: '', persona_id: null, instance_id: null, locality: null });
  backend.on('GET', /\/accounts$/, () => json([]));
  await act(async () => {
    root.render(<ProfileDetail profile={semConta} abaInicial="contas" onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('ainda não tem @ de cadastro'));
  expect(text()).toContain('Sem conta de cadastro');
  await click(byRole('button', /Adicionar conta/i));
  // 29.140: o seletor de app só aparece com o catálogo (GET app-catalog); com atraso no fetch, ainda não estava.
  await setValue(await waitFor(() => byRole('combobox', /Aplicativo/i)) as HTMLSelectElement, 'instagram');
  await setValue(byRole('textbox', /Usuário do Instagram/i) as HTMLInputElement, 'luciana.bastos73519');
  await click(byRole('button', /^Adicionar$/i));
  await waitFor(() => backend.callsTo('POST', /^\/api\/instagram\/profiles$/).length === 1);
  expect(backend.callsTo('POST', /^\/api\/instagram\/profiles$/)[0]?.body)
    .toEqual({ username: 'luciana.bastos73519', persona_id: 'ig-1' });
  expect(backend.callsTo('POST', /\/accounts$/)).toHaveLength(0);
});

it('catálogo de apps indisponível: o formulário espera o catálogo e não cria a conta solta no lugar da adoção', async () => {
  // Revisão da 23.10: a falha virava catálogo vazio, nenhum app era o âncora, e o Instagram de uma persona sem @ saía
  // por `POST …/accounts` (conta solta, sem @ de cadastro) em vez da adoção.
  useAppStore.setState({ apps: [
    { id: 'instagram', name: 'Instagram', package: 'com.instagram.android', activity: null, apk_path: null,
      nav_hints: null, known_selectors: null, builtin: true },
  ] });
  backend.on('GET', /app-catalog/, () => apiError(500, 'internal', 'registro indisponível'));
  backend.on('POST', /^\/api\/instagram\/profiles$/, () => json(perfil(), 201));
  backend.on('POST', /\/accounts$/, () => json(conta(), 201));
  backend.on('GET', /\/accounts$/, () => json([]));
  const semConta = perfil({ username: '', persona_id: null, instance_id: null, locality: null });
  await act(async () => {
    root.render(<ProfileDetail profile={semConta} abaInicial="contas" onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('ainda não tem @ de cadastro'));
  await click(byRole('button', /Adicionar conta/i));
  await waitFor(() => text().includes('Não foi possível carregar os aplicativos'));
  expect(() => byRole('combobox', /Aplicativo/i)).toThrow();        // sem catálogo não há seletor de app (o de seção é outro)

  backend.on('GET', /app-catalog/, () => json([
    { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
      session_provider: 'instagram', needs_profile: true, profile_anchor: true },
  ]));
  await click(byRole('button', /Tentar de novo/));
  await setValue(await waitFor(() => byRole('combobox', /Aplicativo/i)) as HTMLSelectElement, 'instagram');
  await setValue(byRole('textbox', /Usuário do Instagram/i) as HTMLInputElement, 'luciana.bastos73519');
  await click(byRole('button', /^Adicionar$/i));
  await waitFor(() => backend.callsTo('POST', /^\/api\/instagram\/profiles$/).length === 1);
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
  await irParaGuia(/Memória/i);
  await waitFor(() => text().includes('Corre maratonas'));
  expect(text()).toContain('visto 2x');
  // Fatos agrupados por assunto (item 11.6): o cartão do assunto aparece com o nome dele.
  expect(text()).toContain('@ana');

  // "Ensinar um fato" é um botão que abre o formulário — ele não fica exposto o tempo todo.
  await click(byRole('button', /Ensinar um fato/i));
  await setValue(byRole('textbox', /Sobre quem/i) as HTMLInputElement, '@quillon');
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
  await irParaGuia(/Aprovações/i);
  await waitFor(() => text().includes('Enviar a mensagem para @ana'));
  expect(byRole('button', /Aprovar/i)).toBeTruthy();
  expect(byRole('button', /Rejeitar/i)).toBeTruthy();

  await click(byRole('button', /Aprovar/i));
  await waitFor(() => backend.callsTo('POST', /decide/).length === 1);
});

it('B3: o selo da Atividade conta aprovações e o clique abre a guia Aprovações; o nome diz o que o número conta', async () => {
  const pendente = {
    id: 'apr-1', profile_id: 'ig-1', run_id: 'run-1', objective_id: 'run-1:android-02', step_id: 's1',
    capability: 'SEND_MESSAGE', target: '@ana', summary: 'Enviar a mensagem para @ana',
    generated_content: 'bom dia!', approved_content: null, content: 'bom dia!', status: 'pending',
    created_at: '2026-09-17T12:00:00Z', decided_at: null, decided_note: null,
  };
  backend.on('GET', /approvals/, () => json([pendente]));
  await abrir();
  const nav = byRole('navigation', /Seções da persona/);
  const botao = await waitFor(() => byRole('button', /^Atividade, 1 aprovação aguardando você$/, nav));
  // WCAG 2.5.3: o texto visível ("Atividade 1") é o começo do nome.
  expect(botao.textContent).toBe('Atividade 1');
  await click(botao);
  expect(byRole('tab', /Aprovações/).getAttribute('aria-selected')).toBe('true');
  expect(byRole('tab', /Interações/).getAttribute('aria-selected')).toBe('false');
});

it('B3: sem aprovação pendente a Atividade abre a primeira guia (Interações), como antes', async () => {
  await abrir();
  await click(byRole('button', /^Atividade$/, byRole('navigation', /Seções da persona/)));
  expect(byRole('tab', /Interações/).getAttribute('aria-selected')).toBe('true');
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
  await irParaGuia(/Configurações/i);
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
  await irParaGuia(/Configurações/i);
  await waitFor(() => text().includes('Curtir a publicação'));
  // 31.276 (ADR-083): o backend não aplica mais limite por hora; a tela não mostra o que não vale
  expect(text()).not.toContain('Curtidas por hora');
  // não há mais <select>: a política é um grupo de botões de rádio, as três opções sempre visíveis
  expect(container.querySelectorAll('select[value]')).toHaveLength(0);
  expect(byRole('radiogroup', /Política de Curtir a publicação/i)).toBeTruthy();

  const grupo = byRole('radiogroup', /Política de Curtir a publicação/i);
  await click(byRole('radio', /Só manual/i, grupo));
  await waitFor(() => backend.callsTo('PUT', /\/policy$/).length === 1);
  expect(backend.calls.find((c) => c.method === 'PUT')?.body).toEqual({ capabilities: { LIKE_POST: 'manual_only' } });
  // A diferença em relação ao catálogo fica visível: o botão de voltar a herdar diz o que passaria a valer. (O texto
  // antigo "padrão: Sozinho" não existia mais; o waitFor booleano passava sem afirmar nada até o harness mudar.)
  await waitFor(() => expect(text()).toContain('herdar (Sozinho)'));
});

it('mais de um app com catálogo: o seletor troca a política e a ação, e o PUT leva o pacote certo', async () => {
  // 23.10: antes a aba sempre pegava "o primeiro app com login gerenciado da lista" — com um segundo app de
  // catálogo, a escolha é deliberada (o âncora por padrão) e o PUT vai com o `package` do app em tela.
  backend.on('GET', /app-catalog/, () => json([
    { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
      session_provider: 'instagram', needs_profile: true, profile_anchor: true },
    { package: 'com.exemplo.correio', name: 'Correio', label: 'Correio', has_catalog: true,
      session_provider: 'correio', needs_profile: true, profile_anchor: false },
  ]));
  backend.on('GET', /\/policy$/, (c) => json(
    c.query.get('package') === 'com.exemplo.correio'
      ? { package: 'com.exemplo.correio', limits: {}, capabilities: { LER_CAIXA: 'autonomous' },
          defaults: { LER_CAIXA: 'autonomous' }, loosened: [] }
      : { package: 'com.instagram.android', limits: { likes_per_hour: 30 }, capabilities: { LIKE_POST: 'autonomous' },
          defaults: { LIKE_POST: 'autonomous' }, loosened: [] },
  ));
  backend.on('GET', /capabilities/, (c) => json(
    c.query.get('package') === 'com.exemplo.correio'
      ? [{ key: 'LER_CAIXA', title: 'Ler a caixa de entrada', side_effect: false, risk: 'low',
           default_policy: 'autonomous', limit_bucket: null, needs_draft: false, bindings: [] }]
      : [{ key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
           default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] }],
  ));
  backend.on('GET', /interactions/, () => json([]));
  backend.on('PUT', /\/policy$/, () => json({
    package: 'com.exemplo.correio', limits: {}, capabilities: { LER_CAIXA: 'manual_only' },
    defaults: { LER_CAIXA: 'autonomous' }, loosened: [],
  }));
  await abrir();
  await irParaGuia(/Configurações/i);
  // Por padrão vem o app âncora.
  await waitFor(() => text().includes('Curtir a publicação'));
  await setValue(byRole('combobox', /Aplicativo/i) as HTMLSelectElement, 'com.exemplo.correio');
  await waitFor(() => text().includes('Ler a caixa de entrada'));
  expect(text()).not.toContain('Curtir a publicação');
  await click(byRole('radio', /Só manual/i, byRole('radiogroup', /Política de Ler a caixa de entrada/i)));
  await waitFor(() => backend.callsTo('PUT', /\/policy$/).length === 1);
  const chamada = backend.calls.find((c) => c.method === 'PUT')!;
  expect(chamada.body).toEqual({ capabilities: { LER_CAIXA: 'manual_only' } });
  expect(chamada.query.get('package')).toBe('com.exemplo.correio');
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
  await irParaGuia(/Configurações/i);
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
  await irParaGuia(/Configurações/i);
  await waitFor(() => text().includes('Enviar a mensagem'));
  expect(text()).toContain('mais frouxo que o padrão');
});

it('31.276: backend que ainda manda limites antigos: a aba não mostra limite nem medidor', async () => {
  montarConfigBackend(
    [
      { key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
        default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] },
    ],
    {
      limits: { likes_per_hour: 10, cooldown_between_external_actions_s: 45 },
      own_limits: { likes_per_hour: 10 }, group_limits: {}, limits_origin: { likes_per_hour: 'own' },
      capabilities: { LIKE_POST: 'autonomous' },
      defaults: { LIKE_POST: 'autonomous' },
      loosened: [],
    },
  );
  await abrir();
  await irParaGuia(/Configurações/i);
  await waitFor(() => text().includes('Curtir a publicação'));
  expect(text()).toContain('Nenhuma escolha própria: tudo vem do grupo ou do padrão.');   // limite próprio antigo não conta
  for (const velho of ['Curtidas por hora', 'Intervalo entre ações', 'Aquecimento', 'sem contagem de uso']) expect(text()).not.toContain(velho);
  expect(container.querySelector('[role="progressbar"]')).toBeNull();
});

it('Completar com IA manda a instrução do dono ao enrich e mostra a persona completada', async () => {
  const base = {
    id: 'ig-1', name: 'Luciana — fotografia', summary: null, persona_prompt: null,
    traits: { tone: 'calmo' }, voice_gaps: ['slang'], profile_id: 'ig-1', profile_username: 'luciana.bastos73519',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  };
  backend.on('GET', /\/personas\/ig-1$/, () => json(base));
  backend.on('POST', /\/personas\/ig-1\/enrich$/, () => json({
    ...base, summary: 'Fotógrafa que vai ao culto', voice_gaps: [], updated_at: '2026-09-28T12:00:00Z',
  }));
  await abrir();
  await irParaGuia(/Persona/i);
  await waitFor(() => text().includes('Completar com IA'));
  expect(text()).toContain('Chamada paga');
  await setValue(byRole('textbox', /Instruções para o que falta/i) as HTMLInputElement, 'é evangélica e vai ao culto toda semana');
  await click(byRole('button', /^Completar com IA$/i));
  await waitFor(() => backend.callsTo('POST', /enrich$/).length === 1);
  expect(backend.callsTo('POST', /enrich$/)[0]?.body).toEqual({ instructions: 'é evangélica e vai ao culto toda semana' });
});

it('Completar com IA sem instrução não manda corpo, e sem lacuna avisa que não havia nada a completar', async () => {
  const base = {
    id: 'ig-1', name: 'Luciana — fotografia', summary: 'x', persona_prompt: 'y', traits: { tone: 'calmo' },
    voice_gaps: [], profile_id: 'ig-1', profile_username: 'luciana.bastos73519',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  };
  backend.on('GET', /\/personas\/ig-1$/, () => json(base));
  backend.on('POST', /\/personas\/ig-1\/enrich$/, () => json(base));
  await abrir();
  await irParaGuia(/Persona/i);
  await waitFor(() => text().includes('Completar com IA'));
  await click(byRole('button', /^Completar com IA$/i));
  await waitFor(() => backend.callsTo('POST', /enrich$/).length === 1);
  expect(backend.callsTo('POST', /enrich$/)[0]?.body ?? null).toBeNull();
});

it('avisa quais campos de voz faltam na persona e deixa preencher cada um', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Luciana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: { tone: 'calmo', formality: 'neutro', typical_length: 'curta', emojis: 'raro',
              personality: 'calma', humor: 'leve', interests: ['fotografia'] },
    // O backend calcula: é a MESMA lista que vai ao modelo. A tela não recalcula nada.
    voice_gaps: ['slang', 'dm_style', 'comment_style', 'with_known', 'with_strangers', 'common_phrases',
                 'forbidden_phrases', 'examples'],
    profile_id: 'ig-1', profile_username: 'luciana.bastos73519',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  await abrir();
  await irParaGuia(/Persona/i);
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
    id: 'ig-1', name: 'Luciana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: { tone: 'calmo' }, voice_gaps: [], profile_id: 'ig-1', profile_username: 'luciana.bastos73519',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  backend.on('POST', /preview/, () => json({
    content: 'boa tarde por aí!', rationale: 'cumprimento curto', refused: false,
    refusal_reason: null, memory_candidates: [],
  }));
  await abrir();
  await irParaGuia(/Persona/i);
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
    id: 'ig-1', name: 'Luciana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: { tone: 'calmo' }, profile_id: 'ig-1', profile_username: 'luciana.bastos73519',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  backend.on('POST', /preview/, () => json({
    content: 'oi! tudo ótimo por aqui', rationale: 'resposta curta e calma', refused: false,
    refusal_reason: null, memory_candidates: [],
  }));
  await abrir();
  await irParaGuia(/Persona/i);
  await waitFor(() => text().includes('Testar persona'));
  await click(byRole('button', /Testar persona/i));
  await waitFor(() => text().includes('oi! tudo ótimo por aqui'));
  expect(backend.callsTo('POST', /interactions/)).toHaveLength(0);
});

it('29.57: a recusa da prévia se identifica como ANA, e o rascunho não aparece', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Luciana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: { tone: 'calmo' }, profile_id: 'ig-1', profile_username: 'luciana.bastos73519',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  backend.on('POST', /preview/, () => json({
    content: '', rationale: '', refused: true, refusal_reason: 'pede dado pessoal de terceiro', memory_candidates: [],
  }));
  await abrir();
  await irParaGuia(/Persona/i);
  await waitFor(() => text().includes('Testar persona'));
  await click(byRole('button', /Testar persona/i));
  await waitFor(() => text().includes('ANA recusou escrever esta resposta: pede dado pessoal de terceiro'));
  expect(backend.callsTo('POST', /interactions/)).toHaveLength(0);
});

// ---------------------------------------------------------------- item 11.6: persona como montagem
it('a persona marca o valor certo nas réguas e mostra Diz × Nunca diz e os exemplos como balões', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Luciana — fotografia', summary: 'Fotógrafa de retratos.', persona_prompt: 'Responda com calma.',
    traits: {
      tone: 'calmo', formality: 'formal', typical_length: 'longa', emojis: 'raro', personality: 'Observadora e gentil',
      interests: ['fotografia', 'trilhas'], common_phrases: ['bom dia!'], forbidden_phrases: ['mano'],
      examples: ['Oi! Tudo bem por aí?'], with_known: 'Direta e calorosa', with_strangers: 'Educada e reservada',
    },
    voice_gaps: ['slang', 'humor', 'dm_style', 'comment_style'],
    profile_id: 'ig-1', profile_username: 'luciana.bastos73519',
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
  }));
  await abrir();
  await irParaGuia(/Persona/i);
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
    { id: 'mem-3', profile_id: 'ig-1', subject: '@quillon', content: 'Fato C — gosta de futebol', source: 'taught',
      interaction_id: null, importance: 0.3, confidence: 0.4, occurrences: 1, expires_at: null,
      created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:00:00Z', last_used_at: null },
  ]));
  await abrir();
  await irParaGuia(/Memória/i);
  await waitFor(() => text().includes('Fato A'));

  const html = container.innerHTML;
  const anaIdx = html.indexOf('>@ana<');
  const brunoIdx = html.indexOf('>@quillon<');
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
  await irParaGuia(/Interações/i);
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
  await irParaGuia(/Habilidades/i);
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

it('o mapa de habilidades conta a etapa que fechou sem o ator (`sem_ator`) numa linha própria', async () => {
  backend.on('GET', /capacidades/, () => json({
    profile_id: 'ig-1', flows: [], steps_driven_by: { recipe: 3, ai: 1, sem_ator: 2 }, recipe_share: 0.5,
    interactions: {},
  }));
  await abrir();
  await irParaGuia(/Habilidades/i);
  await waitFor(() => text().includes('Sem o ator'));
  const linha = [...container.querySelectorAll('dt')].find((d) => d.textContent === 'Sem o ator');
  expect(linha?.nextElementSibling?.textContent).toBe('2');
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

  // Os números vêm de `capacidades`, que pode chegar depois da persona (29.104).
  await waitFor(() => text().includes('75%'));
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
  await irParaGuia(/Memória/i);
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
    { id: 'grp-1', name: 'Cautelosos', description: '', capabilities: {}, limits: {}, loosened: [], members: [{ id: 'ig-1', username: 'luciana.bastos73519' }], created_at: '', updated_at: '' },
    { id: 'grp-2', name: 'Soltos', description: '', capabilities: {}, limits: {}, loosened: [], members: [], created_at: '', updated_at: '' },
  ]));
  backend.on('PUT', /\/policy$/, () => json({ ...politica, own: {}, origin: { ...politica.origin, SEND_MESSAGE: 'group' },
                                              capabilities: { ...politica.capabilities, SEND_MESSAGE: 'manual_only' } }));
  backend.on('PATCH', /\/instagram\/profiles\/ig-1$/, () => json(perfil({ policy_group_id: 'grp-2', policy_group_name: 'Soltos' })));
  await abrir();
  await irParaGuia(/Configurações/i);
  await waitFor(() => expect(text()).toContain('Enviar a mensagem'));

  expect(text()).toContain('do grupo Cautelosos');                 // Curtir vem do grupo
  expect(text()).toContain('próprio · sobrepõe o grupo');          // DM foi mudada no perfil e o grupo diz outra coisa
  expect(text()).toContain('padrão');                              // Abrir o feed é o padrão do catálogo
  expect(text()).toContain('1 ação escolhida nesta persona — sobrepõem o grupo');

  // "herdar" manda null — nunca uma cópia do valor do grupo, que prenderia o perfil contra o grupo
  await click(byRole('button', /herdar \(Só manual\)/i));
  await waitFor(() => expect(backend.callsTo('PUT', /\/policy$/)).toHaveLength(1));
  expect(backend.callsTo('PUT', /\/policy$/)[0]!.body).toEqual({ capabilities: { SEND_MESSAGE: null } });
  // 29.140: o PUT registrado não é a resposta. Enquanto ela não chega, o "Grupo" fica desabilitado (salvando); com
  // atraso no fetch (semente 88), o setValue logo depois caía nele. Espera a resposta assentar na tela.
  await waitFor(() => expect(text()).not.toContain('próprio · sobrepõe o grupo'));
  await waitFor(() => expect((byRole('combobox', /^Grupo$/) as HTMLSelectElement).disabled).toBe(false));

  // trocar o grupo é um PATCH no perfil
  const select = byRole('combobox', /^Grupo$/) as HTMLSelectElement;
  await setValue(select, 'grp-2');
  await waitFor(() => expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-1$/)).toHaveLength(1));
  expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-1$/)[0]!.body).toEqual({ policy_group_id: 'grp-2' });
});

// 31.265: o grupo dispensado da aprovação (ADR-082) pede a confirmação que diz o efeito; os outros grupos seguem direto.
async function abrirGrupoComLiberado(grupoAtual: string | null, extraSettings: Record<string, unknown> = {}): Promise<void> {
  const politica = {
    limits: {}, capabilities: {}, defaults: {}, loosened: [], group_id: grupoAtual, group_name: grupoAtual,
    own: {}, group: {}, origin: {}, own_limits: {}, group_limits: {}, limits_origin: {},
  };
  montarConfigBackend([], politica);
  backend.on('GET', /policy-groups$/, () => json([
    { id: 'grp-1', name: 'Cautelosos', description: '', capabilities: {}, limits: {}, loosened: [], members: [], created_at: '', updated_at: '' },
    { id: 'grp-2', name: 'Liberados', description: '', capabilities: {}, limits: {}, loosened: [], members: [{ id: 'ig-9', username: 'x' }], created_at: '', updated_at: '' },
  ]));
  backend.on('PATCH', /\/instagram\/profiles\/ig-1$/, () => json(perfil()));
  useAppStore.setState({ settings: { ...SETTINGS, grupo_sem_aprovacao: 'grp-2', ...extraSettings } });
  await act(async () => {
    root.render(<><ProfileDetail profile={perfil()} onBack={() => {}} onChanged={async () => {}} /><ConfirmHost /></>);
  });
  await waitFor(() => text().includes('@luciana.bastos73519'));
  await irParaGuia(/Configurações/i);
  await waitFor(() => expect((byRole('combobox', /^Grupo$/) as HTMLSelectElement).disabled).toBe(false));
}
const PATCH_DO_GRUPO = () => backend.callsTo('PATCH', /\/instagram\/profiles\/ig-1$/);

it('31.265: pôr a persona no grupo sem aprovação diz o efeito e só grava ao confirmar; cancelar não grava', async () => {
  await abrirGrupoComLiberado('grp-1');
  const select = byRole('combobox', /^Grupo$/) as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.textContent)).toContain('Liberados · 1 persona · sem aprovação');
  await setValue(select, 'grp-2');
  const dialogo = await waitFor(() => byRole('dialog', /no grupo Liberados\?/));
  expect(text(dialogo)).toContain('sem aprovação nas portas');
  expect(text(dialogo)).toContain('sem ninguém ler o texto antes');
  expect(PATCH_DO_GRUPO()).toHaveLength(0);
  await click(byRole('button', /^Voltar$/, dialogo));
  await waitFor(() => expect(allByRole('dialog', /no grupo Liberados/)).toHaveLength(0));
  expect(PATCH_DO_GRUPO()).toHaveLength(0);
  await setValue(select, 'grp-2');
  await click(byRole('button', /^Pôr no grupo$/, await waitFor(() => byRole('dialog', /no grupo Liberados\?/))));
  await waitFor(() => expect(PATCH_DO_GRUPO()).toHaveLength(1));
  expect(PATCH_DO_GRUPO()[0]!.body).toEqual({ policy_group_id: 'grp-2' });
});

it('31.265: com a chave "operacao_grupo_liberado_executa" desligada a confirmação diz que a persona continua esperando o Liberar', async () => {
  await abrirGrupoComLiberado('grp-1', { operacao_grupo_liberado_executa: false });
  await setValue(byRole('combobox', /^Grupo$/) as HTMLSelectElement, 'grp-2');
  const dialogo = await waitFor(() => byRole('dialog', /no grupo Liberados\?/));
  expect(text(dialogo)).toContain('está desligada');
  expect(text(dialogo)).toContain('continua esperando o Liberar');
  expect(text(dialogo)).not.toContain('sem ninguém ler o texto antes');
});

it('31.265: sair do grupo sem aprovação também confirma, dizendo que volta a esperar o Liberar; entre outros grupos segue direto', async () => {
  await abrirGrupoComLiberado('grp-2');
  const select = byRole('combobox', /^Grupo$/) as HTMLSelectElement;
  await setValue(select, '');
  const dialogo = await waitFor(() => byRole('dialog', /Tirar .* do grupo sem aprovação\?/));
  expect(text(dialogo)).toContain('volta a esperar o Liberar');
  expect(PATCH_DO_GRUPO()).toHaveLength(0);
  await click(byRole('button', /^Tirar do grupo$/, dialogo));
  await waitFor(() => expect(PATCH_DO_GRUPO()).toHaveLength(1));
  expect(PATCH_DO_GRUPO()[0]!.body).toEqual({ policy_group_id: null });
});

it('31.265: trocar para um grupo que não é o dispensado grava direto, sem confirmação', async () => {
  await abrirGrupoComLiberado(null);
  await setValue(byRole('combobox', /^Grupo$/) as HTMLSelectElement, 'grp-1');
  await waitFor(() => expect(PATCH_DO_GRUPO()).toHaveLength(1));
  expect(PATCH_DO_GRUPO()[0]!.body).toEqual({ policy_group_id: 'grp-1' });
  expect(allByRole('dialog', /grupo/i)).toHaveLength(0);
});

// Auditoria UX 27/09, P2.11: erro de carga das abas ia só para um toast e o esqueleto ficava para sempre.

it('aba Persona com a API caída mostra o erro com "Tentar de novo" em vez de carregar para sempre', async () => {
  backend.on('GET', /\/personas\/ig-1$/, () => apiError(503, 'unavailable', 'banco indisponível'));
  await abrir();
  await irParaGuia(/Persona/i);
  await waitFor(() => text().includes('Não foi possível carregar a persona'));
  expect(text()).toContain('banco indisponível');

  backend.on('GET', /\/personas\/ig-1$/, () => json({
    id: 'ig-1', name: 'Luciana — fotografia', summary: 'Fala de fotografia', persona_prompt: 'Responda com calma.',
    traits: {}, voice_gaps: [], profile_id: 'ig-1', profile_username: 'luciana.bastos73519',
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
  await irParaGuia(/Configurações/i);
  await waitFor(() => text().includes('Não foi possível carregar as políticas'));
  expect(text()).toContain('consulta estourou o tempo');

  backend.on('GET', /\/policy$/, () => json({ limits: {}, capabilities: {}, defaults: {}, loosened: [] }));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => text().includes('Grupo de acesso'));
  expect(text()).not.toContain('Não foi possível carregar as políticas');
});

it('aba Configurações com o catálogo de apps indisponível mostra o erro com "Tentar de novo", e ele busca o catálogo de novo', async () => {
  // 23.10 (revisão): a falha do catálogo era engolida, o app nunca se resolvia e o esqueleto ficava para sempre.
  montarConfigBackend([], { limits: {}, capabilities: {}, defaults: {}, loosened: [] });
  backend.on('GET', /app-catalog/, () => apiError(500, 'internal', 'registro indisponível'));
  await abrir();
  await irParaGuia(/Configurações/i);
  await waitFor(() => text().includes('Não foi possível carregar as políticas'));
  expect(text()).toContain('registro indisponível');
  expect(backend.callsTo('GET', /\/policy$/)).toHaveLength(0);

  backend.on('GET', /app-catalog/, () => json([
    { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
      session_provider: 'instagram', needs_profile: true, profile_anchor: true },
  ]));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => text().includes('Grupo de acesso'));
  expect(text()).not.toContain('Não foi possível carregar as políticas');
});

it('aba Configurações sem nenhum app com catálogo ainda carrega a política (do app âncora)', async () => {
  montarConfigBackend([], { capabilities: {}, defaults: {}, loosened: [] });
  backend.on('GET', /app-catalog/, () => json([]));
  await abrir();
  await irParaGuia(/Configurações/i);
  await waitFor(() => text().includes('Grupo de acesso'));
  // sem app com catálogo, a política é a do âncora (sem `?package=`) e não se pede catálogo de ações nenhum
  expect(backend.callsTo('GET', /\/policy$/)[0]?.query.get('package')).toBeNull();
  expect(backend.callsTo('GET', /capabilities/)).toHaveLength(0);
});

it('guia Contas e acesso com a API caída mostra o erro com "Tentar de novo", não "Carregando…" nem lista vazia', async () => {
  backend.on('GET', /\/accounts$/, () => apiError(503, 'unavailable', 'banco indisponível'));
  await abrir();
  await irParaGuia(/^Contas e acesso/i);
  await waitFor(() => text().includes('Não foi possível carregar as contas da persona'));
  expect(text()).not.toContain('Carregando…');

  backend.on('GET', /\/accounts$/, () => json([{
    id: 'acc-1', profile_id: 'ig-1', app_id: 'instagram', app_name: 'Instagram', handle: 'luciana.bastos73519',
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
  id: 'ig-1', name: 'Luciana Bastos', summary: 'Fotógrafa de retratos.', persona_prompt: '', username: 'luciana.bastos73519',
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
  await irParaGuia(/^Persona$/i);
  await waitFor(() => text().includes('Origem e casa'));
}

it('salvar uma seção da biografia manda SÓ aquela seção no PATCH', async () => {
  backend.on('PATCH', /\/personas\/ig-1$/, (c) => json({ ...PESSOA, biography: { ...PESSOA.biography, ...(c.body as { biography: object }).biography } }));
  await abrirPersona();
  // O mapa abre LENDO: o formulário da seção fica atrás de "Editar trabalho".
  expect(allByRole('button', /Salvar trabalho/i)).toHaveLength(0);
  await click(byRole('button', /Editar trabalho/i));
  // Nada mudou: salvar fica bloqueado com o motivo.
  expect(byRole('button', /Salvar trabalho/i).getAttribute('aria-disabled')).toBe('true');
  await setValue(byRole('textbox', /^Profissão/i) as HTMLInputElement, 'Fotógrafa de casamentos');
  await click(byRole('button', /Salvar trabalho/i));
  await waitFor(() => backend.callsTo('PATCH', /\/personas\/ig-1$/).length === 1);
  expect(backend.callsTo('PATCH', /\/personas\/ig-1$/)[0]?.body).toEqual({
    biography: { work: { profession: 'Fotógrafa de casamentos', employer: 'Estúdio Luz', education: ['Artes Visuais'] } },
  });
});

// ---------------------------------------------------------------- mapa da pessoa (28/09)
// A guia abre como um MAPA: retrato (quem é num relance), índice com o estado de cada seção, e seções que LEEM
// (etiquetas, linha do tempo, "não preenchido") — o formulário só aparece em "Editar {seção}".
it('o mapa abre lendo: retrato, índice e seções visuais, sem formulário aberto', async () => {
  await abrirPersona();
  const retrato = document.querySelector('[aria-label="Retrato da persona"]') as HTMLElement;
  expect(text(retrato)).toContain('Fotógrafa de retratos.');
  expect(text(retrato)).toContain('Curitiba, PR');
  expect(text(retrato)).toContain('Fotógrafa');
  const indice = byRole('navigation', /Mapa da persona/) as HTMLElement;
  for (const s of ['Identidade', 'Origem e casa', 'Trabalho', 'Vida', 'Gostos', 'Crenças', 'Voz']) {
    expect(text(indice)).toContain(s);
  }
  // Nenhum campo da biografia aberto: leitura com etiquetas e linha do tempo.
  expect(allByRole('textbox', /^Profissão|^Cidade onde mora|^Estado civil/)).toHaveLength(0);
  expect(text()).toContain('Artes Visuais');
  expect(text()).toContain('Mudou para Curitiba em 2015');
  expect(text()).toContain('não preenchido');
});

it('editar uma seção e salvar volta para a leitura; cancelar descarta', async () => {
  backend.on('PATCH', /\/personas\/ig-1$/, (c) => json({ ...PESSOA, biography: { ...PESSOA.biography, ...(c.body as { biography: object }).biography } }));
  await abrirPersona();
  await click(byRole('button', /Editar vida/i));
  await setValue(byRole('textbox', /^Estado civil/i) as HTMLInputElement, 'casada');
  await click(byRole('button', /Cancelar/i));
  expect(allByRole('textbox', /^Estado civil/i)).toHaveLength(0);
  expect(backend.callsTo('PATCH', /\/personas\/ig-1$/)).toHaveLength(0);
  await click(byRole('button', /Editar vida/i));
  await setValue(byRole('textbox', /^Estado civil/i) as HTMLInputElement, 'casada');
  await click(byRole('button', /Salvar vida/i));
  await waitFor(() => allByRole('textbox', /^Estado civil/i).length === 0);
  expect(text()).toContain('casada');
});

it('um fato do retrato leva à seção dele', async () => {
  await abrirPersona();
  const alvo = document.getElementById('mapa-trabalho') as HTMLElement;
  let rolou = false;
  alvo.scrollIntoView = () => { rolou = true; };
  await click(byRole('button', /Trabalho\s*Fotógrafa/));
  expect(rolou).toBe(true);
  expect(document.activeElement).toBe(alvo);
});

// ADR-048 inverteu este teste de propósito: antes, "Crenças" dizia "guardadas, não vão ao modelo" e eram dois campos
// de texto; agora vão ao modelo, com seção própria. O fixture `PESSOA` segue v1 (religião em TEXTO), como um backend
// anterior mandaria: a tela o lê como o resumo da crença.
it('a biografia marca o que vai ao modelo, e as Crenças vão também, com a regra de conduta', async () => {
  await abrirPersona();
  expect(text()).not.toContain('não vão ao modelo');
  // Os campos aparecem ao editar a seção (o mapa abre lendo).
  await click(byRole('button', /Editar identidade/i));
  await click(byRole('button', /Editar origem e casa/i));
  await click(byRole('button', /Editar trabalho/i));
  // A marca acompanha a lista do backend (`PERSONA_BIO_FIELDS`): desde 28/09, a biografia inteira vai ao modelo;
  // da identidade vai o nome, não o primeiro nome solto.
  const doCampo = (rotulo: RegExp) => (byRole('textbox', rotulo).parentElement?.textContent ?? '');
  expect(doCampo(/^Cidade onde mora/)).toContain('vai ao modelo');
  expect(doCampo(/^Profissão/)).toContain('vai ao modelo');
  expect(doCampo(/^Onde trabalha/)).toContain('vai ao modelo');
  expect(doCampo(/^Primeiro nome/)).not.toContain('vai ao modelo');
  // Crença não é mais um campo de texto solto: é a seção rica, marcada como indo ao modelo, sem regra de conteúdo (06/10).
  expect(allByRole('textbox', /^Religião|^Política/)).toHaveLength(0);
  const secao = byRole('group', /^\s*Religião/).closest('section') as HTMLElement;
  expect(text(secao)).toContain('Crenças');
  expect(text(secao)).toContain('vai ao modelo');
  expect(text(secao)).not.toContain('propaganda');
  // A v1 (texto) aparece como o resumo da religião; política nula não inventa nada.
  expect(text(byRole('group', /^\s*Religião/))).toContain('católica');
  expect(text(byRole('group', /^\s*Política/))).toContain('Sem política registrada');
  expect(allByRole('meter', /Orientação política/)).toHaveLength(0);
});

// ---------------------------------------------------------------- ADR-048: crenças ricas
const RELIGIAO = {
  affiliation: 'católica', practice: 'ocasional', practices: ['missa em datas especiais', 'festa junina'],
  importance: 'tradição de família', in_speech: '“se Deus quiser”', values: ['família', 'gratidão'],
  sensitive_topics: ['piada com fé alheia'], summary: 'católica de tradição',
};
const POLITICA = {
  orientation: 'centro_esquerda', engagement: 'baixo',
  issues: [{ topic: 'transporte público', stance: 'quer mais linhas' }, { topic: 'saúde pública', stance: null }],
  discussion_style: 'evita discutir com desconhecidos', sources: ['jornal local'], values: ['igualdade'],
  summary: 'vota e não briga por política',
};
const PESSOA_V2 = { ...PESSOA, biography: { ...PESSOA.biography, schema_version: 2,
                                            beliefs: { religion: RELIGIAO, politics: POLITICA } } };

async function abrirCrencas(pessoa: object = PESSOA_V2): Promise<void> {
  backend.on('GET', /\/personas\/ig-1$/, () => json(pessoa));
  await abrir();
  await irParaGuia(/^Persona$/i);
  await waitFor(() => text().includes('Crenças'));
}

it('crenças ricas: selo de prática e de engajamento, espectro acessível, fichas, pautas, fala e como discute', async () => {
  await abrirCrencas();
  const religiao = byRole('group', /^\s*Religião/);
  expect(text(religiao)).toContain('Pratica às vezes');
  for (const f of ['missa em datas especiais', 'festa junina', 'família', 'gratidão', 'piada com fé alheia',
    '“se Deus quiser”', 'tradição de família', 'Evita ou trata com cuidado', 'Como aparece na fala']) {
    expect(text(religiao)).toContain(f);
  }
  const politica = byRole('group', /^\s*Política/);
  expect(text(politica)).toContain('Engajamento: baixo');
  // A barra: `meter` com o NOME do ponto, não um número solto; o ponto marcado aparece em destaque.
  const barra = byRole('meter', /Orientação política/);
  expect(barra.getAttribute('aria-valuetext')).toBe('Centro-esquerda');
  expect(barra.getAttribute('aria-valuenow')).toBe('1');
  expect(barra.getAttribute('aria-valuemin')).toBe('0');
  expect(barra.getAttribute('aria-valuemax')).toBe('4');
  expect(politica.querySelector('[data-active]')?.textContent).toBe('Centro-esquerda');
  for (const f of ['transporte público', 'quer mais linhas', 'saúde pública', 'evita discutir com desconhecidos',
    'jornal local', 'igualdade', 'Como fala de política', 'Onde se informa']) {
    expect(text(politica)).toContain(f);
  }
  // O enum cru nunca aparece.
  expect(text()).not.toContain('centro_esquerda');
  expect(text()).not.toContain('ocasional');
});

it('apolítica e não declara ficam FORA da barra do espectro, ditas por extenso', async () => {
  await abrirCrencas({ ...PESSOA_V2, biography: { ...PESSOA_V2.biography,
                                                  beliefs: { politics: { orientation: 'apolitica', engagement: 'nenhum' } } } });
  const politica = byRole('group', /^\s*Política/);
  expect(allByRole('meter', /Orientação política/)).toHaveLength(0);
  expect(text(politica)).toContain('Apolítica');
  expect(text(politica)).toContain('fora do espectro');
  expect(text(byRole('group', /^\s*Religião/))).toContain('Sem religião registrada');
});

it('editar a política manda PATCH só de beliefs.politics, com selects, pautas e listas', async () => {
  // O servidor mescla por chave; o dublê imita a mescla em `beliefs` para a tela reler o que foi gravado.
  backend.on('PATCH', /\/personas\/ig-1$/, (c) => {
    const crencas = (c.body as { biography: { beliefs: object } }).biography.beliefs;
    return json({ ...PESSOA_V2, biography: { ...PESSOA_V2.biography,
                                             beliefs: { ...PESSOA_V2.biography.beliefs, ...crencas } } });
  });
  await abrirCrencas();
  await click(byRole('button', /Editar política/));
  // Nada mudou: salvar fica bloqueado, com o motivo.
  expect(byRole('button', /Salvar política/).getAttribute('aria-disabled')).toBe('true');
  await setValue(byRole('combobox', /^Orientação/) as HTMLSelectElement, 'centro');
  await setValue(byRole('combobox', /^Engajamento/) as HTMLSelectElement, 'alto');
  await setValue(byRole('textbox', /^Posição 2/) as HTMLInputElement, 'mais verba para os postos');
  await click(byRole('button', /Adicionar pauta/));
  await setValue(byRole('textbox', /^Tema 3/) as HTMLInputElement, 'ciclovias');
  await click(byRole('button', /Adicionar pauta/));                            // pauta sem tema: não vai
  await click(byRole('button', /Remover pauta 1/));
  await setValue(byRole('textbox', /^Onde se informa/) as HTMLTextAreaElement, 'jornal local\npodcast de notícias\n');
  await click(byRole('button', /Salvar política/));
  await waitFor(() => backend.callsTo('PATCH', /\/personas\/ig-1$/).length === 1);
  expect(backend.callsTo('PATCH', /\/personas\/ig-1$/)[0]?.body).toEqual({
    biography: { beliefs: { politics: {
      orientation: 'centro', engagement: 'alto',
      issues: [{ topic: 'saúde pública', stance: 'mais verba para os postos' }, { topic: 'ciclovias', stance: null }],
      discussion_style: 'evita discutir com desconhecidos', sources: ['jornal local', 'podcast de notícias'],
      values: ['igualdade'], summary: 'vota e não briga por política',
    } } },
  });
  // Gravou: o formulário fecha e a barra relê o gravado.
  await waitFor(() => allByRole('button', /Salvar política/).length === 0);
  expect(byRole('meter', /Orientação política/).getAttribute('aria-valuetext')).toBe('Centro');
});

it('editar a religião: prática por select, e esvaziar tudo manda null (apaga a crença)', async () => {
  backend.on('PATCH', /\/personas\/ig-1$/, () => json(PESSOA_V2));
  await abrirCrencas(PESSOA);                 // v1: religião em texto, lida como o resumo
  await click(byRole('button', /Editar religião/));
  expect((byRole('textbox', /^Resumo/) as HTMLTextAreaElement).value).toBe('católica');
  await setValue(byRole('combobox', /^Prática/) as HTMLSelectElement, 'devota');
  await setValue(byRole('textbox', /^O que pratica/) as HTMLTextAreaElement, 'missa todo domingo\n\nterço');
  await click(byRole('button', /Salvar religião/));
  await waitFor(() => backend.callsTo('PATCH', /\/personas\/ig-1$/).length === 1);
  expect(backend.callsTo('PATCH', /\/personas\/ig-1$/)[0]?.body).toEqual({
    biography: { beliefs: { religion: {
      affiliation: null, practice: 'devota', practices: ['missa todo domingo', 'terço'], importance: null,
      in_speech: null, values: [], sensitive_topics: [], summary: 'católica',
    } } },
  });
  // Relida (PESSOA_V2), abre de novo e esvazia tudo: o PATCH é `null`, que o servidor entende como apagar.
  await waitFor(() => allByRole('button', /Salvar religião/).length === 0);
  await click(byRole('button', /Editar religião/));
  for (const rotulo of [/^Afiliação/, /^O que pratica/, /^Peso na vida/, /^Como aparece na fala/, /^Valores/,
    /^Evita ou trata com cuidado/, /^Resumo/]) {
    await setValue(byRole('textbox', rotulo) as HTMLInputElement, '');
  }
  await setValue(byRole('combobox', /^Prática/) as HTMLSelectElement, '');
  await click(byRole('button', /Salvar religião/));
  await waitFor(() => backend.callsTo('PATCH', /\/personas\/ig-1$/).length === 2);
  expect(backend.callsTo('PATCH', /\/personas\/ig-1$/)[1]?.body).toEqual({ biography: { beliefs: { religion: null } } });
});

it('falha ao salvar uma crença mantém o formulário aberto com o que foi digitado', async () => {
  backend.on('PATCH', /\/personas\/ig-1$/, () => apiError(500, 'internal', 'falhou'));
  await abrirCrencas();
  await click(byRole('button', /Editar religião/));
  await setValue(byRole('textbox', /^Afiliação/) as HTMLInputElement, 'espírita');
  await click(byRole('button', /Salvar religião/));
  await waitFor(() => backend.callsTo('PATCH', /\/personas\/ig-1$/).length === 1);
  expect((byRole('textbox', /^Afiliação/) as HTMLInputElement).value).toBe('espírita');
});

it('a visão geral resume cada crença numa linha', async () => {
  backend.on('GET', /capacidades/, () => json({ profile_id: 'ig-1', flows: [], steps_driven_by: {}, recipe_share: null, interactions: {} }));
  backend.on('GET', /\/interactions$/, () => json([]));
  await abrir(perfil({ biography: { beliefs: { religion: RELIGIAO, politics: POLITICA } } as InstagramProfile['biography'] }));
  // As crenças ficam no bloco recolhido "Atributos de personalidade": abre-se para ler.
  await click(byRole('button', /Atributos de personalidade/));
  await waitFor(() => text().includes('católica · pratica às vezes'));
  expect(text()).toContain('centro-esquerda · engajamento baixo');
});

// ---------------------------------------------------------------- guia Aparelhos
// Desde o N:N (onda E2, ADR-043) o vínculo se faz AQUI: Configuração → Instâncias e contas só mostra, não vincula.
it('Aparelhos sem vínculo diz onde vincular: ali mesmo, com o formulário aberto', async () => {
  await act(async () => {
    root.render(<ProfileDetail profile={perfil({ instance_id: null, locality: null })} abaInicial="aparelhos"
                               onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('Esta persona não está vinculada a nenhum aparelho.'));
  expect(text()).toContain('Vincule um aparelho aqui (“Vincular a um aparelho”)');
  expect(byRole('combobox', 'Aparelho')).toBeTruthy();
  // O primeiro vínculo nasce principal: é o alvo padrão de conectar, verificar e sair.
  expect((byRole('checkbox', 'Tornar o aparelho principal desta persona') as HTMLInputElement).checked).toBe(true);
});

/** Marina em dois aparelhos: o principal (Instagram, conectado) e um do Notebook da LAN (Chrome, sem conta). */
const EM_DOIS = perfil({
  devices: [
    makeBinding('android-02', { is_primary: true, session: { ...makeSession('session_ready', 'android-02'), detail: '@luciana confirmado' } }),
    makeBinding('android-05', { app_id: 'chrome', state: 'stopped', worker_id: 'worker-lan-01', session: null }),
  ],
});

async function abrirAparelhos(p: InstagramProfile = EM_DOIS, onChanged: () => Promise<void> = async () => {}): Promise<HTMLElement> {
  const { useAppStore: loja } = await import('../../store/app');
  loja.setState({
    instances: { 'android-02': makeInstance(2, { state: 'online' }), 'android-03': makeInstance(3, { state: 'online' }),
                 'android-05': makeInstance(5, { state: 'stopped', worker_id: 'worker-lan-01' }) },
    instanceOrder: ['android-02', 'android-03', 'android-05'],
    apps: [{ ...APPS[0]!, id: 'instagram', name: 'Instagram' }, { ...APPS[0]!, id: 'chrome', name: 'Chrome' }],
  });
  backend.on('GET', /\/app-state$/, () => json([]));
  await act(async () => {
    root.render(<><ProfileDetail profile={p} abaInicial="aparelhos" onBack={() => {}} onChanged={onChanged} /><ConfirmHost /></>);
  });
  return waitFor(() => byRole('list', 'Vínculos desta persona'));
}

const cartao = (iid: string) => byRole('listitem', `Vínculo com ${iid}`);

it('Aparelhos lista os N vínculos com estado, servidor, app do vínculo, sessão ali e o selo Principal', async () => {
  const lista = await abrirAparelhos();
  expect(allByRole('listitem', /^Vínculo com/, lista)).toHaveLength(2);
  const principal = text(cartao('android-02'));
  expect(principal).toContain('Principal');
  expect(principal).toContain('Instagram');
  expect(principal).toContain('Conectado');
  expect(principal).toContain('@luciana confirmado');
  expect(allByRole('button', /Tornar principal/, cartao('android-02'))).toHaveLength(0);
  const outro = text(cartao('android-05'));
  expect(outro).not.toContain('Principal');
  expect(outro).toContain('Chrome');
  expect(outro).toContain('worker-lan-01');
  expect(outro).toContain('sem conta que sirva a este vínculo');
  expect(text()).toContain('2 aparelhos');
});

it('Tornar principal manda PUT …/primary; Desvincular confirma, manda ?app_id= e explica persona_in_use', async () => {
  let relidas = 0;
  backend.on('PUT', /\/personas\/ig-1\/devices\/android-05\/primary$/, () => json(EM_DOIS));
  backend.on('DELETE', /\/personas\/ig-1\/devices\/android-05$/, () => apiError(409, 'persona_in_use',
    'Esta persona tem execução em andamento em android-05. Espere terminar ou cancele antes de desvincular.'));
  await abrirAparelhos(EM_DOIS, async () => { relidas += 1; });
  await click(byRole('button', /Tornar principal/, cartao('android-05')));
  await waitFor(() => expect(backend.callsTo('PUT', /\/primary$/)).toHaveLength(1));
  await waitFor(() => expect(relidas).toBe(1));

  await click(byRole('button', /^Desvincular/, cartao('android-05')));
  const dialogo = await waitFor(() => byRole('dialog', /Desvincular Luciana Bastos de android-05/));
  expect(backend.callsTo('DELETE', /\/devices\//)).toHaveLength(0);          // nada sem confirmar
  await click(byRole('button', 'Desvincular', dialogo));
  await waitFor(() => expect(backend.callsTo('DELETE', /\/devices\/android-05$/)).toHaveLength(1));
  expect(backend.callsTo('DELETE', /\/devices\/android-05$/)[0]!.query.get('app_id')).toBe('chrome');
  await waitFor(() => expect(text(cartao('android-05'))).toContain('Persona em uso neste aparelho'));
  expect(text(cartao('android-05'))).toContain('Espere terminar, ou cancele a execução');
  expect(relidas).toBe(1);                                                    // recusado: nada a reler
});

it('Vincular a um aparelho: a recusa D2-a diz de quem é a conta do app naquele aparelho', async () => {
  backend.on('POST', /\/personas\/ig-1\/devices$/, () => apiError(409, 'conta_do_app_ja_no_aparelho',
    'a persona ig-7 já usa instagram em android-03; duas contas do mesmo app no mesmo aparelho não convivem.'));
  backend.on('GET', /\/instances\/android-03\/personas$/, () => json([
    { profile_id: 'ig-7', username: 'rafa.corre', display_name: 'Nelson Lima', name: 'Nelson Lima', status: 'active',
      app_id: 'instagram', is_primary: true, bound_at: null, session: null },
  ]));
  await abrirAparelhos();
  await click(byRole('button', /Vincular a um aparelho/));
  await setValue(byRole('combobox', 'Aparelho') as HTMLSelectElement, 'android-03');
  await setValue(byRole('combobox', 'App do vínculo') as HTMLSelectElement, 'instagram');
  await click(byRole('button', /^Vincular$/));
  await waitFor(() => expect(text()).toContain('O aparelho android-03 já tem a conta do Instagram de Nelson Lima'));
  expect(text()).toContain('um aparelho tem uma conta por app');
  expect(backend.callsTo('POST', /\/personas\/ig-1\/devices$/)[0]!.body)
    .toEqual({ instance_id: 'android-03', app_id: 'instagram', primary: false });
});

it('Contas e acesso: com dois aparelhos na conta, Conectar vai ao aparelho escolhido (?instance_id=)', async () => {
  const liberado = { allowed: true, reason: null };
  backend.on('POST', /\/accounts\/acc-1\/session\/connect$/,
             () => json({ accepted: true, profile_id: 'ig-1', instance_id: 'android-05', account_id: 'acc-1' }, 202));
  const emDoisInstagram = perfil({
    devices: [makeBinding('android-02', { is_primary: true }), makeBinding('android-05', { session: makeSession('auth_required', 'android-05') })],
  });
  await abrirContas([conta({
    session_actions: { phase: 'authenticated', detail: '', connect: { allowed: false, reason: 'Já conectado no principal.' },
                       verify: liberado, logout: liberado, inspect_app: liberado },
  })], async () => {}, emDoisInstagram);
  const escolha = byRole('combobox', /Aparelho para conectar, verificar e sair/) as HTMLSelectElement;
  expect([...escolha.options].map((o) => o.textContent)).toEqual(['android-02 (principal)', 'android-05']);
  // No principal, o portão do backend vale; noutro aparelho, quem decide é a rota.
  expect(byRole('button', /^Reconectar|^Conectar/).getAttribute('aria-disabled')).toBe('true');
  await setValue(escolha, 'android-05');
  expect(text()).toContain('fora do principal');
  await click(byRole('button', /^Conectar|^Reconectar/));
  await waitFor(() => expect(backend.callsTo('POST', /\/session\/connect$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /\/session\/connect$/)[0]!.query.get('instance_id')).toBe('android-05');
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

it('achado da varredura 70/71: o seletor de grupo conta as personas SEM as de teste', async () => {
  const politica = {
    limits: {}, capabilities: {}, defaults: {}, loosened: [], group_id: null, group_name: null,
    own: {}, group: {}, origin: {}, own_limits: {}, group_limits: {}, limits_origin: {},
  };
  montarConfigBackend([], politica);
  backend.on('GET', /policy-groups$/, () => json([
    { id: 'grp-2', name: 'Liberados', description: '', capabilities: {}, limits: {}, loosened: [], created_at: '', updated_at: '',
      members: [{ id: 'ig-9', username: 'x' }, { id: 'ig-8', username: 'y' }, { id: 'ig-t', username: null }] },
  ]));
  backend.on('GET', /^\/api\/personas$/, () => json([{ id: 'ig-9' }, { id: 'ig-8' }, { id: 'ig-t', teste: true }]));
  await act(async () => {
    root.render(<ProfileDetail profile={perfil()} onBack={() => {}} onChanged={async () => {}} />);
  });
  await waitFor(() => text().includes('@luciana.bastos73519'));
  await irParaGuia(/Configurações/i);
  await waitFor(() => expect(text()).toContain('Liberados'));
  const opcoes = [...(byRole('combobox', /^Grupo$/) as HTMLSelectElement).options].map((o) => o.textContent);
  expect(opcoes.find((o) => o?.startsWith('Liberados'))).toContain('2 personas');
});

it('31.346: o ciclo da conta do app âncora fica recolhido e só lê a API ao abrir; a conta de outro app não tem a seção', async () => {
  backend.on('GET', /\/instagram\/contas\/acc-1\/ciclo$/, () => json({
    account_id: 'acc-1', igfarm_account_id: null, origem: 'app', referencia: 'planejamento', criada_em: null,
    registrada_em: '2026-10-11T10:00:00Z', estado: 'ativa', retirada_em: null, minutos_ate_o_primeiro_contato: 12.4,
    ultimo_desfecho: 'parada', contatos: [{ iniciado_em: '2026-10-11T10:12:00Z', minutos_desde_a_criacao: 12.4, desfecho: 'parada', etapa: 'cadastro', detalhe: 'codigo_nao_chegou' }],
  }));
  await abrirContas([conta(), conta({ id: 'acc-2', app_id: 'outlook', app_name: 'Outlook', package: 'com.microsoft.office.outlook', handle: 'x' })]);
  expect(Array.from(container.querySelectorAll('summary')).filter((s) => s.textContent === 'Ciclo da conta')).toHaveLength(1);
  expect(backend.callsTo('GET', /\/ciclo$/)).toHaveLength(0);
  await click(Array.from(container.querySelectorAll('summary')).find((s) => s.textContent === 'Ciclo da conta') as HTMLElement);
  await waitFor(() => text().includes('Criada no app (cadastro guiado)'));
  expect(text()).toContain('Cadastro parado');
  expect(text()).toContain('12 min');
  expect(text()).toContain('desde o planejamento');
  expect(text()).toContain('codigo_nao_chegou');
  expect(backend.callsTo('GET', /\/ciclo$/)).toHaveLength(1);
});
