/**
 * Os apps que um fluxo exige, em texto: "QA Messenger → Chrome" (29.42). A ordem é a do plano (o backend a entrega em
 * `required_apps`); aqui só se troca o id pelo nome cadastrado. App fora da lista (apagado, lista ainda carregando)
 * fica pelo id, nunca some. Com um app, só o nome; sem app, texto vazio. Módulo puro, testável em node.
 */
export const SEPARADOR_DE_APPS = ' → ';

interface AppComNome { id: string; name: string }

export function textoDosApps(ids: readonly string[] | null | undefined, apps: readonly AppComNome[] | ReadonlyMap<string, string>): string {
  if (!Array.isArray(ids) || ids.length === 0) return '';
  const nomes = apps instanceof Map ? apps : new Map((apps as readonly AppComNome[]).map((a) => [a.id, a.name] as const));
  return ids.map((id) => nomes.get(id)?.trim() || id).join(SEPARADOR_DE_APPS);
}
