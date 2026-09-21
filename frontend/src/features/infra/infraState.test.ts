import { describe, expect, it } from 'vitest';
import type { Instance } from '../../api/types';
import { STALE_HEARTBEAT_MS, groupByWorker, instanceStateMeta, isStale, orphanInstances } from './infraState';

function inst(id: string, worker_id: string | null): Instance {
  return { id, worker_id, state: 'online' } as unknown as Instance;
}

describe('groupByWorker — o servidor central também é um servidor', () => {
  it('agrupa por worker e usa `null` para o central', () => {
    const g = groupByWorker([inst('android-01', null), inst('android-09', 'w1'), inst('android-10', 'w1'),
                             inst('android-02', null)]);
    expect([...g.keys()]).toEqual([null, 'w1']);
    expect(g.get(null)?.map((i) => i.id)).toEqual(['android-01', 'android-02']);
    expect(g.get('w1')?.map((i) => i.id)).toEqual(['android-09', 'android-10']);
  });

  it('sem nenhum aparelho remoto, só existe o central', () => {
    expect([...groupByWorker([inst('android-01', null)]).keys()]).toEqual([null]);
  });
});

describe('orphanInstances — o silêncio que a tela precisa quebrar', () => {
  it('acha aparelho amarrado a servidor que não está inscrito', () => {
    // É um estado real e calado: o aparelho fica sem ciclo de vida e nada explica por quê.
    const orfas = orphanInstances([inst('android-09', 'w1'), inst('android-10', 'fantasma'), inst('android-01', null)],
                                  ['w1']);
    expect(orfas.map((i) => i.id)).toEqual(['android-10']);
  });

  it('aparelho do central nunca é órfão', () => {
    expect(orphanInstances([inst('android-01', null)], [])).toEqual([]);
  });
});

describe('isStale — dado velho não pode parecer atual', () => {
  it('idade desconhecida não é considerada velha', () => {
    expect(isStale(null)).toBe(false);
  });

  it('acima do limite da batida, é velha', () => {
    expect(isStale(STALE_HEARTBEAT_MS - 1)).toBe(false);
    expect(isStale(STALE_HEARTBEAT_MS + 1)).toBe(true);
  });

  it('o limite tolera três batidas perdidas (10 s cada), como o ceifador do backend', () => {
    expect(STALE_HEARTBEAT_MS).toBeGreaterThan(30_000);
  });
});

describe('instanceStateMeta', () => {
  it('hibernado é distinto de parado — um acorda em segundos, o outro não', () => {
    expect(instanceStateMeta('hibernated').label).toBe('hibernado');
    expect(instanceStateMeta('stopped').label).toBe('parado');
    expect(instanceStateMeta('error').tone).toBe('danger');
    expect(instanceStateMeta('online').tone).toBe('success');
  });

  it('estado desconhecido não quebra a tela', () => {
    expect(instanceStateMeta('inventado' as Instance['state']).label).toBe('inventado');
  });
});
