import { describe, expect, it } from 'vitest';
import type { Command, Instance, InstanceAction, InstanceState, Worker } from '../../api/types';
import {
  NO_FRAME_TITLE, bulkActionsFor, bulkBlockersFor, canHibernate, countByServer, countByState, noFrameTitle,
  focusActionGroups, primaryActionFor, quickActionsFor, serverHintOf, unsupportedReason, type FocusActionGroups,
  type ServerHint,
} from './deviceState';

describe('primaryActionFor — ação principal do cartão', () => {
  it('hibernado → "wake" (Acordar); os demais estados seguem como antes', () => {
    expect(primaryActionFor('hibernated')).toBe('wake');
    expect(primaryActionFor('absent')).toBe('create');
    expect(primaryActionFor('stopped')).toBe('start');
    expect(primaryActionFor('error')).toBe('restart');
  });

  it('não oferece nada para online nem para estados de transição', () => {
    const none: InstanceState[] = ['online', 'booting', 'stopping'];
    for (const s of none) expect(primaryActionFor(s)).toBeNull();
  });
});

describe('estado sem tela ao vivo', () => {
  it('hibernado explica que acorda rápido e não ocupa RAM', () => {
    expect(NO_FRAME_TITLE.hibernated).toBe('Hibernado — acorda em segundos, sem ocupar RAM');
  });

  it('todo estado que não é online tem um título', () => {
    const states: Exclude<InstanceState, 'online'>[] = ['absent', 'stopped', 'hibernated', 'booting', 'stopping', 'error'];
    for (const s of states) expect(NO_FRAME_TITLE[s]).toBeTruthy();
  });
});

describe('Hibernar', () => {
  it('só para aparelho online E com health.features.hibernation ligado', () => {
    expect(canHibernate('online', true)).toBe(true);
    expect(canHibernate('online', false)).toBe(false);
    expect(canHibernate('hibernated', true)).toBe(false);
    expect(canHibernate('stopped', true)).toBe(false);
    expect(canHibernate('booting', true)).toBe(false);
  });

  it('barra em lote: Hibernar depende do recurso; Acordar e Criar AVD dependem da seleção', () => {
    expect(bulkActionsFor({ hasAbsent: false, hasHibernated: false, hibernation: false }))
      .toEqual(['start', 'stop', 'restart', 'install_apk', 'open_app']);
    expect(bulkActionsFor({ hasAbsent: false, hasHibernated: false, hibernation: true }))
      .toEqual(['start', 'stop', 'hibernate', 'restart', 'install_apk', 'open_app']);
    expect(bulkActionsFor({ hasAbsent: true, hasHibernated: true, hibernation: false }))
      .toEqual(['create', 'wake', 'start', 'stop', 'restart', 'install_apk', 'open_app']);
  });
});

// O aparelho de outra máquina não faz tudo o que o emulador local faz. Oferecer o botão de todo jeito foi o que
// produziu "Parar" que se desfaz, "Hibernar" ignorado e "Criar AVD" virando AVD fantasma.
const REMOTO = { supported_verbs: ['home', 'back', 'recents', 'install_apk', 'open_app', 'start'] };
const LOCAL = { supported_verbs: ['create', 'start', 'stop', 'hibernate', 'wake', 'restart', 'reset', 'install_apk', 'open_app', 'home', 'back', 'recents'] };

describe('capacidade do aparelho filtra o que o painel oferece', () => {
  it('sem a lista, nada é escondido — o pré-voo do backend ainda recusa, e com explicação', () => {
    expect(primaryActionFor('absent', undefined)).toBe('create');
    expect(primaryActionFor('absent', { supported_verbs: [] })).toBe('create');
    expect(canHibernate('online', true, undefined)).toBe(true);
  });

  it('aparelho remoto não oferece Criar AVD nem Hibernar', () => {
    expect(canHibernate('online', true, REMOTO)).toBe(false);
    expect(canHibernate('online', true, LOCAL)).toBe(true);
    // `absent` num remoto não existe de verdade, mas se o estado chegar assim o cartão cai em "Iniciar"
    // (que ali significa reconectar o ADB) em vez de oferecer a criação de um AVD que nunca será usado.
    expect(primaryActionFor('absent', REMOTO)).toBe('start');
    expect(primaryActionFor('hibernated', REMOTO)).toBe('start');
    expect(primaryActionFor('error', REMOTO)).toBe('start');
  });

  it('seleção mista só oferece o que TODOS aceitam', () => {
    const ctx = { hasAbsent: true, hasHibernated: true, hibernation: true };
    expect(bulkActionsFor({ ...ctx, selected: [LOCAL, REMOTO] }))
      .toEqual(['start', 'install_apk', 'open_app']);
    expect(bulkActionsFor({ ...ctx, selected: [LOCAL, LOCAL] }))
      .toEqual(['create', 'wake', 'start', 'stop', 'hibernate', 'restart', 'install_apk', 'open_app']);
  });
});

