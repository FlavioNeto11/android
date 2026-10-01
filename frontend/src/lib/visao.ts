import { loadJson, saveJson } from './storage';

/**
 * A forma de ver uma lista (cartões, lista, tabela) — decisão D3 da revisão de UX (RF-08). Antes o Painel guardava a
 * visão só no navegador e Personas só no link: um link do Painel não levava a visão, e voltar a Personas pelo menu
 * perdia a escolha. A regra agora é uma só, nas duas telas:
 *
 * - `?visao=` no link MANDA (link colado, Voltar, recarregar);
 * - sem `?visao=` (o menu leva à tela limpa), vale a última visão escolhida neste navegador;
 * - escolher grava nos dois: no link (substituindo a entrada, sem empilhar) e no navegador.
 *
 * O armazenamento é protegido (`lib/storage`): modo privado ou cota cheia caem no padrão da tela.
 */
export function visaoDoLink<V extends string>(daUrl: string | undefined, validas: readonly V[]): V | null {
  return daUrl && (validas as readonly string[]).includes(daUrl) ? (daUrl as V) : null;
}

/** A última visão escolhida neste navegador, ou o padrão da tela. Lida uma vez, ao montar a tela. */
export function visaoPreferida<V extends string>(chave: string, validas: readonly V[], padrao: V): V {
  return loadJson(chave, (v: unknown): v is V => typeof v === 'string' && (validas as readonly string[]).includes(v)) ?? padrao;
}

/** A preferência do navegador: só muda quando a pessoa escolhe (ler um link não a altera). */
export function lembrarVisao(chave: string, visao: string): void {
  saveJson(chave, visao);
}
