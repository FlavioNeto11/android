// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Worker } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { APPS, makeBinding, makeInstance, makePersona, makeRun, makeSession, makeSnapshot } from '../../test/fixtures';
import {
  FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor,
} from '../../test/harness';
import { InfraPage } from './InfraPage';

// Achados #168/#150: "remova o worker no painel" precisa ser um botão de verdade, não só uma frase na doc.

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', os: 'windows', os_version: '11', agent_version: '0.1.0',
    appium_mode: 'local', appium_url: 'http://127.0.0.1:4723', max_slots: 6, verbs: ['stop', 'hibernate'],
    state: 'online', observed_state: 'online', maintenance: false, state_detail: null, connected: true,
    local: false,
    resources: { cpu_percent: 10, cpu_count: 12, ram_total_mb: 65273, ram_free_mb: 46367, disk_free_gb: 400 },
    devices: [], enrolled_at: '2026-09-17T10:00:00Z', last_seen_at: '2026-09-22T10:00:00Z',
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
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    hydrateCount: 1,
    workers: { 'worker-lan-01': worker() },
    instances: {},
    instanceOrder: [],
  });
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
    root.render(<><InfraPage /><ConfirmHost /></>);
  });
  await waitFor(() => text().includes('Notebook da LAN'));
}

function noDialogo(nome: RegExp): HTMLElement {
  return byRole('button', nome, byRole('dialog', /.+/));
}

it('remover pede confirmação e só chama a rota depois do sim', async () => {
  backend.on('DELETE', /workers\/worker-lan-01/, () => json({ ok: true, worker_id: 'worker-lan-01' }));
  await render();

  await click(byRole('button', /^Remover$/));
  await waitFor(() => text().includes('Remover "Notebook da LAN"?'));
  expect(backend.callsTo('DELETE', /workers/)).toHaveLength(0);            // nada saiu antes da confirmação

  await click(noDialogo(/Remover servidor/));
  await waitFor(() => backend.callsTo('DELETE', /workers/).length === 1);
  const chamada = backend.callsTo('DELETE', /workers/)[0]!;
  // Worker conectado: a confirmação vira force=true, senão a remoção seria recusada com 409.
  expect(chamada.body).toEqual({ force: true });
});

it('rotacionar credencial mostra o token novo uma única vez', async () => {
  backend.on('POST', /workers\/worker-lan-01\/rotate-credential/, () => json({ credential: 'novo-token-secreto-123' }));
  await render();

  await click(byRole('button', /Rotacionar credencial/));
  await waitFor(() => text().includes('Rotacionar credencial de'));
  await click(noDialogo(/^Rotacionar credencial$/));

  await waitFor(() => text().includes('novo-token-secreto-123'));
  expect(text()).toContain('Credencial rotacionada');
});

// Achado #179: a queda do túnel SSH aparecia só como sintomas espalhados (aparelhos "sem ADB", worker "sem
// batida"), sem nada apontando a causa. O cartão do worker agora mostra a linha "Túnel" com o motivo.
it('mostra o túnel fora com o motivo quando transport_state é down', async () => {
  useAppStore.setState({
    workers: {
      'worker-lan-01': worker({
        transport_state: 'down',
        transport_detail: 'porta(s) local(is) do túnel recusando conexão: 127.0.0.1:15555',
        transport_since: '2026-09-22T09:00:00Z',
      }),
    },
  });
  await render();
  expect(text()).toContain('Túnel: fora');
  expect(text()).toContain('porta(s) local(is) do túnel recusando conexão');
});

it('não mostra a linha do túnel quando nunca foi sondado', async () => {
  await render();                                    // worker() default: transport_state ausente
  expect(text()).not.toContain('Túnel:');
});