describe('countByState — resumo da grade', () => {
  it('conta hibernados à parte (não são online nem parados) e omite estados vazios', () => {
    const list = (['online', 'hibernated', 'hibernated', 'stopped', 'online', 'error'] as InstanceState[]).map((state) => ({ state }));
    expect(countByState(list)).toEqual([
      { state: 'online', count: 2 },
      { state: 'hibernated', count: 2 },
      { state: 'stopped', count: 1 },
      { state: 'error', count: 1 },
    ]);
    expect(countByState([])).toEqual([]);
  });
});

// ---------------------------------------------------------------- achado #61: as TRÊS realidades do aparelho

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

const NO_WORKER: Pick<Instance, 'id' | 'worker_id'> = { id: 'android-13', worker_id: null };
const NA_LAN: Pick<Instance, 'id' | 'worker_id'> = { id: 'android-13', worker_id: 'worker-lan-01' };

describe('serverHintOf — em que servidor o aparelho está', () => {
  it('aparelho do central não ganha selo (nem sem worker_id, nem apontando para o worker local)', () => {
    expect(serverHintOf(NO_WORKER, {})).toBeNull();
    const local = worker({ id: 'central', name: 'Este servidor', local: true });
    expect(serverHintOf({ id: 'android-01', worker_id: 'central' }, { central: local })).toBeNull();
  });

  it('traz o processo, o AVD e a porta que o WORKER reporta, não o palpite local', () => {
    const w = worker({ devices: [{ serial: 'emulator-5554', avd_name: 'worker-01', state: 'stopped',
                                   adb_port: 5555, instance_id: 'android-13' }] });
    expect(serverHintOf(NA_LAN, { 'worker-lan-01': w })).toEqual({
      id: 'worker-lan-01', name: 'Notebook da LAN', enrolled: true, connected: true, process: 'stopped',
      detail: null, avd_name: 'worker-01', serial: 'emulator-5554', adb_port: 5555, transport: null,
    });
  });

  it('servidor que não está inscrito vira órfão declarado, não "aparelho do central"', () => {
    const hint = serverHintOf(NA_LAN, {});
    expect(hint?.enrolled).toBe(false);
    expect(hint?.name).toBe('worker-lan-01');
  });
});

describe('noFrameTitle — o mesmo "parado" tem três causas diferentes', () => {
  const hint = (over: Partial<ServerHint> = {}): ServerHint => ({
    id: 'worker-lan-01', name: 'Notebook da LAN', enrolled: true, connected: true, process: null,
    detail: null, avd_name: null, serial: null, adb_port: null, transport: null, ...over,
  });

  it('desligado de propósito no worker NÃO é problema de conexão', () => {
    expect(noFrameTitle('stopped', 'external', hint({ process: 'stopped' })))
      .toBe('Emulador desligado em Notebook da LAN');
  });

  it('servidor fora do ar admite que não se sabe o estado do emulador', () => {
    expect(noFrameTitle('stopped', 'external', hint({ connected: false, process: 'online' })))
      .toBe('Servidor Notebook da LAN fora do ar — estado desconhecido');
  });

  it('emulador ligado lá com ADB inalcançável culpa o Android de lá — não o túnel, que ninguém sondou', () => {
    // Medido: android-12/15 travados no próprio notebook (adb `offline` lá) apareciam como "túnel ADB caiu".
    expect(noFrameTitle('stopped', 'external', hint({ process: 'running' })))
      .toBe('Emulador ligado em Notebook da LAN, mas o Android lá não responde ao ADB');
    expect(noFrameTitle('stopped', 'external', hint({ process: 'running', transport: 'up' })))
      .toBe('Emulador ligado em Notebook da LAN, mas o Android lá não responde ao ADB');
  });

  it('só a sonda do túnel autoriza dizer que o túnel caiu', () => {
    expect(noFrameTitle('stopped', 'external', hint({ process: 'running', transport: 'down' })))
      .toBe('Túnel para Notebook da LAN fora — o emulador lá está ligado');
  });

  it('"unknown" não é "ligado": aparelho que o agente não gere ou sonda que falhou', () => {
    expect(noFrameTitle('stopped', 'external', hint({ process: 'unknown' })))
      .toBe('Notebook da LAN não sabe o estado do emulador deste aparelho');
  });

  it('serverHintOf carrega a sonda do túnel do worker', () => {
    const w = { id: 'worker-lan-01', name: 'Notebook da LAN', connected: true, local: false, transport_state: 'down',
                devices: [{ instance_id: 'android-12', state: 'running' }] } as unknown as Worker;
    expect(serverHintOf(NA_LAN, { 'worker-lan-01': w })?.transport).toBe('down');
  });

  it('sem worker o texto de antes continua valendo, e o local nunca fala em outra máquina', () => {
    expect(noFrameTitle('stopped', 'external')).toBe('Sem conexão ADB com a outra máquina');
    expect(noFrameTitle('stopped')).toBe('Emulador desligado');
    expect(noFrameTitle('booting', 'external', hint({ process: 'booting' }))).toBe('Iniciando o emulador…');
  });
});

