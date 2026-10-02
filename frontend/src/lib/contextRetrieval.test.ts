import { describe, expect, it } from 'vitest';
import type { ContextRetrievalStatus } from '../api/types';
import {
  cacheHitRate, contagens, fallbackLabel, modeLabel, ms, provas, sendReasonLabel, usd, veredito, visibilityLabel,
} from './contextRetrieval';

function base(over: Partial<ContextRetrievalStatus> = {}): ContextRetrievalStatus {
  return {
    enabled: false, mode: 'disabled', top_k: 5,
    provider: { name: 'none', model: '', available: false, unavailable_reason: 'no_provider' },
    external_send: {
      allowed: false, reason: 'private_repository', repository_class: 'private', configured_for_remote: false,
      visibility: 'not_applicable', visibility_verified: false, remote_visibility_verified: false, head_public_verified: false,
      worktree_clean: null,
    },
    budget: { timeout_ms: 5000, max_calls: 2, max_cost_usd: 0.05 },
    summary: {
      requests: 0, by_mode: {}, cache: { hit: 0, miss: 0 }, latency_ms: { p50: null, p95: null, n: 0 },
      cost_usd: 0, input_tokens: 0, fallbacks: {}, privacy_blocks: {},
    },
    ...over,
  };
}

describe('rótulos', () => {
  it('modo, razão, fallback e visibilidade conhecidos viram português; códigos novos aparecem como vieram', () => {
    expect(modeLabel('local_only')).toBe('Só local');
    expect(modeLabel('modo_novo')).toBe('modo_novo');
    expect(sendReasonLabel('repository_worktree_dirty')).toContain('não commitada');
    expect(sendReasonLabel('razao_nova')).toBe('razao_nova');
    expect(fallbackLabel('budget_exceeded')).toBe('Orçamento estourado');
    expect(fallbackLabel('outra')).toBe('outra');
    expect(visibilityLabel('unverified')).toBe('Não provada');
    expect(modeLabel('toString')).toBe('toString');          // não herda do Object.prototype
  });
});

describe('veredito', () => {
  it('desligado: nada é consultado nem sai', () => {
    const v = veredito(base());
    expect(v.tone).toBe('neutral');
    expect(v.titulo).toBe('Desligado');
    expect(v.detalhe).toContain('nada sai');
  });

  it('enabled mas modo disabled continua desligado', () => {
    expect(veredito(base({ enabled: true, mode: 'disabled' })).titulo).toBe('Desligado');
  });

  it('só local: nenhum código sai', () => {
    const v = veredito(base({ enabled: true, mode: 'local_only' }));
    expect(v.tone).toBe('success');
    expect(v.detalhe).toContain('Nenhum código sai');
  });

  it('híbrido com política negando: bloqueado e diz por quê', () => {
    const v = veredito(base({ enabled: true, mode: 'hybrid', provider: { name: 'jev', model: 'm', available: true, unavailable_reason: null } }));
    expect(v.titulo).toBe('Envio externo bloqueado');
    expect(v.tone).toBe('success');
    expect(v.detalhe).toContain('Repositório privado');
  });

  it('só avisa "envio permitido" quando a política permite E o provedor está disponível', () => {
    const permite = base().external_send;
    const ok = base({
      enabled: true, mode: 'hybrid', provider: { name: 'jev', model: 'm', available: true, unavailable_reason: null },
      external_send: { ...permite, allowed: true, reason: 'allowed' },
    });
    expect(veredito(ok).titulo).toBe('Envio externo permitido');
    expect(veredito(ok).tone).toBe('warning');
    const semChave = { ...ok, provider: { ...ok.provider, available: false, unavailable_reason: 'key_missing' } };
    expect(veredito(semChave).titulo).toBe('Envio externo bloqueado');
  });
});

describe('formatos', () => {
  it('ms, usd e cache tratam o vazio sem NaN', () => {
    expect(ms(null)).toBe('—');
    expect(ms(1234.4)).toContain('1.234');
    expect(usd(0)).toBe('US$ 0,00');
    expect(usd(0.004266)).toBe('US$ 0,004266');
    expect(usd(1.5)).toBe('US$ 1,50');
    expect(cacheHitRate({ hit: 0, miss: 0 })).toBe('—');
    expect(cacheHitRate({ hit: 3, miss: 1 })).toBe('75%');
  });

  it('contagens ordena do maior ao menor, ignora zero e rotula', () => {
    const r = contagens({ timeout: 1, budget_exceeded: 3, zerado: 0, a: 1 }, fallbackLabel);
    expect(r.map((x) => x.chave)).toEqual(['budget_exceeded', 'a', 'timeout']);
    expect(r[0]).toEqual({ chave: 'budget_exceeded', rotulo: 'Orçamento estourado', n: 3 });
  });

  it('as três provas mantêm null quando o git não respondeu', () => {
    const e = { ...base().external_send, remote_visibility_verified: true, head_public_verified: false, worktree_clean: null };
    expect(provas(e).map((p) => p.ok)).toEqual([true, false, null]);
  });
});
