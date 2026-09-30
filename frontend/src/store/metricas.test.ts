import { describe, expect, it } from 'vitest';
import type { AiBalance, Health, Instance, Worker } from '../api/types';
import { hashDe } from '../lib/rotas';
import { makeInstance, makeRun, makeSnapshot } from '../test/fixtures';
import {
  JANELA_DE_EXECUCOES, contarAparelhos, contarSelecao, nivelDoAmbiente, objetivosAguardando, ocupacaoDoServidor,
  ocupacoesDoParque, personasBloqueadas,
} from './metricas';

/**
 * O parque de 30/09 em miniatura: o central (WIN) com android-01..08 e a loja android-11; o notebook da LAN com
 * android-09, 10 e 12..15. Cinco ligados no central para quatro vagas — o "5 de 4" da avaliação, que é real.
 */
const CENTRAL = 'WIN-CENTRAL';
const LAN = 'worker-lan-01';

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: LAN, name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

function parque(): Instance[] {
  const online = new Set([1, 2, 3, 5, 6, 10, 12]);
  const lista: Instance[] = [];
  for (const n of [1, 2, 3, 4, 5, 6, 7, 8]) {
    lista.push(makeInstance(n, { state: online.has(n) ? 'online' : 'stopped', worker_id: CENTRAL }));
  }
  for (const n of [9, 10, 12, 13, 14, 15]) {
    lista.push(makeInstance(n, { state: online.has(n) ? 'online' : 'stopped', worker_id: LAN, kind: 'external' }));
  }
  lista.push(makeInstance(11, { state: 'stopped', worker_id: CENTRAL, kind: 'store' }));
  return lista;
}

function estado(lista: Instance[]) {
  return { instances: Object.fromEntries(lista.map((i) => [i.id, i])), instanceOrder: lista.map((i) => i.id) };
}

function workers(lan: Partial<Worker> = {}): Record<string, Worker> {
  return {
    [CENTRAL]: worker({ id: CENTRAL, name: 'WIN-CENTRAL (este servidor)', local: true, max_slots: 4 }),
    [LAN]: worker(lan),
  };
}

const LAN_FORA: Partial<Worker> = { connected: false, state: 'offline', observed_state: 'offline' };

function saude(over: Partial<Health> = {}): Health {
  return { ...makeSnapshot().health, status: 'ok', problems: [], ...over };
}

function ambiente(over: { lan?: Partial<Worker>; conn?: 'connected' | 'disconnected'; health?: Health; lista?: Instance[];
                          semWorkers?: boolean } = {}) {
  const lista = over.lista ?? parque();
  const ws = over.semWorkers ? undefined : workers(over.lan);
  const contagem = contarAparelhos(estado(lista), ws);
  return {
    contagem,
    ...nivelDoAmbiente({ conn: over.conn ?? 'connected', health: over.health ?? saude(), workers: ws, contagem,
                         ocupacoes: ocupacoesDoParque(lista, ws) }),
  };
}

describe('contagem de aparelhos', () => {
  it('uma base só: aparelhos de tarefa, sem a loja, que fica à parte', () => {
    const c = contarAparelhos(estado(parque()), workers());
    expect(c.total).toBe(14);
    expect(c.online).toBe(7);
    expect(c.loja).toEqual({ id: 'android-11', estado: 'stopped' });
    // A soma dos grupos fecha com o total: o resumo por estado e o "online / total" não se contradizem.
    expect(c.porEstado.reduce((n, g) => n + g.ids.length, 0)).toBe(c.total);
    // "paradas" conta o mesmo conjunto que o botão seleciona (antes: 10 no texto, 9 no clique).
    const paradas = c.porEstado.find((g) => g.estado === 'stopped');
    expect(paradas?.ids).not.toContain('android-11');
    expect(paradas?.ids).toHaveLength(7);
  });

  it('servidor da LAN fora do ar: os aparelhos dele são desconhecidos, não paradas nem online', () => {
    const c = contarAparelhos(estado(parque()), workers(LAN_FORA));
    expect(c.total).toBe(14);
    expect(c.desconhecidos).toBe(6);
    expect(c.online).toBe(5);
    expect(c.porEstado.find((g) => g.estado === 'stopped')?.ids).toHaveLength(3);
    expect(c.porEstado.at(-1)).toMatchObject({ estado: 'desconhecido' });
  });

  it('aparelho apontando para servidor que não está inscrito também é desconhecido', () => {
    const lista = [makeInstance(1, { state: 'online', worker_id: 'worker-que-sumiu', kind: 'external' })];
    expect(contarAparelhos(estado(lista), workers()).desconhecidos).toBe(1);
  });

  it('sem lista de servidores (backend antigo) ninguém vira desconhecido', () => {
    const c = contarAparelhos(estado(parque()), undefined);
    expect(c.desconhecidos).toBe(0);
    expect(c.online).toBe(7);
  });
});