describe('countByServer — resumo por servidor (item 11.5, seleção rápida)', () => {
  it('tudo local: um único balde "aqui", sem consultar workers', () => {
    const insts = [{ id: 'android-01', worker_id: null }, { id: 'android-02', worker_id: null }];
    expect(countByServer(insts, {})).toEqual([{ id: null, name: 'Este servidor', ids: ['android-01', 'android-02'] }]);
  });

  it('worker local (central) cai no mesmo balde "aqui" que sem worker_id', () => {
    const central = worker({ id: 'central', name: 'Este servidor', local: true });
    const insts = [{ id: 'android-01', worker_id: null }, { id: 'android-02', worker_id: 'central' }];
    expect(countByServer(insts, { central })).toEqual([{ id: null, name: 'Este servidor', ids: ['android-01', 'android-02'] }]);
  });

  it('agrupa por servidor remoto e ordena os remotos por nome, com "aqui" sempre primeiro', () => {
    const lan = worker({ id: 'worker-lan-01', name: 'Notebook da LAN' });
    const zeta = worker({ id: 'worker-zeta', name: 'Zeta' });
    const insts = [
      { id: 'android-13', worker_id: 'worker-lan-01' },
      { id: 'android-01', worker_id: null },
      { id: 'android-14', worker_id: 'worker-zeta' },
      { id: 'android-15', worker_id: 'worker-lan-01' },
    ];
    expect(countByServer(insts, { 'worker-lan-01': lan, 'worker-zeta': zeta })).toEqual([
      { id: null, name: 'Este servidor', ids: ['android-01'] },
      { id: 'worker-lan-01', name: 'Notebook da LAN', ids: ['android-13', 'android-15'] },
      { id: 'worker-zeta', name: 'Zeta', ids: ['android-14'] },
    ]);
  });

  it('worker órfão (desinscrito) ainda vira balde, com o próprio id como nome', () => {
    const insts = [{ id: 'android-13', worker_id: 'worker-sumiu' }];
    expect(countByServer(insts, {})).toEqual([{ id: 'worker-sumiu', name: 'worker-sumiu', ids: ['android-13'] }]);
  });

  it('lista vazia não produz baldes', () => {
    expect(countByServer([], {})).toEqual([]);
  });
});

// ---------------------------------------------------------------- achado #62: capacidade ANTES do clique

type Capacidade = Pick<Instance, 'id' | 'state' | 'kind' | 'supported_verbs'>;

const LOJA: Capacidade = { id: 'android-11', state: 'online', kind: 'store',
                           supported_verbs: ['start', 'stop', 'restart'] };
const label = (a: InstanceAction): string => a;

describe('quickActionsFor — o foco não oferece verbo que o aparelho recusa', () => {
  it('a loja não oferece Instalar APK nem Abrir app: ela explica por quê', () => {
    const offers = new Map(quickActionsFor(LOJA, true, label).map((o) => [o.action, o.disabledReason]));
    expect(offers.get('install_apk')).toContain('aparelho-loja');
    expect(offers.get('open_app')).toContain('aparelho-loja');
    expect(offers.get('hibernate')).toContain('não aceita');
    expect(offers.get('stop')).toBeNull();        // este ela aceita, e está online
  });

  it('capacidade vem antes do estado: verbo não suportado não vira "ligue o aparelho"', () => {
    const offers = new Map(quickActionsFor({ ...LOJA, state: 'stopped' }, true, label)
      .map((o) => [o.action, o.disabledReason]));
    expect(offers.get('install_apk')).toContain('não aceita');
    expect(offers.get('start')).toBeNull();
  });

  it('sem lista de verbos (backend antigo) nada é travado por capacidade', () => {
    const generica: Capacidade = { id: 'android-01', state: 'online', kind: 'emulator', supported_verbs: [] };
    const offers = new Map(quickActionsFor(generica, true, label).map((o) => [o.action, o.disabledReason]));
    expect(offers.get('install_apk')).toBeNull();
    expect(offers.get('start')).toContain('instância parada');
  });
});

