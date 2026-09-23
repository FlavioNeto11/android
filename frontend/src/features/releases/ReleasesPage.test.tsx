// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { AppRelease, DeviceAppState, StoreStatus } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { ReleasesPage } from './ReleasesPage';

function release(over: Partial<AppRelease> = {}): AppRelease {
  return {
    id: 'rel-1', package_name: 'com.instagram.android', version_name: '300.0.0.29.110', version_code: 300,
    artifact_type: 'split_set', signature_sha256: 'ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12',
    min_sdk: 28, target_sdk: 34, supported_abis: ['arm64-v8a'], source_type: 'inbox',
    source_reference: null, imported_at: '2026-09-17T10:00:00Z', status: 'installable', detail: null,
    channel: 'candidate', channel_at: null, channel_detail: null, canary_instance_id: null, validations: [],
    files: [{ role: 'base', split_name: null, file_name: 'base.apk', sha256: 'aa', size_bytes: 1024 }],
    devices: ['android-02'], serves: ['arm64-v8a'],
    ...over,
  };
}

function appState(over: Partial<DeviceAppState> = {}): DeviceAppState {
  return {
    instance_id: 'android-02', package_name: 'com.instagram.android', desired_release_id: 'rel-1',
    installed_release_id: 'rel-1', observed_version_name: '300.0.0.29.110', observed_version_code: 300,
    observed_splits: ['base'], first_install_time: null, last_update_time: null, state: 'ready',
    pending_op: null, verified_at: '2026-09-17T11:00:00Z', drift_kind: null, detail: null,
    expected_splits: [], previous_release_id: null, last_operation: 'install',
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
  // O registro de aplicativos: é dele que sai o seletor de app da loja. Sem ele a página não tem como saber de
  // que aplicativo falar — e não fala de nenhum por omissão, que era o defeito.
  backend.on('GET', /app-catalog/, () => json([
    { package: 'com.instagram.android', name: 'Instagram', label: 'Instagram', has_catalog: true,
      session_provider: 'instagram', needs_profile: true },
    { package: 'com.pocqa.messenger', name: 'QA Messenger', label: 'QA Messenger', has_catalog: false,
      session_provider: null, needs_profile: false },
  ]));
  backend.on('GET', /instances/, () => json([{ id: 'android-01' }, { id: 'android-02' }]));
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
    // O host do diálogo vai junto, como em App.tsx: toda ação de ciclo de vida passa por uma confirmação,
    // e um teste que pulasse o diálogo provaria um caminho que ninguém percorre.
    root.render(<><ReleasesPage /><ConfirmHost /></>);
  });
  await waitFor(() => text().includes('Aplicativos'));
}

/** Botão dentro do diálogo aberto — o rótulo se repete de propósito entre o cartão e a confirmação. */
function noDialogo(nome: RegExp): HTMLElement {
  return byRole('button', nome, byRole('dialog', /.+/));
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
  backend.on('GET', /app-state/, () => json([appState()]));
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

it('versão nunca provada oferece prova num aparelho, e não promoção', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  await render();
  expect(byRole('button', /Colocar em prova/i)).toBeTruthy();
  expect(allByRole('button', /Promover/i)).toHaveLength(0);
});

it('colocar em prova avisa que a falha manda para a quarentena e manda o aparelho escolhido', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('POST', /releases\/rel-1\/lifecycle/, () => json({ accepted: true }));
  await render();
  await click(byRole('button', /Colocar em prova/i));
  await waitFor(() => text().includes('quarentena automaticamente'));
  await click(noDialogo(/^Colocar em prova$/i));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 1);
  const enviado = backend.callsTo('POST', /lifecycle/)[0]!.body as { verb: string; instance_id: string };
  expect(enviado.verb).toBe('canary');
  expect(enviado.instance_id).toBe('android-01');
});

it('versão em prova mostra o canal, as provas observadas e o botão de promover', async () => {
  backend.on('GET', /releases/, () => json([release({
    channel: 'canary', canary_instance_id: 'android-01', channel_detail: 'em prova em android-01',
    validations: [
      { instance_id: 'android-01', stage: 'install', ok: true, detail: 'versionCode 300 no aparelho',
        observed_at: '2026-09-17T12:00:00Z' },
      { instance_id: 'android-01', stage: 'launch', ok: true, detail: 'app abriu e permaneceu em primeiro plano',
        observed_at: '2026-09-17T12:01:00Z' },
    ],
  })]));
  backend.on('POST', /lifecycle/, () => json({ accepted: true }));
  await render();
  expect(text()).toContain('em prova (canário)');
  expect(text()).toContain('app abriu e permaneceu em primeiro plano');
  await click(byRole('button', /Promover/i));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 1);
  expect((backend.callsTo('POST', /lifecycle/)[0]!.body as { verb: string }).verb).toBe('promote');
});

