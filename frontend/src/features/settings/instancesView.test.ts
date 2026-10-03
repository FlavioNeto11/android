import { describe, expect, it } from 'vitest';
import type { Instance, Worker } from '../../api/types';
import { makeInstance } from '../../test/fixtures';
import { EMPTY_FILTERS, groupByServer, hasActiveFilter, matchesFilters, observedMatchOf } from './instancesView';

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z', last_seen_at: '2026-09-22T10:00:00Z',
    ...over,
  };
}
const CENTRAL = worker({ id: 'central', name: 'Este servidor', local: true });

describe('observedMatchOf', () => {
  it('nada observado ainda', () => {
    expect(observedMatchOf({ account_label: 'qa-user-01', account_evidence: null })).toBe('none');
  });

  it('bate quando o rótulo aparece na evidência, sem ligar para caixa nem espaços extras', () => {
    expect(observedMatchOf({ account_label: 'QA-User-01', account_evidence: 'Conta:   qa-user-01  confirmada' })).toBe('match');
  });

  it('diverge quando a evidência fala de outra conta', () => {
    expect(observedMatchOf({ account_label: 'qa-user-01', account_evidence: 'Conta: qa-user-02' })).toBe('diverge');
  });

  it('sem rótulo configurado não há divergência: não há conta esperada (validação do deploy 4)', () => {
    expect(observedMatchOf({ account_label: null, account_evidence: 'Conta: qa-user-02' })).toBe('none');
    expect(observedMatchOf({ account_label: '  ', account_evidence: 'Conta: qa-user-02' })).toBe('none');
  });
});

describe('groupByServer', () => {
  it('agrupa o central primeiro, e o resto em ordem alfabética pelo nome do servidor', () => {
    const zebra = worker({ id: 'worker-zebra', name: 'Zebra' });
    const groups = groupByServer(
      [
        makeInstance(1, { worker_id: 'worker-zebra' }),
        makeInstance(2, { worker_id: null }),
        makeInstance(3, { worker_id: 'worker-lan-01' }),
      ],
      Object.fromEntries([CENTRAL, worker(), zebra].map((w) => [w.id, w])),
      'central',
    );
    expect(groups.map((g) => g.name)).toEqual(['Este servidor', 'Notebook da LAN', 'Zebra']);
    expect(groups[0]!.items.map((i) => i.id)).toEqual(['android-02']);
  });

  it('worker local (mesmo id do central) cai no grupo "Este servidor"', () => {
    const groups = groupByServer([makeInstance(1, { worker_id: 'central' })], { central: CENTRAL }, 'central');
    expect(groups).toHaveLength(1);
    expect(groups[0]!.key).toBe('');
  });

  it('worker que saiu da lista aparece como grupo próprio, marcado não inscrito', () => {
    const groups = groupByServer([makeInstance(1, { worker_id: 'worker-sumido' })], { central: CENTRAL }, 'central');
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ key: 'worker-sumido', name: 'worker-sumido', enrolled: false });
  });
});

describe('matchesFilters', () => {
  const instances: Instance[] = [
    makeInstance(1, { worker_id: null, app_id: 'qa', account_label: 'qa-user-01', account_evidence: 'Conta: qa-user-01' }),
    makeInstance(2, { worker_id: 'worker-lan-01', app_id: 'notes', account_label: 'qa-user-02', account_evidence: 'Conta: outra-coisa' }),
    makeInstance(3, { worker_id: null, app_id: null, account_label: null, account_evidence: null }),
  ];
  const noProfiles = () => false;

  it('sem filtro nenhum, tudo passa', () => {
    expect(instances.filter((i) => matchesFilters(i, EMPTY_FILTERS, null, noProfiles))).toHaveLength(3);
  });

  it('filtro por servidor central só pega quem não tem worker remoto', () => {
    const found = instances.filter((i) => matchesFilters(i, { ...EMPTY_FILTERS, server: '' }, null, noProfiles));
    expect(found.map((i) => i.id)).toEqual(['android-01', 'android-03']);
  });

  it('filtro por app', () => {
    const found = instances.filter((i) => matchesFilters(i, { ...EMPTY_FILTERS, app: 'notes' }, null, noProfiles));
    expect(found.map((i) => i.id)).toEqual(['android-02']);
  });

  it('só divergências pega quem tem evidência que não bate com o rótulo', () => {
    const found = instances.filter((i) => matchesFilters(i, { ...EMPTY_FILTERS, onlyDivergent: true }, null, noProfiles));
    expect(found.map((i) => i.id)).toEqual(['android-02']);
  });

  it('sem perfil usa o predicado hasProfile por instância', () => {
    const hasProfile = (id: string) => id === 'android-01';
    const found = instances.filter((i) => matchesFilters(i, { ...EMPTY_FILTERS, noProfile: true }, null, hasProfile));
    expect(found.map((i) => i.id)).toEqual(['android-02', 'android-03']);
  });
});

describe('hasActiveFilter', () => {
  it('vazio não é ativo, qualquer campo setado é', () => {
    expect(hasActiveFilter(EMPTY_FILTERS)).toBe(false);
    expect(hasActiveFilter({ ...EMPTY_FILTERS, server: '' })).toBe(true);
    expect(hasActiveFilter({ ...EMPTY_FILTERS, onlyDivergent: true })).toBe(true);
  });
});
