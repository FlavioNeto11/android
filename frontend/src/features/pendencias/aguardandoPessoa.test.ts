import { describe, expect, it } from 'vitest';
import { makeRun } from '../../test/fixtures';
import { montarPendencias } from './modelo';

describe('29.93: a execução aguardando você não muda a regra das Pendências (ADR-062, D1)', () => {
  it('só `needs_input` conta como execução pendente; `awaiting_person` segue em Execuções, no chip "Pede atenção"', () => {
    const pendencias = montarPendencias({
      aprendizado: [],
      aprovacoes: [],
      execucoes: [makeRun({ id: 'r-aguardando', status: 'awaiting_person' }), makeRun({ id: 'r-pergunta', status: 'needs_input' })],
    });
    expect(pendencias).toHaveLength(1);
    expect(JSON.stringify(pendencias)).toContain('r-pergunta');
    expect(JSON.stringify(pendencias)).not.toContain('r-aguardando');
  });
});
