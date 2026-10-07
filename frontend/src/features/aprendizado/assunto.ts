/**
 * 31.209: o assunto das lições do Livro (adendo v1.113, Aprendizado 31.200). O servidor guarda UM assunto por item, já na forma
 * canônica (minúsculas, sem acento, sem pontuação, espaço simples, até 120), e `null` no que não tem (receita, fluxo, habilidade,
 * memória e a lição sem assunto). `?assunto=` filtra por IGUALDADE exata depois de canonizar do lado dele (até 200 caracteres; mais
 * é 422). Não há rota que liste os assuntos nem busca por trecho: a lista de assuntos se monta aqui, das lições, e a busca é do painel.
 */
import type { EntradaDoLivro } from './model';
import { tituloDoItem } from './model';

/** O limite que o servidor aceita no `?assunto=` (acima disso, 422): o painel nem o manda. */
export const ASSUNTO_MAX = 200;

/** Sem acento, em minúsculas, espaço simples: a comparação da busca não depende de como a pessoa digitou. */
export function semAcento(t: string): string {
  return t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
}

/** A mesma forma canônica do servidor (sem pontuação), para comparar o filtro digitado ou vindo de um link com o `assunto` do item. */
export function canonizarAssunto(t: string): string {
  return semAcento(t.replace(/[^\p{L}\p{N}\s]/gu, ' ')).slice(0, 120).trim();
}

/** O assunto do item: texto não vazio ou `null` (o campo ausente, de um backend anterior, também é `null`). */
export function assuntoDoItem(e: Pick<EntradaDoLivro, 'assunto'>): string | null {
  return typeof e.assunto === 'string' && e.assunto.trim() ? e.assunto.trim() : null;
}

export interface OpcaoDeAssunto { assunto: string; n: number }

/** Os assuntos distintos das lições, com quantas lições cada um tem: o mais comum primeiro, e o empate em ordem alfabética. */
export function assuntosDasLicoes(itens: readonly EntradaDoLivro[]): OpcaoDeAssunto[] {
  const conta = new Map<string, number>();
  for (const e of itens) {
    const a = assuntoDoItem(e);
    if (a) conta.set(a, (conta.get(a) ?? 0) + 1);
  }
  return [...conta].map(([assunto, n]) => ({ assunto, n })).sort((x, y) => y.n - x.n || x.assunto.localeCompare(y.assunto, 'pt-BR'));
}

/**
 * A guarda para o backend que ainda ignora `?assunto=`: com o filtro ligado, só fica o item cujo assunto é o pedido. O servidor que
 * filtra devolve o mesmo conjunto; o que ele não filtrou não vaza para a tela.
 */
export function filtrarPorAssunto(itens: readonly EntradaDoLivro[], assunto: string | undefined): EntradaDoLivro[] {
  if (!assunto) return [...itens];
  const alvo = canonizarAssunto(assunto);
  return itens.filter((e) => canonizarAssunto(assuntoDoItem(e) ?? '') === alvo && alvo !== '');
}

/**
 * A busca por trecho (do painel): todas as palavras digitadas precisam aparecer no título, no assunto, na etapa, na capability ou no
 * nome do app do item, sem diferenciar caixa nem acento. Busca vazia = tudo.
 */
export function buscarNoLivro(itens: readonly EntradaDoLivro[], termo: string, titulos?: ReadonlyMap<EntradaDoLivro, string>): EntradaDoLivro[] {
  const palavras = semAcento(termo).split(' ').filter(Boolean);
  if (palavras.length === 0) return [...itens];
  return itens.filter((e) => {
    const palheiro = semAcento([titulos?.get(e) ?? tituloDoItem(e), e.title, assuntoDoItem(e), e.etapa, e.capability_nome, e.app_nome].filter(Boolean).join(' '));
    return palavras.every((p) => palheiro.includes(p));
  });
}
