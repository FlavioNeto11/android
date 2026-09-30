import type { AiBalance, Health, Instance, InstanceState, RunSummary, Worker, WorkerDevice } from '../api/types';
import { serverHintOf } from '../features/devices/deviceState';
import { hashDe } from '../lib/rotas';
import { isRunActive, type ConnStatus } from '../lib/status';
import { useMemo } from 'react';
import { selectInstanceList, useAppStore } from './app';
import type { DataState } from './reducer';

/**
 * Fonte única dos números do portal (revisão de UX, tarefa 02). O mesmo indicador aparecia com valores diferentes
 * porque cada tela contava do seu jeito: o cabeçalho contava a loja e a grade não ("5/15 online" ao lado de "1 de 14
 * selecionados"), o resumo por estado dizia "10 paradas" e o botão selecionava 9, e o "bloqueadas" do cabeçalho
 * (objetivos de execução) era lido como personas. Toda tela que mostra um destes números lê daqui.
 *
 * Definições (a tabela completa está em `docs/revisoes-ux/02-numeros-saude.md`):
 * - **aparelhos (total)**: aparelhos de TAREFA cadastrados, sem a loja. É a mesma base de "selecionados de N" e da
 *   seleção por estado; a loja (Play Store) nunca é alvo de tarefa e aparece à parte, com o estado dela.
 * - **online**: aparelho de tarefa com `state = online` e servidor alcançável.
 * - **paradas** (e os demais estados): o `state` do aparelho, só entre os de servidor alcançável.
 * - **desconhecido**: aparelho cujo servidor não está inscrito ou está fora do ar. O que o central guardou dele é
 *   velho; não conta como online nem como parado, e aparece separado.
 * - **selecionados**: ids selecionados que ainda são aparelhos de tarefa, de `total`.
 * - **vagas**: por servidor, nunca somadas no parque (a soma escondia um servidor acima da capacidade atrás da folga
 *   de outro). Ocupa vaga o que come RAM: `online`, `booting`, `stopping` (a loja também, se ligada). A vaga do
 *   central é `max_online_devices`; a de cada worker, `max_slots`.
 * - **aguardando você**: objetivos `waiting_user` + `uncertain` das execuções ativas e das 20 mais recentes (a
 *   mesma janela do snapshot). Não são personas.
 * - **personas bloqueadas**: personas com `status = blocked` (bloqueadas pela plataforma).
 */

// ---- Aparelhos ------------------------------------------------------------------------------------

export type EstadoContado = InstanceState | 'desconhecido';

/** Ordem fixa do resumo: do que ocupa RAM para o que não ocupa, e o desconhecido por último (não se sabe). */
export const ORDEM_DOS_ESTADOS: readonly EstadoContado[] = [
  'online', 'booting', 'stopping', 'hibernated', 'stopped', 'absent', 'error', 'desconhecido',
];

/** Rótulo curto [singular, plural] de cada estado no resumo. */
export const ROTULO_DO_ESTADO: Record<EstadoContado, readonly [string, string]> = {
  online: ['online', 'online'],
  booting: ['iniciando', 'iniciando'],
  stopping: ['parando', 'parando'],
  hibernated: ['hibernado', 'hibernados'],
  stopped: ['parada', 'paradas'],
  absent: ['sem AVD', 'sem AVD'],
  error: ['com erro', 'com erro'],
  desconhecido: ['desconhecido', 'desconhecidos'],
};

type MapaDeWorkers = Readonly<Record<string, Worker>> | undefined | null;

/**
 * O servidor que hospeda o aparelho está fora de alcance? Sem lista de servidores (backend antigo) não se sabe, e
 * aí ninguém vira desconhecido — dizer "desconhecido" de todo o parque por falta de campo seria alarme falso.
 */
export function servidorInalcancavel(inst: Pick<Instance, 'id' | 'worker_id'>, workers: MapaDeWorkers): boolean {
  if (!workers || Object.keys(workers).length === 0) return false;
  const hint = serverHintOf(inst, workers);
  return hint !== null && (!hint.enrolled || !hint.connected);
}

export function estadoContado(inst: Pick<Instance, 'id' | 'worker_id' | 'state'>, workers: MapaDeWorkers): EstadoContado {
  return servidorInalcancavel(inst, workers) ? 'desconhecido' : inst.state;
}

export interface GrupoDeEstado { estado: EstadoContado; ids: string[] }

export interface ContagemDeAparelhos {
  /** Aparelhos de tarefa cadastrados (sem a loja). */
  total: number;
  online: number;
  desconhecidos: number;
  /** Só os estados presentes, em `ORDEM_DOS_ESTADOS`; a soma dos `ids` é `total`. */
  porEstado: GrupoDeEstado[];
  /** A loja, à parte: nunca é alvo e não entra em `total`. */
  loja: { id: string; estado: EstadoContado } | null;
}

