/**
 * 31.208: a fila do aparelho e a previsão de início de cada alvo pendente (adendo v1.114, Jev 31.206) e o corte pelo teto da operação
 * (Jev 31.205). O leitor é tolerante e vive aqui: se a Jev mudar um nome, muda neste arquivo. A previsão é uma ESTIMATIVA (fila do
 * aparelho × duração mediana dos alvos já terminados) e sem amostra vem `null`: a tela diz "sem amostra", nunca um horário inventado.
 */
import { formatQuando } from '../../lib/time';

export interface FilaDoAlvo {
  /** Contada a partir de 1 (1 = é o próximo do aparelho); `null` = o central não disse. */
  posicao: number | null;
  /** Quantos trabalhos abertos, de qualquer operação, vêm antes dele no mesmo aparelho. */
  a_frente: number | null;
  /** O instante previsto (ISO UTC); `null` sem amostra. */
  previsao_inicio_em: string | null;
  /** A duração mediana dos alvos já terminados da operação, usada na conta; `null` sem amostra. */
  base_ms: number | null;
}

/** O motivo (frase estável do backend) do alvo cortado pelo teto de gasto da operação, em `alvos[].motivo` e em `capacidade.motivos`. */
export const MOTIVO_DO_TETO = 'teto da operação';

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const natural = (v: unknown, minimo: number): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= minimo ? v : null);

/** `null` quando o alvo não está pendente (já começou, terminou ou parou) ou o formato não é o do contrato: nunca uma fila inventada. */
export function lerFila(v: unknown): FilaDoAlvo | null {
  const o = registro(v);
  if (!o) return null;
  const posicao = natural(o.posicao, 1);
  const aFrente = natural(o.a_frente, 0);
  if (posicao === null && aFrente === null) return null;
  const previsao = typeof o.previsao_inicio_em === 'string' && Number.isFinite(Date.parse(o.previsao_inicio_em)) ? o.previsao_inicio_em : null;
  return { posicao, a_frente: aFrente, previsao_inicio_em: previsao, base_ms: natural(o.base_ms, 0) };
}

export interface FilaEmPalavras {
  /** "Próximo do aparelho" ou "3º na fila do aparelho". */
  posicao: string;
  /** "2 trabalhos à frente", ou `null` quando o central não disse. */
  aFrente: string | null;
  /** "início previsto hoje, 20:47" ou "início sem previsão: ainda sem amostra". */
  previsao: string;
}

export function filaEmPalavras(f: FilaDoAlvo, agoraMs: number = Date.now()): FilaEmPalavras {
  return {
    posicao: f.posicao === null ? 'Posição na fila não informada' : f.posicao === 1 ? 'Próximo do aparelho' : `${f.posicao}º na fila do aparelho`,
    aFrente: f.a_frente === null ? null : f.a_frente === 0 ? 'ninguém à frente' : `${f.a_frente} ${f.a_frente === 1 ? 'trabalho à frente' : 'trabalhos à frente'}`,
    previsao: f.previsao_inicio_em === null ? 'início sem previsão: ainda sem amostra' : `início previsto ${formatQuando(f.previsao_inicio_em, agoraMs)}`,
  };
}
