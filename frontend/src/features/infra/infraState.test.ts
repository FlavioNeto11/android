import { describe, expect, it } from 'vitest';
import type { EventRecord, Health, Instance, RunSummary, WorkerDevice } from '../../api/types';
import {
  STALE_HEARTBEAT_MS, centralMeta, eventosDoServidor, filaDoServidor, fracaoDeDisco, groupByWorker,
  instanceStateMeta, isStale, orphanInstances, pausaDoReparoMeta, renderizadorMeta, tipoDoAparelho, vagasOcupadas,
} from './infraState';

// 29.11: argumento aceito não é renderizador usado — a linha mostra o que o emulador SELECIONOU.
describe('renderizadorMeta — o renderizador selecionado pelo emulador', () => {
  it('no ar mostra o efetivo; fora do ar só o configurado, dito como configurado e com o porquê', () => {
    const noAr = renderizadorMeta({ configured: 'host', gles: 'host', vulkan: 'host', fallback: false });
    expect(noAr).toMatchObject({ label: 'renderizador: GPU do host', fallback: false });
    const parado = renderizadorMeta({ configured: 'swiftshader_indirect', gles: null, vulkan: null, fallback: false });
    expect(parado?.label).toBe('renderizador configurado: SwiftShader');
    expect(parado?.title).toContain('o pedido ao emulador');
    expect(parado?.title).toContain('O efetivo, o que o emulador selecionou, só se sabe com o aparelho no ar');
    expect(noAr?.title).toContain('efetivo (selecionado pelo emulador): GLES host');
  });

  it('fallback diz o efetivo E o configurado', () => {
    const caiu = renderizadorMeta({ configured: 'host', gles: 'swiftshader', vulkan: 'swiftshader', fallback: true });
    expect(caiu).toMatchObject({ label: 'renderizador: SwiftShader (configurado: host)', fallback: true });
    expect(caiu?.title).toContain('sem avisar');
    expect(caiu?.title).toContain('vale no próximo reinício');      // a configuração mudada depois da subida
  });

  it('sem dado não inventa nada', () => {
    expect(renderizadorMeta(null)).toBeNull();
    expect(renderizadorMeta(undefined)).toBeNull();
    expect(renderizadorMeta({ configured: null, gles: null, vulkan: null, fallback: false })).toBeNull();
  });
});

function inst(id: string, worker_id: string | null): Instance {
  return { id, worker_id, state: 'online' } as unknown as Instance;
}

describe('groupByWorker — o servidor central também é um servidor', () => {
  it('agrupa por worker e usa `null` para o central', () => {
    const g = groupByWorker([inst('android-01', null), inst('android-09', 'w1'), inst('android-10', 'w1'),
                             inst('android-02', null)]);
    expect([...g.keys()]).toEqual([null, 'w1']);
    expect(g.get(null)?.map((i) => i.id)).toEqual(['android-01', 'android-02']);
    expect(g.get('w1')?.map((i) => i.id)).toEqual(['android-09', 'android-10']);
  });

  it('sem nenhum aparelho remoto, só existe o central', () => {
    expect([...groupByWorker([inst('android-01', null)]).keys()]).toEqual([null]);
  });
});

describe('orphanInstances — o silêncio que a tela precisa quebrar', () => {
  it('acha aparelho amarrado a servidor que não está inscrito', () => {
    // É um estado real e calado: o aparelho fica sem ciclo de vida e nada explica por quê.
    const orfas = orphanInstances([inst('android-09', 'w1'), inst('android-10', 'fantasma'), inst('android-01', null)],
                                  ['w1']);
    expect(orfas.map((i) => i.id)).toEqual(['android-10']);
  });

  it('aparelho do central nunca é órfão', () => {
    expect(orphanInstances([inst('android-01', null)], [])).toEqual([]);
  });
});

describe('isStale — dado velho não pode parecer atual', () => {
  it('idade desconhecida não é considerada velha', () => {
    expect(isStale(null)).toBe(false);
  });

  it('acima do limite da batida, é velha', () => {
    expect(isStale(STALE_HEARTBEAT_MS - 1)).toBe(false);
    expect(isStale(STALE_HEARTBEAT_MS + 1)).toBe(true);
  });

  it('o limite tolera três batidas perdidas (10 s cada), como o ceifador do backend', () => {
    expect(STALE_HEARTBEAT_MS).toBeGreaterThan(30_000);
  });
});

describe('instanceStateMeta', () => {
  it('hibernado é distinto de parado — um acorda em segundos, o outro não', () => {
    expect(instanceStateMeta('hibernated').label).toBe('hibernado');
    expect(instanceStateMeta('stopped').label).toBe('parado');
    expect(instanceStateMeta('error').tone).toBe('danger');
    expect(instanceStateMeta('online').tone).toBe('success');
  });

  it('estado desconhecido não quebra a tela', () => {
    expect(instanceStateMeta('inventado' as Instance['state']).label).toBe('inventado');
  });
});

// ---------------------------------------------------------------- achado #63: honestidade da Infraestrutura

const ev = (kind: string, instance_id: string | null, message = 'x'): EventRecord =>
  ({ id: null, ts: '2026-09-22T10:00:00Z', kind, level: 'info', run_id: null, instance_id, objective_id: null,
     step_id: null, attempt_id: null, message, data: null }) as EventRecord;

