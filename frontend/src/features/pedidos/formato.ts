/**
 * Formatação de datas, fuso e agenda da tela Pedidos (item 28.9). A API devolve instantes ISO e o fuso do pedido; a pessoa
 * lê "sex, 02/10 às 19:00". O fuso só aparece quando é diferente do navegador, e uma vez só (no título da seção, nunca em
 * cada data).
 */
import type { PedidoView, ProximaData } from '../../api/pedidos';
import type { AppConfig, ResolvedTarget } from '../../api/types';
import { parseTs } from '../../lib/time';
import { appsDoAlvo } from '../command/alvos';
import { handleDe, nomeDe, rotuloDoIdentificador, type Pessoa } from '../profiles/pessoa';

/** O fuso IANA do navegador ("America/Sao_Paulo"); vazio quando o ambiente não informa. */
export function fusoDoNavegador(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? '';
  } catch {
    return '';
  }
}

/** O fuso do pedido, só quando difere do navegador (senão a hora já está no relógio da pessoa); `null` = não mostrar. */
export function fusoParaMostrar(fuso: string | null | undefined): string | null {
  if (!fuso) return null;
  const meu = fusoDoNavegador();
  return meu && meu === fuso ? null : fuso;
}

function formatadorDeDia(fuso?: string): Intl.DateTimeFormat {
  const base: Intl.DateTimeFormatOptions = { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
  try {
    return new Intl.DateTimeFormat('pt-BR', fuso ? { ...base, timeZone: fuso } : base);
  } catch {
    return new Intl.DateTimeFormat('pt-BR', base);   // fuso desconhecido: cai no relógio do navegador em vez de quebrar a tela
  }
}

/** "sex, 02/10 às 19:00", na hora do fuso dado (o do pedido) ou, sem fuso, na do navegador; "—" sem instante válido. */
export function dataCurta(iso: string | null | undefined, fuso?: string): string {
  const t = parseTs(iso);
  if (t === null) return '—';
  const p: Record<string, string> = {};
  for (const parte of formatadorDeDia(fuso).formatToParts(t)) p[parte.type] = parte.value;
  const dia = (p.weekday ?? '').replace('.', '');
  return `${dia ? `${dia}, ` : ''}${p.day}/${p.month} às ${p.hour}:${p.minute}`;
}

/** Versão sem "às", para os cartões densos: "sex 02/10 19:00". */
export function dataCompacta(iso: string | null | undefined, fuso?: string): string {
  return dataCurta(iso, fuso).replace(', ', ' ').replace(' às ', ' ');
}

/** "19:00" de um ISO com ou sem deslocamento ("2026-10-02T19:00:00-03:00"): a hora que está escrita, no fuso do pedido. */
export function horaEscrita(iso: string | null | undefined): string | null {
  const m = /[T ](\d{2}):(\d{2})/.exec(iso ?? '');
  return m ? `${m[1]}:${m[2]}` : null;
}

const COM_HORA_FIXA = /^(Todo |Toda |A cada \d+ (dias|semanas|meses))/;

/**
 * A agenda do gatilho como a pessoa lê: sem o fuso entre parênteses (ele vai uma vez, à parte) e, nas repetições por dia,
 * semana ou mês, com a hora ("Todo dia às 19:00"). O backend só escreve a hora quando a regra a traz explícita; sem ela,
 * `hora` (do `dtstart` ou da próxima data) completa o texto.
 */
export function agendaLegivel(descricao: string, fuso: string, hora?: string | null): string {
  const sufixo = ` (${fuso})`;
  const texto = descricao.endsWith(sufixo) ? descricao.slice(0, -sufixo.length) : descricao;
  if (hora && COM_HORA_FIXA.test(texto) && !texto.includes(' às ')) return `${texto} às ${hora}`;
  return texto;
}

/** Quem faz, como a pessoa lê: as personas do pedido ou, quando o alvo é um aparelho, os aparelhos (`android-01`). */
export function quemFazDoPedido(p: Pick<PedidoView, 'personas' | 'alvos'>): { personas: string[]; aparelhos: string[] } {
  const personas = (p.personas ?? []).map((x) => x.nome);
  const aparelhos = [...new Set((p.alvos?.targets ?? []).map((t) => t.instance_id).filter((x): x is string => !!x))];
  return { personas, aparelhos };
}

/** A persona como a pessoa a conhece: "Bruno Ferreira (@bruno)"; sem a lista (ainda lendo ou persona apagada), o id, como o Comando faz. */
export function nomeDaPersonaNoPedido(id: string, pessoas: readonly Pessoa[] | null | undefined): string {
  const p = pessoas?.find((x) => x.id === id);
  if (!p) return id;
  const nome = nomeDe(p);
  const h = handleDe(p);
  return h && nome !== `@${h}` ? `${nome} (${rotuloDoIdentificador(h)})` : nome;
}

/** Um alvo da prévia em uma linha, sem id interno de persona: "android-03 · Bruno Ferreira (@bruno) · Outlook". */
export function rotuloDoAlvo(
  t: Pick<ResolvedTarget, 'instance_id' | 'profile_id' | 'app_id' | 'app_ids'>,
  pessoas: readonly Pessoa[] | null | undefined, apps: readonly Pick<AppConfig, 'id' | 'name'>[],
): string {
  const partes = [t.instance_id];
  if (t.profile_id) partes.push(nomeDaPersonaNoPedido(t.profile_id, pessoas));
  const nomesDosApps = appsDoAlvo(t, apps);
  if (nomesDosApps.length > 0) partes.push(nomesDosApps.join(', '));
  return partes.join(' · ');
}

/** Os avisos do backend citam a persona pelo id ("… mais de um aparelho (ig-f0zk…)"): na tela, pelo nome. */
export function comNomesDePersonas(texto: string, ids: readonly string[], pessoas: readonly Pessoa[] | null | undefined): string {
  return ids.reduce((t, id) => t.split(id).join(nomeDaPersonaNoPedido(id, pessoas)), texto);
}

/**
 * A próxima data do pedido. `proxima_em` é o que o laço já materializou; sem ele, a primeira das `proximas` (que a API
 * calcula pelos gatilhos, só no detalhe) vale como data PREVISTA, marcada como `calculada`. Sem nenhuma das duas, `null`.
 */
export function proximaDoPedido(
  p: Pick<PedidoView, 'proxima_em' | 'proxima_local'>, proximas?: readonly Pick<ProximaData, 'utc' | 'local'>[] | null,
): { iso: string; calculada: boolean } | null {
  const iso = p.proxima_em ?? p.proxima_local;
  if (iso) return { iso, calculada: false };
  const primeira = proximas?.[0];
  return primeira ? { iso: primeira.utc, calculada: true } : null;
}

/** "sáb, 03/10 às 08:00" de um valor de `<input type="datetime-local">` (`YYYY-MM-DDTHH:MM`, hora do navegador). */
export function dataCurtaDeCampo(campo: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(campo);
  if (!m) return campo || '—';
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
  return dataCurta(d.toISOString());
}

/** "19:00" de um instante, lido no fuso do pedido (para completar "Todo dia" quando só se tem `proxima_em`). */
export function horaNoFuso(iso: string | null | undefined, fuso: string): string | null {
  const t = parseTs(iso);
  if (t === null) return null;
  try {
    const p: Record<string, string> = {};
    for (const parte of new Intl.DateTimeFormat('pt-BR', { timeZone: fuso, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(t)) p[parte.type] = parte.value;
    return `${p.hour}:${p.minute}`;
  } catch {
    return null;
  }
}
