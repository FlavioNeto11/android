// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { OperationalContext } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { OperationalContextCard } from './OperationalContextCard';

// Auditoria UX 27/09, P2.9: o cartão mostrava `device.state`, `readiness.phase`, `stream.status`,
// `connectivity.state` e `session.status` crus, em inglês; o erro saía num <p role=alert> sem estilo.

function contexto(over: Partial<OperationalContext> = {}): OperationalContext {
  return {
    instance_id: 'android-06',
    server: { id: 'worker-lan-01', name: 'Notebook da LAN', local: false, connected: true, state: 'online',
              transport_state: null, verbs: [] },
    device: { state: 'online', state_detail: null, kind: 'emulator', supported_verbs: [],
              automation: { state: 'ready', detail: null }, attention: null },
    stream: { status: 'capture_error', detail: 'a captura falhou 3x seguidas', last_frame_at: null, frame_age_s: null,
              last_capture_error: 'timeout', last_capture_error_at: null, consecutive_capture_failures: 3 },
    connectivity: { state: 'degraded', route: true, dns: true, tcp_443: false, validated: false,
                    checked_at: '2026-09-27T10:00:00Z', detail: 'porta 443 não responde' },
    readiness: { phase: 'android_responsive', detail: 'framework de automação ainda não subiu', since: null },
    apps: [{ app_id: 'instagram', name: 'Instagram', package: 'com.instagram.android', presence: 'installed', state: 'ready',
             installed_version_name: '448.0', installed_version_code: 448, verified_at: null, pending_op: null, detail: null,
             promoted_release_id: 'rel-448', promoted_version_name: '448.0', promoted_version_code: 448 }],
    profiles: [{ profile_id: 'ig-1', username: 'mariana', display_name: 'Mariana', persona_id: null, persona_name: null,
                 credential_configured: true, credential_status: 'active',
                 session: { status: 'auth_required', instance_id: 'android-06', observed_username: null, verified_at: null,
                            detail: null, stale: false },
                 app_on_device: null, session_actions: null }],
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
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(): Promise<void> {
  await act(async () => { root.render(<OperationalContextCard instanceId="android-06" />); });
}

describe('Contexto operacional', () => {
  it('cada camada sai com rótulo em português; nenhum enum do contrato chega à tela', async () => {
    backend.on('GET', /operational-context$/, () => json(contexto()));
    await render();
    await waitFor(() => expect(text()).toContain('Notebook da LAN'));
    expect(text()).toContain('Online');                       // device.state
    expect(text()).toContain('Android respondendo');          // readiness.phase
    expect(text()).toContain('Falha na captura');             // stream.status
    expect(text()).toContain('Internet instável');            // connectivity.state
    expect(text()).toContain('Precisa entrar');               // session.status
    for (const cru of ['android_responsive', 'capture_error', 'degraded', 'auth_required']) {
      expect(text()).not.toContain(cru);
    }
    // O detalhe que o backend explica fica junto do selo.
    expect(text()).toContain('porta 443 não responde');
  });

  it('valor fora do contrato não quebra o cartão: aparece como veio, em tom neutro', async () => {
    const ctx = contexto();
    (ctx.stream as { status: string }).status = 'estado_novo';
    backend.on('GET', /operational-context$/, () => json(ctx));
    await render();
    await waitFor(() => expect(text()).toContain('estado_novo'));
  });

  it('erro de leitura vira faixa com o motivo, e "Reler" tenta de novo', async () => {
    backend.on('GET', /operational-context$/, () => apiError(503, 'unavailable', 'worker fora do ar'));
    await render();
    await waitFor(() => expect(text()).toContain('Não foi possível ler o contexto'));
    expect(text()).toContain('worker fora do ar');
    expect(byRole('alert', /Não foi possível ler o contexto/)).toBeTruthy();

    backend.on('GET', /operational-context$/, () => json(contexto()));
    await click(byRole('button', /Reler/));
    await waitFor(() => expect(text()).toContain('Notebook da LAN'));
    expect(text()).not.toContain('Não foi possível ler o contexto');
  });
});
