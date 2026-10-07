// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { filaEmPalavras, lerFila, MOTIVO_DO_TETO } from './fila';
import { lerAlvo } from './modelo';
import { OperacaoPage } from './OperacaoPage';

/**
 * 31.208: a fila do aparelho e a previsão de início por alvo (adendo v1.114, Jev 31.206) e o corte pelo teto da operação (Jev 31.205). Prova
 * `simulated`: servidor falso no formato que a Jev combinou (o código dela ainda não está implantado).
 */

describe('lerFila', () => {
  it('lê posição, à frente, previsão e base', () => {
    expect(lerFila({ posicao: 3, a_frente: 2, previsao_inicio_em: '2026-10-07T20:47:00Z', base_ms: 90_000 }))
      .toEqual({ posicao: 3, a_frente: 2, previsao_inicio_em: '2026-10-07T20:47:00Z', base_ms: 90_000 });
  });
  it('sem amostra: previsão e base null, posição preenchida; não pendente (null) e formato torto viram null, nunca fila inventada', () => {
    expect(lerFila({ posicao: 1, a_frente: 0, previsao_inicio_em: null, base_ms: null })).toEqual({ posicao: 1, a_frente: 0, previsao_inicio_em: null, base_ms: null });
    expect(lerFila(null)).toBeNull();
    expect(lerFila('3')).toBeNull();
    expect(lerFila({})).toBeNull();
    expect(lerFila({ posicao: 0, a_frente: -1 })).toBeNull();                                 // posição começa em 1
    expect(lerFila({ posicao: 2, a_frente: 1, previsao_inicio_em: 'ontem', base_ms: 'x' })).toEqual({ posicao: 2, a_frente: 1, previsao_inicio_em: null, base_ms: null });
  });
  it('o alvo guarda a fila só quando o central a manda (ausente = não manda; null = não está pendente)', () => {
    expect(lerAlvo({ profile_id: 'p1' }, 0)!.fila).toBeUndefined();
    expect(lerAlvo({ profile_id: 'p1', fila: null }, 0)!.fila).toBeNull();
    expect(lerAlvo({ profile_id: 'p1', fila: { posicao: 2, a_frente: 1, previsao_inicio_em: null, base_ms: null } }, 0)!.fila).toMatchObject({ posicao: 2 });
  });
});

describe('filaEmPalavras', () => {
  const agora = Date.parse('2026-10-07T20:00:00Z');
  it('o próximo do aparelho e os que esperam', () => {
    expect(filaEmPalavras({ posicao: 1, a_frente: 0, previsao_inicio_em: null, base_ms: null }, agora))
      .toEqual({ posicao: 'Próximo do aparelho', aFrente: 'ninguém à frente', previsao: 'início sem previsão: ainda sem amostra' });
    const f = filaEmPalavras({ posicao: 3, a_frente: 2, previsao_inicio_em: '2026-10-07T23:59:00Z', base_ms: 1000 }, agora);
    expect([f.posicao, f.aFrente]).toEqual(['3º na fila do aparelho', '2 trabalhos à frente']);
    expect(f.previsao).toMatch(/^início previsto /);
    expect(filaEmPalavras({ posicao: 2, a_frente: 1, previsao_inicio_em: null, base_ms: null }).aFrente).toBe('1 trabalho à frente');
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
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [] } });
});

const abrir = async (corpo: Record<string, unknown>) => {
  backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ id: 'op-1', command: 'x', status: 'em_andamento', ...corpo }));
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'] } });
  await act(async () => root.render(<OperacaoPage />));
  await waitFor(() => expect(container.querySelectorAll('tbody tr[data-alvo]').length).toBeGreaterThan(0));
};
const alvo = (id: string, extra: Record<string, unknown> = {}) => ({ run_id: `r-${id}`, profile_id: id, persona_nome: `Persona ${id}`, estado: 'pendente', estagio: null, estagios: [], ...extra });
const celula = (id: string) => text(container.querySelector(`tr[data-alvo="r-${id}"] [data-fila]`)!);

describe('a tela: fila do aparelho', () => {
  it('mostra a posição, quantos à frente e a previsão por alvo pendente; sem amostra diz isso; o que não está pendente fica com traço', async () => {
    await abrir({
      alvos: [
        alvo('1', { instance_id: 'android-01', fila: { posicao: 1, a_frente: 0, previsao_inicio_em: null, base_ms: null } }),
        alvo('2', { instance_id: 'android-01', fila: { posicao: 3, a_frente: 2, previsao_inicio_em: '2026-10-07T20:47:00Z', base_ms: 90_000 } }),
        alvo('3', { estado: 'concluido', estagio: 'resultado_verificado', fila: null }),
        alvo('4'),                                                                           // o central ainda não manda a fila
      ],
    });
    expect(Array.from(container.querySelectorAll('thead th')).map((h) => text(h))).toContain('Fila do aparelho');
    expect(celula('1')).toContain('Próximo do aparelho');
    expect(celula('1')).toContain('ninguém à frente');
    expect(celula('1')).toContain('início sem previsão: ainda sem amostra');
    expect(celula('2')).toContain('3º na fila do aparelho');
    expect(celula('2')).toContain('2 trabalhos à frente');
    expect(celula('2')).toContain('início previsto');
    expect(celula('3')).toBe('—');
    expect(celula('4')).toBe('—');
  });
});

describe('a tela: teto da operação', () => {
  const comTeto = { alvos: [alvo('1')], max_usd: 4.5, custo: { pesquisa_usd: 0.5, alvos_usd: 4.0, total_usd: 4.5 }, capacidade: { solicitados: 5, motivos: { [MOTIVO_DO_TETO]: 2, 'sem conta': 1 } } };

  it('destaca o corte pelo teto, com quantos foram cortados e o gasto contra o teto, além da lista de motivos', async () => {
    await abrir(comTeto);
    const faixa = container.querySelector('[data-teto-da-operacao]')!;
    expect(text(faixa)).toContain('2 agentes cortados pelo teto da operação');
    expect(text(faixa)).toContain('US$ 4,5000 de um teto de US$ 4,5000');
    expect(container.querySelector(`li[data-motivo-do-teto]`)!.textContent).toContain('teto da operação');
    expect(container.querySelectorAll('li[data-motivo-do-teto]')).toHaveLength(1);           // só o do teto é destacado, não "sem conta"
  });

  it('um cortado fala no singular; sem o gasto ou sem o teto a frase não inventa números', async () => {
    await abrir({ ...comTeto, max_usd: undefined, custo: undefined, capacidade: { motivos: { [MOTIVO_DO_TETO]: 1 } } });
    const faixa = container.querySelector('[data-teto-da-operacao]')!;
    expect(text(faixa)).toContain('1 agente cortado pelo teto da operação');
    expect(text(faixa)).not.toContain('US$');
  });

  it('sem o motivo do teto não há faixa', async () => {
    await abrir({ ...comTeto, capacidade: { motivos: { 'sem conta': 1 } } });
    expect(container.querySelector('[data-teto-da-operacao]')).toBeNull();
  });
});
