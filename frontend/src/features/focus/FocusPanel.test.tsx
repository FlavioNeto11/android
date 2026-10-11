// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Command, FrameInfo, Instance, OperationalContext, ProfileAccount, Worker } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { initialDataState } from '../../store/reducer';
import { useToastStore } from '../../store/toasts';
import { useTrainingStore } from '../training/trainingStore';
import { useUiStore } from '../../store/ui';
import { APPS, makeBinding, makeInstance, makePersona, makeSnapshot } from '../../test/fixtures';
import {
  FakeBackend, allByRole, apiError, botaoPronto, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor,
} from '../../test/harness';
import { FocusPanel } from './FocusPanel';

// Achados #61 e #62: o foco decidia só pelo estado do aparelho — oferecia verbo que o backend recusa no
// pré-voo, oferecia campo de texto que a loja nunca aceita, e não dizia em que máquina o aparelho roda.

const LOJA_VERBS = ['start', 'stop', 'restart', 'home', 'back', 'recents'];

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

async function renderFocus(instance: Instance, workers: Worker[] = []): Promise<HTMLElement> {
  useAppStore.setState({
    instances: { [instance.id]: instance },
    instanceOrder: [instance.id],
    workers: Object.fromEntries(workers.map((w) => [w.id, w])),
  });
  await act(async () => {
    // O `ConfirmHost` junto: "Parar" com execução e "Resetar dados…" perguntam antes de agir.
    root.render(<><FocusPanel instanceId={instance.id} /><ConfirmHost /></>);
  });
  return container;
}

/** O aparelho com o controle manual NESTA aba: lease concedido e `control: 'user'` (o que `userHasControl` exige). */
function comControle(instance: Instance): Instance {
  useControlStore.setState({ leases: { [instance.id]: { leaseId: 'lease-t', status: 'granted', acquiredAt: Date.now() } } });
  return { ...instance, control: 'user' };
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.on('GET', /^\/api\/instances\/[^/]+\/hierarchy/, () => json({ elements: [] }));
  backend.install();
  const snap = makeSnapshot();
  useAppStore.setState({ ...initialDataState, settings: snap.settings, health: snap.health });
  useControlStore.setState({ leases: {}, busy: {} });
  useTrainingStore.setState({ gravando: {}, recusadas: {} });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('FocusPanel — capacidades do aparelho (achado #62)', () => {
  // O controle manual só aparece com o controle na mão (evolução 2, E1): para ver o campo, o teste assume antes.
  it('a loja recebe o campo de texto como os outros; só a senha da conta Google vai pela janela do emulador', async () => {
    const el = await renderFocus(comControle(makeInstance(11, { kind: 'store', state: 'online', supported_verbs: LOJA_VERBS })));
    expect(el.querySelector('input[aria-label="Texto para digitar no aparelho"]')).not.toBeNull();
    expect(text(el)).toContain('senha da conta Google é digitada direto na janela do emulador');
  });

  it('verbo que o aparelho não aceita aparece indisponível COM o motivo, antes do clique', async () => {
    const el = await renderFocus(makeInstance(11, { kind: 'store', state: 'online', supported_verbs: LOJA_VERBS }));
    const t = text(el);
    // O rótulo acompanhou o verbo: ele deixou de instalar um arquivo configurado à mão e passa a instalar a
    // versão PROMOVIDA pela camada de releases (#83).
    expect(t).toContain('Instalar app');
    expect(t).toContain('Este aparelho não aceita “Instalar app”');
    expect(t).toContain('aparelho-loja');
    // O que ela aceita continua clicável: o filtro é de capacidade, não um cadeado geral.
    expect(t).not.toContain('Este aparelho não aceita “Parar”');
  });

  it('aparelho comum sem restrição segue oferecendo tudo que o estado permite', async () => {
    const el = await renderFocus(comControle(makeInstance(1, { state: 'online' })));
    expect(text(el)).not.toContain('Este aparelho não aceita');
    expect(text(el)).not.toContain('Indisponíveis');
    expect(el.querySelector('input[aria-label="Texto para digitar no aparelho"]')).not.toBeNull();
  });

  it('os verbos recusados ficam juntos em "Indisponíveis (n)", cada um com o motivo', async () => {
    const el = await renderFocus(makeInstance(11, { kind: 'store', state: 'online', supported_verbs: LOJA_VERBS }));
    const t = text(el);
    // Abrir, Instalar, Hibernar (ligada no snapshot) e Resetar: a loja só liga, desliga, reinicia e abre a loja
    expect(t).toContain('Indisponíveis (4)');
    expect(t).toContain('Este aparelho não aceita “Abrir app”');
    expect(t).toContain('Este aparelho não aceita “Resetar dados”');
    // e não aparecem como botão, como se valessem
    expect(allByRole('button', /^Instalar app/, el)).toHaveLength(0);
    expect(allByRole('button', /^Resetar dados/, el)).toHaveLength(0);
  });
});

describe('FocusPanel — em que servidor o aparelho roda (achado #61)', () => {
  it('aparelho de worker mostra o servidor e os dados que o WORKER reporta', async () => {
    const w = worker({ devices: [{ serial: 'emulator-5554', avd_name: 'worker-01', state: 'running',
                                   adb_port: 5555, instance_id: 'android-09' }] });
    const el = await renderFocus(
      makeInstance(9, { state: 'online', kind: 'external', worker_id: 'worker-lan-01' }), [w]);
    const t = text(el);
    expect(t).toContain('Notebook da LAN');
    expect(t).toContain('worker-01');          // AVD do worker, não o `poc_avd_9` do central
    expect(t).toContain('Porta do ADB no servidor');
  });

  it('aparelho do central não ganha selo de servidor nem linha de processo', async () => {
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(text(el)).not.toContain('Processo no servidor');
  });
});

describe('FocusPanel — tela no ritmo da IA (r-20260928195344-02ee9e)', () => {
  // Com a IA no controle a prévia não captura por conta própria: o frame chega a cada observação da IA. O dono
  // acompanha pelo Foco, e o intervalo entre dois frames não pode virar "Desatualizado" com a imagem cinza.
  const haSegundos = (s: number) => new Date(Date.now() - s * 1000).toISOString();
  const frameDe = (id: string, ts: string): FrameInfo => ({ id, ts, width: 1080, height: 2400, orientation: 'portrait', stale: false });

  function imagem(id: string, ts: string): Response {
    return new Response(new TextEncoder().encode('jpeg'), {
      status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'X-Frame-Id': id, 'X-Frame-Ts': ts, 'X-Frame-Width': '1080',
                 'X-Frame-Height': '2400', 'X-Frame-Orientation': 'portrait' },
    });
  }

  async function focoCom(control: Instance['control'], idadeS: number): Promise<HTMLElement> {
    const ts = haSegundos(idadeS);
    backend.on('GET', /\/frame$/, () => imagem('f1', ts));
    const el = await renderFocus(makeInstance(1, { state: 'online', control, frame: frameDe('f1', ts) }));
    await waitFor(() => expect(el.querySelector('img')).not.toBeNull());
    return el;
  }

  it('um ciclo da IA sem frame novo não é "Desatualizado"', async () => {
    const el = await focoCom('ai', 10);
    expect(text(el)).not.toContain('Desatualizado');
  });

  it('sem a IA no controle, o mesmo frame de 10 s continua desatualizado', async () => {
    const el = await focoCom('none', 10);
    expect(text(el)).toContain('Desatualizado (Sem imagem nova)');
  });

  it('a IA sem olhar a tela além do prazo: desatualizado, com o motivo certo', async () => {
    const el = await focoCom('ai', 45);
    expect(text(el)).toContain('Desatualizado (IA sem olhar a tela)');
    expect(text(el)).not.toContain('Sem imagem nova');
  });
});