export function contarAparelhos(s: Pick<DataState, 'instances' | 'instanceOrder'>, workers: MapaDeWorkers): ContagemDeAparelhos {
  const grupos = new Map<EstadoContado, string[]>();
  let loja: ContagemDeAparelhos['loja'] = null;
  let total = 0;
  for (const inst of selectInstanceList(s)) {
    const estado = estadoContado(inst, workers);
    if (inst.kind === 'store') {
      loja ??= { id: inst.id, estado };
      continue;
    }
    total += 1;
    const lista = grupos.get(estado);
    if (lista) lista.push(inst.id);
    else grupos.set(estado, [inst.id]);
  }
  const porEstado = ORDEM_DOS_ESTADOS.filter((e) => grupos.has(e)).map((e) => ({ estado: e, ids: grupos.get(e) ?? [] }));
  return {
    total,
    online: grupos.get('online')?.length ?? 0,
    desconhecidos: grupos.get('desconhecido')?.length ?? 0,
    porEstado,
    loja,
  };
}

/** "N de T selecionados": só conta o que ainda é aparelho de tarefa (um id que sumiu do parque não é seleção). */
export function contarSelecao(selecionados: readonly string[], ordemDeTarefa: readonly string[]): { selecionados: number; total: number } {
  const validos = new Set(ordemDeTarefa);
  return { selecionados: selecionados.filter((id) => validos.has(id)).length, total: ordemDeTarefa.length };
}

// ---- Vagas por servidor ---------------------------------------------------------------------------

/** Estados de PROCESSO, no vocabulário do worker, que ocupam uma vaga na máquina dele. */
const PROCESSO_OCUPA = new Set(['online', 'running', 'booting', 'starting', 'stopping']);

/**
 * Quantas vagas estão ocupadas neste servidor. Contar só instância `online` subestimava: um aparelho `booting`
 * já come a RAM da vaga, e quem decide se o processo está de pé é o worker — não o ADB visto daqui (#63).
 * Mesma regra de `slots_used_of` no backend (`devices/manager.py`).
 */
export function vagasOcupadas(instances: readonly Instance[], doWorker?: readonly WorkerDevice[]): number {
  const proc = new Map((doWorker ?? []).filter((d) => d.instance_id).map((d) => [d.instance_id as string, d.state]));
  return instances.filter((i) => {
    const p = proc.get(i.id);
    if (p && p !== 'unknown') return PROCESSO_OCUPA.has(p);
    return i.state === 'online' || i.state === 'booting' || i.state === 'stopping';
  }).length;
}

export interface Ocupacao {
  /** `null` quando o servidor está fora do ar: a ocupação lá não se sabe, e zero seria mentira. */
  ocupadas: number | null;
  vagas: number;
  /** Mais aparelhos ligados do que vagas. */
  acima: boolean;
}

export function ocupacaoDoServidor(instancias: readonly Instance[], worker: Worker | null): Ocupacao {
  const vagas = worker?.max_slots ?? instancias.length;
  if (worker && !worker.local && !worker.connected) return { ocupadas: null, vagas, acima: false };
  const ocupadas = vagasOcupadas(instancias, worker?.devices);
  return { ocupadas, vagas, acima: vagas > 0 && ocupadas > vagas };
}

export interface OcupacaoDeServidor extends Ocupacao { id: string | null; nome: string; local: boolean }

/** A ocupação de cada servidor inscrito. Aparelho sem `worker_id` é do central (backend anterior ao `LocalWorker`). */
export function ocupacoesDoParque(instances: readonly Instance[], workers: MapaDeWorkers): OcupacaoDeServidor[] {
  const lista = Object.values(workers ?? {});
  const central = lista.find((w) => w.local) ?? null;
  const out: OcupacaoDeServidor[] = [];
  if (central) {
    const doCentral = instances.filter((i) => !i.worker_id || i.worker_id === central.id);
    out.push({ id: central.id, nome: 'Este servidor (central)', local: true, ...ocupacaoDoServidor(doCentral, central) });
  }
  for (const w of lista) {
    if (w.local) continue;
    out.push({ id: w.id, nome: w.name, local: false,
               ...ocupacaoDoServidor(instances.filter((i) => i.worker_id === w.id), w) });
  }
  return out;
}

// ---- Execuções e personas -------------------------------------------------------------------------

/** A mesma janela do snapshot (`runs`: ativas + recentes, até 20). */
export const JANELA_DE_EXECUCOES = 20;

/**
 * Objetivos que esperam uma pessoa (`waiting_user`) ou uma revisão (`uncertain`). A lista de execuções do store
 * CRESCE quando a tela Execuções carrega mais histórico — contar sobre ela inteira fazia o número mudar conforme a
 * tela visitada. Fixar a janela (ativas + as 20 mais recentes) deixa o valor igual em qualquer tela.
 */
