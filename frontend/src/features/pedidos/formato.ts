/**
 * Formatação de datas, fuso e agenda da tela Pedidos (item 28.9). A API devolve instantes ISO e o fuso do pedido; a pessoa
 * lê "sex, 02/10 às 19:00". O fuso só aparece quando é diferente do navegador, e uma vez só (no título da seção, nunca em
 * cada data).
 */
import { parseTs } from '../../lib/time';

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
  const m = /T(\d{2}):(\d{2})/.exec(iso ?? '');
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