describe('vagasOcupadas — quem ocupa RAM, não quem já respondeu ao ADB', () => {
  it('booting ocupa vaga; o processo do worker ganha do estado visto daqui', () => {
    const lista = [
      { id: 'a', state: 'booting' }, { id: 'b', state: 'stopped' }, { id: 'c', state: 'online' },
    ] as unknown as Instance[];
    expect(vagasOcupadas(lista)).toBe(2);
    // O worker diz que 'b' está de pé lá e que 'c' já caiu: é ele quem sabe do processo.
    const doWorker = [
      { serial: 's1', state: 'running', instance_id: 'b' }, { serial: 's2', state: 'stopped', instance_id: 'c' },
    ] as unknown as WorkerDevice[];
    expect(vagasOcupadas(lista, doWorker)).toBe(2);   // a (booting) + b (running), c não conta
  });

  it('"unknown" do worker não apaga o que o central observou', () => {
    const lista = [{ id: 'a', state: 'online' }] as unknown as Instance[];
    expect(vagasOcupadas(lista, [{ serial: 's', state: 'unknown', instance_id: 'a' }] as WorkerDevice[])).toBe(1);
  });
});

describe('fracaoDeDisco — a barra era sempre vazia por falta do total', () => {
  it('usa livre/total e não inventa nada quando o total falta', () => {
    expect(fracaoDeDisco(100, 400)).toBeCloseTo(0.75);
    expect(fracaoDeDisco(400, 400)).toBe(0);
    expect(fracaoDeDisco(100, null)).toBe(0);
    expect(fracaoDeDisco(null, 400)).toBe(0);
  });
});

describe('centralMeta — o central era "online" fixo', () => {
  it('a saúde declarada e a conexão do painel decidem o selo', () => {
    expect(centralMeta({ status: 'degraded' } as Health, true, null).label).toBe('degradado');
    expect(centralMeta({ status: 'error' } as Health, true, null).tone).toBe('danger');
    expect(centralMeta({ status: 'ok' } as Health, true, null).label).toBe('online');
    expect(centralMeta({ status: 'ok' } as Health, false, null).label).toBe('sem conexão com o painel');
    expect(centralMeta(null, true, null).label).toBe('sem dado de saúde');
    expect(centralMeta({ status: 'ok' } as Health, true, { state: 'maintenance' }).label).toBe('em manutenção');
  });
});

describe('eventos e fila por servidor', () => {
  const ids = new Set(['android-01', 'android-02']);

  it('logs do servidor são os dos aparelhos dele, do mais novo para o mais velho', () => {
    const events = [ev('log', 'android-01', 'a'), ev('log', 'android-09', 'b'), ev('frame', 'android-01', 'c'),
                    ev('log', 'android-02', 'd')];
    expect(eventosDoServidor(events, ids, ['log']).map((e) => e.message)).toEqual(['d', 'a']);
    expect(eventosDoServidor(events, ids, ['evidence.added'])).toEqual([]);
  });

  it('a fila só traz execução em voo que toca este servidor', () => {
    const runs = [
      { id: 'r1', status: 'running', instance_ids: ['android-01'] },
      { id: 'r2', status: 'completed', instance_ids: ['android-01'] },
      { id: 'r3', status: 'planned', instance_ids: ['android-09'] },
    ] as unknown as RunSummary[];
    expect(filaDoServidor(runs, ids).map((r) => r.id)).toEqual(['r1']);
  });
});

// Polimento dos deploys 9 a 11: "android-11 · parado · store" (o kind cru) na linha do aparelho.
describe('tipoDoAparelho — o tipo em português, com o porquê', () => {
  it('a loja e o externo ganham nome; o emulador do projeto não ganha selo', () => {
    expect(tipoDoAparelho('store')).toMatchObject({ label: 'loja' });
    expect(tipoDoAparelho('store')?.title).toContain('nunca recebe tarefa');
    expect(tipoDoAparelho('external')).toMatchObject({ label: 'externo' });
    expect(tipoDoAparelho('emulator')).toBeNull();
  });
});

// 25.13 sem tela: a pausa do reparo só existia em /api/instances[].repair_pause.
describe('pausaDoReparoMeta — "reparo pausado até hh:mm"', () => {
  const agora = new Date(2026, 9, 3, 15, 0, 0).getTime();          // 03/10 15:00 no fuso local
  const pausa = (ate: Date) => ({
    since: new Date(2026, 9, 3, 14, 57, 5).toISOString(), until: ate.toISOString(),
    reason: '29.46: braços por flag do emulador (config temporária)', by: 'panel', remaining_s: 1800,
  });

  it('hoje mostra só a hora; o motivo e quem pausou vão na dica', () => {
    const m = pausaDoReparoMeta(pausa(new Date(2026, 9, 3, 15, 27, 5)), agora);
    expect(m?.label).toBe('reparo pausado até 15:27');
    expect(m?.title).toContain('pelo painel');
    expect(m?.title).toContain('o reparo automático não age neste aparelho');
    expect(m?.title).toContain('29.46: braços por flag do emulador');
    expect(m?.label).not.toContain('29.46');                        // texto do procedimento fica fora da linha
  });

  it('outro dia leva a data', () => {
    expect(pausaDoReparoMeta(pausa(new Date(2026, 9, 4, 8, 0, 0)), agora)?.label).toBe('reparo pausado até 04/10 08:00');
  });

  it('vencida ou ausente não aparece', () => {
    expect(pausaDoReparoMeta(pausa(new Date(2026, 9, 3, 14, 59, 0)), agora)).toBeNull();
    expect(pausaDoReparoMeta(null, agora)).toBeNull();
    expect(pausaDoReparoMeta(undefined, agora)).toBeNull();
  });
});
