// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, click, byRole, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { latenciaDaOperacao, latenciaDoAlvo, mediana } from './latencia';
import { lerOperacao } from './modelo';
import { OperacaoPage } from './OperacaoPage';

/**
 * 31.185: latência por estágio e por alvo dos carimbos que o central já manda. Prova `simulated`; os carimbos do caso "real" são
 * os da operação op-20261006194323-0a1540 (deploy 57), com o formato de hora do central: mesma hora na liberação e hora fora de ordem.
 */

const T = (s: string) => `2026-10-06T19:${s}Z`;
const est = (estagio: string, em: string | null) => ({ estagio, em });

/** Os carimbos reais da operação de 06/10: aparelho/instagram_aberto com 3 ms de diferença ao contrário; liberação toda na mesma hora. */
const CARIMBOS_REAIS = [
  est('persona', T('43:23.422')), est('conta', T('43:23.422')), est('sessao', T('43:23.422')), est('aparelho', T('43:29.961')),
  est('instagram_aberto', T('43:29.958')), est('target_localizado', T('43:54.920')), est('post_localizado', T('44:28.063')),
  est('conteudo_lido', T('44:33.206')), est('conhecimento_recuperado', T('44:42.438')), est('resposta_gerada', T('48:05.915')),
  est('interface_de_comentario_alcancada', T('44:32.674')), est('acao_preparada', T('48:05.915')), est('acao_executada', T('48:05.915')),
  est('resultado_verificado', T('48:05.915')),
];

describe('latenciaDoAlvo', () => {
  it('diferença entre estágios consecutivos na ordem do pipeline; total do carimbo mais cedo ao mais tarde', () => {
    const l = latenciaDoAlvo({ estagios: [est('persona', T('00:00.000')), est('conta', T('00:02.500')), est('sessao', T('00:12.500')), est('aparelho', T('01:12.500'))] as never });
    expect(l.passos.map((p) => [p.estagio, p.ms, p.situacao])).toEqual([['persona', null, null], ['conta', 2500, 'ok'], ['sessao', 10_000, 'ok'], ['aparelho', 60_000, 'ok']]);
    expect(l.totalMs).toBe(72_500);
    expect(l.maisLento).toEqual({ estagio: 'aparelho', ms: 60_000 });
  });
  it('a ordem em que o central lista não importa: vale a ordem do pipeline', () => {
    const l = latenciaDoAlvo({ estagios: [est('sessao', T('00:10.000')), est('persona', T('00:00.000'))] as never });
    expect(l.passos.map((p) => p.estagio)).toEqual(['persona', 'sessao']);
    expect(l.passos[1]).toMatchObject({ deEstagio: 'persona', ms: 10_000, situacao: 'ok' });
  });
  it('hora igual = "mesma hora" (fora da conta, não zero medido) e hora anterior à do estágio de antes = "fora de ordem"', () => {
    const l = latenciaDoAlvo({ estagios: CARIMBOS_REAIS as never });
    expect(l.mesmaHora).toBe(5);                        // conta e sessão (3 carimbos iguais) + acao_preparada, acao_executada e resultado_verificado (a hora da liberação)
    expect(l.foraDeOrdem).toBe(2);                        // instagram_aberto (3 ms antes do aparelho) e a interface de comentário (antes da resposta)
    const aparelho = l.passos.find((p) => p.estagio === 'instagram_aberto')!;
    expect(aparelho).toMatchObject({ situacao: 'fora_de_ordem', ms: null, deEstagio: 'aparelho' });
    expect(l.totalMs).toBe(282_493);                    // 19:43:23.422 → 19:48:05.915
    // o mais lento só entre os intervalos medidos
    expect(l.maisLento!.ms).toBeGreaterThan(0);
  });
  it('sem hora ou com menos de dois carimbos não há total nem estágio lento; hora torta e estágio repetido são ignorados', () => {
    expect(latenciaDoAlvo({ estagios: [] })).toMatchObject({ passos: [], totalMs: null, maisLento: null, mesmaHora: 0, foraDeOrdem: 0 });
    expect(latenciaDoAlvo({ estagios: [est('persona', T('00:00.000'))] as never }).totalMs).toBeNull();
    const l = latenciaDoAlvo({ estagios: [est('persona', 'lixo'), est('conta', null), est('sessao', T('00:05.000')), est('sessao', T('09:00.000'))] as never });
    expect(l.passos).toHaveLength(1);
    expect(l.totalMs).toBeNull();
  });
});

