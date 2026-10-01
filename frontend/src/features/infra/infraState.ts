import type { EventRecord, Health, Instance, RunSummary } from '../../api/types';
import type { Tone } from '../../lib/status';
import type { EstadoContado } from '../../store/metricas';

/**
 * Regras puras da visão de infraestrutura, fora do componente para serem testáveis em node — mesmo padrão de
 * `features/devices/deviceState.ts`.
 */

const ESTADO: Record<EstadoContado, { label: string; tone: Tone }> = {
  online: { label: 'online', tone: 'success' },
  booting: { label: 'iniciando', tone: 'info' },
  stopping: { label: 'parando', tone: 'info' },
  hibernated: { label: 'hibernado', tone: 'muted' },
  stopped: { label: 'parado', tone: 'neutral' },
  absent: { label: 'sem AVD', tone: 'neutral' },
  error: { label: 'com erro', tone: 'danger' },
  // Servidor fora do ar ou sem canal (RF-40): a mesma palavra do Painel e do Foco, nunca o estado guardado.
  desconhecido: { label: 'desconhecido', tone: 'warning' },
};

/** Recebe o estado CONTADO (`store/metricas::estadoContado`), não o `state` cru: a regra do desconhecido é a mesma. */
export function instanceStateMeta(state: EstadoContado): { label: string; tone: Tone } {
  return ESTADO[state] ?? { label: state, tone: 'neutral' };
}

const RENDERIZADOR: Record<string, string> = { host: 'GPU do host', swiftshader: 'SwiftShader' };

/** `swiftshader_indirect` e `swiftshader` são o mesmo renderizador: o sufixo diz só como o convidado fala com ele. */
function nomeDoRenderizador(modo: string): string {
  const canonico = modo.trim().toLowerCase().replace(/_indirect$/, '');
  return RENDERIZADOR[canonico] ?? modo;
}

/**
 * O renderizador do emulador em uma linha (29.11). Vale o SELECIONADO pelo emulador quando se sabe; fora do ar só
 * existe o pedido, e a linha diz que é pedido. `fallback` é o caso que derruba aparelho: pediu `host`, o emulador
 * caiu para o SwiftShader sem reclamar, e um app que não roda nele (o Outlook) levaria o emulador junto.
 */
export function renderizadorMeta(r: Instance['renderer']): { label: string; title: string; fallback: boolean } | null {
  if (!r || (!r.configured && !r.gles)) return null;
  const pedido = r.configured ? `pedido (gpu_mode): ${r.configured}` : 'pedido (gpu_mode): não se sabe';
  if (!r.gles) {
    return { label: `renderizador pedido: ${nomeDoRenderizador(r.configured ?? '')}`, fallback: false,
             title: `${pedido}. O emulador só diz o que selecionou quando está no ar.` };
  }
  const selecionado = `selecionado pelo emulador: GLES ${r.gles}${r.vulkan ? `, Vulkan ${r.vulkan}` : ''}`;
  if (r.fallback) {
    return { label: `renderizador: ${nomeDoRenderizador(r.gles)} (pediu ${r.configured ?? '?'})`, fallback: true,
             title: `${pedido}; ${selecionado}. O emulador trocou de renderizador sem avisar.` };
  }
  return { label: `renderizador: ${nomeDoRenderizador(r.gles)}`, fallback: false, title: `${pedido}; ${selecionado}.` };
}

/** Aparelhos agrupados por servidor. `null` é o servidor central — ele também é um servidor. */
export function groupByWorker(instances: readonly Instance[]): Map<string | null, Instance[]> {
  const out = new Map<string | null, Instance[]>();
  for (const i of instances) {
    const k = i.worker_id ?? null;
    const lista = out.get(k);
    if (lista) lista.push(i);
    else out.set(k, [i]);
  }
  return out;
}

/**
 * Aparelhos que apontam para um servidor que não está inscrito. É um estado real e silencioso: o aparelho fica
 * sem ciclo de vida e nada explica por quê, então a tela precisa dizer em voz alta.
 */
export function orphanInstances(instances: readonly Instance[], workerIds: readonly string[]): Instance[] {
  const conhecidos = new Set(workerIds);
  return instances.filter((i) => i.worker_id && !conhecidos.has(i.worker_id));
}

/** A batida ficou velha o bastante para o que está na tela não valer mais? */
export const STALE_HEARTBEAT_MS = 35_000;

export function isStale(ageMs: number | null): boolean {
  return ageMs !== null && ageMs > STALE_HEARTBEAT_MS;
}

/** A regra das vagas mora na fonte única de números (`store/metricas`); fica exportada aqui por compatibilidade. */
export { ocupacaoDoServidor, vagasOcupadas } from '../../store/metricas';

/** Fração 0..1 para a barra; `null` quando falta o total (aí a barra não inventa um valor). */
export function fracaoDeDisco(livreGb?: number | null, totalGb?: number | null): number {
  if (!totalGb || livreGb == null) return 0;
  return Math.min(1, Math.max(0, 1 - livreGb / totalGb));
}

/** Eventos daquele servidor: os dos aparelhos que ele hospeda. O evento não carrega `worker_id`, mas carrega
 *  `instance_id` — e quem hospeda cada instância é justamente o que esta tela sabe. */
export function eventosDoServidor(events: readonly EventRecord[], ids: ReadonlySet<string>,
                                  kinds: readonly string[], limite = 8): EventRecord[] {
  const kset = new Set(kinds);
  return events.filter((e) => kset.has(e.kind) && e.instance_id && ids.has(e.instance_id))
    .slice(-limite).reverse();
}

const RUNS_ABERTAS = new Set(['planning', 'needs_input', 'planned', 'running', 'paused', 'cancelling']);

/** Execuções ainda em voo com pelo menos um objetivo nos aparelhos deste servidor — a "fila" do pedido. */
export function filaDoServidor(runs: readonly RunSummary[], ids: ReadonlySet<string>): RunSummary[] {
  return runs.filter((r) => RUNS_ABERTAS.has(r.status) && r.instance_ids.some((i) => ids.has(i)));
}

/**
 * Selo do servidor central. Ele era `online` fixo — dizia "online" com a saúde degradada e sem o painel
 * conectado (#63). Agora sai da saúde declarada E do estado da conexão do painel, que é o que o usuário vê.
 */
export function centralMeta(health: Health | null, conectado: boolean,
                            worker: { state: string } | null): { label: string; tone: Tone } {
  if (!conectado) return { label: 'sem conexão com o painel', tone: 'danger' };
  if (worker && worker.state === 'maintenance') return { label: 'em manutenção', tone: 'info' };
  if (health === null) return { label: 'sem dado de saúde', tone: 'neutral' };
  if (health.status === 'error') return { label: 'com erro', tone: 'danger' };
  if (health.status === 'degraded') return { label: 'degradado', tone: 'warning' };
  return { label: 'online', tone: 'success' };
}
