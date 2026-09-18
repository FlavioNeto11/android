// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { AppRelease } from '../../api/types';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { ReleasesPage } from './ReleasesPage';

function release(over: Partial<AppRelease> = {}): AppRelease {
  return {
    id: 'rel-1', package_name: 'com.instagram.android', version_name: '300.0.0.29.110', version_code: 300,
    artifact_type: 'split_set', signature_sha256: 'ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12',
    min_sdk: 28, target_sdk: 34, supported_abis: ['arm64-v8a'], source_type: 'inbox',
    source_reference: null, imported_at: '2026-09-17T10:00:00Z', status: 'installable', detail: null,
    files: [{ role: 'base', split_name: null, file_name: 'base.apk', sha256: 'aa', size_bytes: 1024 }],
    devices: ['android-02'],
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
  backend.on('GET', /app-state/, () => json([]));
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
    root.render(<ReleasesPage />);
  });
  await waitFor(() => text().includes('Aplicativos'));
}

it('explica que nada é baixado sozinho quando não há release nenhuma', async () => {
  backend.on('GET', /releases/, () => json([]));
  await render();
  await waitFor(() => text().includes('Nenhum aplicativo importado'));
  expect(text()).toContain('apks/inbox');
  expect(byRole('button', /Importar da pasta/i)).toBeTruthy();
});

it('mostra o que foi lido do arquivo e o que está instalado no aparelho', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /app-state/, () => json([{
    instance_id: 'android-02', package_name: 'com.instagram.android', desired_release_id: 'rel-1',
    installed_release_id: 'rel-1', observed_version_name: '300.0.0.29.110', observed_version_code: 300,
    observed_splits: ['base'], first_install_time: null, last_update_time: null, state: 'ready',
    pending_op: null, verified_at: '2026-09-17T11:00:00Z', drift_kind: null, detail: null,
  }]));
  await render();
  await waitFor(() => text().includes('com.instagram.android 300.0.0.29.110'));
  expect(text()).toContain('versionCode 300');
  expect(text()).toContain('arm64-v8a');
  expect(text()).toContain('base.apk');
  expect(text()).toContain('android-02');
});

it('importar chama a pasta de entrada e recarrega a lista', async () => {
  backend.on('GET', /releases/, () => json([]));
  backend.on('POST', /releases\/import/, () => json({ imported: [{ id: 'rel-1' }] }, 202));
  await render();
  await click(byRole('button', /Importar da pasta/i));
  await waitFor(() => backend.callsTo('POST', /releases\/import/).length === 1);
  await waitFor(() => backend.callsTo('GET', /releases/).length >= 2);
});
