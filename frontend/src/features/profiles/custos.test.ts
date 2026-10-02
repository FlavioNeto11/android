import { describe, expect, it } from 'vitest';
import type { AiStatus } from '../../api/types';
import { custoDaGeracao, papelDaPersona, personaSimulado } from './custos';

// Só o que `custos.ts` lê de `GET /api/ai`: os papéis. O resto do `AiStatus` não importa aqui.
const papel = (role: string, provider: string, kind: string, model: string) =>
  ({ role, provider, kind, model, endpoint: '' }) as unknown as NonNullable<AiStatus['roles']>[number];
const ai = (...roles: ReturnType<typeof papel>[]) => ({ simulated: false, roles }) as unknown as AiStatus;

describe('papel da geração de persona (item 17.8)', () => {
  it('usa o papel persona quando o backend o informa, mesmo que o social seja outro', () => {
    const status = ai(papel('social', 'anthropic', 'anthropic', 'claude-x'), papel('persona', 'simulated', 'simulated', 'simulado'));
    expect(papelDaPersona(status)?.role).toBe('persona');
    expect(personaSimulado(status)).toBe(true);
    expect(custoDaGeracao(status, 3).pago).toBe(false);
  });

  it('com a persona num provedor pago, o custo mostra o modelo e o provedor DELA', () => {
    const status = ai(papel('social', 'simulated', 'simulated', 'simulado'), papel('persona', 'openai-flex', 'openai', 'modelo-flex'));
    expect(personaSimulado(status)).toBe(false);
    const linha = custoDaGeracao(status, 2);
    expect(linha.pago).toBe(true);
    expect(linha.texto).toContain('modelo-flex em openai-flex');
  });

  it('num backend sem o papel persona, cai no social (que era quem escrevia)', () => {
    const status = ai(papel('social', 'anthropic', 'anthropic', 'claude-x'));
    expect(papelDaPersona(status)?.role).toBe('social');
    expect(personaSimulado(status)).toBe(false);
  });

  it('sem nada lido, conta com custo (nunca finge que é grátis)', () => {
    expect(papelDaPersona(null)).toBeNull();
    expect(personaSimulado(null)).toBe(false);
    expect(custoDaGeracao(null, 1).pago).toBe(true);
  });
});