describe('FocusPanel — celular (P1.1 da auditoria UX de 27/09)', () => {
  // O jsdom não tem `matchMedia`. O dublê responde à consulta pelo `max-width` que ela declara, como o navegador
  // faria numa janela dessa largura — assim o teste não depende do limiar exato escrito no componente.
  function fingirLarguraDaJanela(px: number): void {
    const matchMedia = (query: string): MediaQueryList => {
      const m = /max-width:\s*(\d+)px/.exec(query);
      return {
        matches: m ? px <= Number(m[1]) : false,
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      };
    };
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: matchMedia });
  }

  afterEach(() => {
    delete (window as { matchMedia?: unknown }).matchMedia;
    useUiStore.setState({ focusInstanceId: null });
  });

  it('em 390 px o cabeçalho tem "Voltar", que fecha o foco (o "Fechar" do desktop some)', async () => {
    fingirLarguraDaJanela(390);
    useUiStore.setState({ focusInstanceId: 'android-01' });
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(allByRole('button', 'Fechar', el)).toHaveLength(0);
    await click(byRole('button', 'Voltar', el));
    expect(useUiStore.getState().focusInstanceId).toBeNull();
  });

  it('no desktop não há "Voltar": o painel fica ao lado e fecha por "Fechar"', async () => {
    fingirLarguraDaJanela(1440);
    useUiStore.setState({ focusInstanceId: 'android-01' });
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(allByRole('button', 'Voltar', el)).toHaveLength(0);
    await click(byRole('button', 'Fechar', el));
    expect(useUiStore.getState().focusInstanceId).toBeNull();
  });

  it('aparelho que sumiu do backend também tem "Voltar" em tela estreita', async () => {
    fingirLarguraDaJanela(390);
    useUiStore.setState({ focusInstanceId: 'android-99' });
    useAppStore.setState({ instances: {}, instanceOrder: [] });
    await act(async () => {
      root.render(<FocusPanel instanceId="android-99" />);
    });
    await click(byRole('button', 'Voltar', container));
    expect(useUiStore.getState().focusInstanceId).toBeNull();
  });
});

// ---------------------------------------------------------------- evolução 2, onda E1: o Foco em seções

