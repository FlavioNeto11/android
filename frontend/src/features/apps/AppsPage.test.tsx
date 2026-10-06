// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { AppDetail, AppOverview } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { AppsPage } from './AppsPage';

// Auditoria UX 27/09, P1.3 e P2.11: falha de carga virava "Nenhum aplicativo cadastrado" (e o detalhe ficava em
// esqueleto para sempre). P2.9: os estados vinham crus em inglês (`ready`, `downgrade_refused`, `session_ready`).

function visao(over: Partial<AppOverview> = {}): AppOverview {
  return {
    app_id: 'instagram', name: 'Instagram', package: 'com.instagram.android', has_catalog: true, automated_login: true,
    accounts: 2, accounts_ready: 1, devices: { installed: 3 }, default_on_devices: 3, runs: 4, runs_completed: 3,
    last_run_at: null, ai_usd: 0.12, recipes: { active: 2 }, flows: 1, releases: 2, days: 7,
    ...over,
  };
}

function detalhe(): AppDetail {
  return {
    app_id: 'instagram', name: 'Instagram', package: 'com.instagram.android', activity: null, has_catalog: true,
    automated_login: true,
    accounts: [{ id: 'acc-1', profile_id: 'ig-1', username: 'luciana', handle: 'luciana', status: 'active',
                 session_status: 'session_ready', session_verified_at: null }],
    devices: [
      { instance_id: 'android-01', state: 'ready', observed_version_name: '448.0', verified_at: null, drift_kind: null },
      { instance_id: 'android-02', state: 'version_drift', observed_version_name: '449.0', verified_at: null,
        drift_kind: 'downgrade_refused' },
    ],
    runs: [], ai_usd_by_day: [], steps: [], recent_failures: [],
    recipes: [{ id: 7, step_key: 'abrir_dm', app_version: '448.0', status: 'quarantined', replay_ok: 3, replay_fail: 1,
                created_at: '2026-09-20T10:00:00Z', last_used_at: null }],
    flows: [{ id: 'fl-1', name: 'Enviar oi', command_template: 'enviar oi', uses: 5, status: 'active' }],
    days: 30,
  };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  // A aba inicial é a Loja; ela carrega a vitrine sozinha e não é o assunto destes testes.
  backend.on('GET', /app-store/, () => json([]));
  useAppStore.setState({ ...initialDataState });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function abrirPorApp(): Promise<void> {
  await act(async () => { root.render(<AppsPage />); });
  await click(byRole('tab', /Por app/));
}

describe('Aplicativos — Por app', () => {
  it('API caída mostra o erro com "Tentar de novo", não "Nenhum aplicativo cadastrado"', async () => {
    backend.on('GET', /apps-overview/, () => apiError(503, 'unavailable', 'banco indisponível'));
    await abrirPorApp();
    await waitFor(() => expect(text()).toContain('Não foi possível carregar os aplicativos'));
    expect(text()).toContain('banco indisponível');
    expect(text()).not.toContain('Nenhum aplicativo cadastrado');

    // A API voltou: "Tentar de novo" relê e a lista aparece.
    backend.on('GET', /apps-overview/, () => json([visao()]));
    await click(byRole('button', /Tentar de novo/));
    await waitFor(() => expect(text()).toContain('com.instagram.android'));
    expect(text()).not.toContain('Não foi possível carregar');
  });

  it('lista vazia de verdade continua sendo "Nenhum aplicativo cadastrado"', async () => {
    backend.on('GET', /apps-overview/, () => json([]));
    await abrirPorApp();
    await waitFor(() => expect(text()).toContain('Nenhum aplicativo cadastrado'));
    expect(text()).not.toContain('Tentar de novo');
  });

  it('detalhe do app: erro sai do esqueleto e oferece "Tentar de novo"; ao voltar, os estados vêm traduzidos', async () => {
    backend.on('GET', /apps-overview/, () => json([visao()]));
    backend.on('GET', /\/apps\/instagram\/overview/, () => apiError(500, 'internal', 'consulta estourou o tempo'));
    await abrirPorApp();
    await waitFor(() => expect(text()).toContain('com.instagram.android'));
    await click(byRole('button', /Abrir Instagram/));
    await waitFor(() => expect(text()).toContain('Não foi possível carregar o aplicativo'));
    expect(text()).toContain('consulta estourou o tempo');

    backend.on('GET', /\/apps\/instagram\/overview/, () => json(detalhe()));
    await click(byRole('button', /Tentar de novo/));
    await waitFor(() => expect(text()).toContain('Instalado e conferido'));
    // Cada enum vira rótulo em português; nenhum valor cru do contrato chega à tela.
    expect(text()).toContain('Versão diferente da pedida');
    expect(text()).toContain('Downgrade recusado');
    expect(text()).toContain('Conectado');
    expect(text()).toContain('Ativo');
    expect(text()).toContain('Quarentena');
    for (const cru of ['session_ready', 'version_drift', 'downgrade_refused', 'quarantined']) {
      expect(text()).not.toContain(cru);
    }
  });
});

// Revisão de UX, tarefa 06: "Proxy (legado)" deixou de ser aba de primeiro nível, e o vocabulário da loja ganhou
// definição acessível por teclado.
describe('Aplicativos — Proxy antigo e vocabulário da loja', () => {
  afterEach(() => {
    useUiStore.getState().navegar({ tela: 'painel' }, 'replace');
  });

  it('as abas são Loja, Por app, Versões e instalação e Rede (sem "Proxy (legado)")', async () => {
    useUiStore.getState().navegar({ tela: 'aplicativos' }, 'replace');
    await act(async () => { root.render(<AppsPage />); });
    const nomes = Array.from(container.querySelectorAll('[role="tab"]')).map((t) => (t.textContent ?? '').trim());
    expect(nomes).toEqual(['Loja', 'Por app', 'Versões e instalação', 'Rede']);
  });

  it('a legenda explica "promovida", "pendente" e "fora do catálogo", e abre por teclado (<summary>)', async () => {
    useUiStore.getState().navegar({ tela: 'aplicativos' }, 'replace');
    await act(async () => { root.render(<AppsPage />); });
    const resumo = Array.from(container.querySelectorAll('summary')).find((x) => /significam os selos/.test(x.textContent ?? ''));
    expect(resumo).toBeTruthy();
    const corpo = text(container);
    for (const termo of ['Versão promovida', 'Sem versão promovida', 'Pendente', 'Fora do catálogo', 'Atualização para N']) {
      expect(corpo).toContain(termo);
    }
  });

  it('o link antigo #/aplicativos?aba=proxy abre a Rede com o trecho "descontinuado" já aberto', async () => {
    useUiStore.getState().navegar({ tela: 'aplicativos', query: { aba: 'proxy' } }, 'replace');
    await act(async () => { root.render(<AppsPage />); });
    expect(byRole('tab', /^Rede/).getAttribute('aria-selected')).toBe('true');
    const trecho = Array.from(container.querySelectorAll('details')).find((d) => /Proxy global \(antigo\)/.test(d.textContent ?? ''));
    expect(trecho).toBeTruthy();
    expect((trecho as HTMLDetailsElement).open).toBe(true);
    expect(text(trecho as HTMLElement)).toContain('descontinuado');
  });

  it('o Termo da loja abre a definição por foco do teclado e fecha com Esc', async () => {
    const { Termo } = await import('./glossario');
    await act(async () => { root.render(<Termo termo="promovida">promovida</Termo>); });
    const gatilho = container.querySelector('[tabindex="0"]') as HTMLElement;
    await act(async () => { gatilho.focus(); });
    await waitFor(() => expect(document.querySelector('[role="tooltip"]')?.textContent ?? '').toContain('escolhida como a oficial'));
    await act(async () => { gatilho.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
  });
});
