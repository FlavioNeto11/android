import { expect, it } from 'vitest';
import type { ConteudoDoFluxo } from './model';
import { lerOrigemDoTreino, linkDaSessaoDeTreino } from './origemDoTreino';

// 31.136 (adendo v1.88): a origem do fluxo demonstrado no treino. Prova `simulated`.
const origem = (extra: object): ConteudoDoFluxo['origem'] => ({ tipo: 'treino', fonte: 'training:trn-1', source_run_id: null, ...extra });

it('lê sessão, aparelho, quem ensinou e a data; sem sessão (ou fora do treino) não há origem de treino', () => {
  expect(lerOrigemDoTreino(origem({ session_id: 'trn-1', instance_id: 'android-04', operator: 'Fulano', ensinado_em: '2026-10-06T10:00:00Z' })))
    .toEqual({ sessao: 'trn-1', aparelho: 'android-04', pessoa: 'Fulano', quando: '2026-10-06T10:00:00Z' });
  expect(lerOrigemDoTreino(origem({ session_id: 'trn-1' }))).toEqual({ sessao: 'trn-1', aparelho: null, pessoa: null, quando: null });   // v1.81: só a sessão
  expect(lerOrigemDoTreino(origem({ session_id: 'trn-1', instance_id: '  ', operator: null, ensinado_em: 5 as never }))).toEqual({ sessao: 'trn-1', aparelho: null, pessoa: null, quando: null });
  expect(lerOrigemDoTreino(origem({}))).toBeNull();                                                  // treino sem sessão (backend antigo)
  expect(lerOrigemDoTreino({ ...origem({ session_id: 'trn-1' }), tipo: 'execucao' })).toBeNull();   // veio de execução
  expect(lerOrigemDoTreino(undefined)).toBeNull();
});

it('o link abre o Foco do aparelho com a sessão salva; sem aparelho não há link', () => {
  expect(linkDaSessaoDeTreino({ sessao: 'trn-1', aparelho: 'android-04', pessoa: null, quando: null })).toBe('#/aprendizado?aba=aprendido&foco=android-04&treino=trn-1');
  expect(linkDaSessaoDeTreino({ sessao: 'trn-1', aparelho: null, pessoa: null, quando: null })).toBeNull();
});
