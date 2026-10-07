// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { lerRendimentoDoEnsino, rendimentoDeExemplo } from './contratoDoRendimento';
import { RendimentoDoEnsinoSecao } from './RendimentoDoEnsino';

/**
 * 31.203: o rendimento de uma sessão de ensino (adendo v1.101, `GET /api/training/{id}/rendimento`). Prova `simulated`: servidor falso no
 * formato do contrato (o mesmo do teste do backend, `test_rendimento_do_ensino.py`); sem a rota, a tela lê um exemplo e diz isso.
 */

const RESPOSTA = {
  sessao: 'trn-1',
  resumo: { receitas: 2, receitas_liberadas: 1, etapas_sem_ia: { real: 1, prova: 1, simulada: 0 }, execucoes_do_fluxo: { real: 1, prova: 1, simulada: 0 }, licoes: 1, vizinhos: 1, usado_de_verdade: true },
  fluxo: { id: 'mandar-mensagem', status: 'active', uses: 3, nascido_de_prova: false, em_uso_real_desde: '2026-10-06T22:00:00Z' },
  execucoes_do_fluxo: { real: 1, prova: 1, simulada: 0 },
  receitas: [
    { id: 7, step_key: 'buscar', app: 'x.y', status: 'active', liberada: false, sem_ia: { real: 1, prova: 1, simulada: 0 }, caiu_na_ia: { real: 1, prova: 0, simulada: 0 }, outras: { real: 0, prova: 0, simulada: 0 }, usd_da_ia_na_retencao: 0.01 },
    { id: 8, step_key: 'enviar', app: 'x.y', status: 'active', liberada: true, sem_ia: { real: 0, prova: 0, simulada: 0 }, caiu_na_ia: { real: 0, prova: 0, simulada: 0 }, outras: { real: 0, prova: 0, simulada: 0 }, usd_da_ia_na_retencao: 0 },
  ],
  licoes: [{ id: 'li-1', estado: 'candidate', papel: 'caminho_alternativo', texto: 'Se a busca vier vazia, abra pelo menu.' }],
  vizinhos: [{ app: 'qa-messenger', pacote: 'com.pocqa.busca', etapas_em_planos_livres: { real: 1, prova: 0, simulada: 0 } }],
};

