// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PersonaDTO } from '../../api/types';
import { PersonaTarget } from '../command/PersonaTarget';
import { SeloDeTeste } from '../../components/SeloDeTeste';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { ehTeste, filtrarPersonas, FILTRO_VAZIO, LIMPAR_FILTROS, lerFiltroPersonas, queryDoFiltro } from './filtroPersonas';
import { ProfilesPage } from './ProfilesPage';
import { MarcaDeTeste } from './MarcaDeTeste';
import { VincularForm } from './VincularForm';

/**
 * 31.315 (adendo v1.139, `personas.teste`): a persona de teste leva um selo na lista, na ficha e nos seletores, fica fora da
 * lista até a pessoa pedir e nasce marcada quando se marca ao criar. Prova `simulated`.
 */

function pessoa(over: Partial<PersonaDTO> = {}): PersonaDTO {
  return {
    id: 'ig-1', name: 'Luciana Bastos', summary: null, username: 'luciana.bastos73519', display_name: 'Luciana Bastos',
    first_name: 'Luciana', last_name: 'Bastos', birth_date: null, email: null, persona_id: 'ig-1', persona_name: 'Luciana Bastos',
    status: 'active', instance_id: null, locality: null, offline_policy: 'wait',
    credential: { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null, updated_at: null, last_used_at: null },
    session: { status: 'unknown', instance_id: null, observed_username: null, verified_at: null, detail: null, stale: false },
    last_verified_at: null, last_activity_at: null, accounts_count: 0,
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z', ...over,
  };
}

const REAL = pessoa();
const TESTE = pessoa({ id: 'ig-t', name: 'TESTE Portal 31.283 (nao usar)', username: null, display_name: 'TESTE Portal 31.283 (nao usar)',
                       persona_id: 'ig-t', persona_name: 'TESTE Portal 31.283 (nao usar)', teste: true });

describe('regras puras', () => {
  it('só `teste === true` conta; ausente (backend anterior) e falso valem como persona comum', () => {
    expect(ehTeste(TESTE)).toBe(true);
    expect(ehTeste(REAL)).toBe(false);
    expect(ehTeste(pessoa({ teste: false }))).toBe(false);
  });

  it('por padrão esconde as de teste; `testes=1` as traz; o filtro é do link e sai no limpar', () => {
    const todas = [REAL, TESTE];
    expect(filtrarPersonas(todas, FILTRO_VAZIO).map((p) => p.id)).toEqual(['ig-1']);
    expect(filtrarPersonas(todas, { ...FILTRO_VAZIO, testes: true }).map((p) => p.id)).toEqual(['ig-1', 'ig-t']);
    expect(lerFiltroPersonas({ testes: '1' }).testes).toBe(true);
    for (const v of ['0', 'true', '']) expect(lerFiltroPersonas({ testes: v }).testes).toBe(false);
    expect(queryDoFiltro({ testes: true }).testes).toBe('1');
    expect(queryDoFiltro({ testes: false }).testes).toBeUndefined();
    expect('testes' in LIMPAR_FILTROS).toBe(true);
  });

  it('o selo só aparece com a marca verdadeira', async () => {
    const container = document.createElement('div');
    document.body.append(container);
    const root = createRoot(container);
    for (const v of [false, undefined, null]) {
      await act(async () => root.render(<SeloDeTeste teste={v} />));
      expect(container.textContent).toBe('');
    }
    await act(async () => root.render(<SeloDeTeste teste />));
    expect(container.textContent).toContain('teste');
    await act(async () => root.unmount());
    container.remove();
  });
});

