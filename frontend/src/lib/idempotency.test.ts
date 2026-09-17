import { describe, expect, it } from 'vitest';
import { IdempotencyKeeper, intentFingerprint } from './idempotency';

function counterGen() {
  let n = 0;
  return () => `key-${++n}`;
}

describe('intentFingerprint', () => {
  it('ignora espaços nas pontas e a ordem da seleção', () => {
    const a = intentFingerprint({ command: '  abrir app ', instanceIds: ['android-02', 'android-01'], mode: 'execute' });
    const b = intentFingerprint({ command: 'abrir app', instanceIds: ['android-01', 'android-02'], mode: 'execute' });
    expect(a).toBe(b);
  });

  it('muda com texto, seleção ou modo', () => {
    const base = { command: 'abrir app', instanceIds: ['android-01'], mode: 'execute' as const };
    const fp = intentFingerprint(base);
    expect(intentFingerprint({ ...base, command: 'abrir app!' })).not.toBe(fp);
    expect(intentFingerprint({ ...base, instanceIds: ['android-01', 'android-02'] })).not.toBe(fp);
    expect(intentFingerprint({ ...base, mode: 'plan' })).not.toBe(fp);
  });
});

describe('IdempotencyKeeper', () => {
  const intent = { command: 'enviar mensagem', instanceIds: ['android-01', 'android-02'], mode: 'execute' as const };

  it('reutiliza a mesma chave em cliques repetidos e novas tentativas', () => {
    const k = new IdempotencyKeeper(counterGen());
    const first = k.keyFor(intent);
    expect(k.keyFor(intent)).toBe(first);
    expect(k.keyFor({ ...intent, instanceIds: ['android-02', 'android-01'] })).toBe(first);
  });

  it('gera outra chave quando a intenção muda e preserva a anterior pendente', () => {
    const k = new IdempotencyKeeper(counterGen());
    const exec = k.keyFor(intent);
    const plan = k.keyFor({ ...intent, mode: 'plan' });
    expect(plan).not.toBe(exec);
    // voltar à intenção original (ex.: após falha de rede) continua usando a chave original
    expect(k.keyFor(intent)).toBe(exec);
  });

  it('só troca a chave depois de confirmar o sucesso', () => {
    const k = new IdempotencyKeeper(counterGen());
    const first = k.keyFor(intent);
    expect(k.keyFor(intent)).toBe(first); // falhou → tenta de novo com a mesma
    k.confirm(intent);
    expect(k.keyFor(intent)).not.toBe(first);
  });

  it('limita a quantidade de intenções pendentes', () => {
    const k = new IdempotencyKeeper(counterGen(), 2);
    const a = k.keyFor({ ...intent, command: 'a' });
    k.keyFor({ ...intent, command: 'b' });
    k.keyFor({ ...intent, command: 'c' });
    expect(k.keyFor({ ...intent, command: 'a' })).not.toBe(a);
  });
});
