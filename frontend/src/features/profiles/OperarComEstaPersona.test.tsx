// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useUiStore } from '../../store/ui';
import { APPS, makeInstance } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { lerRascunho, limparRascunho, rascunhoDaPersona } from '../operacao/criar';
import { OperacaoPage } from '../operacao/OperacaoPage';
import { PersonaHeader } from './PersonaHeader';
import type { Pessoa } from './pessoa';

/**
 * 31.188: "Operar com esta persona", o atalho da tela Persona para o formulário de nova operação (31.176), com a persona já marcada.
 * Prova `simulated`.
 */

const pessoa = (over: Record<string, unknown> = {}) => ({
  id: 'p1', username: 'ana.exemplo', display_name: 'Ana', persona_name: 'Ana', status: 'active', instance_id: 'android-02',
  session: { status: 'session_ready', instance_id: 'android-02', observed_username: null, verified_at: null, detail: null, stale: false },
  has_avatar: false, ...over,
}) as unknown as Pessoa;

describe('rascunhoDaPersona', () => {
  it('só a persona marcada; o resto vazio; origem "persona"', () => {
    expect(rascunhoDaPersona('p7')).toEqual({
      command: '', appId: '', acaoFinal: 'preparar', assunto: '', maxUsd: '', fontes: '', username: '', legenda: '', profileIds: ['p7'], parametrosNaoCopiados: [], origem: 'persona',
    });
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  limparRascunho();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'personas', segmentos: [], query: {} } });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  limparRascunho();
  await act(async () => root.unmount());
  container.remove();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'painel', segmentos: [] } });
});

describe('o botão no cabeçalho da persona', () => {
  it('abre a nova operação com a persona marcada', async () => {
    backend.on('GET', /^\/api\/apps$/, () => json([{ ...APPS[0]!, id: 'instagram', name: 'Instagram' }]));
    backend.on('GET', /^\/api\/instances$/, () => json([makeInstance(2)]));
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([{ id: 'p1', persona_name: 'Ana' }, { id: 'p2', persona_name: 'Bia' }]));
    backend.on('GET', /^\/api\/instagram\/profiles\/p1\/accounts$/, () => json([]));
    await act(async () => root.render(<PersonaHeader profile={pessoa()} onBack={() => {}} irPara={() => {}} />));
    await click(byRole('button', /^Operar com esta persona$/, container));
    expect(useUiStore.getState().rota).toMatchObject({ tela: 'operacoes', segmentos: ['nova'] });
    expect(lerRascunho()?.profileIds).toEqual(['p1']);

    // a tela de destino: persona marcada, as outras não, e o aviso de onde veio
    await act(async () => { root.unmount(); root = createRoot(container); root.render(<><OperacaoPage /><ConfirmHost /></>); });
    await waitFor(() => expect(container.querySelector('form')).not.toBeNull());
    expect((container.querySelector('li[data-persona="p1"] input[type="checkbox"]') as HTMLInputElement).checked).toBe(true);
    expect((container.querySelector('li[data-persona="p2"] input[type="checkbox"]') as HTMLInputElement).checked).toBe(false);
    expect(text(container)).toContain('Persona escolhida na tela Persona');
    expect(text(container)).not.toContain('Copiado de uma operação anterior');
    expect(text(container)).toContain('Personas (1 escolhida de 2');
    await waitFor(() => expect(backend.callsTo('GET', /profiles\/p1\/accounts/)).toHaveLength(1));
    expect(lerRascunho()).toBeNull();                                                      // vale para uma abertura só
  });

  it('persona que não está ativa: o botão fica desabilitado com o motivo e nada é levado', async () => {
    await act(async () => root.render(<PersonaHeader profile={pessoa({ status: 'blocked' })} onBack={() => {}} irPara={() => {}} />));
    const b = byRole('button', /^Operar com esta persona — indisponível: A persona não está ativa/, container);
    expect(b.getAttribute('aria-disabled')).toBe('true');
    await click(b);
    expect(lerRascunho()).toBeNull();
    expect(useUiStore.getState().rota.tela).toBe('personas');
  });
});
