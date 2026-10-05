/**
 * Catraca das esperas (item 29.104): `waitFor` só trata `false` e exceção como "ainda não". Um `querySelector` que
 * ainda não acha nada devolve `null`, e `waitFor(() => raiz.querySelector('…'))` passava NA HORA, sem esperar: o teste
 * corria contra a tela de antes e só falhava quando o fetch falso demorava (o cron do CI, no runner carregado). Desde
 * o W1 o `waitFor` também trata `null` como "ainda não"; a catraca fica como segunda guarda e como regra de estilo:
 * para esperar um elemento, use `esperarElemento(seletor, raiz)` do harness, que diz na falha o seletor e onde.
 *
 * `undefined`, `0` e `''` continuam passando na hora no `waitFor`: `undefined` é o retorno de `() => expect(…)`. Por isso
 * a catraca também aponta o `find(…)` no fim do corpo. O `?.textContent` e o `.get(…)` no fim ficam fora dela: compare
 * (`=== 'x'`, `!== undefined`) ou use `includes` (29.119).
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { esperarElemento, waitFor } from './harness';

const ASPAS = new Set(["'", '"', '`']);

/** Índice da aspa que fecha a string aberta em `i` (o escape `\x` é pulado). */
function fimDaString(codigo: string, i: number): number {
  const aspa = codigo.charAt(i);
  let j = i + 1;
  for (; j < codigo.length && codigo.charAt(j) !== aspa; j++) if (codigo.charAt(j) === '\\') j++;
  return j;
}

/**
 * O código sem comentários, com cada caractere na mesma posição: o comentário vira espaço e a quebra de linha fica, e
 * as linhas dos achados continuam certas. As strings são copiadas como estão: o `//` de `'https://…'` não é comentário
 * (29.119, K2). Limite conhecido (K3): um regex literal não é lido como tal. O `\/` escapado dentro dele é pulado e não
 * abre comentário, mas uma aspa sem escape dentro de um regex abriria uma string.
 */
function semComentarios(codigo: string): string {
  let saida = '';
  for (let i = 0; i < codigo.length; i++) {
    const c = codigo.charAt(i);
    if (c === '\\') {
      saida += codigo.slice(i, i + 2);
      i++;
    } else if (c === '/' && codigo.charAt(i + 1) === '/') {
      let fim = codigo.indexOf('\n', i);
      if (fim < 0) fim = codigo.length;
      saida += ' '.repeat(fim - i);
      i = fim - 1;
    } else if (c === '/' && codigo.charAt(i + 1) === '*') {
      const fechamento = codigo.indexOf('*/', i + 2);
      const fim = fechamento < 0 ? codigo.length : fechamento + 2;
      saida += codigo.slice(i, fim).replace(/[^\n]/g, ' ');
      i = fim - 1;
    } else if (ASPAS.has(c)) {
      const fim = fimDaString(codigo, i);
      saida += codigo.slice(i, fim + 1);
      i = fim;
    } else {
      saida += c;
    }
  }
  return saida;
}

