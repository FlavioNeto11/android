import { Moon } from 'lucide-react';
import { describe, expect, it } from 'vitest';
import type { InstanceState } from '../api/types';
import { DRIVEN_BY, INSTANCE_STATE, aiWaitMeta, drivenByMeta, isAiBlocked, metaOf, slotWaitDetail } from './status';

describe('INSTANCE_STATE', () => {
  it('cobre todos os estados do contrato v0.2, inclusive hibernated', () => {
    const states: InstanceState[] = ['absent', 'stopped', 'hibernated', 'booting', 'online', 'stopping', 'error'];
    expect(Object.keys(INSTANCE_STATE).sort()).toEqual([...states].sort());
    for (const s of states) {
      expect(INSTANCE_STATE[s].label).not.toBe('');
      expect(INSTANCE_STATE[s].icon).toBeDefined();
    }
  });

  it('hibernated: rótulo "Hibernado", ícone de lua, cor própria e explicação (texto + cor + forma)', () => {
    const meta = metaOf(INSTANCE_STATE, 'hibernated');
    expect(meta.label).toBe('Hibernado');
    expect(meta.icon).toBe(Moon);
    expect(meta.tone).toBe('info');
    expect(meta.spin).toBeUndefined(); // não é um estado de transição
    expect(meta.description).toMatch(/acorda em segundos/);
    expect(meta.description).toMatch(/não ocupa RAM/);
    // não se confunde com "parada" nem com o fallback de valor desconhecido
    expect(meta.label).not.toBe(INSTANCE_STATE.stopped.label);
    expect(meta.icon).not.toBe(INSTANCE_STATE.stopped.icon);
  });
});

describe('drivenByMeta — selo por etapa', () => {
  it('traduz cada valor de driven_by', () => {
    expect(drivenByMeta('recipe')?.label).toBe('Receita');
    expect(drivenByMeta('recipe+ai')?.label).toBe('Receita + IA');
    expect(drivenByMeta('ai')?.label).toBe('IA');
  });

  it('só a receita pura diz "sem chamada de modelo"; cada selo tem ícone e tom distintos', () => {
    expect(DRIVEN_BY.recipe.description).toMatch(/nenhuma chamada de modelo/);
    expect(DRIVEN_BY['recipe+ai'].description).toMatch(/IA assumiu/);
    const icons = new Set([DRIVEN_BY.recipe.icon, DRIVEN_BY['recipe+ai'].icon, DRIVEN_BY.ai.icon]);
    const tones = new Set([DRIVEN_BY.recipe.tone, DRIVEN_BY['recipe+ai'].tone, DRIVEN_BY.ai.tone]);
    expect(icons.size).toBe(3);
    expect(tones.size).toBe(3);
  });

  it('não mostra selo enquanto driven_by é nulo/ausente e tolera valor fora do contrato', () => {
    expect(drivenByMeta(null)).toBeNull();
    expect(drivenByMeta(undefined)).toBeNull();
    expect(drivenByMeta('')).toBeNull();
    expect(drivenByMeta('macro')?.label).toBe('macro');
  });
});

describe('slotWaitDetail — rodízio', () => {
  it('devolve o texto só para objetivo pendente cujo detalhe começa com "aguardando vaga"', () => {
    expect(slotWaitDetail({ status: 'pending', status_detail: 'aguardando vaga (3/3 ligados)' })).toBe('aguardando vaga (3/3 ligados)');
    expect(slotWaitDetail({ status: 'pending', status_detail: '  Aguardando vaga — nenhum aparelho ligado pode ser desligado agora (3/3 ligados)' }))
      .toBe('Aguardando vaga — nenhum aparelho ligado pode ser desligado agora (3/3 ligados)');
  });

  it('ignora outros estados e outros detalhes', () => {
    expect(slotWaitDetail({ status: 'running', status_detail: 'aguardando vaga (3/3 ligados)' })).toBeNull();
    expect(slotWaitDetail({ status: 'waiting_user', status_detail: 'aguardando vaga' })).toBeNull();
    expect(slotWaitDetail({ status: 'pending', status_detail: 'na fila; aguardando vaga' })).toBeNull();
    expect(slotWaitDetail({ status: 'pending', status_detail: null })).toBeNull();
    expect(slotWaitDetail(null)).toBeNull();
  });

  it('item 7.3: com wait_reason gravado, a fonte da verdade é o campo estruturado — não o texto', () => {
    expect(slotWaitDetail({ status: 'pending', status_detail: 'aguardando vaga (3/3 ligados)', wait_reason: 'device_slot' }))
      .toBe('aguardando vaga (3/3 ligados)');
    // motivo tipado presente mas NÃO é device_slot (ex.: aparelho ligando por outro caminho): não é "vaga"
    expect(slotWaitDetail({ status: 'pending', status_detail: 'aguardando vaga (3/3 ligados)', wait_reason: 'profile_limit' }))
      .toBeNull();
  });
});

describe('aiWaitMeta / isAiBlocked — item 7.3 (achados #93, #68)', () => {
  it('distingue vaga de IA de resposta do modelo, só em objetivo em andamento', () => {
    expect(aiWaitMeta({ status: 'running', wait_reason: 'ai_capacity' })?.label).toBe('Aguardando vaga de IA');
    expect(aiWaitMeta({ status: 'running', wait_reason: 'model_response' })?.label).toBe('Aguardando resposta do modelo');
    expect(aiWaitMeta({ status: 'running', wait_reason: 'device_slot' })).toBeNull();
    expect(aiWaitMeta({ status: 'pending', wait_reason: 'ai_capacity' })).toBeNull();
    expect(aiWaitMeta({ status: 'running', wait_reason: null })).toBeNull();
    expect(aiWaitMeta(null)).toBeNull();
  });

  it('blocked_kind="ai" é o único que conta como bloqueio DA IA', () => {
    expect(isAiBlocked({ blocked_kind: 'ai' })).toBe(true);
    expect(isAiBlocked({ blocked_kind: 'policy' })).toBe(false);
    expect(isAiBlocked({ blocked_kind: null })).toBe(false);
    expect(isAiBlocked(null)).toBe(false);
  });
});