describe('bulkBlockersFor — o verbo que sumiu da barra passa a dizer quem o impede', () => {
  it('nomeia os aparelhos que não aceitam o verbo', () => {
    const sel = [{ id: 'android-01', supported_verbs: [] }, { id: 'android-11', supported_verbs: ['start'] }];
    expect(bulkBlockersFor(sel, 'install_apk')).toEqual(['android-11']);
    expect(bulkBlockersFor(sel, 'start')).toEqual([]);
    expect(bulkBlockersFor(undefined, 'start')).toEqual([]);
  });
});

describe('unsupportedReason — a frase diz POR QUE, não só "indisponível"', () => {
  it('cita a loja, a outra máquina, ou a ausência de declaração', () => {
    expect(unsupportedReason(LOJA, 'install_apk', 'Instalar APK')).toContain('aparelho-loja');
    expect(unsupportedReason({ kind: 'external', supported_verbs: ['start'] }, 'create', 'Criar AVD'))
      .toContain('outra máquina');
    expect(unsupportedReason({ kind: 'emulator', supported_verbs: ['start'] }, 'create', 'Criar AVD'))
      .toContain('não declarou');
    expect(unsupportedReason(LOJA, 'stop', 'Parar')).toBeNull();
  });
});

describe('focusActionGroups — as ações do Foco em grupos, decididas como no cartão', () => {
  const aparelho = (over: Partial<Capacidade> = {}): Capacidade =>
    ({ id: 'android-01', state: 'online', kind: 'emulator', supported_verbs: [], ...over });
  const emVoo = (verb: string, state: Command['state'] = 'running'): Command => ({
    id: 'cmd-1', instance_id: 'android-01', worker_id: null, verb, state, fence: 1, requested_by: 'painel',
    reason: null, attempt: 1, created_at: '2026-09-28T10:00:00Z', dispatched_at: null, acked_at: null,
    started_at: null, finished_at: null,
  });
  const acoes = (g: FocusActionGroups, grupo: keyof Omit<FocusActionGroups, 'primary' | 'unavailable'>) =>
    g[grupo].map((i) => i.action);
  const acionaveis = (g: FocusActionGroups) =>
    [g.primary, ...g.lifecycle, ...g.apps, ...g.danger].filter((i) => i !== null);

  it('online com todos os verbos: cada grupo com o seu, nada indisponível e nada bloqueado por comando', () => {
    const g = focusActionGroups(aparelho(), true, undefined);
    expect(g.primary).toBeNull();                                   // online não tem ação principal
    expect(acoes(g, 'lifecycle')).toEqual(['start', 'stop', 'hibernate', 'restart']);
    expect(acoes(g, 'apps')).toEqual(['open_app', 'install_apk', 'verify_app']);
    expect(acoes(g, 'manual')).toEqual(['back', 'home', 'recents', 'enter', 'delete']);
    expect(acoes(g, 'observe')).toEqual(['refresh_frame', 'reload_context', 'hierarchy']);
    expect(acoes(g, 'danger')).toEqual(['reset']);
    expect(g.unavailable).toEqual([]);
    const porAcao = new Map([...g.lifecycle, ...g.apps, ...g.manual].map((i) => [i.action, i.disabledReason]));
    expect(porAcao.get('start')).toContain('parada');                // estado, não capacidade
    for (const a of ['stop', 'hibernate', 'restart', 'open_app', 'install_apk', 'verify_app', 'back', 'enter'] as const) {
      expect(porAcao.get(a)).toBeNull();
    }
  });

  it('sem hibernação ligada, Hibernar não aparece (o backend responderia 409)', () => {
    expect(acoes(focusActionGroups(aparelho(), false, undefined), 'lifecycle')).not.toContain('hibernate');
  });

  it('aparelho sem install_apk: sai de Apps e vai para Indisponíveis com o motivo', () => {
    const g = focusActionGroups(aparelho({ kind: 'store', supported_verbs: ['start', 'stop', 'restart', 'open_app'] }),
                                false, undefined);
    expect(acoes(g, 'apps')).toEqual(['open_app', 'verify_app']);
    const inst = g.unavailable.find((u) => u.action === 'install_apk');
    expect(inst?.label).toBe('Instalar app');
    expect(inst?.reason).toContain('Este aparelho não aceita “Instalar app”');
    expect(inst?.reason).toContain('aparelho-loja');
    // Resetar também não é aceito pela loja: some da zona de perigo e ganha o seu motivo
    expect(acoes(g, 'danger')).toEqual([]);
    expect(g.unavailable.map((u) => u.action)).toContain('reset');
    // o que ela aceita não entra em "Indisponíveis"
    expect(g.unavailable.map((u) => u.action)).not.toContain('stop');
  });

  it('com comando em voo, todo item acionável leva o motivo do comando (o Foco bloqueia como o cartão)', () => {
    for (const estado of ['online', 'stopped', 'error', 'hibernated'] as const) {
      const g = focusActionGroups(aparelho({ state: estado }), true, emVoo('start'));
      // "Cancelar comando" é a saída do próprio comando em voo: é o único que ele não bloqueia.
      const itens = acionaveis(g).filter((i) => i.action !== 'cancel_command');
      expect(itens.length).toBeGreaterThan(0);
      expect(g.danger.find((i) => i.action === 'cancel_command')?.disabledReason).toBeNull();
      for (const item of itens) {
        expect(item.disabledReason, `${estado}/${item.action}`).toContain('android-01 está ocupado: “Iniciar” em andamento');
      }
    }
  });

  it('com comando em voo, Cancelar comando entra na zona de perigo; depois de pedido, não se oferece de novo', () => {
    expect(acoes(focusActionGroups(aparelho(), true, emVoo('restart')), 'danger')).toEqual(['reset', 'cancel_command']);
    expect(acoes(focusActionGroups(aparelho(), true, emVoo('restart', 'cancel_requested')), 'danger')).toEqual(['reset']);
  });

  it('as teclas do controle manual não dependem do comando em voo nem do supported_verbs', () => {
    const g = focusActionGroups(aparelho({ supported_verbs: ['start'] }), true, emVoo('install_apk'));
    expect(g.manual.every((i) => i.disabledReason === null)).toBe(true);
  });

  it('desligado: a ação principal é Iniciar; hibernado: Acordar; em erro: Tentar novamente', () => {
    const parado = focusActionGroups(aparelho({ state: 'stopped' }), true, undefined);
    expect(parado.primary).toEqual({ action: 'start', label: 'Iniciar', disabledReason: null });
    expect(acoes(parado, 'lifecycle')).not.toContain('start');      // a principal não se repete no grupo
    expect(parado.manual.every((i) => i.disabledReason === 'O aparelho precisa estar online.')).toBe(true);

    const hibernado = focusActionGroups(aparelho({ state: 'hibernated' }), true, undefined);
    expect(hibernado.primary).toEqual({ action: 'wake', label: 'Acordar', disabledReason: null });
    expect(hibernado.manual[0]?.disabledReason).toContain('hibernado');

    const erro = focusActionGroups(aparelho({ state: 'error' }), true, undefined);
    expect(erro.primary?.action).toBe('restart');
    expect(erro.primary?.label).toBe('Tentar novamente');
    expect(acoes(erro, 'lifecycle')).not.toContain('restart');
  });

  it('verbo principal que o aparelho não aceita: recai em Iniciar e o recusado aparece com o motivo', () => {
    const g = focusActionGroups(aparelho({ state: 'absent', kind: 'external', supported_verbs: ['start', 'stop'] }),
                                true, undefined);
    expect(g.primary?.action).toBe('start');
    expect(g.unavailable.find((u) => u.action === 'create')?.reason).toContain('outra máquina');
  });

  it('Resetar dados só existe na zona de perigo, e sem AVD fica indisponível com o motivo', () => {
    const g = focusActionGroups(aparelho(), true, undefined);
    const fora = [g.primary, ...g.lifecycle, ...g.apps, ...g.manual, ...g.observe].map((i) => i?.action);
    expect(fora).not.toContain('reset');
    expect(g.danger[0]).toEqual({ action: 'reset', label: 'Resetar dados…', disabledReason: null });
    expect(focusActionGroups(aparelho({ state: 'absent' }), true, undefined).danger[0]?.disabledReason)
      .toBe('Não há AVD para resetar.');
  });
});
