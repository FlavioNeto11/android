import type { CanalAnexo } from '../../api/types';

/** 28.24 F4: o que a tela Anexos sabe dizer de cada anexo, em português e sem tocar a rede (testável à parte). */

export const CANAIS: Record<string, string> = { telegram: 'Telegram', trello: 'Trello' };

export const PERIODOS = [
  { id: 'tudo', rotulo: 'Todo o período', ms: null },
  { id: '24h', rotulo: 'Últimas 24 horas', ms: 24 * 3_600_000 },
  { id: '7d', rotulo: 'Últimos 7 dias', ms: 7 * 24 * 3_600_000 },
  { id: '30d', rotulo: 'Últimos 30 dias', ms: 30 * 24 * 3_600_000 },
] as const;
export type PeriodoId = (typeof PERIODOS)[number]['id'];

/** O limite inferior do período em ISO (a rota guarda `criado_em` em UTC), ou `undefined` para "todo o período". */
export function desdeDoPeriodo(id: PeriodoId, agoraMs: number): string | undefined {
  const ms = PERIODOS.find((p) => p.id === id)?.ms;
  return ms ? new Date(agoraMs - ms).toISOString() : undefined;
}

/** "812 B", "123 KB", "1,5 MB". */
export function tamanhoLegivel(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',').replace(',0', '')} MB`;
}

export type Tipo = 'imagem' | 'pdf' | 'outro';

export function tipoDoAnexo(a: Pick<CanalAnexo, 'mime'>): Tipo {
  if (a.mime?.startsWith('image/')) return 'imagem';
  if (a.mime === 'application/pdf') return 'pdf';
  return 'outro';
}

/** De onde veio, sem nome de ninguém: o produto não guarda o nome do remetente. */
export function origemDoAnexo(a: Pick<CanalAnexo, 'direcao' | 'do_dono'>): string {
  if (a.direcao === 'saida') return 'Enviado pela Central';
  return a.do_dono ? 'Recebido de você' : 'Recebido de convidado';
}

/** Por que não há miniatura nem prévia, quando não há. */
export function semPrevia(a: CanalAnexo): string | null {
  if (a.tem_conteudo) return null;
  if (a.estado === 'recusado') return a.motivo_recusa ?? 'A Central recusou este anexo.';
  if (a.estado === 'apagado') return 'O arquivo venceu a retenção e foi apagado.';
  if (!a.do_dono && a.direcao === 'entrada') return 'Anexo de convidado não é baixado.';
  return 'Este tipo de arquivo fica guardado na Central e não tem prévia.';
}

/**
 * O cartão do Trello que a pessoa colou: o link (`trello.com/c/<código>/...`), o código curto de 8 caracteres ou o id
 * de 24. Devolve o que vai à rota (o código ou o id; o backend resolve o id inteiro), ou o erro em português. A revisão
 * da fila da suíte 31 achou que só o id de 24 passava, e o dono só tem o link.
 */
export function validarCartao(texto: string): { ok: true; card: string } | { ok: false; erro: string } {
  const t = texto.trim();
  if (!t) return { ok: false, erro: 'Cole o link do cartão do Trello.' };
  const link = /^https?:\/\/(?:www\.)?trello\.com\/c\/([A-Za-z0-9]{8})(?:[/?#].*)?$/.exec(t);
  if (link?.[1]) return { ok: true, card: link[1] };
  if (/^[0-9a-fA-F]{24}$/.test(t)) return { ok: true, card: t.toLowerCase() };
  if (/^[A-Za-z0-9]{8}$/.test(t)) return { ok: true, card: t };
  return { ok: false, erro: 'Cole o link do cartão (trello.com/c/...), o código de 8 caracteres dele ou o id de 24.' };
}