function conta(over: Partial<ProfileAccount>): ProfileAccount {
  return {
    id: 'acc-1', profile_id: 'ig-1', app_id: 'instagram', app_name: 'Instagram', package: 'com.instagram.android',
    handle: 'luciana', host: null, login_identifier: null, status: 'active', session_status: 'unknown',
    session_detail: null, session_verified_at: null, automated_login: true, credential_configured: false,
    notes: '', created_at: '2026-09-27T10:00:00Z', updated_at: '2026-09-27T10:00:00Z',
    ...over,
  };
}

function contexto(over: Partial<OperationalContext> = {}): OperationalContext {
  const sessao = (status: 'auth_required' | 'session_ready') =>
    ({ status, instance_id: 'android-01', observed_username: null, verified_at: null, detail: null, stale: false });
  return {
    instance_id: 'android-01', server: null,
    device: { state: 'online', state_detail: null, kind: 'emulator', supported_verbs: [],
              automation: { state: 'ready', detail: null }, attention: null },
    stream: null,
    apps: [{ app_id: 'instagram', name: 'Instagram', package: 'com.instagram.android', presence: 'installed', state: 'ready',
             installed_version_name: '447.0', installed_version_code: 447, verified_at: null, pending_op: null, detail: null,
             promoted_release_id: 'rel-448', promoted_version_name: '448.0', promoted_version_code: 448 }],
    profiles: [
      { profile_id: 'ig-1', username: 'luciana', display_name: 'Luciana Souza', persona_id: 'p-1', persona_name: 'Luciana',
        credential_configured: true, credential_status: 'active', session: sessao('auth_required'), app_on_device: null,
        session_actions: null,
        accounts: [
          conta({ session: sessao('session_ready'), credential_configured: true,
                  credential: { configured: true, login_identifier: null, status: 'active', failed_attempts: 0,
                                blocked_until: null, updated_at: null, last_used_at: null, consent_at: '2026-09-27T10:00:00Z' } }),
          conta({ id: 'acc-2', app_id: 'portal', app_name: 'Portal do cliente', package: 'com.android.chrome', handle: '',
                  host: 'portal.exemplo.com.br', session_status: 'logged_out', credential_configured: true,
                  credential: { configured: true, login_identifier: null, status: 'active', failed_attempts: 0,
                                blocked_until: null, updated_at: null, last_used_at: null, consent_at: null } }),
        ] },
      // Persona sem conta (onda D): `username` vazio — nada de "@" solto na tela.
      { profile_id: 'ig-2', username: '', display_name: 'Quillon', persona_id: 'p-2', persona_name: 'Quillon',
        credential_configured: false, credential_status: null, session: sessao('auth_required'), app_on_device: null,
        session_actions: null, accounts: [] },
    ],
    ...over,
  };
}

function secoes(el: HTMLElement): string[] {
  return Array.from(el.querySelectorAll('[data-focus-section]')).map((s) => s.getAttribute('data-focus-section') ?? '');
}

function secao(el: HTMLElement, nome: string): HTMLElement {
  const s = el.querySelector(`[data-focus-section="${nome}"]`);
  if (!s) throw new Error(`seção ${nome} ausente`);
  return s as HTMLElement;
}

