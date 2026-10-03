/**
 * Saúde por app, fila "Atenção" e agrupamento por capability (30.15, `docs/design/aprendizado-vivo.md` §11.1).
 *
 * Aqui só se CONTA e se FILTRA o que o backend já mediu: o rótulo de saúde (`saude.rotulo`), o motivo e a capability são
 * dele (`domain/saude.py`, `conteudo.py`). Nada de taxa, tendência ou limiar é recalculado, e a fila é só leitura (D-5:
 * nenhum aviso sai do painel; quem decide é a pessoa, no item).
 *
 * Por que a contagem vem da lista do Livro e não de `/apps`: a visão por app (v0.47) não traz `saude`, e as linhas de
 * `/apps/{pacote}` chegam com `saude: null`. A lista do Livro (v0.52) traz o rótulo de cada item, então se conta ali.
 */
import { textoDoMotivo } from './detalhe';
import type { EntradaDoLivro, RotuloDeSaude } from './model';

/** O que pede a atenção da pessoa, do mais grave ao menos grave (a ordem da fila). */
export const ROTULOS_DE_ATENCAO = ['degradando', 'obsoleto_provavel', 'sem_evidencia'] as const satisfies readonly RotuloDeSaude[];
export type RotuloDeAtencao = (typeof ROTULOS_DE_ATENCAO)[number];

/** A ordem em que as contagens aparecem (a de avaliação do backend, v0.52). */
const ORDEM_DOS_ROTULOS: readonly string[] = [
  'degradando', 'obsoleto_provavel', 'sem_evidencia', 'parado', 'indeterminado', 'em_prova', 'pouca_amostra', 'saudavel', 'inativo',
];

export function pedeAtencao(e: Pick<EntradaDoLivro, 'saude'>): boolean {
  const r = e.saude?.rotulo;
  return !!r && (ROTULOS_DE_ATENCAO as readonly string[]).includes(r);
}

/** O pacote do item, ou `null` (memória e o que não tem eixo de app). */
const appDe = (e: Pick<EntradaDoLivro, 'app'>): string | null => e.app || null;

/** Itens de UM app (o balde `nao_resolvido` é um app como outro: o backend já pôs a chave no item). */
export function doApp<T extends Pick<EntradaDoLivro, 'app'>>(itens: readonly T[], pacote: string): T[] {
  return itens.filter((e) => appDe(e) === pacote);
}

/** Quantos itens há por rótulo de saúde; item sem saúde (memória, backend antigo) fica de fora, nunca vira "saudável". */
export function contarPorRotulo(itens: readonly Pick<EntradaDoLivro, 'saude'>[]): { rotulo: string; n: number }[] {
  const por = new Map<string, number>();
  for (const e of itens) {
    const r = e.saude?.rotulo;
    if (r) por.set(r, (por.get(r) ?? 0) + 1);
  }
  const pos = (r: string) => { const i = ORDEM_DOS_ROTULOS.indexOf(r); return i < 0 ? ORDEM_DOS_ROTULOS.length : i; };
  return [...por.entries()].map(([rotulo, n]) => ({ rotulo, n })).sort((a, b) => pos(a.rotulo) - pos(b.rotulo) || a.rotulo.localeCompare(b.rotulo));
}

/** Os itens que pedem atenção, os mais graves primeiro; dentro do mesmo rótulo, a ordem em que o backend mandou. */
export function filaDeAtencao<T extends EntradaDoLivro>(itens: readonly T[]): T[] {
  const peso = (e: T) => (ROTULOS_DE_ATENCAO as readonly string[]).indexOf(e.saude?.rotulo ?? '');
  return itens.map((e, i) => ({ e, i })).filter(({ e }) => pedeAtencao(e))
    .sort((a, b) => peso(a.e) - peso(b.e) || a.i - b.i).map(({ e }) => e);
}

/** O motivo principal, em português: o primeiro que o backend listou (o que produziu o rótulo). `null` se não veio. */
export function motivoPrincipal(e: Pick<EntradaDoLivro, 'saude'>): string | null {
  const m = e.saude?.motivos[0];
  return m ? textoDoMotivo(m) : null;
}

/** `*` é a etapa livre (sem capability definida), como nas falhas. */
export const ETAPA_LIVRE = '*';

/** O nome do grupo: o nome em português do catálogo (`capability_nome`) quando o backend o manda; senão o código. */
export function rotuloDaCapability(c: string, nome?: string | null): string {
  return c === ETAPA_LIVRE ? 'Etapa livre (fora do catálogo)' : nome || c;
}

/** Primeiro `capability_nome` não vazio da lista (todos do grupo têm a mesma capability e o mesmo app). */
function nomeDoGrupo(itens: readonly object[]): string | null {
  for (const e of itens) {
    const n = (e as { capability_nome?: unknown }).capability_nome;
    if (typeof n === 'string' && n) return n;
  }
  return null;
}

/**
 * O título de uma linha numa lista curta (Atenção): o fluxo tem por título o comando inteiro, de duas linhas ou mais.
 * Corta na última palavra que cabe em `max` e põe reticências; o texto inteiro vai no `title` e no detalhe do item.
 */
