// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PersonaDTO } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { useToastStore } from '../../store/toasts';
import { initialDataState } from '../../store/reducer';
import { aplicarHash, useUiStore } from '../../store/ui';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, esperarElemento, flush, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { ProfilesPage } from './ProfilesPage';

/** A pessoa como `GET /personas` a devolve (v0.27): `username` nulo quando ainda não tem conta de cadastro. */
function pessoa(over: Partial<PersonaDTO> = {}): PersonaDTO {
  return {
    id: 'ig-1', name: 'Luciana Bastos', summary: null, username: 'luciana.bastos73519', display_name: 'Luciana Bastos',
    first_name: 'Luciana', last_name: 'Bastos', birth_date: null, email: null, persona_id: 'ig-1', persona_name: 'Luciana Bastos',
    status: 'active', instance_id: 'android-02',
    locality: { worker_id: null, worker_name: 'este servidor', worker_state: 'online', known: true,
                available: true, moved: false, physical_id: null, detail: null },
    offline_policy: 'wait',
    credential: { configured: true, login_identifier: 'luciana@exemplo.com', status: 'active', failed_attempts: 0,
                  blocked_until: null, updated_at: '2026-09-17T10:00:00Z', last_used_at: null },
    session: { status: 'unknown', instance_id: 'android-02', observed_username: null, verified_at: null,
               detail: 'Perfil recém-cadastrado; sessão ainda não verificada.', stale: false },
    last_verified_at: null, last_activity_at: null, accounts_count: 1,
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

/** Pessoa sem conta nenhuma: nasceu pelo cadastro de persona (ou pela 047), sem @ e sem aparelho. */
const SEM_CONTA = pessoa({
  id: 'ig-9', name: 'Elaine Prado', username: null, display_name: 'Elaine Prado', first_name: 'Elaine',
  last_name: 'Prado', persona_id: 'ig-9', persona_name: 'Elaine Prado', instance_id: null, locality: null,
  accounts_count: 0, age: 29, biography: { home: { city: 'Recife' }, work: { profession: 'Professora de biologia' } },
  credential: { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                updated_at: null, last_used_at: null },
  session: { status: 'unknown', instance_id: null, observed_username: null, verified_at: null, detail: null, stale: false },
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  try { window.localStorage.clear(); } catch { /* sem armazenamento */ }
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
  useUiStore.getState().navegar({ tela: 'personas', query: { foco: undefined } }, 'replace');
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
    root.render(<><ProfilesPage /><ConfirmHost /></>);
  });
  await waitFor(() => text().includes('Cada persona é uma pessoa'));
}

describe('remover persona', () => {
  it('desistir no diálogo NÃO apaga a persona', async () => {
    // `confirm` devolve um objeto `{confirmed, note}`, sempre verdadeiro. Testar o objeto em vez de `confirmed`
    // fazia "Voltar" apagar o perfil e a credencial do mesmo jeito — e nenhum teste renderizava o diálogo para ver.
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));
    backend.on('DELETE', /^\/api\/personas\//, () => json(null, 204));
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));

    // Remover mora no menu "⋯" (tarefa UX 05), longe do "Abrir".
    await click(byRole('button', /Mais ações de Luciana Bastos/i));
    await click(byRole('button', /Remover persona/i));
    await waitFor(() => text().includes('as senhas guardadas no cofre vão junto'));
    await click(byRole('button', /^Voltar$/i, byRole('dialog', /Remover/)));
    await waitFor(() => !text().includes('as senhas guardadas no cofre vão junto'));
    expect(backend.callsTo('DELETE', /personas/)).toHaveLength(0);
    expect(text()).toContain('luciana.bastos73519');
  });

  it('confirmar no diálogo apaga pela rota da persona (que recusa quem está vinculado ou em execução)', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));
    backend.on('DELETE', /^\/api\/personas\//, () => json(null, 204));
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));

    // Remover mora no menu "⋯" (tarefa UX 05), longe do "Abrir".
    await click(byRole('button', /Mais ações de Luciana Bastos/i));
    await click(byRole('button', /Remover persona/i));
    await waitFor(() => text().includes('as senhas guardadas no cofre vão junto'));
    await click(byRole('button', /^Remover$/i, byRole('dialog', /Remover/)));
    await waitFor(() => backend.callsTo('DELETE', /^\/api\/personas\/ig-1$/).length === 1);
  });
});

describe('personas', () => {
  it('lista as PESSOAS, inclusive quem não tem conta: nome sem @, idade, cidade e profissão', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa(), SEM_CONTA]));
    await render();
    await waitFor(() => text().includes('Elaine Prado'));
    expect(text()).toContain('sem conta de cadastro');
    expect(text()).toContain('29 anos · Recife · Professora de biologia');
    expect(text()).not.toContain('@null');
    expect(text()).toContain('@luciana.bastos73519');
    // A lista vem de `GET /personas` (todas as pessoas), não de `GET /instagram/profiles` (só quem tem @).
    expect(backend.callsTo('GET', /^\/api\/instagram\/profiles$/)).toHaveLength(0);
  });

  it('foto só com `has_avatar`: sem foto são as iniciais e NENHUMA <img> (nem o 404 de /avatar); com foto, a imagem (29.26)', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa({ has_avatar: true }), SEM_CONTA]));
    await render();
    await waitFor(() => text().includes('Elaine Prado'));
    const imgs = [...container.querySelectorAll('img')].map((i) => i.getAttribute('src') ?? '');
    // Só a Luciana (com foto) pede a imagem; a Elaine aparece com as iniciais "EP", sem <img> e sem requisição.
    expect(imgs.length).toBeGreaterThan(0);
    expect(imgs.every((u) => u.endsWith('/instagram/profiles/ig-1/avatar'))).toBe(true);
    expect(imgs.some((u) => u.includes('ig-9'))).toBe(false);
    expect(text()).toContain('EP');
  });

  it('o cartão é a pessoa: contas, aparelho e situação — sem senha, sessão nem Conectar', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa({ accounts_count: 2 })]));
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));
    expect(text()).toContain('Contas');
    expect(text()).toContain('2');
    expect(text()).toContain('android-02');
    expect(text()).toContain('Ativa');
    expect(text()).not.toContain('••••••••••••');
    expect(() => byRole('button', /Conectar/i)).toThrow();
    expect(() => byRole('button', /Verificar conta/i)).toThrow();
  });

  it('N:N: o cartão mostra TODOS os aparelhos da persona, o principal marcado', async () => {
    const binding = (instance_id: string, is_primary: boolean) => ({
      instance_id, app_id: 'instagram', is_primary, state: 'online', worker_id: null, bound_at: null, session: null,
    });
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa({
      instance_id: 'android-02', devices: [binding('android-02', true), binding('android-05', false)],
    })]));
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));
    expect(text()).toContain('Aparelhos');
    expect(text()).toContain('android-02 (principal) · android-05');
  });

  it('API caída mostra o erro com "Tentar de novo", não "Nenhuma persona cadastrada" (P1.3)', async () => {
    backend.on('GET', /^\/api\/personas$/, () => apiError(503, 'unavailable', 'banco indisponível'));
    await act(async () => {
      root.render(<ProfilesPage />);
    });
    await waitFor(() => text().includes('Não foi possível carregar as personas'));
    expect(text()).toContain('banco indisponível');
    expect(text()).not.toContain('Nenhuma persona cadastrada');

    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));
    await click(byRole('button', /Tentar de novo/));
    await waitFor(() => text().includes('luciana.bastos73519'));
    expect(text()).not.toContain('Não foi possível carregar as personas');
  });

  it('avisa quando não há persona e oferece os dois caminhos de cadastro', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    expect(text()).toContain('Nenhuma persona cadastrada');
    expect(byRole('button', /Nova persona a partir de uma descrição/i)).toBeTruthy();
    expect(byRole('button', /Nova persona manual/i)).toBeTruthy();
  });

  it('pedido de outra tela (openPersona) vira o link da persona e abre a guia pedida', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa(), SEM_CONTA]));
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    useUiStore.getState().openPersona('ig-9', 'contas');
    await act(async () => {
      root.render(<ProfilesPage />);
    });
    await waitFor(() => text().includes('Elaine Prado') && text().includes('Personas'));
    await waitFor(() => byRole('tab', /Contas e acesso/i).getAttribute('aria-selected') === 'true');
    // O pedido chega pelo id; a tela o troca pelo nome legível (mesmo lugar, sem empilhar).
    expect(window.location.hash).toBe('#/personas/elaine-prado/contas');
    await waitFor(() => text().includes('ainda não tem @ de cadastro'));             // a guia lê as contas pelo fetch
  });

  it('guia pedida que não existe cai na Visão geral', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    useUiStore.getState().openPersona('ig-1', 'autenticacao');
    await act(async () => {
      root.render(<ProfilesPage />);
    });
    await waitFor(() => byRole('button', /^Visão geral/).getAttribute('aria-current') === 'page');
    await waitFor(() => window.location.hash === '#/personas/luciana-bastos');
  });
});