// Achado #63: a tela era honesta sobre o worker e muda sobre si mesma — selo fixo "online", sem disco, sem
// capacidades, sem logs/evidências/fila/perfis por servidor.
describe('InfraPage — o central e as abas por servidor', () => {
  const evento = (kind: string, instance_id: string, message: string) => ({
    id: 1, ts: '2026-09-22T10:00:00Z', kind, level: 'info' as const, run_id: null, instance_id,
    objective_id: null, step_id: null, attempt_id: null, message, data: null,
  });

  async function comEstado(over: Record<string, unknown>): Promise<void> {
    useAppStore.setState({
      ...initialDataState, hydrated: true, hydrateCount: 1,
      workers: { 'worker-lan-01': worker({ devices: [
        { serial: 'emulator-5554', state: 'running', instance_id: 'android-13' },
      ] }) },
      instances: { 'android-13': makeInstance(13, { worker_id: 'worker-lan-01', state: 'stopped' }) },
      instanceOrder: ['android-13'],
      ...over,
    });
    await render();
  }

  it('o selo do central sai da saúde declarada, não de um "online" fixo', async () => {
    const health = { ...makeSnapshot().health, status: 'degraded' as const };
    await comEstado({ health, conn: { status: 'connected', attempt: 0, nextRetryAt: null, lastError: null,
                                      lastConnectedAt: Date.now() } });
    expect(text()).toContain('degradado');
  });

  it('as capacidades do worker aparecem (verbos e de quem é o Appium)', async () => {
    await comEstado({});
    expect(text()).toContain('verbos: stop, hibernate');
    expect(text()).toContain('Appium local');
  });

  // 29.11: o renderizador do emulador é capacidade do aparelho; o fallback silencioso sai em destaque.
  it('o aparelho mostra o renderizador selecionado, e o fallback aparece com o que foi pedido', async () => {
    await comEstado({
      instances: {
        'android-13': makeInstance(13, { worker_id: 'worker-lan-01', state: 'online',
          renderer: { configured: 'host', gles: 'host', vulkan: 'host', fallback: false } }),
        'android-14': makeInstance(14, { worker_id: 'worker-lan-01', state: 'online',
          renderer: { configured: 'host', gles: 'swiftshader', vulkan: 'swiftshader', fallback: true } }),
        'android-15': makeInstance(15, { worker_id: 'worker-lan-01', state: 'stopped' }),   // agente antigo: sem dado
      },
      instanceOrder: ['android-13', 'android-14', 'android-15'],
    });
    expect(text()).toContain('renderizador: GPU do host');
    expect(text()).toContain('renderizador: SwiftShader (pediu host)');
    expect(text().match(/renderizador/g)).toHaveLength(2);
  });

  it('a aba Registros mostra os eventos DOS APARELHOS daquele servidor, e não os dos outros', async () => {
    await comEstado({ recentEvents: [evento('log', 'android-13', 'reiniciei o system_server'),
                                     evento('log', 'android-01', 'coisa do central')] });
    await click(byRole('tab', /^Registros/, byRole('tablist', /Detalhes de worker-lan-01/)));
    expect(text()).toContain('reiniciei o system_server');
    expect(text()).not.toContain('coisa do central');
  });

  it('a aba Fila mostra a execução em voo daquele servidor', async () => {
    await comEstado({ runs: [makeRun({ instance_ids: ['android-13'], status: 'running' })] });
    await click(byRole('tab', /^Fila/, byRole('tablist', /Detalhes de worker-lan-01/)));
    expect(text()).toContain('r-0001');
  });

  // Evolução 2, onda E2 (N:N): Servidor → Aparelho → Persona(s). As N personas de cada aparelho saem dos devices[]
  // de GET /personas (uma leitura só), cada uma com o app do vínculo, a sessão AQUI e o atalho para abri-la.
  it('cada aparelho do servidor mostra as personas vinculadas, e o clique abre a persona', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([
      makePersona('ig-1', 'Marina Costa', { username: 'marina.fotografa', devices: [
        makeBinding('android-13', { is_primary: true, session: makeSession('session_ready', 'android-13') }),
        makeBinding('android-01'),
      ] }),
      makePersona('ig-2', 'Rafael Lima', { devices: [makeBinding('android-13', { app_id: 'chrome', session: null })] }),
    ]));
    await comEstado({ apps: [{ ...APPS[0]!, id: 'instagram', name: 'Instagram' }, { ...APPS[0]!, id: 'chrome', name: 'Chrome' }] });
    const aqui = await waitFor(() => byRole('list', 'Personas em android-13'));
    const t = text(aqui);
    expect(t).toContain('Marina Costa');
    expect(t).toContain('@marina.fotografa');
    expect(t).toContain('Instagram');
    expect(t).toContain('Conectado');
    expect(t).toContain('Rafael Lima');
    expect(t).toContain('Chrome');
    expect(allByRole('button', /^Abrir a persona/, aqui)).toHaveLength(2);
    await click(byRole('button', 'Abrir a persona Rafael Lima (Chrome)', aqui));
    expect(useUiStore.getState().rota.segmentos[0]).toBe('ig-2');
    // A aba do servidor lista as mesmas personas por aparelho.
    await click(byRole('tab', /^Personas e apps/, byRole('tablist', /Detalhes de worker-lan-01/)));
    expect(text()).toContain('@marina.fotografa (Conectado) · Rafael Lima');
  });
});