describe('o leitor', () => {
  it('lê o contrato; sem a lista de receitas não é resposta; contagem torta é "não informado", nunca zero', () => {
    const r = lerRendimentoDoEnsino(RESPOSTA)!;
    expect(r.resumo).toMatchObject({ receitas: 2, receitasLiberadas: 1, usadoDeVerdade: true });
    expect(r.execucoesDoFluxo).toEqual({ real: 1, prova: 1, simulada: 0 });
    expect(r.receitas[0]).toMatchObject({ id: 7, stepKey: 'buscar', liberada: false, usdDaIaNaRetencao: 0.01 });
    expect(r.vizinhos[0]).toMatchObject({ pacote: 'com.pocqa.busca', etapasEmPlanosLivres: { real: 1, prova: 0, simulada: 0 } });
    expect(lerRendimentoDoEnsino(null)).toBeNull();
    expect(lerRendimentoDoEnsino({ sessao: 'x' })).toBeNull();
    const t = lerRendimentoDoEnsino({ receitas: [{ id: 1, sem_ia: { real: 'dez', prova: -2 } }], resumo: {}, fluxo: null })!;
    expect(t.receitas[0]!.semIa).toEqual({ real: null, prova: null, simulada: null });
    expect(t.resumo.usadoDeVerdade).toBeNull();
    expect(t.fluxo).toBeNull();
  });
  it('o exemplo é inventado e marcado', () => {
    expect(rendimentoDeExemplo('trn-1')).toMatchObject({ exemplo: true, sessao: 'trn-1' });
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const abrir = async () => {
  await act(async () => root.render(<RendimentoDoEnsinoSecao sessionId="trn-1" />));
  await waitFor(() => expect(container.querySelector('[data-usado], button')).not.toBeNull());
};
const celula = (linha: string, uso: string) => text(container.querySelector(`tr[data-linha="${linha}"] td[data-uso="${uso}"]`)!);

describe('a tela', () => {
  it('diz se foi usado de verdade, mostra o fluxo, o uso por colunas separadas, as receitas, as lições e os vizinhos', async () => {
    backend.on('GET', /^\/api\/training\/trn-1\/rendimento$/, () => json(RESPOSTA));
    await abrir();
    expect(text(container.querySelector('[data-usado]')!)).toContain('Usado de verdade');
    expect(text(container.querySelector('[data-fluxo]')!)).toContain('mandar-mensagem');
    expect(text(container.querySelector('[data-fluxo]')!)).toContain('3 usos');
    expect(text(container.querySelector('[data-fluxo]')!)).toContain('em uso real desde');
    expect([celula('execucoes_do_fluxo', 'real'), celula('execucoes_do_fluxo', 'prova'), celula('execucoes_do_fluxo', 'simulada')]).toEqual(['1', '1', '0']);
    expect(celula('vizinho:com.pocqa.busca', 'real')).toBe('1');
    const buscar = container.querySelector('tr[data-receita="7"]')!;
    expect(text(buscar)).toContain('buscar');
    expect(text(buscar.querySelector('[data-sem-ia]')!)).toBe('1 / 1 / 0');
    expect(text(buscar.querySelector('[data-caiu-na-ia]')!)).toBe('1 / 0 / 0');
    expect(text(buscar)).toContain('não');                                                      // liberada: não (presa a quem ensinou)
    expect(text(container.querySelector('[data-licao="li-1"]')!)).toContain('Se a busca vier vazia');
    expect(text(container)).not.toContain('Dados de exemplo');
  });

  it('só prova e simulação: diz "Ainda sem uso real" e não dá o selo de usado de verdade', async () => {
    backend.on('GET', /rendimento$/, () => json({ ...RESPOSTA, resumo: { ...RESPOSTA.resumo, usado_de_verdade: false, etapas_sem_ia: { real: 0, prova: 2, simulada: 3 } }, execucoes_do_fluxo: { real: 0, prova: 2, simulada: 0 },
                                                  fluxo: { ...RESPOSTA.fluxo, em_uso_real_desde: null } }));
    await abrir();
    expect(text(container.querySelector('[data-usado]')!)).toContain('Ainda sem uso real');
    expect(text(container.querySelector('[data-usado]')!)).not.toContain('Usado de verdade');
    expect(text(container.querySelector('[data-fluxo]')!)).toContain('sem uso real ainda');
  });

  it('sem o campo usado_de_verdade o central não disse: a tela não decide', async () => {
    backend.on('GET', /rendimento$/, () => json({ ...RESPOSTA, resumo: {} }));
    await abrir();
    expect(text(container.querySelector('[data-usado]')!)).toContain('não informou');
  });

  it('sessão sem fluxo e sem receita: diz isso, sem inventar', async () => {
    backend.on('GET', /rendimento$/, () => json({ ...RESPOSTA, fluxo: null, receitas: [], licoes: [], vizinhos: [] }));
    await abrir();
    expect(text(container.querySelector('[data-fluxo]')!)).toContain('não gerou fluxo');
    expect(text(container)).toContain('Nenhuma receita saiu desta sessão.');
  });

  it('sem a rota (404) lê o exemplo e AVISA; sessão desconhecida e outros erros viram erro, nunca exemplo', async () => {
    backend.on('GET', /rendimento$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Dados de exemplo'));
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /rendimento$/, () => apiError(404, 'not_found', 'A sessão não existe.'));
    await act(async () => root.render(<RendimentoDoEnsinoSecao sessionId="trn-1" />));
    await waitFor(() => expect(text(container)).toContain('Tentar de novo'));
    expect(text(container)).not.toContain('Dados de exemplo');
  });
});
