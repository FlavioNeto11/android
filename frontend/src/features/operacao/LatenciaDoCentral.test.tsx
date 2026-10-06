// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { latenciaDaOperacao, latenciaDoAlvo } from './latencia';
import { lerAlvo, lerOperacao } from './modelo';
import { OperacaoPage } from './OperacaoPage';

/**
 * 31.185 (complemento, adendo v1.108 / 31.187): quando o central manda `estagios[].etapa_ms`, `alvos[].latencia` e `latencia_por_estagio`,
 * a tela usa a conta DELE (no tempo, desde a criação da operação); sem esses campos, a conta local de antes. Prova `simulated`: os
 * números são os do contrato (a rota ainda não está implantada, corte 60).
 */

const T = (s: string) => `2026-10-06T19:${s}Z`;
const est = (estagio: string, em: string | null, etapa_ms?: number | null) => ({ estagio, em, ...(etapa_ms === undefined ? {} : { etapa_ms }) });

describe('o leitor', () => {
  it('lê etapa_ms, latencia do alvo e latencia_por_estagio; campo ausente fica ausente, nunca zero', () => {
    const a = lerAlvo({
      run_id: 'r', estagios: [est('persona', T('00:00.000'), 1200), est('conta', T('00:01.200'), null), est('sessao', null)],
      latencia: { duracao_ms: 90_000, espera_do_liberar_ms: null },
    }, 0)!;
    expect(a.estagios.map((e) => e.etapa_ms)).toEqual([1200, null, undefined]);
    expect(a.latencia).toEqual({ duracao_ms: 90_000, espera_do_liberar_ms: null });
    expect(lerAlvo({ run_id: 'r2', estagios: [] }, 1)!.latencia).toBeUndefined();
    const op = lerOperacao({
      id: 'op', command: 'x', status: 'concluida', alvos: [],
      latencia_por_estagio: { conta: { n: 3, p50_ms: 2000, p95_ms: 5000, max_ms: 6000 }, estagio_que_nao_existe: { n: 1, p50_ms: 1, p95_ms: 1, max_ms: 1 }, sessao: { n: 0, p50_ms: 1, p95_ms: 1, max_ms: 1 } },
    })!;
    expect(op.latencia_por_estagio).toEqual({ conta: { n: 3, p50_ms: 2000, p95_ms: 5000, max_ms: 6000 } });
    expect(lerOperacao({ id: 'op', command: 'x', status: 'concluida', alvos: [] })!.latencia_por_estagio).toBeUndefined();
  });
});

describe('latenciaDoAlvo com a conta do central', () => {
  it('usa etapa_ms (no tempo) e a duração do alvo; 0 é "mesma hora", null não é medido, e o painel não reordena nem recalcula', () => {
    const l = latenciaDoAlvo({
      estagios: [est('persona', T('00:00.000'), 500), est('conta', T('00:00.000'), 0), est('sessao', null, null), est('interface_de_comentario_alcancada', T('00:10.000'), 9500), est('resposta_gerada', T('00:20.000'), 10_000)] as never,
      latencia: { duracao_ms: 20_000, espera_do_liberar_ms: 4000 },
    });
    expect(l.fonte).toBe('central');
    expect(l.passos.map((p) => [p.estagio, p.ms, p.situacao])).toEqual([
      ['persona', 500, 'ok'], ['conta', 0, 'mesma_hora'], ['sessao', null, null], ['interface_de_comentario_alcancada', 9500, 'ok'], ['resposta_gerada', 10_000, 'ok'],
    ]);
    expect(l.totalMs).toBe(20_000);
    expect(l.maisLento).toEqual({ estagio: 'resposta_gerada', ms: 10_000 });
    expect(l.esperaDoLiberarMs).toBe(4000);
    expect(l.mesmaHora).toBe(1);
    expect(l.foraDeOrdem).toBe(0);
  });

  it('sem a duração do central o total não é inventado do carimbo mais cedo ao mais tarde', () => {
    const l = latenciaDoAlvo({ estagios: [est('persona', T('00:00.000'), 0), est('conta', T('00:05.000'), 5000)] as never });
    expect(l.fonte).toBe('central');
    expect(l.totalMs).toBeNull();
  });

  it('sem nenhum campo do central, a conta local de antes', () => {
    const l = latenciaDoAlvo({ estagios: [est('persona', T('00:00.000')), est('conta', T('00:02.000'))] as never });
    expect(l).toMatchObject({ fonte: 'local', totalMs: 2000, esperaDoLiberarMs: null });
  });
});