// ---------------------------------------------------------------- aparelho novo sem editar YAML (item 4.5)
describe('adotar aparelho anunciado', () => {
  it('o aparelho que o worker anuncia e não é instância aparece com botão de adotar', async () => {
    // Antes, o inventário do `hello` era descartado: acrescentar aparelho era editar config.yaml, reinstalar a
    // tarefa do túnel e reiniciar o backend. Aqui é um clique, e a resposta diz o mapa do túnel novo.
    useAppStore.setState({
      workers: {
        'worker-lan-01': worker({
          devices: [{ serial: 'emulator-5560', avd_name: 'worker-07', state: 'online', adb_port: 5561,
                      instance_id: null }],
        }),
      },
    });
    backend.on('POST', /\/api\/workers\/worker-lan-01\/devices\/adopt$/, () => json({
      instance: makeInstance(16, { id: 'android-16' }),
      tunnel_map: '15555:5555,15556:5561', tunnel_map_file: 'C:/git/android/data/tunnel/worker-lan-01.map',
    }, 201));
    await render();
    expect(text()).toContain('emulator-5560');
    expect(text()).toContain('adb 5561');

    await click(byRole('button', /^Adotar$/i));
    await waitFor(() => backend.callsTo('POST', /devices\/adopt$/).length === 1);
    const chamadas = backend.callsTo('POST', /devices\/adopt$/);
    expect(chamadas[0]?.body).toEqual({ serial: 'emulator-5560' });
  });

  it('aparelho anunciado sem porta de ADB não pode ser adotado, e a tela diz por quê', async () => {
    useAppStore.setState({
      workers: {
        'worker-lan-01': worker({
          devices: [{ serial: 'emulator-5560', avd_name: null, state: 'online', adb_port: null,
                      instance_id: null }],
        }),
      },
    });
    await render();
    expect(text()).toContain('sem porta de ADB declarada');
    // O motivo entra no NOME acessível do botão, por isso ele não bate mais com /^Adotar$/.
    expect(byRole('button', /Adotar.*indisponível/i).getAttribute('aria-disabled')).toBe('true');
    expect(backend.callsTo('POST', /devices\/adopt$/)).toHaveLength(0);
  });

  it('inventário divergente aparece no aparelho do servidor', async () => {
    const inst = makeInstance(3, {
      worker_id: 'worker-lan-01', kind: 'external', inventory_state: 'divergent',
      inventory_detail: 'o worker declara a porta de ADB 5557 para android-03 e o túnel encaminha para 5555',
    });
    useAppStore.setState({ instances: { [inst.id]: inst }, instanceOrder: [inst.id] });
    await render();
    expect(text()).toContain('inventário divergente');
  });
});

