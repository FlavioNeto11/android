// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { RunTargetsSuggestion } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeRun, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { CommandPanel } from './CommandPanel';

/**
 * Modo Automático (ADR-050): é o padrão; Planejar/Executar mostram antes "Quem faz e onde" (com o motivo de cada
 * persona, aparelho e servidor), e só a confirmação cria a execução, ecoando os alvos. Alerta de conduta não deixa
 * confirmar. Os modos manuais ficam atrás do controle "Automático | Manual". Prova `simulated` (backend falso).
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const SUGESTAO: RunTargetsSuggestion = {
  modo: 'ia', app_id: 'instagram', app_ids: ['instagram'],
  targets: [{ instance_id: 'android-02', profile_id: 'p-marina', app_id: 'instagram', origem: 'vinculo',
              app_ids: ['instagram'] }],
  escolhidas: [{ profile_id: 'p-marina', nome: 'Marina', motivo: 'católica devota, fala de fé com naturalidade',
                 aderencia: 'alta', instance_id: 'android-02', servidor: 'Central' }],
  descartadas: [{ profile_id: 'p-rafael', nome: 'Rafael', motivo: 'ateu: o pedido exige falar como fiel' }],
  nao_avaliaveis: [{ profile_id: 'p-bia', nome: 'Beatriz', falta: 'crenças não registradas' }],
  alerta_conduta: null, perguntas: [], questions: [], command_sem_destinos: 'responda à tia sobre a missa',
  resumo: 'Marina: a única com fé declarada e sessão pronta.', warnings: [],
};

beforeAll(() => installBrowserStubs());

beforeEach(async () => {
  backend = new FakeBackend();
  backend
    .on('GET', /^\/api\/flows\/match$/, () => json(null))
    .on('POST', /^\/api\/runs\/targets\/suggest$/, () => json(SUGESTAO))
    .on('POST', /^\/api\/runs$/, () => json(makeRun()));
  backend.install();
  window.localStorage.clear();
  useAppStore.setState({ ...initialDataState });
  useAppStore.getState().hydrate(makeSnapshot());
  useUiStore.setState({ selectedIds: [] });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<CommandPanel />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const campo = () => byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement;

describe('Modo Automático', () => {
  it('é o padrão: sem aparelho marcado, Executar mostra quem faz e onde, e só a confirmação cria a execução', async () => {
    expect(byRole('button', /Automático/).getAttribute('aria-pressed')).toBe('true');
    expect(byRole('button', /^Manual/).getAttribute('aria-pressed')).toBe('false');
    await setValue(campo(), 'responda à tia sobre a missa de domingo');
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(text(container)).toContain('católica devota, fala de fé com naturalidade'));
    expect(backend.callsTo('POST', /^\/api\/runs\/targets\/suggest$/)[0]!.body).toEqual({
      command: 'responda à tia sobre a missa de domingo' });
    expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(0);          // nada criado antes de confirmar
    expect(text(container)).toContain('Central');
    expect(text(container)).toContain('1 persona descartada');
    expect(text(container)).toContain('Beatriz');

    await click(byRole('button', /^Confirmar e executar/));
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /^\/api\/runs$/)[0]!.body).toMatchObject({
      command: 'responda à tia sobre a missa de domingo', mode: 'execute', instance_ids: [],
      targets: [{ profile_id: 'p-marina', instance_ids: ['android-02'], app_id: 'instagram' }],
    });
  });

  it('alerta de conduta: nada para confirmar e nenhuma execução', async () => {
    backend.on('POST', /^\/api\/runs\/targets\/suggest$/, () => json({
      ...SUGESTAO, targets: [], escolhidas: [], descartadas: [], nao_avaliaveis: [],
      alerta_conduta: 'o pedido é propaganda eleitoral', resumo: '' }));
    await setValue(campo(), 'comente pedindo voto no candidato X');
    await click(byRole('button', /^Planejar/));
    await waitFor(() => expect(text(container)).toContain('o pedido é propaganda eleitoral'));
    await click(byRole('button', /^Confirmar e planejar/));
    expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(0);
  });

  it('"Escolher manualmente" a partir da sugestão leva ao modo por persona com as sugeridas marcadas', async () => {
    await setValue(campo(), 'responda à tia sobre a missa de domingo');
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(text(container)).toContain('Quem faz e onde'));
    await click(byRole('button', /^Escolher manualmente/));
    expect(byRole('button', /Por persona/).getAttribute('aria-pressed')).toBe('true');
    expect(JSON.parse(window.localStorage.getItem('cda.commandPersonas') ?? '[]')).toEqual(['p-marina']);
  });

  it('persona sem dados abre a própria persona para completar (sem formulário novo aqui)', async () => {
    await setValue(campo(), 'responda à tia sobre a missa de domingo');
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(text(container)).toContain('Beatriz'));
    await click(byRole('button', /Beatriz/));
    expect(useUiStore.getState().view).toBe('personas');
  });
});