describe('FocusPanel — seções da coluna lateral', () => {
  afterEach(() => {
    useUiStore.getState().navegar({ tela: 'painel', query: { foco: undefined } }, 'replace');
  });

  it('as seções vêm na ordem do desenho, com as de consulta longa no fim e recolhidas', async () => {
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(secoes(el)).toEqual([
      'Identidade', 'Estado e saúde', 'Servidor', 'Automação e tarefa', 'Personas neste aparelho', 'Contas', 'Apps',
      'Ações', 'Comandos recentes', 'Detalhes técnicos', 'Hierarquia',
    ]);
    const aberta = (nome: string) => secao(el, nome).querySelector('details')?.open;
    expect(aberta('Identidade')).toBe(true);
    expect(aberta('Ações')).toBe(true);
    for (const fim of ['Comandos recentes', 'Detalhes técnicos', 'Hierarquia']) expect(aberta(fim)).toBe(false);
  });

  it('estado e saúde em português, com o semáforo no título; nenhum enum cru', async () => {
    const el = await renderFocus(makeInstance(1, {
      state: 'online',
      readiness: { phase: 'android_responsive', detail: 'falta o framework', since: null },
      connectivity: { state: 'degraded', route: true, dns: true, tcp_443: false, validated: false, checked_at: null,
                      detail: 'porta 443 não responde' },
      automation: { state: 'ready', detail: null },
      inventory_state: 'divergent', inventory_detail: 'o worker declara outro serial',
    }));
    const saude = secao(el, 'Estado e saúde');
    const t = text(saude);
    expect(t).toContain('Com falha');                 // o pior sinal (inventário divergente) vira o semáforo
    expect(t).toContain('Android respondendo');
    expect(t).toContain('Internet instável');
    expect(t).toContain('Automação pronta');
    expect(t).toContain('Divergente');
    for (const cru of ['android_responsive', 'degraded', 'divergent']) expect(t).not.toContain(cru);
  });

  it('personas e contas saem do operational-context; a senha nunca, o consentimento sim', async () => {
    backend.on('GET', /\/operational-context$/, () => json(contexto()));
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    const personas = await waitFor(() => {
      const s = secao(el, 'Personas neste aparelho');
      expect(text(s)).toContain('Luciana Souza');
      return s;
    });
    expect(text(personas)).toContain('@luciana');
    expect(text(personas)).toContain('Precisa entrar');           // sessão do perfil, traduzida
    expect(text(personas)).toContain('Quillon');
    expect(text(personas)).not.toMatch(/@(\s|·|$)/);              // sem usuário, sem "@" solto
    expect(allByRole('button', 'Abrir persona', personas)).toHaveLength(2);

    const contas = text(secao(el, 'Contas'));
    expect(contas).toContain('Instagram');
    expect(contas).toContain('Conectado');                        // sessão da CONTA neste aparelho
    expect(contas).toContain('consentimento: sim');
    expect(contas).toContain('Portal do cliente');
    expect(contas).toContain('portal.exemplo.com.br');
    expect(contas).toContain('Fora da conta');
    expect(contas).toContain('consentimento: não');
    expect(contas).toContain('Quillon ainda não tem conta cadastrada.');
    expect(backend.callsTo('GET', /\/accounts$/)).toHaveLength(0); // uma leitura só, sem N chamadas por persona

    const apps = text(secao(el, 'Apps'));
    expect(apps).toContain('instalada 447.0 · promovida 448.0');
    expect(apps).toContain('diferente da promovida');
  });

  // Evolução 2, onda E2 (N:N, ADR-043): a lista vem de GET /instances/{id}/personas — uma linha por vínculo, com o
  // app dele, a sessão AQUI e o selo "Principal" —, e o aparelho vincula outra persona pela mesma rota da persona.
  it('lista as N personas do aparelho pela rota N:N e vincula mais uma a partir do aparelho', async () => {
    backend.on('GET', /\/operational-context$/, () => json(contexto()));
    let leituras = 0;
    backend.on('GET', /^\/api\/instances\/android-01\/personas$/, () => {
      leituras += 1;
      return json([
        { profile_id: 'ig-1', username: 'luciana', display_name: 'Luciana Souza', name: 'Luciana Souza', status: 'active',
          app_id: 'instagram', is_primary: true, bound_at: null,
          session: { status: 'session_ready', instance_id: 'android-01', observed_username: null, verified_at: null,
                     detail: null, stale: false } },
        { profile_id: 'ig-3', username: null, display_name: 'Nelson Lima', name: 'Nelson Lima', status: 'active',
          app_id: 'chrome', is_primary: false, bound_at: null, session: null },
      ]);
    });
    backend.on('GET', /^\/api\/personas$/, () => json([makePersona('ig-4', 'Sueli Quintela')]));
    backend.on('POST', /^\/api\/personas\/ig-4\/devices$/, () => json(makePersona('ig-4', 'Sueli Quintela', {
      devices: [makeBinding('android-01', { app_id: null, is_primary: true })] }), 201));
    useAppStore.setState({ apps: [{ ...APPS[0]!, id: 'instagram', name: 'Instagram' }, { ...APPS[0]!, id: 'chrome', name: 'Chrome' }] });
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    const personas = await waitFor(() => {
      const s = secao(el, 'Personas neste aparelho');
      expect(text(s)).toContain('Nelson Lima');
      return s;
    });
    const t = text(personas);
    expect(t).toContain('Luciana Souza');
    expect(t).toContain('Principal');                              // android-01 é o principal da Luciana
    expect(t).toContain('@luciana · conta do Instagram');
    expect(t).toContain('conta do Chrome');
    expect(t).toContain('sem conta que sirva a este vínculo');
    expect(t).not.toContain('Quillon');                             // o contexto operacional não manda mais na lista
    expect(allByRole('button', 'Abrir persona', personas)).toHaveLength(2);

    await click(byRole('button', /Vincular persona/, personas));
    await waitFor(() => expect(text(personas)).toContain('Sueli Quintela'));
    await setValue(byRole('combobox', 'Persona', personas) as HTMLSelectElement, 'ig-4');
    const antes = leituras;
    await click(byRole('button', /^Vincular$/, personas));
    await waitFor(() => expect(backend.callsTo('POST', /\/personas\/ig-4\/devices$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/personas\/ig-4\/devices$/)[0]!.body)
      .toEqual({ instance_id: 'android-01', app_id: null, primary: false });
    await waitFor(() => expect(leituras).toBeGreaterThan(antes));           // relê a lista depois de vincular
  });

  it('"Abrir persona" leva à tela Personas com aquela pessoa aberta', async () => {
    backend.on('GET', /\/operational-context$/, () => json(contexto()));
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    const personas = await waitFor(() => {
      const s = secao(el, 'Personas neste aparelho');
      expect(text(s)).toContain('Luciana Souza');
      return s;
    });
    await click(allByRole('button', 'Abrir persona', personas)[0] as HTMLElement);
    expect(useUiStore.getState().view).toBe('personas');
    // O link nomeia a pessoa; o Foco continua aberto (o `foco` vai junto).
    expect(useUiStore.getState().rota.segmentos).toEqual(['ig-1']);
  });
});

describe('FocusPanel — ações em grupos', () => {
  const emVoo: Command = {
    id: 'cmd-7', instance_id: 'android-01', worker_id: null, verb: 'install_apk', state: 'running', fence: 1,
    requested_by: 'painel', reason: null, attempt: 1, created_at: new Date().toISOString(), dispatched_at: null,
    acked_at: null, started_at: null, finished_at: null,
  };
  const grupos = (el: HTMLElement) => Array.from(secao(el, 'Ações').querySelectorAll('[role="group"]'))
    .map((g) => g.getAttribute('aria-label'))
    .filter((n) => n !== 'Botões do Android');

  it('a zona de perigo vem por último, separada, com "Resetar dados…"', async () => {
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    expect(grupos(el)).toEqual(['Ciclo de vida', 'Apps', 'Controle manual', 'Observação', 'Zona de perigo']);
    const zona = secao(el, 'Ações').querySelector('[role="group"][aria-label="Zona de perigo"]') as HTMLElement;
    expect(byRole('button', /^Resetar dados…/, zona)).toBeTruthy();
    // o reset não divide grade com os demais verbos
    expect(allByRole('button', /^Resetar dados/, el)).toHaveLength(1);
  });

  it('sem o controle, o controle manual é só o motivo, uma vez; com ele, a barra e o campo de texto', async () => {
    let el = await renderFocus(makeInstance(1, { state: 'online' }));
    const semControle = secao(el, 'Ações').querySelector('[aria-label="Controle manual"]') as HTMLElement;
    expect(text(semControle)).toContain('Assuma o controle');
    expect(el.querySelector('input[aria-label="Texto para digitar no aparelho"]')).toBeNull();
    for (const tecla of ['Voltar', 'Início', 'Recentes', 'Enter', 'Apagar']) expect(allByRole('button', tecla, el)).toHaveLength(0);

    await act(async () => root.unmount());
    root = createRoot(container);
    el = await renderFocus(comControle(makeInstance(1, { state: 'online' })));
    expect(el.querySelector('input[aria-label="Texto para digitar no aparelho"]')).not.toBeNull();
    for (const tecla of ['Voltar', 'Início', 'Recentes', 'Enter', 'Apagar']) expect(allByRole('button', tecla, el)).toHaveLength(1);
    expect(text(secao(el, 'Ações'))).not.toContain('Assuma o controle');
  });

  it('com comando em voo, o Foco bloqueia como o cartão e mostra o comando no topo', async () => {
    useAppStore.setState({ lastCommand: { 'android-01': emVoo } });
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    const reiniciar = byRole('button', /^Reiniciar/, el);
    expect(reiniciar.getAttribute('aria-disabled')).toBe('true');
    expect(text(reiniciar)).toContain('android-01 está ocupado: “Instalar app” em andamento');
    expect(byRole('button', /^Resetar dados…/, el).getAttribute('aria-disabled')).toBe('true');
    // o resumo do comando antes da faixa de controle, e a saída dele na zona de perigo
    expect(el.querySelector('[class*="commandBar"]')?.textContent).toContain('Instalar app');
    const zona = secao(el, 'Ações').querySelector('[aria-label="Zona de perigo"]') as HTMLElement;
    expect(byRole('button', /^Cancelar comando/, zona)).toBeTruthy();
  });

  it('o topo do Foco mostra o último comando de verdade e o incerto que ele deixou para trás, como o cartão', async () => {
    const concluido: Command = { ...emVoo, id: 'cmd-8', verb: 'stop', state: 'succeeded', finished_at: new Date().toISOString() };
    const incerto: Command = { ...emVoo, id: 'cmd-1', verb: 'open_app', state: 'uncertain',
                               created_at: '2026-09-25T10:00:00.000Z', finished_at: '2026-09-25T10:01:00.000Z' };
    useAppStore.setState({ lastCommand: { 'android-01': concluido }, ultimoDePessoa: { 'android-01': concluido },
                           comandoSemDesfecho: { 'android-01': incerto } });
    const el = await renderFocus(makeInstance(1, { state: 'online' }));
    const barra = el.querySelector('[class*="commandBar"]')?.textContent ?? '';
    expect(barra).toContain('Concluído');
    expect(barra).toContain('Anterior sem resposta: Abrir app');
    expect(byRole('button', /^Reiniciar/, el).getAttribute('aria-disabled')).not.toBe('true');   // concluído não bloqueia
  });

  it('Parar com uma execução no aparelho pede confirmação; sem execução, vai direto', async () => {
    backend.on('POST', /\/actions\/stop$/, () => json({ command_id: 'c-stop', state: 'created', deduplicated: false }, 202));
    backend.on('GET', /^\/api\/commands\/c-stop$/, () => json({ ...emVoo, id: 'c-stop', verb: 'stop', state: 'succeeded' }));
    const execucao = { run_id: 'run-0001', objective_id: 'obj-1', objective_status: 'running' as const, step_id: 's-1',
                       step_title: 'Abrir o app', step_status: 'running' as const, steps_done: 1, steps_total: 3 };
    const el = await renderFocus(makeInstance(1, { state: 'online', current: execucao }));
    await click(await botaoPronto(/^Parar/, el));
    const dialogo = await waitFor(() => byRole('dialog', /Parar android-01 no meio de uma execução/));
    expect(text(dialogo)).toContain('run-0001');
    expect(backend.callsTo('POST', /\/actions\/stop$/)).toHaveLength(0);      // nada sem confirmar
    await click(await botaoPronto('Parar o aparelho', dialogo));
    await waitFor(() => expect(backend.callsTo('POST', /\/actions\/stop$/)).toHaveLength(1));

    await act(async () => root.unmount());
    root = createRoot(container);
    const livre = await renderFocus(makeInstance(1, { state: 'online', current: null }));
    await click(await botaoPronto(/^Parar/, livre));
    await waitFor(() => expect(backend.callsTo('POST', /\/actions\/stop$/)).toHaveLength(2));
    expect(allByRole('dialog', /Parar android-01/)).toHaveLength(0);
    await flush(20);
  });
});

// RF-40 (prova simulada 13): com o servidor do aparelho fora do ar, a lista dizia "Desconhecido" e o Foco ao lado
// "Parada / O emulador está desligado", oferecendo "Iniciar". A regra é uma só (`store/metricas::estadoContado`).
describe('FocusPanel — servidor sem resposta = estado desconhecido (RF-40)', () => {
  const fora = () => worker({ connected: false, state: 'offline' });

  it('o estado diz "Desconhecido" e explica, sem afirmar "Parada" nem "desligado"', async () => {
    const el = await renderFocus(
      makeInstance(9, { state: 'stopped', kind: 'external', worker_id: 'worker-lan-01',
                        state_detail: "Aparelho em 'stopped'" }), [fora()]);
    const t = text(el);
    expect(t).toContain('Desconhecido');
    expect(t).toContain('O servidor deste aparelho não está respondendo; não dá para saber se o emulador está ligado.');
    expect(t).not.toContain('Parada');
    expect(t).not.toContain('O emulador está desligado');
    expect(t).not.toContain("Aparelho em 'stopped'");
    // A seção "Estado e saúde" lê a mesma regra (a linha do aparelho e o semáforo da seção).
    expect(text(secao(el, 'Estado e saúde'))).toContain('Desconhecido');
  });

  it('as ações que dependem do servidor ficam indisponíveis com o motivo no próprio botão', async () => {
    const el = await renderFocus(
      makeInstance(9, { state: 'stopped', kind: 'external', worker_id: 'worker-lan-01' }), [fora()]);
    for (const nome of [/^Iniciar/, /^Assumir controle/, /^Resetar dados…/, /^Instalar app/, /^Abrir app/]) {
      const b = byRole('button', nome, el);
      expect(b.getAttribute('aria-disabled'), String(nome)).toBe('true');
      expect(text(b), String(nome)).toContain('Servidor sem resposta');
    }
  });

  it('estado guardado "online" com o servidor fora: nada de tela ao vivo nem verbos de aparelho ligado', async () => {
    const el = await renderFocus(
      makeInstance(9, { state: 'online', kind: 'external', worker_id: 'worker-lan-01' }), [fora()]);
    expect(el.querySelector('[role="img"][aria-label^="Tela ao vivo"]')).toBeNull();
    expect(text(el)).toContain('Desconhecido');
    for (const nome of [/^Parar/, /^Reiniciar/, /^Assumir controle/]) {
      const b = byRole('button', nome, el);
      expect(b.getAttribute('aria-disabled'), String(nome)).toBe('true');
      expect(text(b), String(nome)).toContain('Servidor sem resposta');
    }
  });

  it('servidor conectado: o estado guardado vale e os verbos seguem clicáveis', async () => {
    const el = await renderFocus(
      makeInstance(9, { state: 'stopped', kind: 'external', worker_id: 'worker-lan-01' }), [worker()]);
    expect(text(el)).not.toContain('Desconhecido');
    expect(byRole('button', /^Iniciar/, el).getAttribute('aria-disabled')).toBeNull();
  });
});

describe('FocusPanel — painel do Modo treinamento (31.80, 31.84, 31.85, 31.86)', () => {
  const agora = () => new Date().toISOString();
  const quadro: FrameInfo = { id: 'f1', ts: agora(), width: 1080, height: 2400, orientation: 'portrait', stale: false };
  // A prévia busca o quadro de verdade: o servidor devolve a imagem do frame f1 com o tamanho do aparelho.
  beforeEach(() => {
    backend.on('GET', /\/frame$/, () => new Response(new TextEncoder().encode('jpeg'), {
      status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'X-Frame-Id': 'f1', 'X-Frame-Ts': agora(), 'X-Frame-Width': '1080',
                 'X-Frame-Height': '2400', 'X-Frame-Orientation': 'portrait' },
    }));
  });
  const GRAVANDO = {
    id: 'trn-9', instance_id: 'android-01', profile_id: null, app_id: null, intent: 'Responder a DM', status: 'recording',
    operator: null, proposal: null, flow_id: null, created_at: '', finished_at: null, updated_at: '', inputs: [],
  };
  const aparelho = (over: Partial<Instance> = {}) => comControle(makeInstance(1, { state: 'online', frame: quadro, ...over }));
  const caixa = (el: HTMLElement) => el.querySelector('input[aria-label="Texto para digitar no aparelho"]') as HTMLInputElement;
  // O nome acessível vem do <label> que envolve o campo; o harness só lê rótulo por `htmlFor`, então acha-se pelo rótulo.
  const marcaLimpar = (el: HTMLElement) => Array.from(el.querySelectorAll('label'))
    .find((l) => /Limpar o campo antes/.test(l.textContent ?? ''))!.querySelector('input') as HTMLInputElement;
  const comGravacao = () => {
    backend.on('GET', /\/training$/, () => json([GRAVANDO]));
    backend.on('GET', /\/training\/trn-9$/, () => json(GRAVANDO));
  };

  async function aguardarQuadro(el: HTMLElement) {
    await waitFor(() => expect(text(el)).toContain('f1 (1080×2400)'));
  }

  it('31.84: com gravação ativa "Limpar o campo antes" vem marcada e o texto vai com clear_first; desmarcada, omite', async () => {
    comGravacao();
    backend.on('POST', /\/input$/, () => json({ ok: true }));
    const el = await renderFocus(aparelho());
    await aguardarQuadro(el);
    await waitFor(() => expect(marcaLimpar(el).checked).toBe(true));
    expect(text(el)).toContain('Para trocar um texto já digitado, marque “Limpar o campo antes” (em Controle manual, ao lado do campo de texto) em vez de apertar Apagar.');

    await setValue(caixa(el), 'olá');
    await click(byRole('button', /^Enviar$/, el));
    await waitFor(() => expect(backend.callsTo('POST', /\/input$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/input$/)[0]!.body).toMatchObject({ type: 'text', text: 'olá', clear_first: true });

    // 29.140: o 1º POST registrado ainda pode estar em voo (atraso do fetch). A resposta limpa a caixa: o texto
    // digitado antes dela sumiria, e o "Enviar" ficaria sem texto. Espera o envio terminar antes do 2º.
    await waitFor(() => expect(text(el)).not.toContain('Enviando ao aparelho…'));
    await click(marcaLimpar(el));
    expect(marcaLimpar(el).checked).toBe(false);
    await setValue(caixa(el), 'de novo');
    await click(await botaoPronto(/^Enviar$/, el));
    await waitFor(() => expect(backend.callsTo('POST', /\/input$/)).toHaveLength(2));
    expect(backend.callsTo('POST', /\/input$/)[1]!.body).not.toHaveProperty('clear_first');
  });

  it('31.84: fora da gravação a opção começa desmarcada e o texto não pede limpeza', async () => {
    backend.on('POST', /\/input$/, () => json({ ok: true }));
    const el = await renderFocus(aparelho());
    await aguardarQuadro(el);
    expect(marcaLimpar(el).checked).toBe(false);
    await setValue(caixa(el), 'oi');
    await click(byRole('button', /^Enviar$/, el));
    await waitFor(() => expect(backend.callsTo('POST', /\/input$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/input$/)[0]!.body).not.toHaveProperty('clear_first');
  });

  it('31.85: 409 stale_frame durante a gravação vira "N entradas recusadas: refaça" na barra', async () => {
    comGravacao();
    backend.on('POST', /\/input$/, () => apiError(409, 'stale_frame', 'A tela mudou.'));
    const el = await renderFocus(aparelho());
    await aguardarQuadro(el);
    await waitFor(() => expect(text(el)).toContain('Gravando: Responder a DM'));
    expect(text(el)).not.toContain('recusada');
    await click(byRole('button', /^Enter$/, el));
    await waitFor(() => expect(text(el)).toContain('1 entrada recusada: refaça'));
    await click(byRole('button', /^Enter$/, el));
    await waitFor(() => expect(text(el)).toContain('2 entradas recusadas: refaça'));
  });

  it('31.85: enquanto a entrada está em voo, "Enviando ao aparelho…" aparece (aria-live) e some ao responder', async () => {
    // 29.140: com atraso no fetch, o handler só roda depois da espera; liberar antes disso não solta nada.
    let liberar: ((r: Response) => void) | null = null;
    backend.on('POST', /\/input$/, () => new Promise<Response>((r) => { liberar = r; }));
    const el = await renderFocus(aparelho());
    await aguardarQuadro(el);
    expect(text(el)).not.toContain('Enviando ao aparelho…');
    await click(byRole('button', /^Enter$/, el));
    await waitFor(() => expect(text(el)).toContain('Enviando ao aparelho…'));
    const viva = Array.from(el.querySelectorAll('[aria-live="polite"]')).find((n) => n.textContent === 'Enviando ao aparelho…');
    expect(viva).toBeTruthy();
    await waitFor(() => expect(liberar).not.toBeNull());
    await act(async () => liberar!(json({ ok: true })));
    await waitFor(() => expect(text(el)).not.toContain('Enviando ao aparelho…'));
  });

  it('31.86: aparelho fora do ar (erro) mantém "Para revisar", sem o formulário de iniciar', async () => {
    backend.on('GET', /\/training$/, () => json([{ ...GRAVANDO, id: 'trn-3', status: 'recorded' }]));
    const el = await renderFocus(makeInstance(1, { state: 'error' }));
    await waitFor(() => expect(text(el)).toContain('Para revisar:'));
    expect(text(el)).toContain('Aparelho fora do ar: dá para revisar e salvar o fluxo');
    expect(allByRole('button', /Iniciar treinamento/, el)).toHaveLength(0);
    expect(byRole('button', /Responder a DM/, el)).toBeTruthy();
  });

  it('A4: a escolha de "Limpar o campo antes" não atravessa gravações: ao começar ou terminar uma, volta ao padrão', async () => {
    const el = await renderFocus(aparelho());
    await aguardarQuadro(el);
    expect(marcaLimpar(el).checked).toBe(false);
    await click(marcaLimpar(el));
    expect(marcaLimpar(el).checked).toBe(true);
    await act(async () => useTrainingStore.getState().definirGravando('android-01', true));
    expect(marcaLimpar(el).checked).toBe(true);
    await click(marcaLimpar(el));
    expect(marcaLimpar(el).checked).toBe(false);
    await act(async () => useTrainingStore.getState().definirGravando('android-01', false));
    expect(marcaLimpar(el).checked).toBe(false);
    await act(async () => useTrainingStore.getState().definirGravando('android-01', true));
    expect(marcaLimpar(el).checked).toBe(true);
  });

  it('A5: 400 bad_input de um envio com clear_first ganha a dica de desmarcar, sem desmarcar sozinho', async () => {
    useToastStore.setState({ toasts: [] });
    comGravacao();
    backend.on('POST', /\/input$/, () => apiError(400, 'bad_input', 'Campo não aceita limpeza.'));
    const el = await renderFocus(aparelho());
    await aguardarQuadro(el);
    await waitFor(() => expect(marcaLimpar(el).checked).toBe(true));
    await setValue(caixa(el), 'oi');
    await click(byRole('button', /^Enviar$/, el));
    await waitFor(() => expect(useToastStore.getState().toasts.length).toBeGreaterThan(0));
    expect(useToastStore.getState().toasts.some((t) => t.hint === 'Desmarque Limpar o campo antes e envie de novo.')).toBe(true);
    expect(marcaLimpar(el).checked).toBe(true);
  });

  it('31.86: a loja continua sem o Modo treinamento, mesmo fora do ar', async () => {
    const el = await renderFocus(makeInstance(1, { state: 'error', kind: 'store' }));
    expect(el.querySelector('section[aria-label="Modo treinamento"]')).toBeNull();
  });
});

describe('FocusPanel — aparelho sem frame (31.350, C)', () => {
  const frameDe = (id: string): FrameInfo => ({ id, ts: new Date().toISOString(), width: 1080, height: 2400, orientation: 'portrait', stale: false });

  it('sem frame no servidor (instance.frame nulo) não pede GET /frame: o 404 esperado vira erro no console; a tela diz que ainda não há imagem', async () => {
    backend.on('GET', /\/frame$/, () => new Response('', { status: 404 }));
    const el = await renderFocus(makeInstance(1, { state: 'online', frame: null }));
    await waitFor(() => expect(text(el)).toContain('Ainda não há imagem deste aparelho'));
    expect(backend.callsTo('GET', /\/frame$/)).toHaveLength(0);
  });

  it('quando o primeiro frame chega, a imagem é buscada', async () => {
    const ts = new Date().toISOString();
    backend.on('GET', /\/frame$/, () => new Response(new TextEncoder().encode('jpeg'), {
      status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'X-Frame-Id': 'f1', 'X-Frame-Ts': ts, 'X-Frame-Width': '1080', 'X-Frame-Height': '2400', 'X-Frame-Orientation': 'portrait' },
    }));
    const el = await renderFocus(makeInstance(1, { state: 'online', frame: null }));
    await waitFor(() => expect(text(el)).toContain('Ainda não há imagem deste aparelho'));
    await act(async () => { useAppStore.setState({ instances: { 'android-01': makeInstance(1, { state: 'online', frame: frameDe('f1') }) } }); });
    await waitFor(() => expect(el.querySelector('img')).not.toBeNull());
    expect(backend.callsTo('GET', /\/frame$/)).toHaveLength(1);
  });
});
