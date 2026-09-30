// @vitest-environment jsdom
/**
 * Operações em lote na lista de Personas (v0.34): seleção, e cada operação da barra chama a rota certa por pessoa,
 * com resumo por pessoa (falha parcial com o motivo), confirmação digitada do apagar e custo/aviso antes do que é
 * pago. Backend falso: `simulated`.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PersonaDTO } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeSnapshot } from '../../test/fixtures';
import {
  FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor,
} from '../../test/harness';
import { SEM_CONTA_NO_GRUPO } from './AcoesEmLote';
import { ProfilesPage } from './ProfilesPage';

function pessoa(over: Partial<PersonaDTO> = {}): PersonaDTO {
  return {
    id: 'ig-1', name: 'Mariana Costa', summary: null, username: 'mariana.costa91182', display_name: 'Mariana Costa',
    first_name: 'Mariana', last_name: 'Costa', birth_date: null, email: null, persona_id: 'ig-1', persona_name: 'Mariana Costa',
    status: 'active', instance_id: null, locality: null, offline_policy: 'wait',
    credential: { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                  updated_at: null, last_used_at: null },
    session: { status: 'unknown', instance_id: null, observed_username: null, verified_at: null, detail: null, stale: false },
    last_verified_at: null, last_activity_at: null, accounts_count: 1,
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

const MARIANA = pessoa();
const LUCAS = pessoa({ id: 'ig-2', name: 'Lucas Almeida', username: 'lucas.almeida9484', display_name: 'Lucas Almeida',
                       first_name: 'Lucas', last_name: 'Almeida', persona_id: 'ig-2', persona_name: 'Lucas Almeida',
                       status: 'blocked' });
const HELENA = pessoa({ id: 'ig-9', name: 'Helena Prado', username: null, display_name: 'Helena Prado',
                        first_name: 'Helena', last_name: 'Prado', persona_id: 'ig-9', persona_name: 'Helena Prado',
                        accounts_count: 0 });

const SOCIAL_SIMULADO = { role: 'social', provider: 'simulated', kind: 'simulated', model: 'simulado', endpoint: '',
                          sends_data_externally: false, configured: true, priced: false, vision: false, tools: false,
                          refusal_fallback: false };
const SOCIAL_PAGO = { ...SOCIAL_SIMULADO, provider: 'anthropic', kind: 'anthropic', model: 'claude-sonnet-x',
                      endpoint: 'api.anthropic.com', sends_data_externally: true, priced: true };
const IMAGEM_SIMULADA = { provider: 'simulated', model: 'degrade', quality: 'medium', configured: true, simulated: true,
                          sends_data_externally: false, per_persona: 1, on_create: true, price_per_image_usd: 0 };
const IMAGEM_PAGA = { ...IMAGEM_SIMULADA, provider: 'openai', model: 'gpt-image-2', simulated: false,
                      sends_data_externally: true, price_per_image_usd: 0.055 };
const IA_SIMULADA = { provider: 'simulated', model: null, configured: false, simulated: true, sends_data_externally: false,
                      notice: '', effort: null, roles: [SOCIAL_SIMULADO], image: IMAGEM_SIMULADA };
const IA_PAGA = { ...IA_SIMULADA, provider: 'anthropic', model: 'claude-sonnet-x', configured: true, simulated: false,
                  sends_data_externally: true, roles: [SOCIAL_PAGO], image: IMAGEM_PAGA };

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

// ---------------------------------------------------------------- operações em lote na lista
describe('ações em lote', () => {
  function rotas(pessoas: PersonaDTO[] = [MARIANA, LUCAS, HELENA]) {
    backend.on('GET', /^\/api\/personas$/, () => json(pessoas));
    backend.on('GET', /\/instagram\/policy-groups$/, () => json([
      { id: 'grp-1', name: 'Cautelosos', description: '', capabilities: {}, limits: {}, loosened: [], members: [],
        created_at: '', updated_at: '' },
    ]));
    backend.on('GET', /\/instagram\/policy-defaults$/, () => json({ limits: {} }));
    backend.on('GET', /app-catalog/, () => json([]));
    backend.on('GET', /capabilities/, () => json([]));
  }

  const barra = () => byRole('toolbar', /Ações em/);

  async function selecionar(...nomes: string[]): Promise<void> {
    for (const nome of nomes) await click(byRole('checkbox', new RegExp(`^Selecionar ${nome}$`)));
  }

  it('seleção: cada cartão tem a caixa pelo nome; "Selecionar todas" marca todas; limpar some com a barra', async () => {
    rotas();
    await render();
    await waitFor(() => text().includes('Helena Prado'));
    expect(() => barra()).toThrow();
    await selecionar('Helena Prado');
    await waitFor(() => text().includes('1 selecionada'));
    await click(byRole('checkbox', /Selecionar todas as 3 personas/));
    await waitFor(() => text().includes('3 selecionadas'));
    expect((byRole('checkbox', /^Selecionar Mariana Costa$/) as HTMLInputElement).checked).toBe(true);
    await click(byRole('button', /Limpar seleção/, barra()));
    await waitFor(() => { expect(() => barra()).toThrow(); });
  });

  it('gerar mais fotos: custo com gerador pago, POST …/images {count} por persona e resumo com falha parcial', async () => {
    rotas();
    backend.on('GET', /^\/api\/ai$/, () => json(IA_PAGA));
    backend.on('POST', /\/personas\/ig-1\/images$/, () => json({ accepted: true, persona_id: 'ig-1', count: 2,
                                                                 provider: 'openai', simulated: false }, 202));
    backend.on('POST', /\/personas\/ig-9\/images$/,
               () => apiError(409, 'persona_minor', 'A persona tem 17 anos; só se fotografa pessoa adulta.'));
    await render();
    await waitFor(() => text().includes('Helena Prado'));
    await selecionar('Mariana Costa', 'Helena Prado');
    await click(byRole('button', /Gerar mais fotos/, barra()));
    const dialogo = await waitFor(() => byRole('dialog', /Gerar mais fotos/));
    await waitFor(() => text(dialogo).includes('É uma geração paga de imagem'));
    await setValue(byRole('combobox', /Fotos por persona/, dialogo) as HTMLSelectElement, '2');
    await waitFor(() => text(dialogo).includes('4 × US$ 0,055 = ≈ US$ 0,22 (openai)'));
    expect(backend.calls.filter((c) => c.method === 'POST')).toHaveLength(0);   // nada sem confirmar
    await click(byRole('button', /Gerar 4 fotos/, dialogo));
    await waitFor(() => text().includes('Terminado: 1 ok · 1 falhou.'));
    expect(backend.callsTo('POST', /\/personas\/ig-1\/images$/)[0]?.body).toEqual({ count: 2 });
    expect(backend.callsTo('POST', /\/personas\/ig-9\/images$/)[0]?.body).toEqual({ count: 2 });
    expect(text()).toContain('2 fotos em geração');
    expect(text()).toContain('só se fotografa pessoa adulta');
    expect(backend.callsTo('POST', /\/personas\/ig-2\/images$/)).toHaveLength(0);   // não selecionada
  });

  it('completar com IA: avisa que é pago e manda a instrução comum a cada persona', async () => {
    rotas();
    backend.on('GET', /^\/api\/ai$/, () => json(IA_PAGA));
    backend.on('POST', /\/personas\/ig-1\/enrich$/, () => json({ ...MARIANA, updated_at: '2026-09-28T12:00:00Z' }));
    backend.on('POST', /\/personas\/ig-2\/enrich$/, () => json(LUCAS));
    await render();
    await waitFor(() => text().includes('Lucas Almeida'));
    await selecionar('Mariana Costa', 'Lucas Almeida');
    await click(byRole('button', /Completar com IA/, barra()));
    const dialogo = await waitFor(() => byRole('dialog', /Completar/));
    await waitFor(() => text(dialogo).includes('É uma chamada paga de IA por persona'));
    expect(text(dialogo)).toContain('≈ US$ 0,04–0,06');
    await setValue(byRole('textbox', /Instruções para o que falta/, dialogo) as HTMLTextAreaElement, 'moram no interior');
    await click(byRole('button', /^Completar 2/, dialogo));
    await waitFor(() => text().includes('Terminado: 2 ok · 0 falharam.'));
    expect(backend.callsTo('POST', /\/enrich$/).map((c) => [c.path, c.body])).toEqual(expect.arrayContaining([
      ['/api/personas/ig-1/enrich', { instructions: 'moram no interior' }],
      ['/api/personas/ig-2/enrich', { instructions: 'moram no interior' }],
    ]));
    expect(text()).toContain('completada');
    expect(text()).toContain('nada faltava');
  });

  it('grupo de acesso: PATCH do perfil com policy_group_id; sem conta falha com motivo e sem requisição', async () => {
    let lista = [MARIANA, LUCAS, HELENA];
    rotas();
    backend.on('GET', /^\/api\/personas$/, () => json(lista));
    backend.on('PATCH', /\/instagram\/profiles\//, (c) => {
      const id = c.path.split('/').pop();
      lista = lista.map((p) => (p.id === id ? { ...p, ...(c.body as object) } : p));
      return json(lista.find((p) => p.id === id));
    });
    await render();
    await waitFor(() => text().includes('Helena Prado'));
    await selecionar('Mariana Costa', 'Helena Prado');
    await click(byRole('button', /Grupo de acesso/, barra()));
    const dialogo = await waitFor(() => byRole('dialog', /Grupo de acesso de/));
    await waitFor(() => text(dialogo).includes('1 selecionada não tem conta'));
    await click(byRole('button', /Pôr no grupo Cautelosos/, dialogo));
    await waitFor(() => text().includes('Terminado: 1 ok · 1 falhou.'));
    expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-1$/)[0]?.body).toEqual({ policy_group_id: 'grp-1' });
    expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-9$/)).toHaveLength(0);
    expect(text()).toContain(SEM_CONTA_NO_GRUPO);
    await click(byRole('button', /^Fechar$/, dialogo));

    // Tirar do grupo vale para qualquer um, inclusive quem não tem conta.
    await click(byRole('button', /Grupo de acesso/, barra()));
    const outro = await waitFor(() => byRole('dialog', /Grupo de acesso de/));
    await setValue(byRole('combobox', /^Grupo/, outro) as HTMLSelectElement, '');
    await click(byRole('button', /Tirar do grupo/, outro));
    await waitFor(() => text().includes('Terminado: 2 ok'));
    expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-1$/)[1]?.body).toEqual({ policy_group_id: null });
    expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-9$/)).toHaveLength(0);   // já estava sem grupo
  });

  it('bloquear e reativar: PATCH status por persona; quem já está no status não é chamado', async () => {
    rotas();
    backend.on('PATCH', /\/instagram\/profiles\//, (c) => json({ ...MARIANA, ...(c.body as object) }));
    await render();
    await waitFor(() => text().includes('Lucas Almeida'));
    await selecionar('Mariana Costa', 'Lucas Almeida');
    await click(byRole('button', /^Bloquear$/, barra()));
    const dialogo = await waitFor(() => byRole('dialog', /Bloquear 2 personas/));
    await click(byRole('button', /^Bloquear 2$/, dialogo));
    await waitFor(() => text().includes('Terminado: 2 ok'));
    expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-1$/).map((c) => c.body)).toEqual([{ status: 'blocked' }]);
    expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-2$/)).toHaveLength(0);   // já estava bloqueada
    expect(text()).toContain('já estava bloqueada');
    await click(byRole('button', /^Fechar$/, dialogo));

    await click(byRole('button', /^Reativar$/, barra()));
    const reativar = await waitFor(() => byRole('dialog', /Reativar 2 personas/));
    await click(byRole('button', /^Reativar 2$/, reativar));
    await waitFor(() => text().includes('Terminado: 2 ok'));
    expect(backend.callsTo('PATCH', /\/instagram\/profiles\/ig-2$/).map((c) => c.body)).toEqual([{ status: 'active' }]);
  });

  it('apagar: só com "apagar N" digitado; DELETE por persona; a trava do servidor volta no resumo e a lista se relê', async () => {
    rotas();
    backend.on('DELETE', /^\/api\/personas\/ig-1$/, () => new Response(null, { status: 204 }));
    backend.on('DELETE', /^\/api\/personas\/ig-9$/,
               () => apiError(409, 'persona_in_use', 'Esta pessoa está vinculada a um aparelho. Desvincule antes de apagar.'));
    await render();
    await waitFor(() => text().includes('Helena Prado'));
    await selecionar('Mariana Costa', 'Helena Prado');
    await click(byRole('button', /Apagar…/, barra()));
    const dialogo = await waitFor(() => byRole('dialog', /Apagar 2 personas/));
    const apagar = byRole('button', /^Apagar 2/, dialogo);
    expect(apagar.getAttribute('aria-disabled')).toBe('true');
    await click(apagar);
    expect(backend.callsTo('DELETE', /personas/)).toHaveLength(0);
    await setValue(byRole('textbox', /Para confirmar/, dialogo) as HTMLInputElement, 'apagar 3');
    expect(byRole('button', /^Apagar 2/, dialogo).getAttribute('aria-disabled')).toBe('true');
    await setValue(byRole('textbox', /Para confirmar/, dialogo) as HTMLInputElement, 'Apagar 2');
    await waitFor(() => byRole('button', /^Apagar 2/, dialogo).getAttribute('aria-disabled') === null);
    const leiturasAntes = backend.callsTo('GET', /^\/api\/personas$/).length;
    await click(byRole('button', /^Apagar 2/, dialogo));
    await waitFor(() => text().includes('Terminado: 1 ok · 1 falhou.'));
    expect(backend.callsTo('DELETE', /^\/api\/personas\/ig-1$/)).toHaveLength(1);
    expect(backend.callsTo('DELETE', /^\/api\/personas\/ig-9$/)).toHaveLength(1);
    expect(text()).toContain('vinculada a um aparelho');
    await waitFor(() => backend.callsTo('GET', /^\/api\/personas$/).length > leiturasAntes);
  });
});