// ---------------------------------------------------------------- rotas da persona: nome legível e links antigos
// O link carrega a persona (slug legível ou o id antigo) e a GUIA; a seção da tela sai da guia.
describe('rotas da persona', () => {
  const LUCAS_A = pessoa({ id: 'ig-A1B2C3D4E5F6g7h8', name: 'Tadeu Quintela', display_name: 'Tadeu Quintela', username: 'tadeu.a' });
  const LUCAS_B = pessoa({ id: 'ig-Z9Y8X7W6V5U4t3s2', name: 'Tadeu Quintela', display_name: 'Tadeu Quintela', username: 'tadeu.b' });

  function apis(lista: PersonaDTO[]) {
    backend.on('GET', /^\/api\/personas$/, () => json(lista));
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    backend.on('GET', /\/memory/, () => json([]));
  }

  async function montar(): Promise<void> {
    await act(async () => {
      root.render(<ProfilesPage />);
    });
  }

  it('link antigo com id + guia (…/ig-1/memoria) abre a Memória DENTRO de Perfil e troca o id pelo nome, sem empilhar', async () => {
    apis([pessoa(), SEM_CONTA]);
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['ig-1', 'memoria'] }); });
    const historico = window.history.length;
    await montar();
    await waitFor(() => byRole('tab', /Memória/).getAttribute('aria-selected') === 'true');
    expect(byRole('button', /^Perfil/).getAttribute('aria-current')).toBe('page');
    await waitFor(() => window.location.hash === '#/personas/luciana-bastos/memoria');
    expect(window.history.length).toBe(historico);                       // replace: nenhuma entrada nova
    expect(useUiStore.getState().rota.segmentos).toEqual(['luciana-bastos', 'memoria']);
  });

  it('cada guia antiga continua abrindo a guia certa na seção certa', async () => {
    apis([pessoa()]);
    const casos: [string, RegExp, RegExp][] = [
      ['persona', /^Perfil/, /^Persona/], ['imagens', /^Perfil/, /^Imagens/], ['memoria', /^Perfil/, /^Memória/],
      ['contas', /^Contas e aparelhos/, /^Contas e acesso/], ['aparelhos', /^Contas e aparelhos/, /^Aparelhos/],
      ['interacoes', /^Atividade/, /^Interações/], ['execucoes', /^Atividade/, /^Execuções/], ['aprovacoes', /^Atividade/, /^Aprovações/],
      ['habilidades', /^Avançado/, /^Habilidades/], ['config', /^Avançado/, /^Configurações/],
    ];
    for (const [guia, secao, aba] of casos) {
      await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['ig-1', guia] }, 'replace'); });
      await act(async () => { root.render(<ProfilesPage key={guia} />); });
      await waitFor(() => byRole('tab', aba).getAttribute('aria-selected') === 'true');
      expect(byRole('button', secao, byRole('navigation', /Seções da persona/)).getAttribute('aria-current')).toBe('page');
      await waitFor(() => window.location.hash === `#/personas/luciana-bastos/${guia}`);
      await act(async () => root.unmount());
      root = createRoot(container);
    }
  });

  it('link com o nome legível (…/elaine-prado/contas) abre a persona certa direto', async () => {
    apis([pessoa(), SEM_CONTA]);
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['elaine-prado', 'contas'] }); });
    await montar();
    await waitFor(() => byRole('tab', /Contas e acesso/).getAttribute('aria-selected') === 'true');
    expect(container.querySelector('h1')?.textContent).toBe('Elaine Prado');
    expect(window.location.hash).toBe('#/personas/elaine-prado/contas');
  });

  it('o alias #/perfis/<id>/<guia> segue valendo e termina no link com o nome', async () => {
    apis([pessoa()]);
    window.history.replaceState(window.history.state, '', '#/perfis/ig-1/aparelhos');
    await act(async () => { aplicarHash(true); });
    await montar();
    await waitFor(() => byRole('tab', /Aparelhos/).getAttribute('aria-selected') === 'true');
    await waitFor(() => window.location.hash === '#/personas/luciana-bastos/aparelhos');
  });

  it('o aparelho em Foco (?foco=) atravessa a troca do id pelo nome', async () => {
    apis([pessoa()]);
    await act(async () => {
      useUiStore.getState().navegar({ tela: 'personas', segmentos: ['ig-1'], query: { foco: 'android-02' } });
    });
    await montar();
    await waitFor(() => window.location.hash === '#/personas/luciana-bastos?foco=android-02');
    expect(useUiStore.getState().focusInstanceId).toBe('android-02');
  });

  it('guia que não existe cai na Visão geral e o link vira só o nome da persona', async () => {
    apis([pessoa()]);
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['luciana-bastos', 'visao'] }); });
    await montar();
    await waitFor(() => window.location.hash === '#/personas/luciana-bastos');
    expect(byRole('button', /^Visão geral/).getAttribute('aria-current')).toBe('page');
  });

  it('com a lista ainda carregando NÃO pisca "Persona não encontrada"; só avisa depois, se ninguém bate', async () => {
    let liberar: () => void = () => {};
    const segura = new Promise<void>((resolve) => { liberar = resolve; });
    backend.on('GET', /^\/api\/personas$/, async () => { await segura; return json([pessoa()]); });
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['ninguem-assim'] }); });
    await montar();
    expect(text()).not.toContain('Persona não encontrada');
    await act(async () => { liberar(); });
    await waitFor(() => text().includes('Persona não encontrada'));
    expect(window.location.hash).toBe('#/personas/ninguem-assim');          // não reescreve o que não resolveu
  });

  it('com a lista carregando, o link por nome também espera e depois abre a persona (sem aviso no meio)', async () => {
    let liberar: () => void = () => {};
    const segura = new Promise<void>((resolve) => { liberar = resolve; });
    backend.on('GET', /^\/api\/personas$/, async () => { await segura; return json([pessoa()]); });
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['luciana-bastos'] }); });
    await montar();
    expect(text()).not.toContain('Persona não encontrada');
    await act(async () => { liberar(); });
    await waitFor(() => container.querySelector('h1')?.textContent === 'Luciana Bastos');
    expect(text()).not.toContain('Persona não encontrada');
  });

  it('homônimos: cada um tem o seu link (nome + sufixo do id) e o nome puro não adivinha', async () => {
    apis([LUCAS_A, LUCAS_B, pessoa()]);
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: [LUCAS_B.id] }); });
    await montar();
    await waitFor(() => container.querySelector('h1')?.textContent === 'Tadeu Quintela');
    await waitFor(() => /^#\/personas\/tadeu-quintela-[a-z0-9]{4}$/.test(window.location.hash));
    expect(window.location.hash).toBe('#/personas/tadeu-quintela-t3s2');
    expect(text()).toContain('@tadeu.b');
    expect(text()).not.toContain('@tadeu.a');
    // O link do outro homônimo abre o outro.
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['tadeu-quintela-g7h8'] }); });
    await waitFor(() => text().includes('@tadeu.a'));
    expect(text()).not.toContain('@tadeu.b');
    // O nome sem sufixo é ambíguo: nada é aberto.
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['tadeu-quintela'] }); });
    await waitFor(() => text().includes('Mais de uma persona com esse nome'));
    expect(text()).not.toContain('Persona não encontrada');
  });

  it('renomear a persona com a tela aberta não vira "não encontrada": o link acompanha o nome novo', async () => {
    apis([pessoa()]);
    await act(async () => { useUiStore.getState().navegar({ tela: 'personas', segmentos: ['luciana-bastos', 'persona'] }); });
    await montar();
    await waitFor(() => container.querySelector('h1')?.textContent === 'Luciana Bastos');
    apis([pessoa({ name: 'Luciana Souza', display_name: 'Luciana Souza' })]);
    await act(async () => { useAppStore.setState({ hydrateCount: 2 }); });          // a lista se relê
    await waitFor(() => container.querySelector('h1')?.textContent === 'Luciana Souza');
    await waitFor(() => window.location.hash === '#/personas/luciana-souza/persona');
    expect(text()).not.toContain('Persona não encontrada');
  });

  it('abrir pela lista já usa o nome legível no link', async () => {
    apis([pessoa(), SEM_CONTA]);
    await montar();
    await waitFor(() => text().includes('Elaine Prado'));
    await click(byRole('button', /Abrir Elaine Prado/));
    await waitFor(() => container.querySelector('h1')?.textContent === 'Elaine Prado');
    expect(window.location.hash).toBe('#/personas/elaine-prado');
  });
});

