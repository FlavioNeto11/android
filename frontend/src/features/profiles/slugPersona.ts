/**
 * Nome legível na URL da persona (`#/personas/tadeu-quintela`, em vez de `#/personas/ig-Ex4mpl0Pers0na12`).
 *
 * Tudo sai do que a tela já carregou (`GET /personas`): não há endpoint de slug, e o id continua sendo a chave real
 * (cada link antigo com id segue abrindo a mesma pessoa).
 *
 * Regras:
 * - o slug é o nome sem acento, em minúsculas, com `-` entre as palavras; nome que não rende letra nenhuma vira `persona`;
 * - homônimos (mesmo slug-base) ganham um sufixo curto tirado do PRÓPRIO id (`tadeu-quintela-fqg8`): é determinístico e
 *   não depende de ordem nem de quem chegou primeiro, então acrescentar um terceiro homônimo não muda o link dos outros
 *   dois (só cresce o sufixo se dois ids terminarem igual);
 * - o link com sufixo continua valendo mesmo depois que o homônimo some (`resolverPersona` aceita a forma sufixada);
 * - o id vence o slug: um segmento que é id de alguém abre essa pessoa.
 */
import { nomeDe, type Pessoa } from './pessoa';

const TAMANHO_MAXIMO = 48;
const SUFIXO_MINIMO = 4;

/** Letras que a decomposição Unicode não separa em "letra + acento". */
const SEM_DECOMPOSICAO: Record<string, string> = { ß: 'ss', æ: 'ae', œ: 'oe', ø: 'o', đ: 'd', ł: 'l', ð: 'd', þ: 'th' };

/** Texto livre → `a-z0-9` separado por `-`. Sem letra nem dígito aproveitável: cadeia vazia. */
export function slugify(texto: string): string {
  const semAcento = texto.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[ßæœøđłðþ]/g, (c) => SEM_DECOMPOSICAO[c] ?? c);
  const slug = semAcento.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  if (slug.length <= TAMANHO_MAXIMO) return slug;
  // Corta numa fronteira de palavra quando dá, para não terminar no meio de um sobrenome.
  const corte = slug.slice(0, TAMANHO_MAXIMO);
  const ultimoTraco = corte.lastIndexOf('-');
  return (ultimoTraco >= 12 ? corte.slice(0, ultimoTraco) : corte).replace(/-+$/, '');
}

/** O slug-base de uma pessoa, sem tratar colisão. */
export function slugBase(p: Pessoa): string {
  return slugify(nomeDe(p)) || 'persona';
}

/** O id em `a-z0-9` minúsculo (`ig-Ex4mpl0Pers0na12` → `igex4mpl0pers0na12`), de onde sai o sufixo. */
function idLimpo(id: string): string {
  return id.toLowerCase().replace(/[^a-z0-9]/g, '');
}

function sufixoDe(id: string, tamanho: number): string {
  const limpo = idLimpo(id);
  return limpo.slice(Math.max(0, limpo.length - tamanho));
}

/**
 * O slug de cada pessoa da lista, por id. Uma pessoa sozinha com o nome fica com o slug limpo; homônimos recebem
 * todos o sufixo (nenhum "ganha" o limpo, para o link não mudar quando alguém entra ou sai da lista de homônimos).
 */
export function slugsDasPersonas(pessoas: readonly Pessoa[]): Map<string, string> {
  const grupos = new Map<string, Pessoa[]>();
  for (const p of pessoas) {
    const base = slugBase(p);
    const grupo = grupos.get(base);
    if (grupo) grupo.push(p);
    else grupos.set(base, [p]);
  }
  const ids = new Set(pessoas.map((p) => p.id));
  const resultado = new Map<string, string>();
  const usados = new Set<string>();
  for (const [base, grupo] of grupos) {
    let candidatos: [Pessoa, string][];
    if (grupo.length === 1) {
      candidatos = [[grupo[0]!, base]];
    } else {
      // O menor sufixo (a partir de 4 caracteres) que distingue todos os homônimos entre si.
      const maior = Math.max(...grupo.map((p) => idLimpo(p.id).length));
      let tamanho = SUFIXO_MINIMO;
      while (tamanho < maior && new Set(grupo.map((p) => sufixoDe(p.id, tamanho))).size < grupo.length) tamanho += 1;
      candidatos = grupo.map((p) => [p, `${base}-${sufixoDe(p.id, tamanho)}`]);
    }
    for (const [p, slug] of candidatos) {
      // Último recurso: o slug coincide com o id de outra pessoa ou com o de um homônimo de sufixo igual → o próprio id.
      const ocupado = usados.has(slug) || (ids.has(slug) && slug !== p.id);
      const final = ocupado ? p.id : slug;
      usados.add(final);
      resultado.set(p.id, final);
    }
  }
  return resultado;
}

/** O slug de UMA pessoa dentro da lista (para quem só tem a pessoa e a lista na mão). */
export function slugDaPersona(p: Pessoa, todas: readonly Pessoa[]): string {
  return slugsDasPersonas(todas).get(p.id) ?? p.id;
}

/**
 * Quem o segmento do link nomeia: o id (links antigos), o slug de hoje, ou a forma com sufixo mesmo que o homônimo
 * já tenha saído (link compartilhado quando havia dois "Tadeu Quintela"). `null` quando ninguém bate — ou quando o
 * nome-base é de vários e o link não diz qual.
 */
export function resolverPersona<T extends Pessoa>(segmento: string | null | undefined, pessoas: readonly T[]): T | null {
  if (!segmento) return null;
  const porId = pessoas.find((p) => p.id === segmento);
  if (porId) return porId;
  const quer = segmento.toLowerCase();
  const slugs = slugsDasPersonas(pessoas);
  const porSlug = pessoas.find((p) => slugs.get(p.id) === quer);
  if (porSlug) return porSlug;
  const achadas = pessoas.filter((p) => {
    const base = slugBase(p);
    if (!quer.startsWith(`${base}-`)) return false;
    const resto = quer.slice(base.length + 1);
    return resto.length >= SUFIXO_MINIMO && idLimpo(p.id).endsWith(resto);
  });
  return achadas.length === 1 ? achadas[0]! : null;
}

/** As pessoas que o link nomeia só pelo nome-base (`tadeu-quintela`): mais de uma = link ambíguo, a tela pede para escolher. */
export function homonimosDoSegmento<T extends Pessoa>(segmento: string | null | undefined, pessoas: readonly T[]): T[] {
  if (!segmento) return [];
  const quer = segmento.toLowerCase();
  return pessoas.filter((p) => slugBase(p) === quer);
}