/** O 1º argumento de cada `waitFor(`, lido até a vírgula ou o `)` do mesmo nível, pulando o que está entre aspas. */
function primeirosArgumentos(codigo: string): { indice: number; argumento: string }[] {
  const achados: { indice: number; argumento: string }[] = [];
  for (const m of codigo.matchAll(/\bwaitFor\(/g)) {
    const inicio = m.index + m[0].length;
    let nivel = 0;
    let i = inicio;
    for (; i < codigo.length; i++) {
      const c = codigo.charAt(i);
      if (c === '\\') i++;                                   // `\(` de um regex não abre nível
      else if (ASPAS.has(c)) i = fimDaString(codigo, i);
      else if ('([{'.includes(c)) nivel++;
      else if (')]}'.includes(c)) {
        if (nivel === 0) break;
        nivel--;
      } else if (c === ',' && nivel === 0) break;
    }
    achados.push({ indice: m.index, argumento: codigo.slice(inicio, i).trim() });
  }
  return achados;
}

/**
 * Há `&&`, `||`, `??` ou um `?` de ternário no nível de fora? Então o valor final da expressão não é o da negação da
 * frente: `!carregando && c.querySelector('li img')` devolve o elemento, que pode ser `null` (29.119, K1).
 */
function temOperadorNoTopo(corpo: string): boolean {
  let nivel = 0;
  for (let i = 0; i < corpo.length; i++) {
    const c = corpo.charAt(i);
    const dois = corpo.slice(i, i + 2);
    if (c === '\\') i++;
    else if (ASPAS.has(c)) i = fimDaString(corpo, i);
    else if ('([{'.includes(c)) nivel++;
    else if (')]}'.includes(c)) nivel--;
    else if (nivel === 0 && (dois === '&&' || dois === '||' || dois === '??')) return true;
    else if (nivel === 0 && c === '?' && corpo.charAt(i + 1) !== '.') return true;
  }
  return false;
}

// O valor devolvido é o de uma busca que pode não achar nada: `querySelector`/`closest` (null) e `find` (undefined,
// que o `waitFor` não trata como "ainda não"). Os parênteses da chamada são lidos balanceados: o seletor com `:not(…)`
// ou `:has(…)` não escapa (29.119, N1). Um `as Tipo` no fim é ignorado.
const BUSCA_QUE_PODE_FALHAR = /(?:querySelector|closest|\.find)$/;

function terminaNumaBusca(corpo: string): boolean {
  const semTipo = corpo.replace(/\s+as\s+[\w.<>|[\]\s]+$/, '').trimEnd();
  if (!semTipo.endsWith(')')) return false;
  // Onde abre o `(` que fecha no fim: a pilha de aberturas, pulando strings.
  const pilha: number[] = [];
  let abre = -1;
  for (let i = 0; i < semTipo.length; i++) {
    const c = semTipo.charAt(i);
    if (c === '\\') i++;
    else if (ASPAS.has(c)) i = fimDaString(semTipo, i);
    else if (c === '(') pilha.push(i);
    else if (c === ')') abre = pilha.pop() ?? -1;
  }
  return abre > 0 && BUSCA_QUE_PODE_FALHAR.test(semTipo.slice(0, abre));
}

function esperasQuePassamSemAchar(codigo: string): number[] {
  return primeirosArgumentos(semComentarios(codigo))
    .filter(({ argumento }) => {
      const corpo = /^\(\)\s*=>\s*([\s\S]*)$/.exec(argumento)?.[1]?.trim();
      if (corpo == null || corpo.startsWith('{')) return false;
      // `!x.querySelector(…)` e `!!x.querySelector(…)` já devolvem booleano, desde que nada no nível de fora mude o valor.
      if (corpo.startsWith('!') && !temOperadorNoTopo(corpo)) return false;
      return terminaNumaBusca(corpo);
    })
    .map(({ indice }) => indice);
}

function arquivosDeTeste(pasta: string): string[] {
  return readdirSync(pasta, { withFileTypes: true }).flatMap((e) => {
    const caminho = join(pasta, e.name);
    if (e.isDirectory()) return arquivosDeTeste(caminho);
    return /\.test\.tsx?$/.test(e.name) ? [caminho] : [];
  });
}

describe('catraca das esperas', () => {
  it('o padrão pega o elemento que pode ser null e poupa a comparação, que já devolve false', () => {
    const pega = [
      "await waitFor(() => container.querySelector('li img'));",
      "await waitFor(() => li3?.querySelector('img'));",
      "await waitFor(() => consulta()?.get('direcao') === 'entrada' && container.querySelector('li img'));",
      "const chips = await waitFor(() => document.querySelector('[aria-label=\"Personas\"]') as HTMLElement);",
      "await waitFor(\n  () => container.querySelector('li img'),\n  8000,\n);",
      "await waitFor(() => container.querySelector('li img') // o item que don't aparece\n);",
      "await waitFor(() => /* a lista */ container.querySelector('li img'));",
      // 29.119, K1: a negação da frente não decide o valor quando há `&&` no nível de fora.
      "await waitFor(() => !carregando && container.querySelector('li img'));",
      // K2: o `//` dentro da string não é comentário e não esconde o resto da linha.
      "await waitFor(() => container.querySelector('a[href^=\"https://x\"]'));",
      // K3: o `\\/` escapado de um regex literal não abre comentário.
      "await waitFor(() => /a\\/\\//.test(x) && container.querySelector('li'));",
      // N1: o seletor com parênteses e as outras buscas que podem não achar nada.
      "await waitFor(() => container.querySelector('li:not(.velho) img'));",
      "await waitFor(() => itens().find((i) => i.id === 'x'));",
      "await waitFor(() => botao.closest('li'));",
    ];
    const poupa = [
      "await waitFor(() => container.querySelector('h1')?.textContent === 'Mariana Costa');",
      "await waitFor(() => document.querySelector('table') !== null);",
      "await waitFor(() => document.querySelector('table') === null);",
      "await waitFor(() => expect(container.querySelector('li img')).not.toBeNull());",
      "await waitFor(() => { expect(container.querySelector('li img')).not.toBeNull(); });",
      "await waitFor(() => text().includes('Mostrando 1 de 1'));",
      "await waitFor(() => !container.querySelector('[aria-busy=\"true\"]'));",
      "await waitFor(() => !!container.querySelector('li img'));",
      "await waitFor(() => !(carregando && container.querySelector('li img')));",
      "await waitFor(() => itens().find((i) => i.id === 'x') !== undefined);",
    ];
    for (const linha of pega) expect(esperasQuePassamSemAchar(linha), linha).toHaveLength(1);
    for (const linha of poupa) expect(esperasQuePassamSemAchar(linha), linha).toEqual([]);
  });

  it('nenhum teste espera um elemento pelo waitFor puro (use esperarElemento)', () => {
    const src = resolve(__dirname, '..');
    const achados = arquivosDeTeste(src).filter((arquivo) => resolve(arquivo) !== resolve(__filename)).flatMap((arquivo) => {
      const codigo = readFileSync(arquivo, 'utf8');
      return esperasQuePassamSemAchar(codigo).map((indice) => {
        const linha = codigo.slice(0, indice).split('\n').length;
        return `${relative(src, arquivo).replace(/\\/g, '/')}:${linha}`;
      });
    });
    expect(achados).toEqual([]);
  });
});

describe('esperarElemento', () => {
  it('raiz que veio undefined é erro, não a busca na tela toda', async () => {
    await expect(esperarElemento('img', undefined as unknown as ParentNode)).rejects.toThrow(
      'esperarElemento: a raiz da busca por "img" veio undefined',
    );
  });

  it('a falha diz o seletor, a raiz e o prazo; null é "ainda não" também no waitFor', async () => {
    const vazia = { querySelector: () => null, toString: () => '[lista vazia]' } as unknown as ParentNode;
    await expect(esperarElemento('li img', vazia, 30)).rejects.toThrow(
      'esperarElemento: nada com "li img" em [lista vazia] depois de 30 ms',
    );
    await expect(waitFor(() => null, 30)).rejects.toThrow('waitFor: a condição continuou nula');
  });
});
