// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PersonaDTO } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { ProfilesPage } from './ProfilesPage';

/** A pessoa como `GET /personas` a devolve (v0.27): `username` nulo quando ainda não tem conta de cadastro. */
function pessoa(over: Partial<PersonaDTO> = {}): PersonaDTO {
  return {
    id: 'ig-1', name: 'Mariana Costa', summary: null, username: 'mariana.costa91182', display_name: 'Mariana Costa',
    first_name: 'Mariana', last_name: 'Costa', birth_date: null, email: null, persona_id: 'ig-1', persona_name: 'Mariana Costa',
    status: 'active', instance_id: 'android-02',
    locality: { worker_id: null, worker_name: 'este servidor', worker_state: 'online', known: true,
                available: true, moved: false, physical_id: null, detail: null },
    offline_policy: 'wait',
    credential: { configured: true, login_identifier: 'mariana@exemplo.com', status: 'active', failed_attempts: 0,
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
  id: 'ig-9', name: 'Helena Prado', username: null, display_name: 'Helena Prado', first_name: 'Helena',
  last_name: 'Prado', persona_id: 'ig-9', persona_name: 'Helena Prado', instance_id: null, locality: null,
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
  useUiStore.setState({ focusInstanceId: null, personaRequest: null });
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
    await waitFor(() => text().includes('mariana.costa91182'));

    await click(byRole('button', /Remover persona/i));
    await waitFor(() => text().includes('as senhas guardadas no cofre vão junto'));
    await click(byRole('button', /^Voltar$/i, byRole('dialog', /Remover/)));
    await waitFor(() => !text().includes('as senhas guardadas no cofre vão junto'));
    expect(backend.callsTo('DELETE', /personas/)).toHaveLength(0);
    expect(text()).toContain('mariana.costa91182');
  });

  it('confirmar no diálogo apaga pela rota da persona (que recusa quem está vinculado ou em execução)', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));
    backend.on('DELETE', /^\/api\/personas\//, () => json(null, 204));
    await render();
    await waitFor(() => text().includes('mariana.costa91182'));

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
    await waitFor(() => text().includes('Helena Prado'));
    expect(text()).toContain('sem conta de cadastro');
    expect(text()).toContain('29 anos · Recife · Professora de biologia');
    expect(text()).not.toContain('@null');
    expect(text()).toContain('@mariana.costa91182');
    // A lista vem de `GET /personas` (todas as pessoas), não de `GET /instagram/profiles` (só quem tem @).
    expect(backend.callsTo('GET', /^\/api\/instagram\/profiles$/)).toHaveLength(0);
  });

  it('o cartão é a pessoa: contas, aparelho e situação — sem senha, sessão nem Conectar', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa({ accounts_count: 2 })]));
    await render();
    await waitFor(() => text().includes('mariana.costa91182'));
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
    await waitFor(() => text().includes('mariana.costa91182'));
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
    await waitFor(() => text().includes('mariana.costa91182'));
    expect(text()).not.toContain('Não foi possível carregar as personas');
  });

  it('avisa quando não há persona e oferece os dois caminhos de cadastro', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    await render();
    expect(text()).toContain('Nenhuma persona cadastrada');
    expect(byRole('button', /Nova persona a partir de um prompt/i)).toBeTruthy();
    expect(byRole('button', /Nova persona manual/i)).toBeTruthy();
  });

  it('pedido de outra tela (openPersona) abre a persona pedida na guia pedida e é consumido', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa(), SEM_CONTA]));
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    useUiStore.setState({ personaRequest: { id: 'ig-9', tab: 'contas', nonce: 1 } });
    await act(async () => {
      root.render(<ProfilesPage />);
    });
    await waitFor(() => text().includes('Helena Prado') && text().includes('Personas'));
    await waitFor(() => byRole('tab', /Contas e acesso/i).getAttribute('aria-selected') === 'true');
    expect(useUiStore.getState().personaRequest).toBeNull();
    expect(text()).toContain('ainda não tem @ de cadastro');
  });

  it('guia pedida que não existe cai na Visão geral', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    useUiStore.setState({ personaRequest: { id: 'ig-1', tab: 'autenticacao', nonce: 2 } });
    await act(async () => {
      root.render(<ProfilesPage />);
    });
    await waitFor(() => byRole('tab', /Visão geral/i).getAttribute('aria-selected') === 'true');
  });
});