// Item 10.1 / achado #137: a cópia do agente na máquina do worker não é checkout, e as duas pontas diziam
// `0.1.0` — nada no painel dizia que aquela máquina roda código velho. E item 10.4 / achado #180: sem KVM o
// emulador não sobe em tempo útil, e isso só aparecia como comando estourando prazo do outro lado da rede.
describe('o que o cartão do worker passou a denunciar', () => {
  it('mostra "agente defasado" com a versão que o central espera', async () => {
    useAppStore.setState({
      workers: {
        'worker-lan-01': worker({
          agent_version: '0.1.0+abc1234', expected_agent_version: '0.1.0+def5678', agent_outdated: true,
        }),
      },
    });
    await render();
    expect(text()).toContain('agente defasado');
    const etiqueta = [...document.querySelectorAll('[title]')]
      .find((e) => e.textContent?.includes('agente defasado'));
    expect(etiqueta?.getAttribute('title')).toContain('0.1.0+def5678');
  });

  it('não mostra nada quando o agente está na mesma versão do central', async () => {
    useAppStore.setState({
      workers: {
        'worker-lan-01': worker({
          agent_version: '0.1.0+abc1234', expected_agent_version: '0.1.0+abc1234', agent_outdated: false,
        }),
      },
    });
    await render();
    expect(text()).not.toContain('agente defasado');
  });

  it('denuncia o KVM sem permissão do worker Linux', async () => {
    useAppStore.setState({
      workers: { 'worker-lan-01': worker({ os: 'linux', accel: 'kvm-inacessivel' }) },
    });
    await render();
    expect(text()).toContain('KVM sem permissão');
  });

  it('não denuncia nada quando o KVM está utilizável', async () => {
    useAppStore.setState({ workers: { 'worker-lan-01': worker({ os: 'linux', accel: 'kvm' }) } });
    await render();
    expect(text()).not.toContain('sem KVM');
    expect(text()).not.toContain('KVM sem permissão');
  });
});

// Achado #167: a tela que responde pela seção 6 do pedido (infraestrutura, dado velho, manutenção) só tinha
// testadas as funções auxiliares de `infraState`. O que faltava era a RENDERIZAÇÃO: o selo de dado velho, o
// botão de manutenção chegando à rota, o token de inscrição aparecendo uma vez e o aparelho órfão.
describe('o que a tela precisa dizer sobre o dado e sobre a máquina (achado #167)', () => {
  it('último contato velho avisa que o que está abaixo pode não valer mais', async () => {
    await render();                                   // worker() default: last_seen_at de ontem
    expect(text()).toContain('os dados abaixo podem estar desatualizados');
  });

  it('batida recente não avisa nada — o aviso é sobre o dado, não decoração fixa', async () => {
    useAppStore.setState({
      workers: { 'worker-lan-01': worker({ last_seen_at: new Date().toISOString() }) },
    });
    await render();
    expect(text()).not.toContain('os dados abaixo podem estar desatualizados');
  });

  it('manutenção chama a rota do worker com o estado novo e o cartão passa a oferecer a volta', async () => {
    backend.on('POST', /workers\/worker-lan-01\/maintenance/, () =>
      json(worker({ maintenance: true, state: 'maintenance' })));
    await render();

    await click(byRole('button', /^Manutenção$/));
    await waitFor(() => backend.callsTo('POST', /maintenance/).length === 1);
    expect(backend.callsTo('POST', /maintenance/)[0]!.body).toEqual({ on: true });

    // O worker em manutenção não oferece "entrar em manutenção" de novo: oferece sair, e o selo diz onde está.
    useAppStore.setState({
      workers: { 'worker-lan-01': worker({ maintenance: true, state: 'maintenance' }) },
    });
    await render();
    expect(text()).toContain('Retomar atribuições');
    expect(text()).toContain('em manutenção');
  });

  it('o token de inscrição aparece uma única vez, e não fica guardado na tela', async () => {
    backend.on('POST', /workers\/enroll/, () =>
      json({ enrollment_token: 'token-de-inscricao-unico', expires_at: '2026-09-23T12:00:00Z' }));
    await render();

    expect(text()).not.toContain('token-de-inscricao-unico');
    await click(byRole('button', /Inscrever servidor/));
    await waitFor(() => text().includes('token-de-inscricao-unico'));
    // Uma vez: o token não é lido de volta do backend, então uma nova montagem da tela não pode reexibi-lo.
    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    expect(text()).not.toContain('token-de-inscricao-unico');
  });

  it('aparelho amarrado a servidor não inscrito aparece com o nome do servidor que falta', async () => {
    useAppStore.setState({
      instances: { 'android-09': makeInstance(9, { worker_id: 'worker-que-sumiu' }) },
      instanceOrder: ['android-09'],
    });
    await render();
    expect(text()).toContain('Aparelhos amarrados a um servidor que não está inscrito');
    expect(text()).toContain('android-09 → worker-que-sumiu');
  });
});