export function objetivosAguardando(runs: readonly RunSummary[]): { total: number; primeiraExecucao: string | null } {
  const recentes = [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at));
  let total = 0;
  let primeira: string | null = null;
  recentes.forEach((r, i) => {
    if (i >= JANELA_DE_EXECUCOES && !isRunActive(r.status)) return;
    const n = (r.counts?.waiting_user ?? 0) + (r.counts?.uncertain ?? 0);
    if (n > 0 && !primeira) primeira = r.id;
    total += n;
  });
  return { total, primeiraExecucao: primeira };
}

/** Personas bloqueadas pela plataforma. `null` enquanto a lista não chegou. */
export function personasBloqueadas(personas: readonly { status?: string | null }[] | null): number | null {
  return personas ? personas.filter((p) => p.status === 'blocked').length : null;
}

// ---- Semáforo do ambiente -------------------------------------------------------------------------

export type NivelDoAmbiente = 'ok' | 'atencao' | 'critico';

export interface Motivo {
  chave: string;
  nivel: Exclude<NivelDoAmbiente, 'ok'>;
  texto: string;
  dica?: string;
  /** Hash da tela que resolve (sempre por `hashDe`); `null` quando não há tela a abrir. */
  destino: string | null;
  rotuloDestino?: string;
}

export interface EntradaDoAmbiente {
  conn: ConnStatus;
  health: Health | null;
  workers: MapaDeWorkers;
  contagem: Pick<ContagemDeAparelhos, 'desconhecidos'>;
  ocupacoes: readonly OcupacaoDeServidor[];
}

const PESO: Record<NivelDoAmbiente, number> = { ok: 0, atencao: 1, critico: 2 };

const INFRA = hashDe('infraestrutura');
const DIAG = hashDe('diagnostico');

function plural(n: number, um: string, varios: string): string {
  return `${n} ${n === 1 ? um : varios}`;
}

/**
 * OK, Atenção ou Crítico, com os motivos. O "Ambiente OK" de antes vinha só de `GET /health`, que não olha os
 * servidores: ficava verde com o notebook da LAN fora do ar e seis aparelhos sem estado conhecido.
 *
 * - Crítico: o painel sem conexão com o central (tudo na tela envelhece), saúde `error`, banco inalcançável, conta
 *   de IA em uso barrada ou sem saldo.
 * - Atenção: saúde `degraded` (cada problema declarado), servidor fora do ar, degradado ou com túnel caído, aparelhos
 *   em estado desconhecido, servidor acima da capacidade, saldo de IA baixo, IA não configurada.
 */