it('versão em quarentena aparece bloqueada e a volta é explícita', async () => {
  backend.on('GET', /releases/, () => json([release({
    channel: 'quarantined', channel_detail: 'o canário android-01 falhou em launch: o app não abriu',
  })]));
  await render();
  expect(text()).toContain('em quarentena');
  expect(text()).toContain('o canário android-01 falhou');
  await click(byRole('button', /Tentar de novo/i));
  await waitFor(() => text().includes('já falhou uma prova antes'));
});

it('aparelho com versão anterior oferece voltar, avisando que o Android pode recusar', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /app-state/, () => json([appState({
    previous_release_id: 'rel-0', last_operation: 'upgrade', observed_version_code: 301,
  })]));
  backend.on('POST', /lifecycle/, () => json({ accepted: true }));
  await render();
  await click(byRole('button', /Voltar versão/i));
  await waitFor(() => text().includes('preservando os dados'));
  expect(text()).toContain('nada é apagado');
  await click(noDialogo(/^Voltar versão$/i));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 1);
  const enviado = backend.callsTo('POST', /lifecycle/)[0]!.body as { verb: string; confirm_reinstall: boolean };
  expect(enviado.verb).toBe('rollback');
  expect(enviado.confirm_reinstall).toBe(false);      // preservar os dados é sempre a primeira tentativa
});

it('depois da recusa do Android, o portal diz em letras claras que a sessão será perdida', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /app-state/, () => json([appState({
    previous_release_id: 'rel-0', drift_kind: 'downgrade_refused', state: 'install_failed',
    detail: 'Voltar preservando os dados foi recusado pelo aparelho.',
  })]));
  backend.on('POST', /lifecycle/, () => json({ accepted: true }));
  await render();
  await click(byRole('button', /Voltar versão/i));
  await waitFor(() => text().includes('APAGA os dados'));
  expect(text()).toContain('login terá de ser refeito');
  await click(noDialogo(/Reinstalar e perder a sessão/i));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 1);
  expect((backend.callsTo('POST', /lifecycle/)[0]!.body as { confirm_reinstall: boolean }).confirm_reinstall).toBe(true);
});

it('canário que não abriu ainda pode voltar, mesmo sem release instalada registrada', async () => {
  // `installed_release_id` fica nulo quando a prova de abertura falha: o app está lá e não roda. É justamente
  // quando voltar importa — o botão não pode ficar inerte.
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /app-state/, () => json([appState({
    state: 'verify_failed', installed_release_id: null, desired_release_id: 'rel-1',
    previous_release_id: 'rel-0', detail: 'o app não ficou em primeiro plano',
  })]));
  backend.on('POST', /lifecycle/, () => json({ accepted: true }));
  await render();
  await click(byRole('button', /Voltar versão/i));
  await click(noDialogo(/^Voltar versão$/i));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 1);
  expect(backend.callsTo('POST', /lifecycle/)[0]!.path).toContain('rel-1');
});

// ==================================================================== a loja (Play Store) como fonte
function estadoDaLoja(over: Partial<StoreStatus> = {}): StoreStatus {
  return {
    configured: true, instance_id: 'android-11', package: 'com.instagram.android', state: 'online',
    store_version_code: 500, store_version_name: '500.0.0', catalog_version_code: 447, update_available: true,
    fleet_target_release_id: null, fleet_target_version_code: null,
    ...over,
  };
}

it('sem loja configurada, o cartão da loja não aparece', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /\/store$/, () => json(estadoDaLoja({ configured: false, instance_id: null, state: null })));
  await render();
  expect(text()).not.toContain('Loja (Play Store)');
});

it('cartão da loja compara loja e catálogo e avisa que há versão nova', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /\/store$/, () => json(estadoDaLoja()));
  await render();
  await waitFor(() => text().includes('Loja (Play Store)'));
  expect(text()).toContain('android-11');
  expect(text()).toContain('500.0.0 (versionCode 500)');
  expect(text()).toContain('versionCode 447');
  expect(text()).toContain('versão nova a buscar');
  expect(text()).toContain('janela do emulador');           // a conta Google nunca passa pelo painel
});