describe('Personas', () => {
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
      ...initialDataState, hydrated: true, hydrateCount: 1,
      instances: Object.fromEntries(snap.instances.map((i) => [i.id, i])), instanceOrder: snap.instances.map((i) => i.id),
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

  const render = async () => {
    await act(async () => { root.render(<ProfilesPage />); });
    await waitFor(() => text().includes('Cada persona é uma pessoa'));
  };
  const cartoes = () => Array.from(container.querySelectorAll('h2')).map((h) => h.textContent ?? '');

  it('a lista esconde a de teste por padrão e o controle diz quantas há; mostrar traz o cartão com o selo', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([REAL, TESTE]));
    await render();
    await waitFor(() => text().includes('Luciana Bastos'));
    expect(text()).not.toContain('TESTE Portal 31.283');
    const filtro = byRole('combobox', /Personas de teste/) as HTMLSelectElement;
    expect([...filtro.options].map((o) => o.textContent)).toEqual(['Esconder as de teste', 'Mostrar as de teste (1)']);
    await setValue(filtro, '1');
    await waitFor(() => text().includes('TESTE Portal 31.283'));
    expect(window.location.hash).toMatch(/[?&]testes=1/);
    const cartao = cartoes().find((t) => t.includes('TESTE Portal'))!;
    expect(cartao).toContain('teste');
    expect(cartoes().find((t) => t.includes('Luciana'))).not.toContain('teste');
  });

  it('sem nenhuma de teste o controle nem aparece; só de teste: a lista diz que estão escondidas', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([REAL]));
    await render();
    await waitFor(() => text().includes('Luciana Bastos'));
    expect(container.querySelector('select[aria-label="Personas de teste"]')).toBeNull();
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /^\/api\/personas$/, () => json([TESTE]));
    await render();
    await waitFor(() => text().includes('Só há 1 persona de teste, escondidas por padrão'));
  });

  it('a ficha da persona de teste leva o selo, mesmo com a lista escondendo', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([REAL, TESTE]));
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    useUiStore.getState().openPersona('ig-t', 'contas');
    await act(async () => { root.render(<ProfilesPage />); });
    await waitFor(() => !!container.querySelector('header h1'));
    const cabecalho = container.querySelector('header') as HTMLElement;
    expect(text(cabecalho)).toContain('TESTE Portal 31.283');
    expect(text(cabecalho)).toContain('teste');
  });

  it('criar manual: marcar "Persona de teste" manda teste:true; sem marcar, o corpo não muda', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => apiError(503, 'unavailable', 'fora'));
    backend.on('POST', /^\/api\/personas$/, (c) => json(pessoa({ ...(c.body as object), id: 'ig-9', username: null }), 201));
    await render();
    await click(byRole('button', /Nova persona manual/i));
    await waitFor(() => text().includes('A persona nasce sem conta e sem aparelho'));
    await setValue(byRole('textbox', /^Nome/i) as HTMLInputElement, 'Persona Piloto');
    await click(byRole('checkbox', /Persona de teste/));
    await click(byRole('button', /Criar persona/i));
    await waitFor(() => backend.callsTo('POST', /^\/api\/personas$/).length === 1);
    expect(backend.callsTo('POST', /^\/api\/personas$/)[0]?.body).toEqual({ name: 'Persona Piloto', birth_date: null, gender: null, summary: null, teste: true });
    await waitFor(() => /^#\/personas\/./.test(window.location.hash));
  });

  it('os seletores mostram a persona de teste como tal: o chip do Comando e a lista do vínculo', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([REAL, TESTE]));
    await act(async () => {
      root.render(<PersonaTarget pessoas={[REAL, TESTE]} selecionadas={[]} politica="one" estreitar={[]} onPessoas={() => {}} onPolitica={() => {}} onEstreitar={() => {}} />);
    });
    const chipDeTeste = byRole('button', /TESTE Portal 31\.283 \(nao usar\) \(teste\)/);
    expect(text(chipDeTeste)).toContain('teste');
    expect(byRole('button', /^Luciana Bastos \(@luciana\.bastos73519\)/).getAttribute('aria-label')).not.toContain('(teste)');
    await act(async () => { root.render(<VincularForm onVinculado={() => {}} />); });
    await waitFor(() => !!container.querySelector('select option[value="ig-t"]'));
    expect(container.querySelector('select option[value="ig-t"]')?.textContent).toContain(' · teste');
    expect(container.querySelector('select option[value="ig-1"]')?.textContent).not.toContain('teste');
  });
});

describe('Marca de teste na ficha', () => {
  let root: Root;
  let container: HTMLElement;
  let backend: FakeBackend;

  beforeAll(() => installBrowserStubs());
  beforeEach(() => {
    backend = new FakeBackend();
    backend.install();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it('marca e desmarca pelo PATCH com o booleano explícito e recarrega a ficha', async () => {
    backend.on('PATCH', /^\/api\/personas\/ig-1$/, (c) => json(pessoa({ ...(c.body as object) })));
    let mudou = 0;
    const onChanged = async () => { mudou += 1; };
    await act(async () => root.render(<MarcaDeTeste profile={REAL} onChanged={onChanged} />));
    expect(text()).toContain('é uma persona comum');
    await click(byRole('button', /Marcar como de teste/));
    await waitFor(() => backend.callsTo('PATCH', /^\/api\/personas\/ig-1$/).length === 1);
    expect(backend.callsTo('PATCH', /^\/api\/personas\/ig-1$/)[0]?.body).toEqual({ teste: true });
    await waitFor(() => mudou === 1);
    await act(async () => root.render(<MarcaDeTeste profile={pessoa({ teste: true })} onChanged={onChanged} />));
    expect(text()).toContain('está marcada como de teste');
    await click(byRole('button', /Desmarcar como de teste/));
    await waitFor(() => backend.callsTo('PATCH', /^\/api\/personas\/ig-1$/).length === 2);
    expect(backend.callsTo('PATCH', /^\/api\/personas\/ig-1$/)[1]?.body).toEqual({ teste: false });
  });
});
