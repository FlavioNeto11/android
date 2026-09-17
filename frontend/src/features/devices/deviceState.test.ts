import { describe, expect, it } from 'vitest';
import type { InstanceState } from '../../api/types';
import { NO_FRAME_TITLE, bulkActionsFor, canHibernate, countByState, primaryActionFor } from './deviceState';

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
