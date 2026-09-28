import { describe, expect, it } from 'vitest';
import type { AiBalance } from '../api/types';
import { balanceAge, balanceTone, balanceUsage, headerBalances, money } from './aiBalance';

function conta(p: Partial<AiBalance>): AiBalance {
  return {
    account: 'openai', label: 'OpenAI', console: 'https://x', currency: 'USD', units_per_usd: 1, warn_below: 2,
    block_below: null, stale_after_h: 72, key_configured: true, roles: [], image: false, in_use: false,
    anchor_balance: null, anchor_at: null, anchor_source: null, anchor_note: null, spent_since_usd: 0,
    estimated_balance: null, estimated_balance_usd: null, age_h: null, admin_key_configured: false, provider_usd: null,
    external_usd: 0, reconciled_at: null, reconcile_error: null, state: 'unknown', stale: false, message: '',
    ...p,
  };
}

describe('saldo das contas de IA', () => {
  it('formata as duas moedas em pt-BR, com negativo visível', () => {
    expect(money(9.25, 'USD')).toBe('US$ 9,25');
    expect(money(29.37, 'BRL')).toBe('R$ 29,37');
    expect(money(-0.5, 'USD')).toBe('−US$ 0,50');
    expect(money(null, 'USD')).toBe('—');
  });

  it('tom: bloqueio e crédito esgotado em perigo; baixo, sem leitura ou velho em aviso', () => {
    expect(balanceTone(conta({ state: 'ok' }))).toBe('success');
    expect(balanceTone(conta({ state: 'ok', stale: true }))).toBe('warning');
    expect(balanceTone(conta({ state: 'low' }))).toBe('warning');
    expect(balanceTone(conta({ state: 'unknown' }))).toBe('warning');
    expect(balanceTone(conta({ state: 'blocked' }))).toBe('danger');
    expect(balanceTone(conta({ state: 'exhausted' }))).toBe('danger');
  });

  it('diz o que a conta paga e quais contas aparecem no cabeçalho', () => {
    expect(balanceUsage(conta({ roles: ['decide', 'verify'], image: true }))).toBe('Decidir, Verificar e imagem da persona');
    expect(balanceUsage(conta({}))).toBe('Nenhuma função usa esta conta');
    expect(balanceUsage(conta({ roles: ['plan', 'escalation'] }))).toBe('Planejar e Escalonamento');
    const lista = [conta({ account: 'openai', in_use: true }), conta({ account: 'gemini' }),
      conta({ account: 'anthropic', state: 'exhausted' })];
    expect(headerBalances(lista).map((b) => b.account)).toEqual(['openai', 'anthropic']);
    expect(balanceAge(0.2)).toBe('há menos de 1 h');
    expect(balanceAge(100)).toBe('há 4 dias');
  });
});
