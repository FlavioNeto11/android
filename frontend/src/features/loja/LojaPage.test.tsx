// @vitest-environment jsdom
import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { AppRelease, AppStoreEntry, DeviceAppState } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { LojaPage } from './LojaPage';
import { ProxyPage } from './ProxyPage';

function entrada(over: Partial<AppStoreEntry> = {}): AppStoreEntry {
  return {
    app_id: 'instagram', name: 'Instagram', package: 'com.instagram.android', category: 'social', builtin: false,
    has_catalog: true, label: 'Instagram', icon_release_id: null,
    promoted: { id: 'rel-448', version_name: '448.0', version_code: 448, status: 'installable', channel: 'promoted' },
    latest: { id: 'rel-448', version_name: '448.0', version_code: 448, status: 'installable', channel: 'promoted' },
    releases: 2, devices_with_app: 3,
    by_version: [{ release_id: 'rel-448', version_name: '448.0', version_code: 448, channel: 'promoted', devices: 1 },
                 { release_id: 'rel-447', version_name: '447.0', version_code: 447, channel: 'rolled_back', devices: 2 }],
    other_version: 0, outdated: 2, pending: 0, installing: 0, failed: 0, attention: [],
    ...over,
  };
}

function release(over: Partial<AppRelease> = {}): AppRelease {
  return {
    id: 'rel-448', package_name: 'com.instagram.android', version_name: '448.0', version_code: 448,
    artifact_type: 'single', signature_sha256: 'ab'.repeat(32), min_sdk: 28, target_sdk: 34, supported_abis: [],
    source_type: 'store', source_reference: null, imported_at: '2026-09-26T10:00:00Z', status: 'installable',
    detail: null, channel: 'promoted', channel_at: null, channel_detail: null, canary_instance_id: 'android-01',
    validations: [], files: [], devices: [], serves: [], label: 'Instagram', has_icon: false,
    ...over,
  };
}

function estado(iid: string, rid: string, codigo: number): DeviceAppState {
  return {
    instance_id: iid, package_name: 'com.instagram.android', desired_release_id: rid, installed_release_id: rid,
    observed_version_name: `${codigo}.0`, observed_version_code: codigo, observed_splits: ['base'],
    first_install_time: null, last_update_time: null, state: 'ready', pending_op: null,
    verified_at: new Date().toISOString(), drift_kind: null, detail: null, expected_splits: [],
    previous_release_id: null, last_operation: 'install',
  };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /app-store/, () => json([
    entrada(),
    entrada({ app_id: 'outlook', name: 'Outlook', package: 'com.microsoft.office.outlook', category: 'email',
              promoted: null, latest: null, releases: 0, devices_with_app: 0, by_version: [], outdated: 0,
              attention: [] }),
  ]));
  backend.on('GET', /releases/, () => json([release(), release({ id: 'rel-447', version_name: '447.0', version_code: 447,
                                                                 channel: 'rolled_back' })]));
  // Registrada DEPOIS de `/releases/`: no FakeBackend a última rota registrada vence.
  backend.on('GET', /releases\/rel-448\/targets/, () => json({ release_id: 'rel-448', package: 'com.instagram.android',
    targets: ['android-01', 'android-02', 'android-03'].map((id) => ({
      id, worker_id: null, state: 'online', compatible: true, reason: null, app_state: 'ready',
      installed_release_id: null, installed_version_name: null, already: id === 'android-01' })) }));
  backend.on('GET', /app-state/, () => json([estado('android-01', 'rel-448', 448), estado('android-02', 'rel-447', 447),
                                              estado('android-03', 'rel-447', 447)]));
  backend.on('GET', /instances/, () => json(['android-01', 'android-02', 'android-03', 'android-09']
    .map((id) => ({ id, kind: id === 'android-09' ? 'store' : 'emulator', state: 'online', worker_id: null }))));
  backend.on('GET', /\/store$/, () => json({ configured: true, state: 'online', instance_id: 'android-09',
    package: 'com.instagram.android', store_version_code: 448, store_version_name: '448.0',
    catalog_version_code: 448, update_available: false, fleet_target_release_id: 'rel-448',
    fleet_target_version_code: 448 }));
  useAppStore.setState({ appState: {}, instances: {}, instanceOrder: [] });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(el: ReactElement): Promise<void> {
  await act(async () => { root.render(<>{el}<ConfirmHost /></>); });
}

it('a vitrine mostra versão promovida, aparelhos e a atualização pendente, e filtra por categoria', async () => {
  await render(<LojaPage />);
  await waitFor(() => text().includes('Outlook'));
  expect(text()).toContain('448.0');
  expect(text()).toContain('atualização para 2');
  expect(text()).toContain('sem versão promovida');
  await click(byRole('radio', /^E-mail$/));
  await waitFor(() => !text().includes('com.instagram.android'));
  expect(text()).toContain('Outlook');
});

