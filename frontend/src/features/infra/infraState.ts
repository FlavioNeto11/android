import type { EventRecord, Health, Instance, RunSummary } from '../../api/types';
import type { Tone } from '../../lib/status';
import type { EstadoContado } from '../../store/metricas';
import { formatQuando, parseTs } from '../../lib/time';

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
 * O renderizador do emulador em uma linha (29.11). Vale o EFETIVO (o que o emulador selecionou) quando se sabe; fora do
 * ar só existe o configurado (o pedido, `gpu_mode`), e a linha diz que é o configurado, com o porquê na dica. A palavra
 * "pedido" sozinha não dizia pedido de quem nem por que não havia outro (polimento dos deploys 9 a 11). `fallback` é o
 * caso que derruba aparelho: configurou `host`, o emulador caiu para o SwiftShader sem reclamar, e um app que não roda
 * nele (o Outlook) levaria o emulador junto.
 */
export function renderizadorMeta(r: Instance['renderer']): { label: string; title: string; fallback: boolean } | null {
  if (!r || (!r.configured && !r.gles)) return null;
  const configurado = r.configured
    ? `Configurado (o pedido ao emulador, gpu_mode): ${r.configured}`
    : 'Configurado (gpu_mode): não se sabe';
  if (!r.gles) {
    return { label: `renderizador configurado: ${nomeDoRenderizador(r.configured ?? '')}`, fallback: false,
             title: `${configurado}. O efetivo, o que o emulador selecionou, só se sabe com o aparelho no ar.` };
  }
  const efetivo = `efetivo (selecionado pelo emulador): GLES ${r.gles}${r.vulkan ? `, Vulkan ${r.vulkan}` : ''}`;
  if (r.fallback) {
    return { label: `renderizador: ${nomeDoRenderizador(r.gles)} (configurado: ${r.configured ?? '?'})`, fallback: true,
             title: `${configurado}; ${efetivo}. Se o gpu_mode mudou depois da subida, vale no próximo reinício; `
               + 'se não, o emulador trocou de renderizador sem avisar.' };
  }
  return { label: `renderizador: ${nomeDoRenderizador(r.gles)}`, fallback: false, title: `${configurado}; ${efetivo}.` };
}

/** O tipo do aparelho na linha, em português e com o porquê: o `kind` cru ("store") saía como selo (polimento dos
 *  deploys 9 a 11). O emulador do projeto não ganha selo; tipo que este painel não conhece sai como veio. */
const TIPO: Record<string, { label: string; title: string }> = {
  store: { label: 'loja', title: 'Aparelho-loja: liga, desliga e abre a Play Store, mas nunca recebe tarefa.' },
  external: { label: 'externo', title: 'Aparelho de outra máquina: os verbos são os que ela expõe.' },
};

export function tipoDoAparelho(kind: Instance['kind']): { label: string; title: string } | null {
  if (kind === 'emulator') return null;
  return TIPO[kind] ?? { label: kind, title: kind };
}

/**
 * A pausa do reparo automático (25.13) numa linha discreta do aparelho. Ela só existia na API: o dono não sabia pelo
 * painel por que a escada não agia no aparelho nem até quando (polimento dos deploys 10 e 11). O `reason` é texto do
 * procedimento, então vai na dica, não na linha. Vencida (o `until` já passou) não aparece.
 */
export function pausaDoReparoMeta(p: Instance['repair_pause'], agoraMs: number): { label: string; title: string } | null {
  const ate = parseTs(p?.until);
  if (!p || ate === null || ate <= agoraMs) return null;
  const quando = formatQuando(p.until, agoraMs);
  const porQuem = p.by === 'panel' ? 'pelo painel' : `por ${p.by}`;
  return {
    label: `reparo pausado até ${quando.startsWith('hoje, ') ? quando.slice('hoje, '.length) : quando}`,
    title: `Pausado ${porQuem} (${formatQuando(p.since, agoraMs)}). Enquanto durar, o reparo automático não age neste `
      + `aparelho. Motivo registrado: ${p.reason}`,
  };
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