describe('seleção', () => {
  it('seleção parcial conta sobre a mesma base da grade, e ignora a loja e ids que sumiram', () => {
    const ordem = contarAparelhos(estado(parque()), workers()).porEstado.flatMap((g) => g.ids);
    expect(contarSelecao(['android-01', 'android-11', 'android-99'], ordem)).toEqual({ selecionados: 1, total: 14 });
    expect(contarSelecao([], ordem)).toEqual({ selecionados: 0, total: 14 });
  });
});

describe('vagas por servidor', () => {
  it('ocupação acima da capacidade é real e é dita como tal: 5 ligados para 4 vagas', () => {
    const doCentral = parque().filter((i) => i.worker_id === CENTRAL);
    expect(ocupacaoDoServidor(doCentral, workers()[CENTRAL] ?? null)).toEqual({ ocupadas: 5, vagas: 4, acima: true });
  });

  it('a loja ligada ocupa vaga do central (come RAM como qualquer emulador)', () => {
    const lista = parque().map((i) => (i.id === 'android-11' ? { ...i, state: 'online' as const } : i));
    const central = ocupacoesDoParque(lista, workers()).find((o) => o.local);
    expect(central?.ocupadas).toBe(6);
  });

  it('servidor fora do ar: ocupação desconhecida (null), nunca zero, e sem alarme de capacidade', () => {
    const lan = ocupacoesDoParque(parque(), workers(LAN_FORA)).find((o) => o.id === LAN);
    expect(lan).toMatchObject({ ocupadas: null, vagas: 6, acima: false });
  });

  it('as vagas nunca se somam no parque: cada servidor com a sua', () => {
    const o = ocupacoesDoParque(parque(), workers());
    expect(o.map((x) => [x.nome, x.ocupadas, x.vagas])).toEqual([
      ['Este servidor (central)', 5, 4],
      ['Notebook da LAN', 2, 6],
    ]);
  });
});