describe('latenciaDaOperacao com o consolidado do central', () => {
  it('a tabela por estágio é a do central (mediana p50, p95, maior), na ordem do pipeline; sem ela, a local', () => {
    const alvos = [{ estagios: [est('persona', T('00:00.000')), est('conta', T('00:02.000'))] as never }];
    const c = latenciaDaOperacao(alvos, { sessao: { n: 2, p50_ms: 4000, p95_ms: 9000, max_ms: 9500 }, conta: { n: 3, p50_ms: 2000, p95_ms: null, max_ms: 2500 } });
    expect(c.fonte).toBe('central');
    expect(c.porEstagio).toEqual([
      { estagio: 'conta', medianaMs: 2000, maiorMs: 2500, agentes: 3, p95Ms: null },
      { estagio: 'sessao', medianaMs: 4000, maiorMs: 9500, agentes: 2, p95Ms: 9000 },
    ]);
    expect(c.maisLento).toMatchObject({ estagio: 'sessao', medianaMs: 4000 });
    expect(latenciaDaOperacao(alvos).fonte).toBe('local');
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
  backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ id: 'op-1', command: 'x', status: 'concluida', ...corpo }));
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'] } });
  await act(async () => root.render(<OperacaoPage />));
  await waitFor(() => expect(container.querySelectorAll('tbody tr[data-alvo]').length).toBeGreaterThan(0));
};
const alvo = (id: string, estagios: ReturnType<typeof est>[], latencia?: Record<string, unknown>) => ({
  run_id: `r-${id}`, profile_id: id, persona_nome: `Persona ${id}`, estado: 'concluido', estagio: 'resultado_verificado', estagios, ...(latencia ? { latencia } : {}),
});

describe('a tela', () => {
  it('mostra a conta do central: coluna p95, a duração do alvo, a espera pelo liberar e "mesma hora do evento anterior"', async () => {
    await abrir({
      alvos: [alvo('1', [est('persona', T('00:00.000'), 800), est('conta', T('00:00.000'), 0), est('sessao', T('00:30.000'), 30_000)], { duracao_ms: 75_000, espera_do_liberar_ms: 12_000 })],
      latencia_por_estagio: { conta: { n: 1, p50_ms: 0, p95_ms: 0, max_ms: 0 }, sessao: { n: 1, p50_ms: 30_000, p95_ms: 30_000, max_ms: 30_000 } },
    });
    const sec = container.querySelector('[data-latencia]')!;
    expect(Array.from(sec.querySelectorAll('thead th')).map((h) => text(h))).toEqual(['Estágio', 'Mediana', 'p95', 'Maior', 'Agentes']);
    expect(text(sec.querySelector('tr[data-estagio="sessao"] [data-p95]')!)).toContain('30 s');
    expect(text(sec)).toContain('Estágio mais lento: Sessão, mediana 30 s');
    expect(Array.from(container.querySelectorAll('td[data-duracao]')).map((d) => text(d))).toEqual(['1 min 15 s']);   // a duração é a do central, não 30 s
    expect(container.querySelector('[data-fora-da-conta]')).toBeNull();
    await click(byRole('button', /^Abrir o detalhe de Persona 1$/, container));
    expect(text(container.querySelector('[data-passo="mesma_hora"]')!)).toContain('mesma hora do evento anterior');
    expect(text(container.querySelector('[data-espera-do-liberar]')!)).toContain('Esperou a aprovação 12 s');
  });

  it('sem os campos do central (versão anterior), a tela é a de antes: sem p95 e sem espera', async () => {
    await abrir({ alvos: [alvo('1', [est('persona', T('00:00.000')), est('conta', T('00:02.000')), est('sessao', T('00:32.000'))])] });
    const sec = container.querySelector('[data-latencia]')!;
    expect(Array.from(sec.querySelectorAll('thead th')).map((h) => text(h))).toEqual(['Estágio', 'Mediana', 'Maior', 'Agentes']);
    expect(container.querySelector('[data-p95]')).toBeNull();
    expect(Array.from(container.querySelectorAll('td[data-duracao]')).map((d) => text(d))).toEqual(['32 s']);
  });
});
