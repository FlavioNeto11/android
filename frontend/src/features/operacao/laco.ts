/**
 * 31.225: o laço do sistema que avança as operações abertas (adendo v1.119, 31.220). O valor mora em `Settings.operacao_laco_s`
 * (segundos; 0 = desligado, o padrão do corte 61). O painel só diz o estado: ligar e desligar é em Configurações.
 */
export interface EstadoDoLaco { ligado: boolean; cadaS: number }

/** `null` quando o central não manda o campo (anterior ao 31.220): a tela não afirma ligado nem desligado, nada de zero inventado. */
export function estadoDoLaco(valor: unknown): EstadoDoLaco | null {
  if (typeof valor !== 'number' || !Number.isFinite(valor) || valor < 0) return null;
  const cadaS = Math.trunc(valor);
  return { ligado: cadaS > 0, cadaS };
}

export function lacoEmPalavras(e: EstadoDoLaco): string {
  return e.ligado ? `ligado, a cada ${e.cadaS} s` : 'desligado';
}

/** Explica o que muda para quem olha a lista: desligado, a operação só avança enquanto alguém abre a tela dela. */
export function lacoExplica(e: EstadoDoLaco): string {
  return e.ligado
    ? 'O sistema lê as operações abertas e as faz andar sozinho, mesmo sem ninguém olhando.'
    : 'A operação só avança quando alguém abre a tela dela (ou outro laço do central a lê); ligue em Configurações › Orquestração de operações.';
}
