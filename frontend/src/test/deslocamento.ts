// 29.150: a leitura do DESLOCAMENTO_DIAS, separada do setup para testar sem deslocar o relógio do teste.
/**
 * O valor de DESLOCAMENTO_DIAS em dias. Ausente ou vazio: 0 (relógio de verdade). Presente e que não é número ("0,4" com
 * vírgula, "40d", "abc"): ERRO, e não zero em silêncio: a rodada passaria sem deslocar nada e pareceria que a varredura
 * das datas fixas deu certo. Decimal é com ponto.
 */
export function lerDeslocamentoEmDias(valor: string | undefined): number {
  if (valor === undefined || valor.trim() === '') return 0;
  const dias = Number(valor.trim());
  if (!Number.isFinite(dias)) {
    throw new Error(`DESLOCAMENTO_DIAS="${valor}" não é um número de dias (negativo atrasa; decimal com ponto, 0.5 e não 0,5).`);
  }
  return dias;
}
