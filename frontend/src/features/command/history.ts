/**
 * Histórico dos últimos comandos usados (item 11.5): digitar o mesmo comando de novo, com pequenas variações,
 * era o trabalho repetitivo que sobrava depois de "Repetir" (que reusa a MESMA execução). Guarda só o texto —
 * quem usa escolhe de novo as instâncias e revisa antes de enviar.
 */
export const MAX_COMMAND_HISTORY = 8;

/**
 * Põe `command` no topo (tira duplicata existente) e corta em `max`. Puro: quem chama decide onde persistir.
 * Comando vazio (ou só espaços) nunca entra — não há o que reaproveitar ali.
 */
export function pushHistory(list: readonly string[], command: string, max: number = MAX_COMMAND_HISTORY): string[] {
  const trimmed = command.trim();
  if (!trimmed || pareceCredencial(trimmed)) return [...list];
  const rest = list.filter((c) => c !== trimmed);
  return [trimmed, ...rest].slice(0, max);
}

/**
 * Formato de credencial no texto (ADR-025), o mesmo critério da recusa do backend (`credencial_no_comando`): par
 * chave/valor com senha, password, token… e `usuário:senha@` numa URL. Texto assim não fica no navegador — nem no
 * rascunho, nem no histórico — e o painel nem envia: a senha mora na conta da persona, com consentimento (ADR-040).
 */
const CREDENCIAL =
  /[\w.-]*(?:password|passwd|senha|\bpin|secret|segredo|token|api[_-]?key|credential|credencial)\b["']?\s*[:=]\s*["']?(?![,}\s])[^"\s,}]+|https?:\/\/[^\s:/?#@]+:[^\s@/]+@/i;

export function pareceCredencial(texto: string): boolean {
  return CREDENCIAL.test(texto);
}

/** O histórico sem entradas com formato de credencial — inclusive as gravadas antes desta regra existir. */
export function historicoSeguro(list: readonly string[]): string[] {
  return list.filter((c) => !pareceCredencial(c));
}
