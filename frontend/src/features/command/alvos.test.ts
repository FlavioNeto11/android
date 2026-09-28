import { describe, expect, it } from 'vitest';
import { ApiError } from '../../api/client';
import type { TargetQuestion } from '../../api/types';
import { ecoDosAlvos, recusaDosAlvos, responder } from './alvos';

/** A regra do eco e das respostas, sem tela (adendo v0.29, ADR-044). Prova `simulated`. */
describe('ecoDosAlvos', () => {
  it('agrupa por persona e app, com os aparelhos explícitos que a prévia mostrou', () => {
    const r = ecoDosAlvos([
      { instance_id: 'android-01', profile_id: 'ig-1', app_id: 'instagram', origem: 'vinculo' },
      { instance_id: 'android-03', profile_id: 'ig-1', app_id: 'instagram', origem: 'balanceamento' },
      { instance_id: 'android-02', profile_id: 'ig-2', app_id: null, origem: 'texto' },
    ]);
    expect(r.erro).toBeNull();
    expect(r.eco).toEqual({
      instance_ids: [],
      targets: [
        { profile_id: 'ig-1', instance_ids: ['android-01', 'android-03'], app_id: 'instagram' },
        { profile_id: 'ig-2', instance_ids: ['android-02'], app_id: null },
      ],
    });
  });

  it('aparelhos sem persona voltam como instance_ids (a seleção exata deixa de ser estreitamento do texto)', () => {
    const r = ecoDosAlvos([{ instance_id: 'android-03', profile_id: null, app_id: null, origem: 'texto' }]);
    expect(r.eco).toEqual({ targets: [], instance_ids: ['android-03'] });
  });

  it('mistura de aparelho com e sem persona não tem forma no contrato: recusa com o que fazer', () => {
    const r = ecoDosAlvos([
      { instance_id: 'android-01', profile_id: 'ig-1', app_id: null, origem: 'texto' },
      { instance_id: 'android-05', profile_id: null, app_id: null, origem: 'texto' },
    ]);
    expect(r.eco).toBeNull();
    expect(r.erro).toContain('duas execuções');
  });

  it('prévia vazia não confirma nada', () => {
    expect(ecoDosAlvos([]).erro).toContain('não tem alvo');
  });
});

describe('recusaDosAlvos', () => {
  it('cada código diz o que fazer, em português, e guarda a mensagem do backend', () => {
    const casos: [string, RegExp][] = [
      ['sem_intersecao', /Tire o filtro/],
      ['sem_vinculo', /vincule a persona/],
      ['no_binding', /Vincule um aparelho/],
      ['aparelho_repetido_na_execucao', /duas\s+execuções/],
      ['sem_alvo', /no android-03/],
    ];
    for (const [code, passo] of casos) {
      const r = recusaDosAlvos(new ApiError(409, code, `mensagem de ${code}`));
      expect(r.passo).toMatch(passo);
      expect(r.mensagem).toBe(`mensagem de ${code}`);
    }
  });
});

function pergunta(over: Partial<TargetQuestion>): TargetQuestion {
  return { code: 'persona_ambigua', question: '?', field: 'profile_id', options: [], instance_id: null, profile_id: null, ...over };
}

describe('responder', () => {
  it('homônimo: a persona escolhida entra no lugar das candidatas, e as outras da seleção ficam', () => {
    const r = responder(pergunta({ options: ['ig-2', 'ig-3'] }), 'ig-3', ['ig-1', 'ig-2', 'ig-3'], []);
    expect(r).toEqual({ personas: ['ig-1', 'ig-3'], aparelhos: null, semDestinos: false });
  });

  it('persona num aparelho com duas: a persona vai junto com o aparelho da pergunta', () => {
    const r = responder(pergunta({ code: 'persona_no_aparelho', options: ['ig-1', 'ig-2'], instance_id: 'android-01' }),
                        'ig-2', [], ['android-01', 'android-02']);
    expect(r).toEqual({ personas: ['ig-2'], aparelhos: ['android-01'], semDestinos: false });
  });

  it('contradição: escolher quem JÁ estava na seleção tira os destinos do texto', () => {
    const q = pergunta({ code: 'destino_contraditorio', options: ['ig-1', 'ig-2'] });
    expect(responder(q, 'ig-1', ['ig-1'], [])).toEqual({ personas: ['ig-1'], aparelhos: null, semDestinos: true });
    // escolher quem o texto citou troca a seleção e o texto fica
    expect(responder(q, 'ig-2', ['ig-1'], [])).toEqual({ personas: ['ig-2'], aparelhos: null, semDestinos: false });
  });

  it('aparelho: vira a seleção; na contradição, o que já estava selecionado fica e o texto perde', () => {
    const q = pergunta({ code: 'destino_contraditorio', field: 'instance_id', options: ['android-01', 'android-05'] });
    expect(responder(q, 'android-05', [], ['android-01'])).toEqual({ personas: null, aparelhos: ['android-05'], semDestinos: false });
    expect(responder(q, 'android-01', [], ['android-01'])).toEqual({ personas: null, aparelhos: null, semDestinos: true });
  });
});