describe('semáforo do ambiente', () => {
  it('tudo em ordem: OK, sem motivos', () => {
    const lista = parque().map((i) => (i.id === 'android-06' ? { ...i, state: 'stopped' as const } : i));
    expect(ambiente({ lista })).toMatchObject({ nivel: 'ok', motivos: [] });
  });

  it('notebook da LAN fora do ar: Atenção, com o servidor e os desconhecidos como motivos, cada um com destino', () => {
    const { nivel, motivos } = ambiente({ lan: LAN_FORA });
    expect(nivel).toBe('atencao');
    const servidor = motivos.find((m) => m.chave === `servidor-${LAN}`);
    expect(servidor).toMatchObject({ texto: 'Servidor Notebook da LAN fora do ar', destino: hashDe('infraestrutura') });
    const desconhecidos = motivos.find((m) => m.chave === 'desconhecidos');
    expect(desconhecidos).toMatchObject({ texto: '6 aparelhos em estado desconhecido',
                                          destino: hashDe('painel', { query: { estado: 'desconhecido' } }) });
  });

  it('com GET /health dizendo ok, o servidor degradado ainda acende Atenção', () => {
    const { nivel, motivos } = ambiente({ lan: { state: 'degraded', state_detail: 'ADB lento' } });
    expect(nivel).toBe('atencao');
    expect(motivos.find((m) => m.chave === `servidor-${LAN}`)).toMatchObject({ texto: 'Servidor Notebook da LAN degradado', dica: 'ADB lento' });
  });

  it('servidor em manutenção não é problema', () => {
    const lista = parque().map((i) => (i.id === 'android-06' ? { ...i, state: 'stopped' as const } : i));
    expect(ambiente({ lista, lan: { state: 'maintenance', maintenance: true } }).nivel).toBe('ok');
  });

  it('ocupação acima da capacidade vira motivo de Atenção apontando para a Infraestrutura', () => {
    const { nivel, motivos } = ambiente();
    expect(nivel).toBe('atencao');
    expect(motivos).toEqual([expect.objectContaining({
      chave: `vagas-${CENTRAL}`, texto: 'Este servidor (central): 5 aparelhos ligados para 4 vagas', destino: hashDe('infraestrutura'),
    })]);
  });

  it('painel sem conexão com o central: Crítico, primeiro da lista, sem inventar desconhecidos', () => {
    const { nivel, motivos, contagem } = ambiente({ conn: 'disconnected' });
    expect(nivel).toBe('critico');
    expect(motivos[0]).toMatchObject({ chave: 'conexao', nivel: 'critico', destino: null });
    expect(contagem.desconhecidos).toBe(0);
  });

  it('saúde com erro é Crítico; degradada é Atenção; cada problema declarado vira um motivo', () => {
    const problema = { code: 'appium_down', message: 'Appium parado', hint: 'Reinicie o Appium.' };
    expect(ambiente({ health: saude({ status: 'error', problems: [problema] }) }).nivel).toBe('critico');
    const degradado = ambiente({ health: saude({ status: 'degraded', problems: [problema] }) });
    expect(degradado.nivel).toBe('atencao');
    expect(degradado.motivos).toContainEqual(expect.objectContaining({ texto: 'Appium parado', dica: 'Reinicie o Appium.',
                                                                      destino: hashDe('diagnostico') }));
  });

  it('saldo de IA: baixo em uso é Atenção; sem saldo em uso é Crítico', () => {
    const conta = (over: Partial<AiBalance>) => ({ account: 'anthropic', label: 'Anthropic', in_use: true, state: 'ok',
                                                   message: '', currency: 'USD' } as unknown as AiBalance & typeof over);
    const base = saude();
    const com = (b: Partial<AiBalance>) => saude({ ai: { ...base.ai, balances: [{ ...conta({}), ...b } as AiBalance] } });
    const lista = parque().map((i) => (i.id === 'android-06' ? { ...i, state: 'stopped' as const } : i));
    expect(ambiente({ lista, health: com({ state: 'low' }) }).nivel).toBe('atencao');
    expect(ambiente({ lista, health: com({ state: 'exhausted' }) }).nivel).toBe('critico');
    expect(ambiente({ lista, health: com({ state: 'low', in_use: false }) }).nivel).toBe('ok');
  });
});

describe('aguardando você e personas bloqueadas', () => {
  it('a janela é fixa (ativas + 20 mais recentes): carregar mais histórico não muda o número', () => {
    const antigas = Array.from({ length: 30 }, (_, i) => makeRun({
      id: `r-${i}`, status: 'completed_with_issues', created_at: `2026-09-${String(10 + (i % 18)).padStart(2, '0')}T${String(i % 24).padStart(2, '0')}:00:00Z`,
    }));
    const doSnapshot = [...antigas].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, JANELA_DE_EXECUCOES);
    expect(objetivosAguardando(doSnapshot).total).toBe(20);
    expect(objetivosAguardando(antigas).total).toBe(20);
  });

  it('execução ativa conta mesmo fora das 20 mais recentes', () => {
    const recentes = Array.from({ length: 20 }, (_, i) => makeRun({ id: `r-${i}`, status: 'completed', created_at: `2026-09-29T${String(i).padStart(2, '0')}:00:00Z`,
                                                                    counts: { ...makeRun().counts, waiting_user: 0 } }));
    const ativaAntiga = makeRun({ id: 'r-ativa', status: 'running', created_at: '2026-09-01T00:00:00Z',
                                  counts: { ...makeRun().counts, waiting_user: 1, uncertain: 1 } });
    expect(objetivosAguardando([...recentes, ativaAntiga])).toEqual({ total: 2, primeiraExecucao: 'r-ativa' });
  });

  it('personas bloqueadas contam só `blocked`; sem lista ainda, não há número', () => {
    expect(personasBloqueadas([{ status: 'blocked' }, { status: 'active' }, { status: 'blocked' }, {}])).toBe(2);
    expect(personasBloqueadas(null)).toBeNull();
  });
});
