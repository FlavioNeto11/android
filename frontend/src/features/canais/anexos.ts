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

/** O id do cartão do Trello: 24 letras de a a f e dígitos. Devolve o id normalizado, ou o erro em português. */
export function validarCartao(texto: string): { ok: true; card: string } | { ok: false; erro: string } {
  const card = texto.trim().toLowerCase();
  if (!card) return { ok: false, erro: 'Informe o id do cartão do Trello.' };
  if (!/^[0-9a-f]{24}$/.test(card)) return { ok: false, erro: 'O id do cartão tem 24 caracteres (letras de a a f e dígitos).' };
  return { ok: true, card };
}
