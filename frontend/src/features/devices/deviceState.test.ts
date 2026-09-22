import { describe, expect, it } from 'vitest';
import type { Instance, InstanceAction, InstanceState, Worker } from '../../api/types';
import {
  NO_FRAME_TITLE, bulkActionsFor, bulkBlockersFor, canHibernate, countByState, noFrameTitle, primaryActionFor,
  quickActionsFor, serverHintOf, unsupportedReason, type ServerHint,
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
      detail: null, avd_name: 'worker-01', serial: 'emulator-5554', adb_port: 5555,
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
    detail: null, avd_name: null, serial: null, adb_port: null, ...over,
  });

  it('desligado de propósito no worker NÃO é problema de conexão', () => {
    expect(noFrameTitle('stopped', 'external', hint({ process: 'stopped' })))
      .toBe('Emulador desligado em Notebook da LAN');
  });

  it('servidor fora do ar admite que não se sabe o estado do emulador', () => {
    expect(noFrameTitle('stopped', 'external', hint({ connected: false, process: 'online' })))
      .toBe('Servidor Notebook da LAN fora do ar — estado desconhecido');
  });

  it('emulador ligado lá com ADB inalcançável aponta o túnel, não o emulador', () => {
    expect(noFrameTitle('stopped', 'external', hint({ process: 'online' })))
      .toBe('Emulador ligado em Notebook da LAN, túnel ADB caiu');
  });

  it('sem worker o texto de antes continua valendo, e o local nunca fala em outra máquina', () => {
    expect(noFrameTitle('stopped', 'external')).toBe('Sem conexão ADB com a outra máquina');
    expect(noFrameTitle('stopped')).toBe('Emulador desligado');
    expect(noFrameTitle('booting', 'external', hint({ process: 'booting' }))).toBe('Iniciando o emulador…');
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