// ---------------------------------------------------------------- criar e aposentar aparelho (adendo v0.26)
describe('criar aparelho neste servidor', () => {
  async function abrirCriacao(): Promise<HTMLElement> {
    await click(byRole('button', /^Criar aparelho$/));
    return waitFor(() => byRole('dialog', /Criar aparelho neste servidor/));
  }

  function caixaLigar(dialogo: HTMLElement): HTMLInputElement {
    return Array.from(dialogo.querySelectorAll('label'))
      .find((l) => l.textContent === 'Ligar depois de criar')?.querySelector('input') as HTMLInputElement;
  }

  it('manda o corpo do contrato e, com o 202, fecha e põe o aparelho novo no store', async () => {
    useAppStore.setState({ apps: APPS });
    backend.on('POST', /^\/api\/instances$/, () => json({
      instance: makeInstance(17, { origin: 'dynamic', worker_id: null }), instance_id: 'android-17',
      command_id: 'cmd-17', command_state: 'dispatched', deduplicated: false, start: 'after_create',
    }, 202));
    await render();

    const dialogo = await abrirCriacao();
    await setValue(byRole('combobox', 'Aplicativo', dialogo) as HTMLSelectElement, 'qa');
    await setValue(byRole('textbox', 'Imagem do sistema', dialogo) as HTMLInputElement,
                   'system-images;android-34;google_apis_playstore;x86_64');
    await setValue(byRole('textbox', 'RAM', dialogo) as HTMLInputElement, '3072');
    await click(caixaLigar(dialogo));
    await click(byRole('button', /^Criar$/, dialogo));

    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/instances$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /^\/api\/instances$/)[0]?.body).toEqual({
      app_id: 'qa', system_image: 'system-images;android-34;google_apis_playstore;x86_64', ram_mb: 3072,
      create: true, start: true, idempotency_key: expect.stringMatching(/.{8,}/),
    });
    await waitFor(() => expect(allByRole('dialog', /Criar aparelho neste servidor/)).toHaveLength(0));
    expect(useAppStore.getState().instances['android-17']?.origin).toBe('dynamic');
  });

  it('campos vazios não vão como valor: o servidor usa os padrões dele', async () => {
    backend.on('POST', /^\/api\/instances$/, () => json({
      instance: makeInstance(18), instance_id: 'android-18', command_id: 'cmd-18', command_state: 'dispatched',
      deduplicated: false, start: 'not_requested',
    }, 202));
    await render();
    const dialogo = await abrirCriacao();
    await click(byRole('button', /^Criar$/, dialogo));
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/instances$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /^\/api\/instances$/)[0]?.body).toMatchObject({
      app_id: null, system_image: null, ram_mb: null, create: true, start: false,
    });
  });

  it('RAM fora da faixa e imagem fora do formato do SDK são recusadas antes de enviar', async () => {
    await render();
    const dialogo = await abrirCriacao();
    await setValue(byRole('textbox', 'RAM', dialogo) as HTMLInputElement, '512');
    await setValue(byRole('textbox', 'Imagem do sistema', dialogo) as HTMLInputElement, 'android-34');
    await click(byRole('button', /^Criar$/, dialogo));
    await waitFor(() => expect(text(dialogo)).toContain('Entre 1.024 e 32.768 MB.'));
    expect(text(dialogo)).toContain('Use o formato do SDK');
    expect(backend.callsTo('POST', /^\/api\/instances$/)).toHaveLength(0);
  });

  it('o 409 de teto fica no diálogo com os números, aponta Limites e a nova tentativa usa a MESMA chave', async () => {
    backend.on('POST', /^\/api\/instances$/, () => json({ detail: {
      code: 'teto_de_aparelhos',
      message: 'Este servidor já tem 3 aparelho(s) e o teto decidido é 3 (max_devices).',
      devices: 3, max_devices: 3,
    } }, 409));
    await render();
    const dialogo = await abrirCriacao();
    await click(byRole('button', /^Criar$/, dialogo));

    const alerta = await waitFor(() => byRole('alert', /Teto de aparelhos atingido/, dialogo));
    expect(text(alerta)).toContain('Este servidor já tem 3 de 3 aparelhos.');
    expect(text(alerta)).toContain('Configuração → Limites → Por servidor');
    expect(text(alerta)).toContain('o teto decidido é 3 (max_devices)');      // a mensagem do backend, como veio
    expect(byRole('button', /Abrir Configuração → Limites/, dialogo)).toBeTruthy();

    // Tentar de novo (por exemplo, depois de subir o teto) não pode virar um SEGUNDO pedido aos olhos do backend.
    await click(byRole('button', /^Criar$/, dialogo));
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/instances$/)).toHaveLength(2));
    const chaves = backend.callsTo('POST', /^\/api\/instances$/)
      .map((c) => (c.body as { idempotency_key: string }).idempotency_key);
    expect(chaves[1]).toBe(chaves[0]);
  });

  it('o 409 de disco diz quanto há livre e quanto é preciso', async () => {
    backend.on('POST', /^\/api\/instances$/, () => json({ detail: {
      code: 'disco_insuficiente', message: 'Este servidor tem 8.5 GB livres e o mínimo para criar mais um AVD é 12 GB.',
      disk_free_gb: 8.5, min_free_disk_gb: 12,
    } }, 409));
    await render();
    const dialogo = await abrirCriacao();
    await click(byRole('button', /^Criar$/, dialogo));
    const alerta = await waitFor(() => byRole('alert', /Disco insuficiente/, dialogo));
    expect(text(alerta)).toContain('Livre: 8,5 GB; mínimo para mais um aparelho: 12 GB.');
  });

  it('no worker remoto o botão existe desabilitado, com o motivo', async () => {
    await render();
    const botao = byRole('button', /Criar aparelho.*indisponível/);
    expect(botao.getAttribute('aria-disabled')).toBe('true');
    expect(text(botao)).toContain('o agente só conhece o inventário do worker.yaml dele');
    await click(botao);
    expect(allByRole('dialog', /Criar aparelho/)).toHaveLength(0);
  });
});

