import { uuid } from './ids';

export interface RunIntent {
  command: string;
  instanceIds: readonly string[];
  mode: 'plan' | 'execute';
}

/** Impressão digital estável de uma intenção: texto aparado + seleção ordenada + modo. */
export function intentFingerprint(intent: RunIntent): string {
  return JSON.stringify([intent.command.trim(), [...intent.instanceIds].sort(), intent.mode]);
}

/**
 * Guarda a `idempotency_key` de cada intenção ainda não confirmada.
 *  - mesma intenção (cliques repetidos, novas tentativas após erro/timeout) → MESMA chave;
 *  - texto, seleção ou modo diferentes → outra chave;
 *  - a chave só é descartada (`confirm`) depois de uma resposta bem-sucedida do backend.
 * Mantém várias intenções pendentes para que alternar Planejar/Executar após uma falha de rede não
 * gere uma chave nova para uma requisição que talvez já tenha sido aceita.
 */
export class IdempotencyKeeper {
  private readonly keys = new Map<string, string>();
  private readonly maxEntries: number;
  private readonly generate: () => string;

  constructor(generate: () => string = uuid, maxEntries = 32) {
    this.generate = generate;
    this.maxEntries = maxEntries;
  }

  keyFor(intent: RunIntent): string {
    const fp = intentFingerprint(intent);
    const existing = this.keys.get(fp);
    if (existing) return existing;
    const key = this.generate();
    this.keys.set(fp, key);
    if (this.keys.size > this.maxEntries) {
      const oldest = this.keys.keys().next().value;
      if (oldest !== undefined) this.keys.delete(oldest);
    }
    return key;
  }

  /** Chame somente após resposta 2xx: a próxima execução do mesmo comando será uma nova intenção. */
  confirm(intent: RunIntent): void {
    this.keys.delete(intentFingerprint(intent));
  }
}
