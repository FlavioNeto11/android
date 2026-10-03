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

/**
 * Os ids dos apps de um fluxo: `required_apps` quando o servidor o preencheu; senão o `app_id` do fluxo (o fluxo
 * aprendido de plano antigo, ou de um app só, chega com `required_apps` vazio; deploy 8 da UX: sem isto a linha
 * saía sem o selo do app).
 */
export function appsDoFluxo(flow: { app_id?: string | null; required_apps?: readonly string[] | null }): string[] {
  if (Array.isArray(flow.required_apps) && flow.required_apps.length > 0) return [...flow.required_apps];
  return flow.app_id ? [flow.app_id] : [];
}
