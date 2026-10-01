/**
 * Como o total de pendências aparece quando uma origem não carregou (B8, rodada 2). O total é a soma das origens
 * lidas; se uma leitura falhou, o número mostrado é um PISO, não o valor. Falha não pode parecer número: o piso leva
 * um "+" ("4+") e, sem nada contado, um "?". Nenhum número é inventado, e o texto de leitor de tela diz o motivo.
 */
import { formatInt } from '../../lib/format';
import type { OrigemDaPendencia } from './modelo';
import type { FalhasDeLeitura } from './store';

export const AVISO_ORIGEM_NAO_CARREGOU = 'Alguma origem não carregou: o número pode ser maior.';

/** O número como se vê: "4", "4+" (piso, uma origem falhou) ou "?" (nada contado e uma origem falhou). */
export function numeroExibido(total: number, incompleto: boolean, teto?: number): string {
  const n = teto !== undefined && total > teto ? `${teto}+` : formatInt(total);
  if (!incompleto) return n;
  return total > 0 ? (n.endsWith('+') ? n : `${n}+`) : '?';
}

/**
 * A frase que acompanha o número ("4 aguardando você"), para nomes acessíveis. Vazia quando não há o que dizer (zero e
 * leitura completa). `legenda` é o que o número conta ("aguardando você", "para aprovar").
 */
export function falaDoTotal(total: number, incompleto: boolean, legenda: string): string {
  if (!incompleto) return total > 0 ? `${formatInt(total)} ${legenda}` : '';
  return total > 0
    ? `${formatInt(total)} ou mais ${legenda}; alguma origem não carregou`
    : 'não foi possível contar; alguma origem não carregou';
}

/** A leitura que alimenta cada origem da caixa (as execuções vêm do snapshot ao vivo e não falham por leitura). */
export function origemFalhou(origem: OrigemDaPendencia, falhas: FalhasDeLeitura): boolean {
  switch (origem) {
    case 'aprendizado': return falhas.aprendizado;
    case 'persona': return falhas.aprovacoes;
    case 'intervencao': return falhas.personas;
    default: return false;
  }
}