describe('aposentar aparelho', () => {
  function comAparelhos(): void {
    useAppStore.setState({
      instances: {
        'android-01': makeInstance(1, { worker_id: null, origin: 'config' }),
        'android-17': makeInstance(17, { worker_id: null, origin: 'dynamic', state: 'stopped' }),
        // Adotado do worker também é `dynamic`, mas o AVD vive na outra máquina: não se aposenta por aqui.
        'android-13': makeInstance(13, { worker_id: 'worker-lan-01', origin: 'dynamic' }),
      },
      instanceOrder: ['android-01', 'android-13', 'android-17'],
    });
  }

  it('só a instância dinâmica deste servidor tem o botão; confirmar chama o DELETE e ela sai da lista', async () => {
    comAparelhos();
    backend.on('DELETE', /^\/api\/instances\/android-17$/, () =>
      json({ instance_id: 'android-17', retired_at: '2026-09-28T10:00:00Z', avd_removed: true }));
    await render();

    expect(allByRole('button', /^Aposentar/).map((b) => b.getAttribute('aria-label'))).toEqual(['Aposentar android-17']);

    await click(byRole('button', 'Aposentar android-17'));
    await waitFor(() => text().includes('Aposentar android-17?'));
    expect(text()).toContain('o AVD dele');
    expect(backend.callsTo('DELETE', /instances/)).toHaveLength(0);           // nada saiu antes da confirmação

    await click(noDialogo(/Aposentar aparelho/));
    await waitFor(() => expect(backend.callsTo('DELETE', /^\/api\/instances\/android-17$/)).toHaveLength(1));
    await waitFor(() => expect(allByRole('button', /Abrir android-17/)).toHaveLength(0));
    expect(allByRole('button', /Abrir android-01/)).toHaveLength(1);
  });

  it('a recusa fica na linha do aparelho, com o próximo passo e a mensagem do backend', async () => {
    comAparelhos();
    backend.on('DELETE', /^\/api\/instances\/android-17$/, () =>
      apiError(409, 'vinculo_ativo', 'android-17 hospeda o perfil p-1: a sessão dele vive no AVD que seria apagado.'));
    await render();

    await click(byRole('button', 'Aposentar android-17'));
    await click(await waitFor(() => noDialogo(/Aposentar aparelho/)));
    const alerta = await waitFor(() => byRole('alert', /Desvincule a persona/));
    expect(text(alerta)).toContain('android-17 hospeda o perfil p-1');
    expect(alerta.closest('li')?.textContent).toContain('android-17');
    expect(allByRole('button', /Abrir android-17/)).toHaveLength(1);         // recusado: continua no parque
  });
});

