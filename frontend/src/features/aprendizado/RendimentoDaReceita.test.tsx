// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { lerRendimento, rendimentoDeExemplo } from './rendimento';
import { RendimentoDaReceitaSecao } from './RendimentoDaReceita';

/**
 * 31.196: o rendimento da receita no Livro (adendo v1.110, `GET /api/aprendizado/receitas/{id}/rendimento`). Prova `simulated`: servidor
 * falso no formato do RASCUNHO da Aprendizado; sem a rota, a tela lê um exemplo e diz isso.
 */

const RESPOSTA = {
  id: 7, step_key: 'abrir_busca', app: 'com.exemplo.app', status: 'published', origem: 'ensino', sessao: 'trn-1', liberada: true,
  reproducoes: { ok: 8, falha: 1 }, sem_ia: { real: 10, prova: 3, simulada: 5 }, caiu_na_ia: { real: 2, prova: 0, simulada: 1 },
  outras: { real: 1, prova: 0, simulada: 0 }, usd_da_ia_na_retencao: 0.12, custo_evitado_usd: 0.4, custo_medio_ia_por_etapa_usd: 0.04,
  ultimo_uso_em: '2026-10-06T12:00:00Z', gerado_em: '2026-10-06T13:00:00Z',
};

describe('o leitor', () => {
  it('lê tudo; sem nenhuma das três contagens não é resposta; contagem torta é "não informado", nunca zero', () => {
    const r = lerRendimento(RESPOSTA)!;
    expect(r).toMatchObject({ id: 7, origem: 'ensino', sessao: 'trn-1', liberada: true, custoEvitadoUsd: 0.4, ultimoUsoEm: '2026-10-06T12:00:00Z', exemplo: false });
    expect(r.semIa).toEqual({ real: 10, prova: 3, simulada: 5 });
    expect(lerRendimento(null)).toBeNull();
    expect(lerRendimento({ id: 1 })).toBeNull();
    const t = lerRendimento({ sem_ia: { real: 'dez', prova: -1 }, caiu_na_ia: { real: 2 } })!;
    expect(t.semIa).toEqual({ real: null, prova: null, simulada: null });
    expect(t.caiuNaIa).toEqual({ real: 2, prova: null, simulada: null });
    expect(t.custoEvitadoUsd).toBeNull();
  });
  it('o exemplo é inventado e marcado', () => {
    expect(rendimentoDeExemplo('7')).toMatchObject({ exemplo: true, id: 7 });
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
  await act(async () => root.render(<RendimentoDaReceitaSecao receitaRef="7" />));
  await waitFor(() => expect(container.querySelector('[data-resumo], [data-custo], button')).not.toBeNull());
};
const celula = (linha: string, uso: string) => text(container.querySelector(`tr[data-linha="${linha}"] td[data-uso="${uso}"]`)!);

describe('a tela', () => {
  it('uso real, prova e simulada em colunas separadas, o resumo só do uso real e o custo evitado', async () => {
    backend.on('GET', /^\/api\/aprendizado\/receitas\/7\/rendimento$/, () => json(RESPOSTA));
    await abrir();
    expect(text(container.querySelector('[data-resumo]')!)).toContain('Usada em 13 etapas de verdade: 10 sem IA, 2 em que a IA assumiu');
    expect([celula('sem_ia', 'real'), celula('sem_ia', 'prova'), celula('sem_ia', 'simulada')]).toEqual(['10', '3', '5']);
    expect([celula('caiu_na_ia', 'real'), celula('caiu_na_ia', 'prova'), celula('caiu_na_ia', 'simulada')]).toEqual(['2', '0', '1']);
    expect(text(container.querySelector('[data-custo-evitado]')!)).toBe('US$ 0,40');
    expect(text(container)).toContain('prova e simulada não contam como uso real');
    expect(text(container)).not.toContain('Dados de exemplo');
  });

  it('só prova e simulada, sem uso real: diz "ainda sem uso real" e não conta a prova como uso', async () => {
    backend.on('GET', /rendimento$/, () => json({ ...RESPOSTA, sem_ia: { real: 0, prova: 4, simulada: 2 }, caiu_na_ia: { real: 0, prova: 0, simulada: 0 }, outras: { real: 0, prova: 0, simulada: 0 }, ultimo_uso_em: null, custo_evitado_usd: 0 }));
    await abrir();
    expect(text(container.querySelector('[data-resumo]')!)).toContain('Ainda sem uso real');
    expect(text(container.querySelector('[data-resumo]')!)).not.toContain('de verdade');
  });

  it('sem referência de custo, o custo evitado é "sem referência", nunca US$ 0', async () => {
    backend.on('GET', /rendimento$/, () => json({ ...RESPOSTA, custo_evitado_usd: null, custo_medio_ia_por_etapa_usd: null, usd_da_ia_na_retencao: null }));
    await abrir();
    expect(text(container.querySelector('[data-custo-evitado]')!)).toBe('sem referência de custo');
    expect(text(container.querySelector('[data-custo]')!)).not.toContain('US$');
  });

  it('sem a rota (404) lê o exemplo e AVISA; receita desconhecida e outros erros viram erro, nunca exemplo', async () => {
    backend.on('GET', /rendimento$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Dados de exemplo'));
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /rendimento$/, () => apiError(404, 'receita_desconhecida', 'A receita não existe.'));
    await act(async () => root.render(<RendimentoDaReceitaSecao receitaRef="7" />));
    await waitFor(() => expect(text(container)).toContain('Tentar de novo'));
    expect(text(container)).not.toContain('Dados de exemplo');
  });

  it('resposta em formato inesperado vira erro, não "nada aconteceu"', async () => {
    backend.on('GET', /rendimento$/, () => json({ id: 7 }));
    await act(async () => root.render(<RendimentoDaReceitaSecao receitaRef="7" />));
    await waitFor(() => expect(text(container)).toContain('Tentar de novo'));
  });
});