// ---------------------------------------------------------------- cadastro em dois caminhos (evolução 2, E1)
describe('nova persona', () => {
  const RASCUNHO = {
    name: 'Elaine Prado', summary: 'Professora que fala de plantas.', birth_date: '1996-03-02', gender: 'feminino',
    persona_prompt: 'Escreva com calma.', traits: { tone: 'acolhedor', interests: ['plantas', 'trilhas'] },
    biography: { home: { city: 'Recife' }, work: { profession: 'Professora' }, tastes: { hobbies: ['trilhas'] } },
    visual: { appearance: 'cabelo curto' },
    generation: { source: 'ai', provider: 'anthropic', model: 'claude-sonnet-x', prompt: 'professora em Recife' },
  };
  const IA_PAGA = {
    provider: 'anthropic', model: 'claude-sonnet-x', configured: true, simulated: false, sends_data_externally: true,
    notice: '', effort: null,
    roles: [{ role: 'social', provider: 'anthropic', kind: 'anthropic', model: 'claude-sonnet-x', endpoint: 'api.anthropic.com',
              sends_data_externally: true, configured: true, priced: true, vision: false, tools: false, refusal_fallback: false }],
    image: { provider: 'simulated', model: 'simulado-v1', quality: 'low', configured: true, simulated: true,
             sends_data_externally: false, per_persona: 1, on_create: true, price_per_image_usd: 0 },
  };

  it('por prompt: avisa que é PAGO, gera o rascunho, deixa editar e cria com o rascunho editado', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => json(IA_PAGA));
    backend.on('POST', /^\/api\/personas\/generate$/, () => json(RASCUNHO));
    backend.on('POST', /^\/api\/personas$/, (c) => json(pessoa({ ...(c.body as object), id: 'ig-9', username: null }), 201));
    await render();
    await click(byRole('button', /Nova persona a partir de uma descrição/i));
    await waitFor(() => text().includes('É uma chamada paga de IA'));
    expect(text()).toContain('claude-sonnet-x');
    expect(text()).toContain('≈ US$ 0,02–0,03 por persona');
    expect(text()).toContain('A primeira foto é gerada em segundo plano');

    const gerar = byRole('button', /Gerar rascunho/i);
    expect(gerar.getAttribute('aria-disabled')).toBe('true');           // pedido vazio
    await setValue(byRole('textbox', /^Pedido/i) as HTMLTextAreaElement, 'professora em Recife');
    await setValue(byRole('textbox', /^Cidade/i) as HTMLInputElement, 'Recife');
    await setValue(byRole('textbox', /Faixa de idade/i) as HTMLInputElement, '28-32');
    await click(byRole('button', /Gerar rascunho/i));
    await waitFor(() => backend.callsTo('POST', /^\/api\/personas\/generate$/).length === 1);
    expect(backend.callsTo('POST', /generate$/)[0]?.body)
      .toEqual({ prompt: 'professora em Recife', constraints: { city: 'Recife', age: '28-32' } });
    // Nada gravado ainda: só o rascunho na tela.
    expect(backend.callsTo('POST', /^\/api\/personas$/)).toHaveLength(0);

    await waitFor(() => text().includes('ainda não gravado'));
    await setValue(byRole('textbox', /^Profissão/i) as HTMLInputElement, 'Professora de biologia');
    await click(byRole('button', /Criar persona/i));
    await waitFor(() => backend.callsTo('POST', /^\/api\/personas$/).length === 1);
    const criada = backend.callsTo('POST', /^\/api\/personas$/)[0]?.body as typeof RASCUNHO;
    expect(criada.name).toBe('Elaine Prado');
    expect(criada.biography.work.profession).toBe('Professora de biologia');       // o que a pessoa editou
    expect(criada.biography.tastes).toEqual({ hobbies: ['trilhas'] });           // o resto do rascunho vai como veio
    expect(criada.traits).toEqual(RASCUNHO.traits);
    expect(criada.generation).toEqual(RASCUNHO.generation);                     // proveniência da IA preservada
  });

  it('por prompt: as crenças do rascunho aparecem numa linha cada e vão inteiras na criação (ADR-048)', async () => {
    const crencas = {
      religion: { affiliation: 'espírita', practice: 'ocasional', practices: ['palestra no centro'], summary: 'frequenta às vezes' },
      politics: { orientation: 'nao_declara', engagement: 'baixo', issues: [{ topic: 'bairro', stance: 'praça cuidada' }] },
    };
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => json(IA_PAGA));
    backend.on('POST', /^\/api\/personas\/generate$/,
      () => json({ ...RASCUNHO, biography: { ...RASCUNHO.biography, beliefs: crencas } }));
    backend.on('POST', /^\/api\/personas$/, (c) => json(pessoa({ ...(c.body as object), id: 'ig-9', username: null }), 201));
    await render();
    await click(byRole('button', /Nova persona a partir de uma descrição/i));
    await waitFor(() => text().includes('É uma chamada paga de IA'));
    await setValue(byRole('textbox', /^Pedido/i) as HTMLTextAreaElement, 'professora em Recife');
    await click(byRole('button', /Gerar rascunho/i));
    await waitFor(() => text().includes('ainda não gravado'));
    expect(text()).toContain('Religião: espírita · pratica às vezes');
    expect(text()).toContain('Política: não declara · engajamento baixo');
    await click(byRole('button', /Criar persona/i));
    await waitFor(() => backend.callsTo('POST', /^\/api\/personas$/).length === 1);
    const criada = backend.callsTo('POST', /^\/api\/personas$/)[0]?.body as { biography: { beliefs: unknown } };
    expect(criada.biography.beliefs).toEqual(crencas);
  });

  it('por prompt: rascunho recusado (422) mostra a mensagem do servidor com a lista', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => json(IA_PAGA));
    backend.on('POST', /^\/api\/personas\/generate$/,
               () => apiError(422, 'persona_draft_invalid', 'Rascunho recusado: idade abaixo de 18; voz incompleta (slang).'));
    await render();
    await click(byRole('button', /Nova persona a partir de uma descrição/i));
    await setValue(byRole('textbox', /^Pedido/i) as HTMLTextAreaElement, 'uma adolescente');
    await click(byRole('button', /Gerar rascunho/i));
    await waitFor(() => text().includes('O rascunho veio fora das regras'));
    expect(text()).toContain('idade abaixo de 18; voz incompleta (slang)');
    expect(backend.callsTo('POST', /^\/api\/personas$/)).toHaveLength(0);
  });

  it('por prompt com provedor simulado diz que é simulado e sem custo', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => json({ ...IA_PAGA, simulated: true,
      roles: [{ ...IA_PAGA.roles[0], provider: 'simulated', kind: 'simulated', model: 'simulado' }] }));
    await render();
    await click(byRole('button', /Nova persona a partir de uma descrição/i));
    await waitFor(() => text().includes('Provedor simulado: sem custo'));
    expect(text()).not.toContain('É uma chamada paga de IA');
  });

  it('manual: nome obrigatório; cria só com o que foi dado, sem senha nem aparelho', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => apiError(503, 'unavailable', 'fora'));
    backend.on('POST', /^\/api\/personas$/, (c) => json(pessoa({ ...(c.body as object), id: 'ig-9', username: null }), 201));
    await render();
    await click(byRole('button', /Nova persona manual/i));
    await waitFor(() => text().includes('A persona nasce sem conta e sem aparelho'));
    await click(byRole('button', /Criar persona/i));
    await waitFor(() => text().includes('Dê um nome à persona.'));
    expect(backend.callsTo('POST', /^\/api\/personas$/)).toHaveLength(0);

    await setValue(byRole('textbox', /^Nome/i) as HTMLInputElement, 'Elaine Prado');
    await setValue(byRole('textbox', /^Nascimento/i) as HTMLInputElement, '1996-03-02');
    await click(byRole('button', /Criar persona/i));
    await waitFor(() => backend.callsTo('POST', /^\/api\/personas$/).length === 1);
    expect(backend.callsTo('POST', /^\/api\/personas$/)[0]?.body)
      .toEqual({ name: 'Elaine Prado', birth_date: '1996-03-02', gender: null, summary: null });
    expect(container.ownerDocument.querySelector('input[type="password"]')).toBeNull();
    // Até a tela abrir a criada: com a resposta em voo, ela navegaria já dentro do teste seguinte.
    await waitFor(() => /^#\/personas\/./.test(window.location.hash));
  });
});