export function resumirTitulo(titulo: string, max = 80): string {
  const t = titulo.replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  const corte = t.slice(0, max + 1);
  const espaco = corte.lastIndexOf(' ');
  const base = (espaco > max * 0.6 ? corte.slice(0, espaco) : t.slice(0, max)).replace(/[\s,.;:–—-]+$/, '');
  return `${base}…`;
}

/**
 * Agrupa por capability o que traz o campo `capability` (string). Devolve `null` quando NENHUM item o traz: o backend
 * não manda capability na lista do Livro nem em `/apps/{pacote}` (só `conteudo.capability` no detalhe do item), e
 * inventar o agrupamento por palpite seria pior que não mostrar. Itens sem o campo, numa lista que o tem, vão a `*`.
 */
export function agruparPorCapability<T extends object>(itens: readonly T[]): { capability: string; itens: T[] }[] | null {
  const cap = (e: T): string | null => {
    const c = (e as { capability?: unknown }).capability;
    return typeof c === 'string' && c ? c : null;
  };
  if (!itens.some((e) => cap(e) !== null)) return null;
  const por = new Map<string, T[]>();
  for (const e of itens) {
    const c = cap(e) ?? ETAPA_LIVRE;
    por.set(c, [...(por.get(c) ?? []), e]);
  }
  // A etapa livre por último: é o resto, não um assunto.
  return [...por.entries()].sort(([a], [b]) => Number(a === ETAPA_LIVRE) - Number(b === ETAPA_LIVRE) || a.localeCompare(b))
    .map(([capability, lista]) => ({ capability, itens: lista }));
}

/** Os grupos de falha agrupados pela capability (que o backlog sempre traz; `*` = etapa livre). */
export function falhasPorCapability<T extends { capability: string }>(
  grupos: readonly T[],
): { capability: string; nome: string | null; itens: T[] }[] {
  const por = new Map<string, T[]>();
  for (const g of grupos) {
    const c = g.capability || ETAPA_LIVRE;
    por.set(c, [...(por.get(c) ?? []), g]);
  }
  return [...por.entries()].sort(([a], [b]) => Number(a === ETAPA_LIVRE) - Number(b === ETAPA_LIVRE) || a.localeCompare(b))
    .map(([capability, itens]) => ({ capability, nome: nomeDoGrupo(itens), itens }));
}

/** Um grupo do aprendido de um app: a capability (ou o que faz as vezes dela) e os itens, com quantos pedem atenção. */
export interface GrupoDoAprendido<T> {
  chave: string;
  titulo: string;
  /** `true` quando o título é o CÓDIGO da capability (sem nome no catálogo): vai em fonte mono, como nas falhas. */
  ehCapability: boolean;
  /** O código da capability quando o título é o nome em português (vai no `title`, para quem desenvolve). */
  codigo: string | null;
  itens: T[];
}

const FLUXOS = '§fluxos';
const SEM_CAPABILITY = '§sem';

/**
 * O nível "Capability" da hierarquia Global → App → Capability → Item. Com a capability na linha, um grupo por
 * capability; o fluxo (um comando inteiro, não uma capability) e o item sem capability conhecida vão a grupos próprios,
 * por último. Sem o campo em nenhuma linha (backend anterior), agrupa pelo tipo, para a lista nunca ficar plana.
 */
export function gruposDoAprendido<T extends Pick<EntradaDoLivro, 'kind'>>(
  itens: readonly T[], rotuloDoTipo: (kind: T['kind']) => string,
): GrupoDoAprendido<T>[] {
  const cap = (e: T): string | null => {
    const c = (e as { capability?: unknown }).capability;
    return typeof c === 'string' && c && c !== ETAPA_LIVRE ? c : null;
  };
  const comCapability = itens.some((e) => cap(e) !== null);
  const por = new Map<string, T[]>();
  for (const e of itens) {
    const chave = comCapability ? (cap(e) ?? (e.kind === 'fluxo' ? FLUXOS : SEM_CAPABILITY)) : `tipo:${e.kind}`;
    por.set(chave, [...(por.get(chave) ?? []), e]);
  }
  const peso = (k: string) => (k === FLUXOS ? 1 : k === SEM_CAPABILITY ? 2 : 0);
  return [...por.entries()]
    // Pela ordem do que a pessoa lê: o nome em português quando há, senão o código.
    .sort(([a, la], [b, lb]) => peso(a) - peso(b) || (nomeDoGrupo(la) ?? a).localeCompare(nomeDoGrupo(lb) ?? b, 'pt-BR'))
    .map(([chave, lista]) => {
      const ehCap = comCapability && peso(chave) === 0;
      const nome = ehCap ? nomeDoGrupo(lista) : null;
      return {
        chave,
        titulo: chave === FLUXOS ? 'Fluxos (o comando inteiro)' : chave === SEM_CAPABILITY ? 'Fora do catálogo'
          : chave.startsWith('tipo:') && lista[0] ? rotuloDoTipo(lista[0].kind) : nome ?? chave,
        ehCapability: ehCap && nome === null,
        codigo: ehCap && nome !== null ? chave : null,
        itens: lista,
      };
    });
}