export function nivelDoAmbiente(e: EntradaDoAmbiente): { nivel: NivelDoAmbiente; motivos: Motivo[] } {
  const motivos: Motivo[] = [];
  const { health } = e;

  if (e.conn === 'disconnected' || e.conn === 'reconnecting') {
    motivos.push({ chave: 'conexao', nivel: 'critico', destino: null,
                   texto: 'O painel está sem conexão com o servidor central',
                   dica: 'Os números na tela podem estar desatualizados até a conexão voltar.' });
  }
  if (health?.database && health.database.reachable === false) {
    motivos.push({ chave: 'banco', nivel: 'critico', destino: DIAG, rotuloDestino: 'Abrir Diagnóstico',
                   texto: 'O banco de dados não respondeu' });
  }
  const problemas = health?.problems ?? [];
  const nivelDaSaude = health?.status === 'error' ? 'critico' : 'atencao';
  problemas.forEach((p, i) => {
    motivos.push({ chave: `saude-${p.code}-${i}`, nivel: nivelDaSaude, texto: p.message, dica: p.hint || undefined,
                   destino: DIAG, rotuloDestino: 'Abrir Diagnóstico' });
  });
  if (problemas.length === 0 && (health?.status === 'degraded' || health?.status === 'error')) {
    motivos.push({ chave: 'saude', nivel: nivelDaSaude, destino: DIAG, rotuloDestino: 'Abrir Diagnóstico',
                   texto: health.status === 'error' ? 'O servidor central declara erro' : 'O servidor central declara saúde degradada' });
  }

  for (const w of Object.values(e.workers ?? {})) {
    if (w.local || w.maintenance) continue;
    if (!w.connected || w.state === 'offline') {
      motivos.push({ chave: `servidor-${w.id}`, nivel: 'atencao', destino: INFRA, rotuloDestino: 'Abrir Infraestrutura',
                     texto: `Servidor ${w.name} fora do ar`,
                     dica: 'Os aparelhos dele ficam em estado desconhecido até ele voltar.' });
    } else if (w.state === 'degraded') {
      motivos.push({ chave: `servidor-${w.id}`, nivel: 'atencao', destino: INFRA, rotuloDestino: 'Abrir Infraestrutura',
                     texto: `Servidor ${w.name} degradado`, dica: w.state_detail || undefined });
    }
    if (w.connected && w.transport_state === 'down') {
      motivos.push({ chave: `tunel-${w.id}`, nivel: 'atencao', destino: INFRA, rotuloDestino: 'Abrir Infraestrutura',
                     texto: `Túnel para ${w.name} fora do ar`, dica: w.transport_detail || undefined });
    }
  }

  if (e.contagem.desconhecidos > 0) {
    motivos.push({ chave: 'desconhecidos', nivel: 'atencao', rotuloDestino: 'Ver no Painel',
                   destino: hashDe('painel', { query: { estado: 'desconhecido' } }),
                   texto: `${plural(e.contagem.desconhecidos, 'aparelho', 'aparelhos')} em estado desconhecido`,
                   dica: 'O servidor que os hospeda está fora do ar ou não está inscrito.' });
  }

  for (const o of e.ocupacoes) {
    if (!o.acima || o.ocupadas === null) continue;
    motivos.push({ chave: `vagas-${o.id ?? 'central'}`, nivel: 'atencao', destino: INFRA, rotuloDestino: 'Abrir Infraestrutura',
                   texto: `${o.nome}: ${o.ocupadas} aparelhos ligados para ${plural(o.vagas, 'vaga', 'vagas')}`,
                   dica: 'Há mais aparelhos ligados do que vagas; a RAM pode faltar para o próximo.' });
  }

  const ai = health?.ai;
  if (ai && !ai.configured && !ai.simulated) {
    motivos.push({ chave: 'ia', nivel: 'atencao', destino: DIAG, rotuloDestino: 'Abrir Diagnóstico',
                   texto: 'IA não configurada', dica: 'Sem a chave, nada é planejado nem executado com IA.' });
  }
  for (const b of (ai?.balances ?? []) as AiBalance[]) {
    const barrada = b.state === 'blocked' || b.state === 'exhausted';
    if (barrada) {
      motivos.push({ chave: `saldo-${b.account}`, nivel: b.in_use ? 'critico' : 'atencao', destino: DIAG,
                     rotuloDestino: 'Abrir Diagnóstico', texto: `Conta de IA ${b.label}: ${b.state === 'exhausted' ? 'sem saldo' : 'barrada'}`,
                     dica: b.message || undefined });
    } else if (b.state === 'low' && b.in_use) {
      motivos.push({ chave: `saldo-${b.account}`, nivel: 'atencao', destino: DIAG, rotuloDestino: 'Abrir Diagnóstico',
                     texto: `Saldo baixo na conta de IA ${b.label}`, dica: b.message || undefined });
    }
  }

  const nivel = motivos.reduce<NivelDoAmbiente>((acc, m) => (PESO[m.nivel] > PESO[acc] ? m.nivel : acc), 'ok');
  // Crítico primeiro: é o que se lê antes.
  motivos.sort((a, b) => PESO[b.nivel] - PESO[a.nivel]);
  return { nivel, motivos };
}

export const ROTULO_DO_NIVEL: Record<NivelDoAmbiente, string> = {
  ok: 'Ambiente OK',
  atencao: 'Ambiente em atenção',
  critico: 'Ambiente crítico',
};

// ---- Ganchos: o que as telas consomem -----------------------------------------------------------

export function useContagemDeAparelhos(): ContagemDeAparelhos {
  const instances = useAppStore((s) => s.instances);
  const instanceOrder = useAppStore((s) => s.instanceOrder);
  const workers = useAppStore((s) => s.workers);
  return useMemo(() => contarAparelhos({ instances, instanceOrder }, workers), [instances, instanceOrder, workers]);
}

export function useObjetivosAguardando(): { total: number; primeiraExecucao: string | null } {
  const runs = useAppStore((s) => s.runs);
  return useMemo(() => objetivosAguardando(runs), [runs]);
}

export function useSaudeDoAmbiente(): { nivel: NivelDoAmbiente; motivos: Motivo[] } {
  const conn = useAppStore((s) => s.conn.status);
  const health = useAppStore((s) => s.health);
  const workers = useAppStore((s) => s.workers);
  const instances = useAppStore((s) => s.instances);
  const instanceOrder = useAppStore((s) => s.instanceOrder);
  const contagem = useContagemDeAparelhos();
  const ocupacoes = useMemo(
    () => ocupacoesDoParque(selectInstanceList({ instances, instanceOrder }), workers), [instances, instanceOrder, workers]);
  return useMemo(() => nivelDoAmbiente({ conn, health, workers, contagem, ocupacoes }),
    [conn, health, workers, contagem, ocupacoes]);
}