// ---------------------------------------------------------------- cadastro em dois caminhos (evolução 2, E1)
describe('nova persona', () => {
  const RASCUNHO = {
    name: 'Helena Prado', summary: 'Professora que fala de plantas.', birth_date: '1996-03-02', gender: 'feminino',
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
    await click(byRole('button', /Nova persona a partir de um prompt/i));
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
    expect(criada.name).toBe('Helena Prado');
    expect(criada.biography.work.profession).toBe('Professora de biologia');       // o que a pessoa editou
    expect(criada.biography.tastes).toEqual({ hobbies: ['trilhas'] });           // o resto do rascunho vai como veio
    expect(criada.traits).toEqual(RASCUNHO.traits);
    expect(criada.generation).toEqual(RASCUNHO.generation);                     // proveniência da IA preservada
  });

  it('por prompt: as crenças do rascunho aparecem numa linha cada e vão inteiras na criação (ADR-047)', async () => {
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
    await click(byRole('button', /Nova persona a partir de um prompt/i));
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
    await click(byRole('button', /Nova persona a partir de um prompt/i));
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
    await click(byRole('button', /Nova persona a partir de um prompt/i));
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

    await setValue(byRole('textbox', /^Nome/i) as HTMLInputElement, 'Helena Prado');
    await setValue(byRole('textbox', /^Nascimento/i) as HTMLInputElement, '1996-03-02');
    await click(byRole('button', /Criar persona/i));
    await waitFor(() => backend.callsTo('POST', /^\/api\/personas$/).length === 1);
    expect(backend.callsTo('POST', /^\/api\/personas$/)[0]?.body)
      .toEqual({ name: 'Helena Prado', birth_date: '1996-03-02', gender: null, summary: null });
    expect(container.ownerDocument.querySelector('input[type="password"]')).toBeNull();
  });
});

// ---------------------------------------------------------------- fila "Aguardando intervenção" (achado #106)
describe('fila de intervenção', () => {
  it('lista perfil, aparelho e motivo de quem está preso, e ignora quem não está', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([
      pessoa({
        id: 'ig-1', username: 'mariana.costa91182',
        session: { status: 'auth_challenge', instance_id: 'android-02', observed_username: null,
                   verified_at: '2026-09-23T09:00:00Z', detail: 'O Instagram exige confirmação adicional.',
                   stale: false },
      }),
      pessoa({ id: 'ig-2', name: 'Lucas Almeida', username: 'lucas.almeida9484', instance_id: 'android-01' }),
      SEM_CONTA,
    ]));
    await render();
    await waitFor(() => text().includes('Aguardando intervenção'));

    expect(text()).toContain('mariana.costa91182');
    expect(text()).toContain('android-02');
    expect(text()).toContain('O Instagram exige confirmação adicional.');
    // "lucas.almeida9484" está `unknown`: NÃO aparece na fila, só no cartão (uma vez).
    expect(text().match(/lucas\.almeida9484/g)?.length ?? 0).toBe(1);
  });

  it('sem ninguém preso, a fila não aparece', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([pessoa()]));   // status padrão: unknown
    await render();
    await waitFor(() => text().includes('mariana.costa91182'));
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
    await waitFor(() => text().includes('mariana.costa91182'));
    expect(text()).toContain('Servidor');
    expect(text()).toContain('Notebook da sala');
    expect(text()).toContain('mudou de servidor');
    expect(text()).toContain('indisponível');
    expect(text()).toContain('aponta hoje para este servidor');
  });
});

describe('grupos de acesso', () => {
  function rotasBase(grupos: unknown[]) {
    backend.on('GET', /^\/api\/personas$/, () => json([
      pessoa({ id: 'ig-1', name: 'André Carvalho', username: 'andre.carvalho9543', policy_group_id: 'grp-1',
               policy_group_name: 'Cautelosos' }),
      pessoa({ id: 'ig-2', name: 'Bruno Ferreira', username: 'bruno.ferreira9267' }),
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
                 limits: { likes_per_hour: 5 }, loosened: [], members: [{ id: 'ig-1', username: 'andre.carvalho9543' }],
                 created_at: '', updated_at: '' }]);
    await render();
    await waitFor(() => expect(text()).toContain('Grupos de acesso'));
    await waitFor(() => expect(text()).toContain('0 sozinho · 2 com aprovação · 0 só manual'));
    expect(text()).toContain('2 mudança(s) em relação ao padrão');
    expect(text()).toContain('1 perfil(is)');
    expect(text()).toContain('nenhum — padrão do catálogo');      // o Bruno não tem grupo
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
    await click(byRole('checkbox', /@bruno.ferreira9267/i));
    await waitFor(() => expect(byRole('radiogroup', /Política de Curtir a publicação/i)).toBeTruthy());
    await click(byRole('radio', /Com aprovação/i, byRole('radiogroup', /Política de Curtir a publicação/i)));
    await click(byRole('button', /Criar grupo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/instagram\/policy-groups$/)[0]!.body).toEqual({
      name: 'Aquecimento', description: '', capabilities: { LIKE_POST: 'approval_required' }, limits: {},
      profile_ids: ['ig-2'],
    });
  });
});
