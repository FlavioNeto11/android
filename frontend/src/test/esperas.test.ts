/**
 * Catraca das esperas (item 29.104): `waitFor` só trata `false` e exceção como "ainda não". Um `querySelector` que
 * ainda não acha nada devolve `null`, e `waitFor(() => raiz.querySelector('…'))` passava NA HORA, sem esperar: o teste
 * corria contra a tela de antes e só falhava quando o fetch falso demorava (o cron do CI, no runner carregado). Desde
 * o W1 o `waitFor` também trata `null` como "ainda não"; a catraca fica como segunda guarda e como regra de estilo:
 * para esperar um elemento, use `esperarElemento(seletor, raiz)` do harness, que diz na falha o seletor e onde.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { esperarElemento, waitFor } from './harness';

// O corpo da seta do `waitFor` termina num `querySelector(...)`, com um `as Tipo` opcional: é o valor devolvido, e ele
// pode ser `null`. Lê-se o argumento com os parênteses balanceados (um regex confundiria o `)` de um `expect(...)` com
// o do `waitFor`), e por isso o padrão vale também em chamadas quebradas em várias linhas e com o prazo.
const TERMINA_NO_ELEMENTO = /querySelector\([^()]*\)\s*(as\s+[\w.<>|[\]\s]+)?$/;

/** O 1º argumento de cada `waitFor(`, lido até a vírgula ou o `)` do mesmo nível, pulando o que está entre aspas. */
function primeirosArgumentos(codigo: string): { indice: number; argumento: string }[] {
  const achados: { indice: number; argumento: string }[] = [];
  for (const m of codigo.matchAll(/\bwaitFor\(/g)) {
    const inicio = m.index + m[0].length;
    let nivel = 0;
    let i = inicio;
    for (; i < codigo.length; i++) {
      const c = codigo.charAt(i);
      // Comentário não é código: um apóstrofo nele ("don't") abriria uma string que nunca fecha.
      if (c === '/' && codigo.charAt(i + 1) === '/') {
        while (i < codigo.length && codigo.charAt(i) !== '\n') i++;
      } else if (c === '/' && codigo.charAt(i + 1) === '*') {
        const fim = codigo.indexOf('*/', i + 2);
        i = fim < 0 ? codigo.length : fim + 1;
      } else if (c === "'" || c === '"' || c === '`') {
        for (i++; i < codigo.length && codigo.charAt(i) !== c; i++) if (codigo.charAt(i) === '\\') i++;
      } else if ('([{'.includes(c)) nivel++;
      else if (')]}'.includes(c)) {
        if (nivel === 0) break;
        nivel--;
      } else if (c === ',' && nivel === 0) break;
    }
    achados.push({ indice: m.index, argumento: codigo.slice(inicio, i).trim() });
  }
  return achados;
}

function esperasQuePassamComNull(codigo: string): number[] {
  return primeirosArgumentos(codigo)
    .filter(({ argumento }) => {
      const corpo = /^\(\)\s*=>\s*([\s\S]*)$/.exec(argumento)?.[1];
      // `!x.querySelector(…)` e `!!x.querySelector(…)` já devolvem booleano: esperam de verdade.
      if (corpo == null || corpo.startsWith('{') || corpo.startsWith('!')) return false;
      return TERMINA_NO_ELEMENTO.test(semComentarios(corpo).trim());
    })
    .map(({ indice }) => indice);
}

function semComentarios(trecho: string): string {
  return trecho.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
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
    ];
    for (const linha of pega) expect(esperasQuePassamComNull(linha), linha).toHaveLength(1);
    for (const linha of poupa) expect(esperasQuePassamComNull(linha), linha).toEqual([]);
  });

  it('nenhum teste espera um elemento pelo waitFor puro (use esperarElemento)', () => {
    const src = resolve(__dirname, '..');
    const achados = arquivosDeTeste(src).filter((arquivo) => resolve(arquivo) !== resolve(__filename)).flatMap((arquivo) => {
      const codigo = readFileSync(arquivo, 'utf8');
      return esperasQuePassamComNull(codigo).map((indice) => {
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
