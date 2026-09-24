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
  if (!trimmed) return [...list];
  const rest = list.filter((c) => c !== trimmed);
  return [trimmed, ...rest].slice(0, max);
}
