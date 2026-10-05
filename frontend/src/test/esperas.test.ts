/**
 * Catraca das esperas (item 29.104): `waitFor` só trata `false` e exceção como "ainda não". Um `querySelector` que
 * ainda não acha nada devolve `null`, e `waitFor(() => raiz.querySelector('…'))` passava NA HORA, sem esperar: o teste
 * corria contra a tela de antes e só falhava quando o fetch falso demorava (o cron do CI, no runner carregado). Para
 * esperar um elemento, use `esperarElemento(seletor, raiz)` do harness, que trata `null` como "ainda não".
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

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
      if (c === "'" || c === '"' || c === '`') {
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
      return corpo != null && !corpo.startsWith('{') && TERMINA_NO_ELEMENTO.test(corpo.trim());
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
    ];
    const poupa = [
      "await waitFor(() => container.querySelector('h1')?.textContent === 'Mariana Costa');",
      "await waitFor(() => document.querySelector('table') !== null);",
      "await waitFor(() => document.querySelector('table') === null);",
      "await waitFor(() => expect(container.querySelector('li img')).not.toBeNull());",
      "await waitFor(() => { expect(container.querySelector('li img')).not.toBeNull(); });",
      "await waitFor(() => text().includes('Mostrando 1 de 1'));",
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