// ---------------------------------------------------------------- fila "Aguardando intervenção" (achado #106)
describe('fila de intervenção', () => {
  it('lista perfil, aparelho e motivo de quem está preso, e ignora quem não está', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([
      pessoa({
        id: 'ig-1', username: 'luciana.bastos73519',
        session: { status: 'auth_challenge', instance_id: 'android-02', observed_username: null,
                   verified_at: '2026-09-23T09:00:00Z', detail: 'O Instagram exige confirmação adicional.',
                   stale: false },
      }),
      pessoa({ id: 'ig-2', name: 'Tadeu Quintela', username: 'tadeu.quintela4821', instance_id: 'android-01' }),
      SEM_CONTA,
    ]));
    await render();
    await waitFor(() => text().includes('Aguardando intervenção'));

    expect(text()).toContain('luciana.bastos73519');
    expect(text()).toContain('android-02');
    expect(text()).toContain('O Instagram exige confirmação adicional.');
    // "tadeu.quintela4821" está `unknown`: NÃO aparece na fila, só no cartão (uma vez).
    expect(text().match(/tadeu\.quintela4821/g)?.length ?? 0).toBe(1);
  });

  it('sem ninguém preso, a fila não aparece', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));   // status padrão: unknown
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));
    expect(text()).not.toContain('Aguardando intervenção');
  });

  it('assumir controle na fila pede o lease e abre o painel de foco do aparelho certo', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa({
      session: { status: 'wrong_account', instance_id: 'android-02', observed_username: 'outra.conta',
                 verified_at: '2026-09-23T09:00:00Z', detail: 'a conta aberta é @outra.conta', stale: false },
    })]));
    backend.on('POST', /\/instances\/android-02\/control\/take$/, () => json({ status: 'granted', lease_id: 'lease-1' }));
    await render();
    await waitFor(() => text().includes('Aguardando intervenção'));

    await click(byRole('button', /Assumir controle/i));
    await waitFor(() => backend.callsTo('POST', /control\/take$/).length === 1);
    // Abre o painel de Foco do aparelho certo — é ele quem mostra a tela para a pessoa resolver, local ou
    // remoto (o painel em si é testado em `FocusPanel.test.tsx`; aqui importa que a fila manda para lá).
    await waitFor(() => useUiStore.getState().focusInstanceId === 'android-02');
  });

  it('29.96: a sessão parada no teto entra na fila com o rótulo próprio e o mesmo "Assumir controle"', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa({
      instance_id: 'android-01',
      session: { status: 'unknown', instance_id: 'android-01', observed_username: null, verified_at: null,
                 detail: 'Instagram não voltou ao estado conhecido', stale: false, unknown_at_cap: true },
    })]));
    backend.on('POST', /\/instances\/android-01\/control\/take$/, () => json({ status: 'granted', lease_id: 'lease-2' }));
    await render();
    await waitFor(() => text().includes('Aguardando intervenção'));
    expect(text()).toContain('Tela não reconhecida');
    expect(text()).toContain('tela que a automação não reconheceu');

    await click(byRole('button', /Assumir controle/i));
    await waitFor(() => useUiStore.getState().focusInstanceId === 'android-01');
  });

  it('29.100: a fila ordena pela hora em que o estado começou, não pela última verificação', async () => {
    // A parada de agora tem a verificação mais velha (dias antes de parar); antes ela ia para o topo como "a que espera
    // há mais tempo".
    backend.on('GET', /^\/api\/personas$/, () => json([
      pessoa({ id: 'ig-1', username: 'parou.agora', instance_id: 'android-01',
               session: { status: 'unknown', instance_id: 'android-01', observed_username: null,
                          verified_at: '2026-09-20T10:00:00Z', detail: null, stale: false, unknown_at_cap: true,
                          status_since: '2026-10-05T06:30:00Z' } }),
      pessoa({ id: 'ig-2', username: 'desafio.antigo', instance_id: 'android-02',
               session: { status: 'auth_challenge', instance_id: 'android-02', observed_username: null,
                          verified_at: '2026-10-01T09:00:00Z', detail: null, stale: false,
                          status_since: '2026-10-01T09:00:00Z' } }),
    ]));
    await render();
    await waitFor(() => text().includes('Aguardando intervenção'));
    const fila = text().slice(text().indexOf('Aguardando intervenção'));
    expect(fila.indexOf('desafio.antigo')).toBeLessThan(fila.indexOf('parou.agora'));
  });
});

// ---------------------------------------------------------------- localidade (E9, item 4.4)
describe('onde a persona vive', () => {
  it('o cartão diz em que servidor os dados vivem, e avisa quando ele mudou', async () => {
    // Antes o cartão mostrava só `instance_id`: um perfil cujo servidor mudou (ou caiu) aparecia igual aos
    // demais, e a pessoa só descobria quando a tarefa falhava no aparelho errado.
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa({
      locality: { worker_id: 'worker-lan-02', worker_name: 'Notebook da sala', worker_state: 'offline',
                  known: true, available: false, moved: true, physical_id: null,
                  detail: 'os dados deste perfil vivem em Notebook da sala, mas android-02 aponta hoje para este servidor' },
    })]));
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));
    expect(text()).toContain('Servidor');
    expect(text()).toContain('Notebook da sala');
    expect(text()).toContain('mudou de servidor');
    expect(text()).toContain('indisponível');
    expect(text()).toContain('aponta hoje para este servidor');
  });
});