it('buscar da loja chama a rota e a loja nunca aparece como aparelho de prova', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /\/store$/, () => json(estadoDaLoja()));
  backend.on('GET', /instances/, () => json([{ id: 'android-01', kind: 'emulator' }, { id: 'android-11', kind: 'store' }]));
  backend.on('POST', /store\/sync/, () => json({ accepted: true, command_id: 'cmd-1', state: 'dispatched' }, 202));
  await render();
  await waitFor(() => text().includes('Loja (Play Store)'));
  const aparelhos = Array.from(container.querySelectorAll('select')).pop();
  expect(Array.from(aparelhos!.querySelectorAll('option')).map((o) => o.textContent)).toEqual(['android-01']);
  await click(byRole('button', /Buscar da loja/i));
  await waitFor(() => backend.callsTo('POST', /store\/sync/).length === 1);
  // O pacote vai no corpo: a loja deixou de assumir um aplicativo por omissão.
  expect((backend.callsTo('POST', /store\/sync/)[0]!.body as { package: string }).package)
    .toBe('com.instagram.android');
});

it('a loja pergunta de que aplicativo se trata, e trocar o app refaz a pergunta ao backend', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /\/store/, () => json(estadoDaLoja()));
  await render();
  await waitFor(() => text().includes('Loja (Play Store)'));
  const seletor = container.querySelector('#app-da-loja') as HTMLSelectElement;
  expect(Array.from(seletor.querySelectorAll('option')).map((o) => o.textContent))
    .toEqual(['Instagram', 'QA Messenger']);
  // Toda consulta à loja leva o pacote — nunca um padrão do cliente.
  expect(backend.callsTo('GET', /\/store/).every((c) => c.query.get('package'))).toBeTruthy();
  const antes = backend.callsTo('GET', /\/store/).length;
  await act(async () => {
    seletor.value = 'com.pocqa.messenger';
    seletor.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await waitFor(() => backend.callsTo('GET', /\/store/).length > antes);
  expect(backend.callsTo('GET', /\/store/).at(-1)!.query.get('package')).toBe('com.pocqa.messenger');
});

it('com a loja desligada, buscar e abrir ficam bloqueados com o motivo, e ligar é oferecido', async () => {
  backend.on('GET', /releases/, () => json([]));
  backend.on('GET', /\/store$/, () => json(estadoDaLoja({ state: 'stopped' })));
  backend.on('POST', /store\/sync/, () => json({ accepted: true }, 202));
  await render();
  await waitFor(() => text().includes('Loja (Play Store)'));
  const buscar = byRole('button', /Buscar da loja/i);
  expect(buscar.getAttribute('aria-disabled')).toBe('true');
  expect(buscar.textContent).toContain('Ligue a loja primeiro.');
  await click(buscar);
  expect(backend.callsTo('POST', /store\/sync/)).toHaveLength(0);
  expect(byRole('button', /Ligar a loja/i)).toBeTruthy();
});

// ==================================================================== distribuir
it('só versão promovida oferece distribuir', async () => {
  backend.on('GET', /releases/, () => json([release({ channel: 'canary', canary_instance_id: 'android-01' })]));
  await render();
  expect(allByRole('button', /Distribuir/i)).toHaveLength(0);
  expect(allByRole('button', /Instalar em todos agora/i)).toHaveLength(0);
});

it('distribuir mostra o que aconteceu aparelho por aparelho', async () => {
  backend.on('GET', /releases/, () => json([release({ channel: 'promoted' })]));
  backend.on('POST', /lifecycle/, () => json({
    accepted: true,
    devices: [
      { id: 'android-01', outcome: 'started', reason: 'instalando agora' },
      { id: 'android-02', outcome: 'pending', reason: 'está hibernated: instala ao entrar em serviço, antes da tarefa' },
      { id: 'android-03', outcome: 'already', reason: 'já está nesta versão' },
    ],
  }));
  await render();
  await click(byRole('button', /^Distribuir$/i));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 1);
  const enviado = backend.callsTo('POST', /lifecycle/)[0]!.body as { verb: string; eager: boolean };
  expect(enviado.verb).toBe('distribute');
  expect(enviado.eager).toBe(false);                        // o padrão não liga aparelho nenhum
  await waitFor(() => text().includes('instala ao entrar em serviço'));
  expect(text()).toContain('android-01');
  expect(text()).toContain('já tem');
});

it('instalar em todos agora explica o que o rodízio vai fazer e só então envia eager', async () => {
  backend.on('GET', /releases/, () => json([release({ channel: 'promoted' })]));
  backend.on('POST', /lifecycle/, () => json({ accepted: true, devices: [] }));
  await render();
  await click(byRole('button', /Instalar em todos agora/i));
  await waitFor(() => text().includes('O rodízio vai ligar os aparelhos'));
  expect(backend.callsTo('POST', /lifecycle/)).toHaveLength(0);          // nada sai antes da confirmação
  await click(noDialogo(/Instalar em todos agora/i));
  await waitFor(() => backend.callsTo('POST', /lifecycle/).length === 1);
  expect((backend.callsTo('POST', /lifecycle/)[0]!.body as { eager: boolean }).eager).toBe(true);
});