describe('latenciaDaOperacao', () => {
  it('mediana do total por agente e a mediana de cada estágio; o mais lento é o de maior mediana', () => {
    const a = { estagios: [est('persona', T('00:00.000')), est('conta', T('00:01.000')), est('sessao', T('00:11.000'))] as never };
    const b = { estagios: [est('persona', T('00:00.000')), est('conta', T('00:03.000')), est('sessao', T('00:33.000'))] as never };
    const c = { estagios: [est('persona', T('00:00.000'))] as never };
    const l = latenciaDaOperacao([a, b, c]);
    expect(l).toMatchObject({ agentes: 3, comTempo: 2, medianaDoTotalMs: 22_000 });            // (11 s + 33 s) / 2
    expect(l.porEstagio).toEqual([
      { estagio: 'conta', medianaMs: 2000, maiorMs: 3000, agentes: 2 }, { estagio: 'sessao', medianaMs: 20_000, maiorMs: 30_000, agentes: 2 },
    ]);
    expect(l.maisLento?.estagio).toBe('sessao');
  });
  it('mediana: ímpar pega o do meio, par faz a média dos dois, vazio é null', () => {
    expect(mediana([3, 1, 2])).toBe(2);
    expect(mediana([4, 1, 2, 3])).toBe(2.5);
    expect(mediana([])).toBeNull();
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

const alvo = (id: string, estagios: ReturnType<typeof est>[], over: Record<string, unknown> = {}) => ({
  run_id: `r-${id}`, profile_id: id, persona_nome: `Persona ${id}`, estado: 'concluido', estagio: 'resultado_verificado', estagios, ...over,
});
const abrir = async (alvos: unknown[]) => {
  backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ id: 'op-1', command: 'x', status: 'concluida', alvos }));
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'] } });
  await act(async () => root.render(<OperacaoPage />));
  await waitFor(() => expect(container.querySelectorAll('tbody tr[data-alvo]').length).toBeGreaterThan(0));
};

describe('a tela mostra o que há', () => {
  it('seção Latência com a mediana por agente, o estágio mais lento e a tabela por estágio; coluna Duração por agente', async () => {
    await abrir([
      alvo('1', [est('persona', T('00:00.000')), est('conta', T('00:02.000')), est('sessao', T('00:32.000'))]),
      alvo('2', [est('persona', T('00:00.000')), est('conta', T('00:04.000')), est('sessao', T('00:34.000'))]),
    ]);
    const sec = container.querySelector('[data-latencia]')!;
    expect(text(sec)).toContain('Mediana por agente 33 s (2 de 2 com tempo medido)');
    expect(text(sec)).toContain('Estágio mais lento: Sessão, mediana 30 s');
    expect(text(sec.querySelector('tr[data-estagio="conta"]')!)).toContain('3 s');
    expect(container.querySelector('[data-fora-da-conta]')).toBeNull();
    expect(Array.from(container.querySelectorAll('td[data-duracao]')).map((d) => text(d))).toEqual(['32 s', '34 s']);
    expect(Array.from(container.querySelectorAll('thead')).map((h) => text(h)).join(' ')).toContain('Duração');
  });

  it('hora igual e hora fora de ordem ficam fora da conta e a tela diz quantos; o detalhe marca cada intervalo', async () => {
    await abrir([alvo('1', CARIMBOS_REAIS)]);
    const nota = container.querySelector('[data-fora-da-conta]')!;
    expect(text(nota)).toContain('Ficaram de fora da conta');
    expect(text(nota)).toContain('mesma hora do anterior');
    expect(text(nota)).toContain('fora de ordem');
    await click(byRole('button', /^Abrir o detalhe de Persona 1$/, container));
    expect(container.querySelector('[data-passo="mesma_hora"]')).not.toBeNull();
    expect(container.querySelector('[data-passo="fora_de_ordem"]')).not.toBeNull();
    expect(container.querySelector('[data-passo="ok"]')).not.toBeNull();
    expect(text(container.querySelector('[data-passo="fora_de_ordem"]')!)).toContain('fora de ordem');
  });

  it('sem hora em dois estágios do mesmo agente não há tempo: a seção diz isso, a coluna fica "—" e nada vira zero', async () => {
    await abrir([alvo('1', [est('persona', null)]), alvo('2', [])]);
    expect(text(container.querySelector('[data-latencia]')!)).toContain('Nenhum agente tem hora em pelo menos dois estágios');
    expect(Array.from(container.querySelectorAll('td[data-duracao]')).map((d) => text(d))).toEqual(['—', '—']);
    expect(text(container.querySelector('[data-latencia]')!)).not.toMatch(/\b0 s\b/);
  });

  it('operação sem agente nenhum não mostra a seção', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ id: 'op-1', command: 'x', status: 'em_curso', alvos: [] }));
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'] } });
    await act(async () => root.render(<OperacaoPage />));
    await waitFor(() => expect(text(container)).toContain('Agentes (0 de 0)'));
    expect(container.querySelector('[data-latencia]')).toBeNull();
    // lerOperacao não deixa passar sem `alvos`: a tela diz que não leu (já coberto em OperacaoPage.test)
    expect(lerOperacao({ id: 'x' })!.alvos).toEqual([]);
  });
});