describe('grupos de acesso', () => {
  // A política que o editor mostra marcada numa ação: o pedido registrado no backend falso ainda não é a resposta
  // aplicada, e o "começar a partir de" só vale quando a escolha do perfil aparece aqui.
  function marcada(politica: RegExp, acao: RegExp): boolean {
    const radio = byRole('radio', politica, byRole('radiogroup', acao));
    return radio.getAttribute('aria-checked') === 'true' || (radio as HTMLInputElement).checked === true;
  }

  function rotasBase(grupos: unknown[]) {
    backend.on('GET', /^\/api\/personas$/, () => json([
      pessoa({ id: 'ig-1', name: 'Ravenna Sampaio', username: 'rene.sampaio381524', policy_group_id: 'grp-1',
               policy_group_name: 'Cautelosos' }),
      pessoa({ id: 'ig-2', name: 'Quillon Teixeira', username: 'valdir.teixeira6352' }),
      SEM_CONTA,
    ]));
    backend.on('GET', /\/instagram\/policy-groups$/, () => json(grupos));
    backend.on('GET', /\/instagram\/policy-defaults$/, () => json({ limits: { likes_per_hour: 30 } }));
    backend.on('GET', /app-catalog/, () => json([
      { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
        session_provider: 'instagram', needs_profile: true },
    ]));
    backend.on('GET', /capabilities/, () => json([
      { key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
        default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] },
      { key: 'FOLLOW', title: 'Seguir', side_effect: true, risk: 'high',
        default_policy: 'approval_required', limit_bucket: 'follows', needs_draft: false, bindings: [] },
    ]));
  }

  it('mostra os grupos com o resumo e quem está dentro; o cartão da persona diz o grupo', async () => {
    rotasBase([{ id: 'grp-1', name: 'Cautelosos', description: 'Contas novas', capabilities: { LIKE_POST: 'approval_required' },
                 limits: { likes_per_hour: 5 }, loosened: [], members: [{ id: 'ig-1', username: 'rene.sampaio381524' }],
                 created_at: '', updated_at: '' }]);
    await render();
    await waitFor(() => expect(text()).toContain('Grupos de acesso'));
    await waitFor(() => expect(text()).toContain('0 sozinho · 2 com aprovação · 0 só manual'));
    expect(text()).toContain('2 mudança(s) em relação ao padrão');
    expect(text(document.querySelector('[aria-label="Personas no grupo Cautelosos"]') as HTMLElement)).toContain('1 persona');
    expect(text()).toContain('nenhum — padrão do catálogo');      // o Quillon não tem grupo
  });

  it('29.25 (B3): membro cuja conta saiu aparece pelo nome e "sem conta", nunca como um "@" sozinho', async () => {
    rotasBase([{ id: 'grp-1', name: 'Cautelosos', description: '', capabilities: {}, limits: {}, loosened: [],
                 members: [{ id: 'ig-1', username: 'rene.sampaio381524', name: 'Ravenna Sampaio' },
                           { id: 'ig-7', username: '', name: 'Sueli Barreto' },
                           { id: 'ig-8', username: null, name: null }],
                 created_at: '', updated_at: '' }]);
    await render();
    const chips = await esperarElemento('[aria-label="Personas no grupo Cautelosos"]');
    const textos = Array.from(chips.querySelectorAll('span[data-sem-conta], span[class*="memberChip"]')).map((e) => e.textContent);
    expect(textos).toContain('@rene.sampaio381524');
    expect(textos).toContain('Sueli Barreto · sem conta');
    expect(textos).toContain('Pessoa sem nome · sem conta');              // sem nome nenhum, ainda assim não é "@"
    expect(textos.filter((t) => (t ?? '').trim() === '@')).toHaveLength(0);
    expect(chips.querySelectorAll('[data-sem-conta]')).toHaveLength(2);    // o estilo discreto só nos sem conta
  });

  it('29.25 (B3): o editor lista o membro sem conta (fora da listagem de perfis) e deixa tirá-lo do grupo', async () => {
    const grupo = { id: 'grp-1', name: 'Cautelosos', description: '', capabilities: {}, limits: {}, loosened: [],
                    members: [{ id: 'ig-1', username: 'rene.sampaio381524', name: 'Ravenna Sampaio' },
                              { id: 'ig-7', username: '', name: 'Sueli Barreto' }],
                    created_at: '', updated_at: '' };
    rotasBase([grupo]);
    backend.on('GET', /\/instagram\/policy-groups\/grp-1$/, () => json(grupo));
    backend.on('PUT', /\/instagram\/policy-groups\/grp-1$/, () => json({ ...grupo, members: grupo.members.slice(0, 1) }));
    await render();
    await waitFor(() => expect(text()).toContain('Cautelosos'));
    await click(byRole('button', /^Editar$/i));
    const sueli = await waitFor(() => byRole('checkbox', /^Sueli Barreto · sem conta$/) as HTMLInputElement);
    expect(sueli.checked).toBe(true);                                    // a contagem (2) bate com o que se vê
    await click(sueli);
    await waitFor(() => !(byRole('button', /Salvar grupo/i) as HTMLButtonElement).disabled);    // preso até o catálogo
    await click(byRole('button', /Salvar grupo/i));
    await waitFor(() => expect(backend.callsTo('PUT', /policy-groups\/grp-1$/)).toHaveLength(1));
    expect((backend.callsTo('PUT', /policy-groups\/grp-1$/)[0]!.body as { profile_ids: string[] }).profile_ids)
      .toEqual(['ig-1']);
  });

  it('criar um grupo manda nome, políticas escolhidas e os perfis marcados (só quem tem conta)', async () => {
    rotasBase([]);
    backend.on('POST', /\/instagram\/policy-groups$/, (c) => json({ id: 'grp-9', ...(c.body as object), loosened: [], members: [], created_at: '', updated_at: '' }, 201));
    await render();
    await click(byRole('button', /Novo grupo/i));
    await waitFor(() => expect(text()).toContain('Novo grupo de acesso'));
    // O grupo de acesso governa o que a CONTA faz: a pessoa sem @ não aparece como membro possível.
    expect(() => byRole('checkbox', /@null/i)).toThrow();
    await setValue(byRole('textbox', /Nome/i) as HTMLInputElement, 'Aquecimento');
    await click(byRole('checkbox', /@valdir.teixeira6352/i));
    await waitFor(() => expect(byRole('radiogroup', /Política de Curtir a publicação/i)).toBeTruthy());
    await click(byRole('radio', /Com aprovação/i, byRole('radiogroup', /Política de Curtir a publicação/i)));
    await click(byRole('button', /Criar grupo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)[0]!.body).toEqual({
      name: 'Aquecimento', description: '', capabilities: { LIKE_POST: 'approval_required' }, limits: {},
      profile_ids: ['ig-2'],
    });
  });

  it('mais de um app com catálogo: aparece o seletor, e a política salva vai com o pacote do app escolhido', async () => {
    // 23.10: antes o editor sempre pegava "o primeiro app com login gerenciado da lista" — com dois apps de
    // catálogo, o seletor deixa a escolha deliberada, e cada app grava com a chave e o pacote certos.
    rotasBase([]);
    backend.on('GET', /app-catalog/, () => json([
      { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
        session_provider: 'instagram', needs_profile: true, profile_anchor: true },
      { package: 'com.exemplo.correio', name: 'Correio', label: 'Correio', has_catalog: true,
        session_provider: 'correio', needs_profile: true, profile_anchor: false },
    ]));
    backend.on('GET', /capabilities/, (c) => json(
      c.query.get('package') === 'com.exemplo.correio'
        ? [{ key: 'LER_CAIXA', title: 'Ler a caixa de entrada', side_effect: false, risk: 'low',
             default_policy: 'autonomous', limit_bucket: null, needs_draft: false, bindings: [] }]
        : [{ key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
             default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] }],
    ));
    backend.on('POST', /\/instagram\/policy-groups$/,
      (c) => json({ id: 'grp-9', ...(c.body as object), loosened: [], members: [], created_at: '', updated_at: '' }, 201));
    await render();
    await click(byRole('button', /Novo grupo/i));
    await waitFor(() => expect(text()).toContain('Novo grupo de acesso'));
    // Por padrão vem o app âncora (Instagram): "Curtir a publicação" aparece sem escolher nada.
    await waitFor(() => expect(byRole('radiogroup', /Política de Curtir a publicação/i)).toBeTruthy());
    await setValue(byRole('textbox', /Nome/i) as HTMLInputElement, 'Correio piloto');
    await setValue(byRole('combobox', /Aplicativo/i) as HTMLSelectElement, 'com.exemplo.correio');
    await waitFor(() => expect(byRole('radiogroup', /Política de Ler a caixa de entrada/i)).toBeTruthy());
    expect(() => byRole('radiogroup', /Política de Curtir a publicação/i)).toThrow();     // trocou de app, trocou a lista
    await click(byRole('radio', /Só manual/i, byRole('radiogroup', /Política de Ler a caixa de entrada/i)));
    await click(byRole('button', /Criar grupo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(1));
    const chamada = backend.callsTo('POST', /\/instagram\/policy-groups$/)[0]!;
    expect(chamada.body).toEqual({
      name: 'Correio piloto', description: '', capabilities: { LER_CAIXA: 'manual_only' }, limits: {}, profile_ids: [],
    });
    expect(chamada.query.get('package')).toBe('com.exemplo.correio');
  });

  it('grupo novo editado em dois apps: cria com o app em tela e grava o outro no recorte dele', async () => {
    rotasBase([]);
    backend.on('GET', /app-catalog/, () => json([
      { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
        session_provider: 'instagram', needs_profile: true, profile_anchor: true },
      { package: 'com.exemplo.correio', name: 'Correio', label: 'Correio', has_catalog: true,
        session_provider: null, needs_profile: true, profile_anchor: false },
    ]));
    backend.on('GET', /capabilities/, (c) => json(
      c.query.get('package') === 'com.exemplo.correio'
        ? [{ key: 'LER_CAIXA', title: 'Ler a caixa de entrada', side_effect: false, risk: 'low',
             default_policy: 'autonomous', limit_bucket: null, needs_draft: false, bindings: [] }]
        : [{ key: 'LIKE_POST', title: 'Curtir a publicação', side_effect: true, risk: 'medium',
             default_policy: 'autonomous', limit_bucket: 'likes', needs_draft: false, bindings: [] }],
    ));
    backend.on('POST', /\/instagram\/policy-groups$/,
      (c) => json({ id: 'grp-9', ...(c.body as object), loosened: [], members: [], created_at: '', updated_at: '' }, 201));
    backend.on('PUT', /\/instagram\/policy-groups\/grp-9$/,
      () => json({ id: 'grp-9', name: 'Dois apps', description: '', capabilities: {}, limits: {}, loosened: [],
                   members: [], created_at: '', updated_at: '' }));
    await render();
    await click(byRole('button', /Novo grupo/i));
    await waitFor(() => expect(byRole('radiogroup', /Política de Curtir a publicação/i)).toBeTruthy());
    await setValue(byRole('textbox', /Nome/i) as HTMLInputElement, 'Dois apps');
    await click(byRole('radio', /Com aprovação/i, byRole('radiogroup', /Política de Curtir a publicação/i)));
    await setValue(byRole('combobox', /Aplicativo/i) as HTMLSelectElement, 'com.exemplo.correio');
    await waitFor(() => expect(byRole('radiogroup', /Política de Ler a caixa de entrada/i)).toBeTruthy());
    await click(byRole('radio', /Só manual/i, byRole('radiogroup', /Política de Ler a caixa de entrada/i)));
    await click(byRole('button', /Criar grupo/i));
    await waitFor(() => expect(backend.callsTo('PUT', /policy-groups\/grp-9$/)).toHaveLength(1));
    const post = backend.callsTo('POST', /\/instagram\/policy-groups$/);
    expect(post).toHaveLength(1);
    expect(post[0]!.query.get('package')).toBe('com.exemplo.correio');
    expect((post[0]!.body as { capabilities: unknown }).capabilities).toEqual({ LER_CAIXA: 'manual_only' });
    const put = backend.callsTo('PUT', /policy-groups\/grp-9$/)[0]!;
    expect(put.query.get('package')).toBe('com.instagram.android');
    expect(put.body).toEqual({ capabilities: { LIKE_POST: 'approval_required' } });
  });

  it('"começar a partir de" SUBSTITUI o rascunho: escolher A e depois B não leva a escolha de A para o grupo', async () => {
    // 23.10 (revisão): a mescla levava FOLLOW autônomo de A para um grupo "a partir de B", sem aparecer como de B.
    rotasBase([]);
    backend.on('GET', /\/instagram\/profiles\/ig-1\/policy$/, () => json({
      limits: {}, capabilities: {}, defaults: {}, loosened: [], own: { FOLLOW: 'autonomous' }, group: {},
      own_limits: {}, group_limits: {},
    }));
    backend.on('GET', /\/instagram\/profiles\/ig-2\/policy$/, () => json({
      limits: {}, capabilities: {}, defaults: {}, loosened: [], own: {}, group: {}, own_limits: {}, group_limits: {},
    }));
    backend.on('POST', /\/instagram\/policy-groups$/,
      (c) => json({ id: 'grp-9', ...(c.body as object), loosened: [], members: [], created_at: '', updated_at: '' }, 201));
    await render();
    await click(byRole('button', /Novo grupo/i));
    await waitFor(() => expect(byRole('radiogroup', /Política de Seguir/i)).toBeTruthy());
    await setValue(byRole('textbox', /Nome/i) as HTMLInputElement, 'Do Quillon');
    const partir = byRole('combobox', /Começar a partir de/i) as HTMLSelectElement;
    await setValue(partir, 'ig-1');
    await waitFor(() => expect(backend.callsTo('GET', /ig-1\/policy$/)).toHaveLength(1));
    await waitFor(() => marcada(/Sozinho/i, /Política de Seguir/i));
    await setValue(partir, 'ig-2');
    await waitFor(() => expect(backend.callsTo('GET', /ig-2\/policy$/)).toHaveLength(1));
    await waitFor(() => !marcada(/Sozinho/i, /Política de Seguir/i));
    await click(byRole('button', /Criar grupo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(1));
    expect((backend.callsTo('POST', /\/instagram\/policy-groups$/)[0]!.body as { capabilities: unknown }).capabilities)
      .toEqual({});
  });

  // 29.106 (W2 da leitura do #392): o teste acima espera A aplicado antes de trocar, então não vê a corrida. Este
  // força a ordem (A responde DEPOIS de B): a resposta de A, aposentada pela escolha de B, não entra no rascunho.
  it('29.106: escolher A e logo B, com A respondendo depois de B, parte de B', async () => {
    rotasBase([]);
    let soltarA: () => void = () => undefined;
    const aSegura = new Promise<void>((r) => { soltarA = r; });
    backend.on('GET', /\/instagram\/profiles\/ig-1\/policy$/, async () => {
      await aSegura;
      return json({ limits: {}, capabilities: {}, defaults: {}, loosened: [], own: { FOLLOW: 'autonomous' }, group: {},
                    own_limits: {}, group_limits: {} });
    });
    backend.on('GET', /\/instagram\/profiles\/ig-2\/policy$/, () => json({
      limits: {}, capabilities: {}, defaults: {}, loosened: [], own: {}, group: {}, own_limits: {}, group_limits: {},
    }));
    backend.on('POST', /\/instagram\/policy-groups$/,
      (c) => json({ id: 'grp-9', ...(c.body as object), loosened: [], members: [], created_at: '', updated_at: '' }, 201));
    await render();
    await click(byRole('button', /Novo grupo/i));
    await waitFor(() => expect(byRole('radiogroup', /Política de Seguir/i)).toBeTruthy());
    await setValue(byRole('textbox', /Nome/i) as HTMLInputElement, 'Do Quillon');
    const partir = byRole('combobox', /Começar a partir de/i) as HTMLSelectElement;
    await setValue(partir, 'ig-1');
    await waitFor(() => expect(backend.callsTo('GET', /ig-1\/policy$/)).toHaveLength(1));
    await setValue(partir, 'ig-2');
    await waitFor(() => expect(backend.callsTo('GET', /ig-2\/policy$/)).toHaveLength(1));
    await flush(30);                                                      // B aplicada
    await act(async () => { soltarA(); });
    await flush(30);                                                      // A chega por último
    expect(marcada(/Sozinho/i, /Política de Seguir/i)).toBe(false);
    await waitFor(() => !(byRole('button', /Criar grupo/i) as HTMLButtonElement).disabled);
    await click(byRole('button', /Criar grupo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(1));
    expect((backend.callsTo('POST', /\/instagram\/policy-groups$/)[0]!.body as { capabilities: unknown }).capabilities)
      .toEqual({});
  });

  it('29.106: com a leitura do "começar a partir de" em voo, o Criar grupo e o editor esperam', async () => {
    rotasBase([]);
    let soltar: () => void = () => undefined;
    const segura = new Promise<void>((r) => { soltar = r; });
    backend.on('GET', /\/instagram\/profiles\/ig-2\/policy$/, async () => {
      await segura;
      return json({ limits: {}, capabilities: {}, defaults: {}, loosened: [], own: { LIKE_POST: 'manual_only' },
                    group: {}, own_limits: {}, group_limits: {} });
    });
    backend.on('POST', /\/instagram\/policy-groups$/,
      (c) => json({ id: 'grp-9', ...(c.body as object), loosened: [], members: [], created_at: '', updated_at: '' }, 201));
    await render();
    await click(byRole('button', /Novo grupo/i));
    await waitFor(() => expect(byRole('radiogroup', /Política de Curtir a publicação/i)).toBeTruthy());
    await setValue(byRole('textbox', /Nome/i) as HTMLInputElement, 'Do Quillon');
    await setValue(byRole('combobox', /Começar a partir de/i) as HTMLSelectElement, 'ig-2');
    await waitFor(() => expect(backend.callsTo('GET', /ig-2\/policy$/)).toHaveLength(1));
    const criar = byRole('button', /Criar grupo/i) as HTMLButtonElement;
    expect(criar.disabled).toBe(true);                                    // antes: criava com o rascunho anterior
    const soManual = byRole('radio', /Só manual/i, byRole('radiogroup', /Política de Curtir a publicação/i));
    expect((soManual as HTMLInputElement).disabled).toBe(true);           // mexer agora seria apagado pela resposta
    await expect(click(criar)).rejects.toThrow('está desabilitado');      // 29.130: o harness, como a pessoa, não clica
    expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(0);

    await act(async () => { soltar(); });
    await waitFor(() => marcada(/Só manual/i, /Política de Curtir a publicação/i));
    expect(criar.disabled).toBe(false);
    await click(criar);
    await waitFor(() => expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(1));
    expect((backend.callsTo('POST', /\/instagram\/policy-groups$/)[0]!.body as { capabilities: unknown }).capabilities)
      .toEqual({ LIKE_POST: 'manual_only' });
  });

  // 29.109 (notas da leitura do #393): o erro solta a trava, a troca para o padrão no meio aposenta a leitura, o erro
  // de uma leitura aposentada não vira toast, e voltar ao padrão depois de A tira o que veio de A.
  const POLITICA_DE_A = { limits: {}, capabilities: {}, defaults: {}, loosened: [], own: { FOLLOW: 'autonomous' },
                          group: {}, own_limits: {}, group_limits: {} };
  const NAO_LEU = 'Não foi possível ler o acesso da persona';

  async function abrirNovoGrupo(): Promise<HTMLSelectElement> {
    backend.on('POST', /\/instagram\/policy-groups$/,
      (c) => json({ id: 'grp-9', ...(c.body as object), loosened: [], members: [], created_at: '', updated_at: '' }, 201));
    useToastStore.setState({ toasts: [] });
    await render();
    await click(byRole('button', /Novo grupo/i));
    await waitFor(() => expect(byRole('radiogroup', /Política de Seguir/i)).toBeTruthy());
    await setValue(byRole('textbox', /Nome/i) as HTMLInputElement, 'Do Quillon');
    return byRole('combobox', /Começar a partir de/i) as HTMLSelectElement;
  }

  const criarTravado = () => (byRole('button', /Criar grupo/i) as HTMLButtonElement).disabled;
  const avisou = () => useToastStore.getState().toasts.some((t) => t.title === NAO_LEU);

  it('29.109: a leitura que falha solta a trava e avisa', async () => {
    rotasBase([]);
    backend.on('GET', /\/instagram\/profiles\/ig-2\/policy$/, () => apiError(500, 'falhou', 'O servidor caiu.'));
    const partir = await abrirNovoGrupo();
    await setValue(partir, 'ig-2');
    await waitFor(() => avisou());
    await waitFor(() => !criarTravado());
    expect(text()).not.toContain('Lendo o acesso de hoje da persona');
  });

  it('29.109: trocar para o padrão no meio da leitura destrava na hora e a resposta velha não entra', async () => {
    rotasBase([]);
    let soltarA: () => void = () => undefined;
    const aSegura = new Promise<void>((r) => { soltarA = r; });
    backend.on('GET', /\/instagram\/profiles\/ig-1\/policy$/, async () => { await aSegura; return json(POLITICA_DE_A); });
    const partir = await abrirNovoGrupo();
    await setValue(partir, 'ig-1');
    await waitFor(() => expect(backend.callsTo('GET', /ig-1\/policy$/)).toHaveLength(1));
    expect(criarTravado()).toBe(true);                                    // a trava é imediata
    // M1: a dica não pisca numa leitura comum (~40 ms); só aparece se a leitura demorar (N1: a trava diz por quê).
    expect(text()).not.toContain('Lendo o acesso de hoje da persona');
    await flush(100);
    expect(text()).not.toContain('Lendo o acesso de hoje da persona');
    await waitFor(() => text().includes('Lendo o acesso de hoje da persona'));
    await setValue(partir, '');
    expect(text()).not.toContain('Lendo o acesso de hoje da persona');
    expect(criarTravado()).toBe(false);
    await act(async () => { soltarA(); });
    await flush(30);
    expect(marcada(/Sozinho/i, /Política de Seguir/i)).toBe(false);
  });

  it('29.109: o erro de uma leitura aposentada não vira toast', async () => {
    rotasBase([]);
    let soltarA: () => void = () => undefined;
    const aSegura = new Promise<void>((r) => { soltarA = r; });
    backend.on('GET', /\/instagram\/profiles\/ig-1\/policy$/, async () => {
      await aSegura;
      return apiError(500, 'falhou', 'O servidor caiu.');
    });
    backend.on('GET', /\/instagram\/profiles\/ig-2\/policy$/, () => json({ ...POLITICA_DE_A, own: {} }));
    const partir = await abrirNovoGrupo();
    await setValue(partir, 'ig-1');
    await waitFor(() => expect(backend.callsTo('GET', /ig-1\/policy$/)).toHaveLength(1));
    await setValue(partir, 'ig-2');
    await waitFor(() => !criarTravado());
    await act(async () => { soltarA(); });
    await flush(30);
    expect(avisou()).toBe(false);
    expect(criarTravado()).toBe(false);
  });

  it('29.109: voltar ao "Padrão do catálogo" depois de partir de A tira o que veio de A', async () => {
    rotasBase([]);
    backend.on('GET', /\/instagram\/profiles\/ig-1\/policy$/, () => json(POLITICA_DE_A));
    const partir = await abrirNovoGrupo();
    await setValue(partir, 'ig-1');
    await waitFor(() => marcada(/Sozinho/i, /Política de Seguir/i));
    await setValue(partir, '');
    await waitFor(() => !marcada(/Sozinho/i, /Política de Seguir/i));
    await click(byRole('button', /Criar grupo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(1));
    expect((backend.callsTo('POST', /\/instagram\/policy-groups$/)[0]!.body as { capabilities: unknown }).capabilities)
      .toEqual({});
  });

  it('antes de o catálogo chegar, "começar a partir de" e salvar esperam: o grupo nasce com as escolhas do perfil', async () => {
    // Revisão da 23.10: o rascunho feito antes do catálogo ficava sem app (chave ''), e o grupo era CRIADO com
    // `capabilities: {}` no pacote âncora — os membros herdavam o padrão até um segundo pedido corrigir.
    rotasBase([]);
    let soltar: () => void = () => undefined;
    const catalogoChega = new Promise<void>((r) => { soltar = r; });
    backend.on('GET', /app-catalog/, async () => {
      await catalogoChega;
      return json([{ package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
                     session_provider: 'instagram', needs_profile: true, profile_anchor: true }]);
    });
    backend.on('GET', /\/instagram\/profiles\/ig-2\/policy$/, () => json({
      limits: {}, capabilities: {}, defaults: {}, loosened: [], own: { LIKE_POST: 'manual_only' }, group: {},
      own_limits: {}, group_limits: {},
    }));
    backend.on('POST', /\/instagram\/policy-groups$/,
      (c) => json({ id: 'grp-9', ...(c.body as object), loosened: [], members: [], created_at: '', updated_at: '' }, 201));
    backend.on('PUT', /\/instagram\/policy-groups\/grp-9$/,
      () => json({ id: 'grp-9', name: 'Do Quillon', description: '', capabilities: {}, limits: {}, loosened: [],
                   members: [], created_at: '', updated_at: '' }));
    await render();
    await click(byRole('button', /Novo grupo/i));
    await waitFor(() => expect(text()).toContain('Novo grupo de acesso'));
    await setValue(byRole('textbox', /Nome/i) as HTMLInputElement, 'Do Quillon');
    expect((byRole('combobox', /Começar a partir de/i) as HTMLSelectElement).disabled).toBe(true);
    expect((byRole('button', /Criar grupo/i) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => { soltar(); });
    await waitFor(() => expect((byRole('combobox', /Começar a partir de/i) as HTMLSelectElement).disabled).toBe(false));
    await setValue(byRole('combobox', /Começar a partir de/i) as HTMLSelectElement, 'ig-2');
    await waitFor(() => expect(backend.callsTo('GET', /ig-2\/policy$/)).toHaveLength(1));
    await waitFor(() => marcada(/Só manual/i, /Política de Curtir a publicação/i));
    await click(byRole('button', /Criar grupo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(1));
    const post = backend.callsTo('POST', /\/instagram\/policy-groups$/)[0]!;
    expect((post.body as { capabilities: unknown }).capabilities).toEqual({ LIKE_POST: 'manual_only' });
    expect(post.query.get('package')).toBe('com.instagram.android');
    expect(backend.callsTo('PUT', /policy-groups\/grp-9$/)).toHaveLength(0);
  });

  it('grupo existente em dois apps: o outro app vem do servidor pelo pacote, e salvar grava cada app no seu', async () => {
    // A mesma chave de ação pode existir nos dois catálogos (SEND_MESSAGE): cada app é um recorte à parte do grupo.
    rotasBase([{ id: 'grp-1', name: 'Cautelosos', description: '', package: 'com.instagram.android',
                 capabilities: { SEND_MESSAGE: 'approval_required' }, limits: {}, loosened: [],
                 members: [{ id: 'ig-1', username: 'rene.sampaio381524' }], created_at: '', updated_at: '' }]);
    backend.on('GET', /app-catalog/, () => json([
      { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
        session_provider: 'instagram', needs_profile: true, profile_anchor: true },
      { package: 'com.exemplo.correio', name: 'Correio', label: 'Correio', has_catalog: true,
        session_provider: null, needs_profile: true, profile_anchor: false },
    ]));
    const enviar = (titulo: string) => [{ key: 'SEND_MESSAGE', title: titulo, side_effect: true, risk: 'high',
      default_policy: 'manual_only', limit_bucket: 'dms', needs_draft: false, bindings: [] }];
    backend.on('GET', /capabilities/, (c) => json(
      enviar(c.query.get('package') === 'com.exemplo.correio' ? 'Enviar e-mail' : 'Enviar a mensagem')));
    backend.on('GET', /\/instagram\/policy-groups\/grp-1$/, () => json({
      id: 'grp-1', name: 'Cautelosos', description: '', package: 'com.exemplo.correio',
      capabilities: { SEND_MESSAGE: 'disabled' }, limits: {}, loosened: [], members: [], created_at: '', updated_at: '',
    }));
    backend.on('PUT', /\/instagram\/policy-groups\/grp-1$/, () => json({
      id: 'grp-1', name: 'Cautelosos', description: '', capabilities: {}, limits: {}, loosened: [], members: [],
      created_at: '', updated_at: '',
    }));
    await render();
    await waitFor(() => expect(text()).toContain('Cautelosos'));
    await click(byRole('button', /^Editar$/i));
    await waitFor(() => expect(byRole('radiogroup', /Política de Enviar a mensagem/i)).toBeTruthy());
    await setValue(byRole('combobox', /Aplicativo/i) as HTMLSelectElement, 'com.exemplo.correio');
    await waitFor(() => expect(byRole('radiogroup', /Política de Enviar e-mail/i)).toBeTruthy());
    // (o efeito pode rodar duas vezes no modo estrito; o que importa é que toda leitura pede o recorte do Correio)
    await waitFor(() => expect(backend.callsTo('GET', /policy-groups\/grp-1$/).length).toBeGreaterThan(0));
    expect(backend.callsTo('GET', /policy-groups\/grp-1$/).map((c) => c.query.get('package')))
      .toEqual(backend.callsTo('GET', /policy-groups\/grp-1$/).map(() => 'com.exemplo.correio'));
    // o valor do Correio (desligado) é o do servidor, não o do Instagram (com aprovação)
    await waitFor(() => expect(text()).toContain('definido no grupo'));
    await click(byRole('radio', /Com aprovação/i, byRole('radiogroup', /Política de Enviar e-mail/i)));
    await click(byRole('button', /Salvar grupo/i));
    await waitFor(() => expect(backend.callsTo('PUT', /policy-groups\/grp-1$/)).toHaveLength(1));
    const put = backend.callsTo('PUT', /policy-groups\/grp-1$/)[0]!;
    expect(put.query.get('package')).toBe('com.exemplo.correio');
    expect(put.body).toMatchObject({ capabilities: { SEND_MESSAGE: 'approval_required' } });
    // o Instagram não foi editado: nenhum PUT o regrava (a mesma chave nele continua "com aprovação" no servidor)
    expect(backend.callsTo('PUT', /policy-groups\/grp-1$/).every((c) => c.query.get('package') !== 'com.instagram.android'))
      .toBe(true);
  });
});

// ---------------------------------------------------------------- busca, filtros e tabela (tarefa UX 05)
describe('busca, filtros e visão em tabela', () => {
  const LISTA = () => [
    pessoa(),
    pessoa({ id: 'ig-2', name: 'Quillon Teixeira', username: 'valdir.teixeira6352', display_name: 'Quillon Teixeira',
             status: 'blocked', instance_id: null, locality: null }),
    SEM_CONTA,
  ];

  it('o link da saúde do ambiente (`situacao=bloqueada`) abre a lista já filtrada, e "Limpar filtros" tira o filtro', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json(LISTA()));
    useUiStore.getState().navegar({ tela: 'personas', query: { situacao: 'bloqueada' } }, 'replace');
    await render();
    await waitFor(() => text().includes('valdir.teixeira6352'));
    expect(text()).not.toContain('luciana.bastos73519');
    expect(text()).toContain('1 de 3 personas');
    expect(byRole('button', /Bloqueadas pela plataforma/).getAttribute('aria-pressed')).toBe('true');
    await click(byRole('button', /Limpar filtros/));
    await waitFor(() => text().includes('luciana.bastos73519'));
    expect(window.location.hash).toBe('#/personas');
  });

  it('buscar pelo @ grava `q` na URL substituindo a entrada (sem empilhar), e o vazio diz o filtro', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json(LISTA()));
    await render();
    await waitFor(() => text().includes('valdir.teixeira6352'));
    const antes = window.history.length;
    await setValue(byRole('textbox', /Buscar personas/) as HTMLInputElement, '@quillon');
    await waitFor(() => !text().includes('luciana.bastos73519'));
    expect(text()).toContain('Quillon Teixeira');
    expect(window.location.hash).toBe('#/personas?q=%40quillon');
    expect(window.history.length).toBe(antes);
    await setValue(byRole('textbox', /Buscar personas/) as HTMLInputElement, 'ninguém-assim');
    await waitFor(() => text().includes('Nenhuma persona com "ninguém-assim" no nome ou no @.'));
  });

  it('filtros combinados persistem no link: recarregar (remontar) mostra o mesmo recorte', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json(LISTA()));
    useUiStore.getState().navegar({ tela: 'personas', query: { situacao: 'ativa', vinculo: 'com', ordem: 'situacao' } }, 'replace');
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));
    expect(text()).not.toContain('Elaine Prado');
    expect(text()).not.toContain('valdir.teixeira6352');
    expect((byRole('combobox', /Aparelho vinculado/) as HTMLSelectElement).value).toBe('com');
    expect((byRole('combobox', /Ordenar personas/) as HTMLSelectElement).value).toBe('situacao');
  });

  it('alternar cartões → tabela não perde a seleção; a tabela tem as colunas pedidas', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json(LISTA()));
    await render();
    await waitFor(() => text().includes('valdir.teixeira6352'));
    await click(byRole('checkbox', /^Selecionar Quillon Teixeira$/));
    await click(byRole('button', /^Tabela$/));
    await waitFor(() => document.querySelector('table') !== null);
    expect(window.location.hash).toBe('#/personas?visao=tabela');
    const cabecalhos = [...document.querySelectorAll('thead th')].map((th) => th.textContent);
    expect(cabecalhos).toEqual(['Seleção', 'Nº', 'Persona', 'Conta (@)', 'Contas', 'Aparelho', 'Situação', 'Grupo', 'Ações']);
    expect((byRole('checkbox', /^Selecionar Quillon Teixeira$/) as HTMLInputElement).checked).toBe(true);
    expect((byRole('checkbox', /^Selecionar Luciana Bastos$/) as HTMLInputElement).checked).toBe(false);
    await click(byRole('button', /^Cartões$/));
    await waitFor(() => document.querySelector('table') === null);
    expect((byRole('checkbox', /^Selecionar Quillon Teixeira$/) as HTMLInputElement).checked).toBe(true);
  });

  it('D3 (RF-08): o menu leva à tela limpa, mas a visão escolhida vira o padrão; o link com `visao` manda', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json(LISTA()));
    useUiStore.getState().navegar({ tela: 'personas', query: { situacao: 'ativa' } }, 'replace');
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));
    await click(byRole('button', /^Tabela$/));
    await waitFor(() => document.querySelector('table') !== null);
    expect(window.localStorage.getItem('cda.personas.visao')).toBe('"tabela"');
    // Pelo menu: `#/personas`, sem filtro e sem `visao`. O filtro some (é do link); a visão fica (é preferência).
    await act(async () => useUiStore.getState().navegar({ tela: 'personas' }));
    expect(window.location.hash).toBe('#/personas');
    await waitFor(() => text().includes('Elaine Prado'));
    expect(document.querySelector('table')).not.toBeNull();
    // Um link colado com a outra visão abre nela, sem mudar a preferência.
    await act(async () => useUiStore.getState().navegar({ tela: 'personas', query: { visao: 'cards' } }, 'replace'));
    await waitFor(() => document.querySelector('table') === null);
    expect(window.localStorage.getItem('cda.personas.visao')).toBe('"tabela"');
  });

  it('"Selecionar todas" vale para o que o filtro mostra, e a seleção fora do filtro é avisada', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json(LISTA()));
    await render();
    await waitFor(() => text().includes('valdir.teixeira6352'));
    await click(byRole('checkbox', /^Selecionar Luciana Bastos$/));
    await act(async () => { useUiStore.getState().trocarQuery({ situacao: 'bloqueada' }); });
    await waitFor(() => !text().includes('luciana.bastos73519'));
    expect(text()).toContain('(1 fora do filtro atual)');
    await click(byRole('checkbox', /Selecionar todas as 1 personas/));
    expect(text()).toContain('2 de 3 para as ações em lote');
  });

  it('estado contraditório vira um só ("Ativa · app não instalado") e a ação só leva à guia Aparelhos', async () => {
    const gate = { allowed: false, reason: null };
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa({
      session_actions: { phase: 'app_missing', detail: 'O Instagram não está instalado no android-02.',
                         connect: gate, verify: gate, logout: gate, inspect_app: gate },
    } as Partial<PersonaDTO>)]));
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    await render();
    await waitFor(() => text().includes('Ativa · app não instalado'));
    expect(text()).not.toMatch(/Ativaapp não instalado/);
    const pedidosAntes = backend.calls.filter((c) => c.method !== 'GET').length;
    await click(byRole('button', /Instalar app: abrir Luciana Bastos/));
    expect(window.location.hash).toBe('#/personas/luciana-bastos/aparelhos');
    // Nada foi instalado: só navegou.
    expect(backend.calls.filter((c) => c.method !== 'GET').length).toBe(pedidosAntes);
  });

  it('"Marcar bloqueada" saiu do cartão e mora no menu "⋯"', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));
    await render();
    await waitFor(() => text().includes('luciana.bastos73519'));
    expect(() => byRole('button', /^Marcar bloqueada$/)).toThrow();
    await click(byRole('button', /Mais ações de Luciana Bastos/));
    expect(byRole('button', /^Marcar bloqueada$/)).toBeTruthy();
    expect(byRole('button', /Remover persona/)).toBeTruthy();
  });
});