// ==================================================================== aprovar a assinatura
it('assinatura já aprovada mostra o selo, não o botão', async () => {
  // Antes o botão aparecia SEMPRE: depois de aprovar, ele continuava lá e parecia que o clique tinha falhado —
  // medido no primeiro uso real, com o Instagram vindo da loja.
  backend.on('GET', /releases/, () => json([release({ status: 'installable' })]));
  await render();
  expect(text()).toContain('Assinatura aprovada');
  expect(allByRole('button', /Aprovar assinatura/i)).toHaveLength(0);
});

it('assinatura ainda não aprovada oferece aprovar, e aprovar troca o botão pelo selo', async () => {
  let status = 'validated';
  backend.on('GET', /releases/, () => json([release({ status, detail: status === 'validated' ? 'Assinatura ainda não aprovada.' : null })]));
  backend.on('POST', /approve-signature/, () => {
    status = 'installable';
    return json(release({ status: 'installable' }));
  });
  await render();
  await click(byRole('button', /Aprovar assinatura/i));
  await waitFor(() => text().includes('passa a ser a confiável'));
  await click(noDialogo(/^Aprovar assinatura$/i));
  await waitFor(() => backend.callsTo('POST', /approve-signature/).length === 1);
  await waitFor(() => text().includes('Assinatura aprovada'));
  expect(allByRole('button', /Aprovar assinatura/i)).toHaveLength(0);
});

it('assinatura diferente da aprovada volta a oferecer aprovar', async () => {
  backend.on('GET', /releases/, () => json([release({
    status: 'invalid', detail: 'Assinatura diferente da aprovada para este pacote.',
  })]));
  await render();
  expect(byRole('button', /Aprovar assinatura/i)).toBeTruthy();
});


// ==================================================================== item 6.2: aceito não é concluído
it('canário e rollback dão tom de ACEITO, com o comando, e nunca tom de sucesso', async () => {
  // O backend devolve 202: o trabalho roda no aparelho e leva minutos. Verde ali chamava de sucesso o que só
  // tinha sido aceito — e a mensagem prometia um resultado que a página não entregava.
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('POST', /lifecycle/, () => json({ accepted: true, command_id: 'cmd-9', state: 'dispatched' }, 202));
  useToastStore.setState({ toasts: [] });
  await render();
  await click(byRole('button', /Colocar em prova/i));
  await click(noDialogo(/^Colocar em prova$/i));
  await waitFor(() => useToastStore.getState().toasts.length > 0);
  const t = useToastStore.getState().toasts.at(-1)!;
  expect(t.tone).toBe('info');
  expect(t.message).toContain('cmd-9');
  expect(t.message).not.toContain('quando o aparelho responder');
});

it('promover continua verde: é decisão de banco, e ela aconteceu agora', async () => {
  backend.on('GET', /releases/, () => json([release({ channel: 'canary', canary_instance_id: 'android-01' })]));
  backend.on('POST', /lifecycle/, () => json({ accepted: true }));
  useToastStore.setState({ toasts: [] });
  await render();
  await click(byRole('button', /Promover/i));
  await waitFor(() => useToastStore.getState().toasts.length > 0);
  expect(useToastStore.getState().toasts.at(-1)!.tone).toBe('success');
});

it('o desfecho por aparelho chega sozinho: o evento do backend muda a lista sem recarregar', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /app-state/, () => json([appState({ state: 'installing', detail: 'instalando…' })]));
  await render();
  await waitFor(() => text().includes('installing'));
  await act(async () => {
    useAppStore.setState((s) => ({
      appState: { ...s.appState, 'android-02|com.instagram.android': appState({ state: 'ready', detail: 'app abriu' }) },
    }));
  });
  await waitFor(() => text().includes('app abriu'));
  expect(text()).toContain('ready');
});

it('falha de carga do estado dos aparelhos aparece, em vez de virar "nenhum aplicativo catalogado"', async () => {
  backend.on('GET', /releases/, () => json([release()]));
  backend.on('GET', /app-state/, () => json({ detail: { code: 'boom', message: 'o banco não respondeu' } }, 500));
  useToastStore.setState({ toasts: [] });
  await render();
  await waitFor(() => useToastStore.getState().toasts.some((t) => t.tone === 'danger'));
  expect(useToastStore.getState().toasts.some((t) => /instalado nos aparelhos/i.test(t.title))).toBe(true);
});