it('cadastrar app novo manda nome, pacote e categoria', async () => {
  backend.on('POST', /\/apps$/, () => json({ id: 'tiktok', name: 'TikTok', package: 'com.zhiliaoapp.musically',
    activity: null, apk_path: null, nav_hints: null, known_selectors: null, builtin: false, category: 'social' }));
  await render(<LojaPage />);
  await waitFor(() => text().includes('Outlook'));
  await click(byRole('button', /Novo aplicativo/));
  const dialogo = byRole('dialog', /Novo aplicativo/);
  await setValue(dialogo.querySelector('input[placeholder="ex.: Outlook"]') as HTMLInputElement, 'TikTok');
  await setValue(dialogo.querySelector('input[placeholder="com.microsoft.office.outlook"]') as HTMLInputElement,
                 'com.zhiliaoapp.musically');
  await setValue(dialogo.querySelector('select') as HTMLSelectElement, 'social');
  await click(byRole('button', /^Cadastrar$/, dialogo));
  await waitFor(() => backend.callsTo('POST', /\/apps$/).length === 1);
  expect(backend.callsTo('POST', /\/apps$/)[0]!.body).toMatchObject(
    { name: 'TikTok', package: 'com.zhiliaoapp.musically', category: 'social' });
});

it('atualizar os atrasados: prévia primeiro, e confirmar manda os MESMOS aparelhos da prévia', async () => {
  backend.on('POST', /lifecycle/, (call) => {
    const b = call.body as { dry_run?: boolean; instance_ids?: string[] };
    return json({ accepted: !b.dry_run, dry_run: !!b.dry_run, devices: (b.instance_ids ?? []).map((id) => ({
      id, outcome: b.dry_run ? 'would_start' : 'started', reason: 'ligado' })) });
  });
  await render(<LojaPage />);
  await waitFor(() => text().includes('Outlook'));
  await click(byRole('button', /Abrir Instagram/));
  await waitFor(() => text().includes('Atualização disponível para 2 aparelho(s)'));
  await click(byRole('button', /Atualizar para 448.0/));
  const dialogo = await waitFor(() => byRole('dialog', /Distribuir Instagram 448.0/));
  // Sem prévia não há confirmação: confirmar o que não se viu é o que o diálogo existe para evitar.
  expect(byRole('button', /Confirmar/, dialogo).getAttribute('aria-disabled')).toBe('true');
  await click(byRole('button', /Ver prévia/, dialogo));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 1);
  expect(backend.callsTo('POST', /lifecycle/)[0]!.body).toMatchObject(
    { verb: 'distribute', dry_run: true, instance_ids: ['android-02', 'android-03'] });
  await waitFor(() => text(dialogo).includes('instala agora'));
  await click(byRole('button', /Confirmar para 2/, dialogo));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 2);
  const final = backend.callsTo('POST', /lifecycle/)[1]!.body as Record<string, unknown>;
  expect(final).toMatchObject({ verb: 'distribute', instance_ids: ['android-02', 'android-03'] });
  expect(final.dry_run).toBeUndefined();
});

it('proxy: exige prévia e manda o proxy e os aparelhos escolhidos', async () => {
  backend.on('GET', /proxies/, () => json({
    profiles: [{ id: 'proxy-escritorio', name: 'Escritório', host: '10.0.0.5', port: 3128, created_at: '', created_by: null, devices: 0 }],
    devices: [{ instance_id: 'android-01', worker_id: null, device_state: 'online', managed: false,
                desired_proxy_id: null, observed_value: null, state: null, detail: null, verified_at: null }],
  }));
  backend.on('POST', /proxies\/apply/, (call) => {
    const b = call.body as { dry_run?: boolean };
    return json({ accepted: !b.dry_run, dry_run: !!b.dry_run,
                  devices: [{ id: 'android-01', outcome: b.dry_run ? 'would_start' : 'started', reason: 'ligado' }] });
  });
  await render(<ProxyPage />);
  await waitFor(() => text().includes('Escritório'));
  await setValue(byRole('combobox', /Proxy a aplicar/) as HTMLSelectElement, 'proxy-escritorio');
  await click(byRole('checkbox', /Selecionar android-01/));
  expect(byRole('button', /^Aplicar/).getAttribute('aria-disabled')).toBe('true');
  await click(byRole('button', /Ver prévia/));
  await waitFor(() => text().includes('aplica agora'));
  await click(byRole('button', /^Aplicar/));
  await waitFor(() => backend.callsTo('POST', /proxies\/apply/).length === 2);
  expect(backend.callsTo('POST', /proxies\/apply/)[1]!.body).toEqual(
    { proxy_id: 'proxy-escritorio', instance_ids: ['android-01'] });
});

it('loja com a API caída mostra o erro com "Tentar de novo", não "Nenhum aplicativo cadastrado" (P1.3)', async () => {
  backend.on('GET', /app-store/, () => apiError(503, 'unavailable', 'banco indisponível'));
  await render(<LojaPage />);
  await waitFor(() => text().includes('Não foi possível carregar a loja'));
  expect(text()).toContain('banco indisponível');
  expect(text()).not.toContain('Nenhum aplicativo cadastrado');

  backend.on('GET', /app-store/, () => json([entrada()]));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => text().includes('com.instagram.android'));
  expect(text()).not.toContain('Não foi possível carregar a loja');
});