describe('Nº da persona e aparelho dividido (31.245)', () => {
  const binding = (instance_id: string) => ({ instance_id, app_id: 'instagram', is_primary: true, state: 'online', worker_id: null, bound_at: null, session: null });
  const TRES = () => [
    pessoa({ id: 'ig-b', name: 'Bruno Ferreira', username: 'bruno.f', created_at: '2026-09-18T10:00:00Z', instance_id: 'android-04', devices: [binding('android-04')] }),
    pessoa({ id: 'ig-a', name: 'Ana Souza', username: 'ana.s', created_at: '2026-09-17T10:00:00Z', instance_id: 'android-01', devices: [binding('android-01')] }),
    pessoa({ id: 'ig-c', name: 'Carla Dias', username: 'carla.d', created_at: '2026-09-19T10:00:00Z', instance_id: 'android-04', devices: [binding('android-04')] }),
  ];

  it('cada cartão traz o Nº por ordem de criação, não pela ordem da lista; quem divide o aparelho diz com quem', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json(TRES()));
    await render();
    await waitFor(() => text().includes('Carla Dias'));
    const rotulos = [...document.querySelectorAll('[data-numero-da-persona]')].map((e) => e.textContent);
    expect(rotulos.sort()).toEqual(['Nº 1', 'Nº 2', 'Nº 3']);
    expect(text()).toContain('android-04 · compartilhado com Nº 3');   // Bruno (2) divide com Carla (3)
    expect(text()).toContain('android-04 · compartilhado com Nº 2');   // e Carla (3) com Bruno (2)
    expect(text()).not.toContain('android-01 · compartilhado');        // Ana está sozinha
  });

  it('na tabela há a coluna Nº e o filtro não renumera: a persona 3 continua 3 com a lista filtrada', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json(TRES()));
    await render();
    await waitFor(() => text().includes('Carla Dias'));
    await click(byRole('button', /^Tabela$/));
    await waitFor(() => document.querySelector('table') !== null);
    const linhas = [...document.querySelectorAll('tbody tr')].map((tr) => [tr.querySelector('[data-numero-da-persona]')?.textContent, tr.textContent?.includes('Carla Dias')]);
    expect(linhas.find(([, ehCarla]) => ehCarla)?.[0]).toBe('3');
    await act(async () => useUiStore.getState().navegar({ tela: 'personas', query: { visao: 'tabela', q: 'carla' } }, 'replace'));
    await waitFor(() => document.querySelectorAll('tbody tr').length === 1);
    expect(document.querySelector('tbody tr [data-numero-da-persona]')?.textContent).toBe('3');
  });
});