// Tarefa 02 da revisão de UX: "Vagas ocupadas: 5 de 4" aparecia sem alerta. O número é real (o backend conta igual,
// `slots_used`), então a tela destaca e explica em vez de esconder; e servidor fora do ar não inventa ocupação.
describe('InfraPage — vagas por servidor', () => {
  it('ocupação acima da capacidade fica destacada e explicada', async () => {
    const ligados = [1, 2, 3, 5, 6].map((n) => makeInstance(n, { state: 'online', worker_id: 'central' }));
    useAppStore.setState({
      workers: {
        central: worker({ id: 'central', name: 'central', local: true, max_slots: 4 }),
        'worker-lan-01': worker(),
      },
      instances: Object.fromEntries(ligados.map((i) => [i.id, i])),
      instanceOrder: ligados.map((i) => i.id),
    });
    await render();
    expect(text()).toContain('5 de 4');
    const nota = document.querySelector('[role="note"]') as HTMLElement;
    expect(text(nota)).toContain('Acima da capacidade: 5 aparelhos ligados para 4 vagas');
  });

  it('servidor fora do ar: a ocupação é "?" e a tela diz por quê, em vez de um número velho', async () => {
    useAppStore.setState({
      workers: { 'worker-lan-01': worker({ connected: false, state: 'offline', devices: [
        { serial: 'emulator-5554', state: 'running', instance_id: 'android-13' },
      ] }) },
      instances: { 'android-13': makeInstance(13, { worker_id: 'worker-lan-01', state: 'online' }) },
      instanceOrder: ['android-13'],
    });
    await render();
    expect(text()).toContain('? de 6');
    expect(text()).toContain('a ocupação das vagas lá não é conhecida agora');
    expect(document.querySelector('[role="note"]')).toBeNull();
  });
});
