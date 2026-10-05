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
 * abre comentário, mas uma aspa sem escape dentro de um regex abriria uma string. O mesmo vale para o apóstrofo em
 * TEXTO de JSX (`<p>d'água</p>`): a string vai até a próxima aspa igual e o que há no meio não é limpo (29.129, F4).
 * Os dois só podem esconder um achado, nunca inventar um.
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
 * Os operandos do nível de fora, separados por `&&`, `||` e `??`, cada um com o operador que vem depois dele. O último
 * decide o valor quando os da frente passam: `ok && !c.querySelector('x')` devolve o booleano da negação (29.129, F1).
 */
function operandosDoTopo(corpo: string): { texto: string; depois: string | null }[] {
  const operandos: { texto: string; depois: string | null }[] = [];
  let nivel = 0;
  let inicio = 0;
  for (let i = 0; i < corpo.length; i++) {
    const c = corpo.charAt(i);
    const dois = corpo.slice(i, i + 2);
    if (c === '\\') i++;
    else if (ASPAS.has(c)) i = fimDaString(corpo, i);
    else if ('([{'.includes(c)) nivel++;
    else if (')]}'.includes(c)) nivel--;
    else if (nivel === 0 && (dois === '&&' || dois === '||' || dois === '??')) {
      operandos.push({ texto: corpo.slice(inicio, i).trim(), depois: dois });
      inicio = i + 2;
      i++;
    }
  }
  operandos.push({ texto: corpo.slice(inicio).trim(), depois: null });
  return operandos;
}

/** Há um `?` de ternário no nível de fora (nem `?.`, nem `??`)? Então um ramo que não é o último também decide. */
function temTernarioNoTopo(corpo: string): boolean {
  let nivel = 0;
  for (let i = 0; i < corpo.length; i++) {
    const c = corpo.charAt(i);
    if (c === '\\') i++;
    else if (ASPAS.has(c)) i = fimDaString(corpo, i);
    else if ('([{'.includes(c)) nivel++;
    else if (')]}'.includes(c)) nivel--;
    else if (c === '?' && corpo.charAt(i + 1) === '?') i++;
    else if (nivel === 0 && c === '?' && corpo.charAt(i + 1) !== '.') return true;
  }
  return false;
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
// ou `:has(…)` não escapa (29.119, N1). Um `as Tipo` e a asserção de não nulo `!` no fim são ignorados: em execução o
// valor é o mesmo `null` (29.129, F2).
const BUSCA_QUE_PODE_FALHAR = /(?:querySelector|closest|\.find)$/;

function terminaNumaBusca(corpo: string): boolean {
  const semTipo = corpo.replace(/\s+as\s+[\w.<>|[\]\s]+$/, '').trimEnd().replace(/!+$/, '').trimEnd();
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

/** O corpo sem os parênteses que o envolvem inteiro: `() => (c.querySelector('x'))` é o mesmo valor (29.129, F3). */
function semParentesesDeFora(corpo: string): string {
  let atual = corpo;
  while (atual.startsWith('(') && atual.endsWith(')')) {
    let nivel = 0;
    let fechaNoFim = false;
    for (let i = 0; i < atual.length; i++) {
      const c = atual.charAt(i);
      if (c === '\\') i++;
      else if (ASPAS.has(c)) i = fimDaString(atual, i);
      else if (c === '(') nivel++;
      else if (c === ')' && --nivel === 0) {
        fechaNoFim = i === atual.length - 1;
        break;
      }
    }
    if (!fechaNoFim) break;
    atual = atual.slice(1, -1).trim();
  }
  return atual;
}

// O corpo em bloco (`() => { … }`) segue fora: o `return` pode estar em qualquer ponto dele (29.129, F3, anotado).
// Limites anotados: um `(x as T)!` no MEIO da expressão não é lido como busca, e um operando entre parênteses antes do
// `&&` (`(itens().find(…)) && !carregando`) escapa do G2.
function esperasQuePassamSemAchar(codigo: string): number[] {
  return primeirosArgumentos(semComentarios(codigo))
    .filter(({ argumento }) => {
      // O corpo `async` devolve uma Promise, e o `waitFor` a devolve na hora, sem repetir: qualquer espera `async`
      // passa sem esperar, seja qual for o corpo (29.129, G1).
      if (/^async\b/.test(argumento)) return true;
      const bruto = /^\(\)\s*=>\s*([\s\S]*)$/.exec(argumento)?.[1]?.trim();
      if (bruto == null || bruto.startsWith('{')) return false;
      const corpo = semParentesesDeFora(bruto);
      // `!x.querySelector(…)` e `!!x.querySelector(…)` já devolvem booleano, desde que nada no nível de fora mude o valor.
      if (corpo.startsWith('!') && !temOperadorNoTopo(corpo)) return false;
      const operandos = operandosDoTopo(corpo);
      // No `X && …` o valor é X quando ele é falso: um X que termina numa busca (o `undefined` do `.find(…)`) passa na
      // hora, por mais que o último operando seja negado (29.129, G2).
      // O operando negado já é booleano, e com ternário no topo o `&&` é só a condição.
      const ternario = temTernarioNoTopo(corpo);
      if (!ternario && operandos.some((o) => o.depois === '&&' && !o.texto.startsWith('!') && terminaNumaBusca(o.texto))) return true;
      // Com `&&`, `||` ou `??` no topo, quem decide é o último operando: negado, é booleano. Com ternário no topo, não.
      if (!ternario && operandos[operandos.length - 1]!.texto.startsWith('!')) return false;
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
      // 29.129, F2: a asserção de não nulo no fim não muda o valor em execução.
      "await waitFor(() => container.querySelector('li')!);",
      "await waitFor(() => container.querySelector('li')! as HTMLElement);",
      // F3: o corpo entre parênteses.
      "await waitFor(() => (container.querySelector('li')));",
      // G1: o corpo `async` é uma Promise, devolvida na hora, qualquer que seja o corpo.
      "await waitFor(async () => container.querySelector('li'));",
      "await waitFor(async () => text().includes('x'));",
      "await waitFor(async () => !!c.querySelector('x'));",
      "await waitFor(async () => { await x(); return true; });",
      // G2: o `.find(…)` antes de um `&&` dá `undefined` quando não acha, e é esse o valor.
      "await waitFor(() => itens().find((i) => i.id === 'x') && !carregando);",
      // F1, o outro lado: o último operando não negado decide.
      "await waitFor(() => !a || c.querySelector('x'));",
      "await waitFor(() => ok ? !a : c.querySelector('x'));",
      // Com ternário no topo a negação da frente é só a condição: o ramo que sobra pode ser null.
      "await waitFor(() => !a ? b : c.querySelector('x'));",
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
      // 29.129, F1: a negação no ÚLTIMO operando do topo devolve booleano.
      "await waitFor(() => !a || !c.querySelector('x'));",
      "await waitFor(() => ok && !c.querySelector('x'));",
      "await waitFor(() => (ok && !c.querySelector('x')));",
      // G2 sem falso positivo: o `.find(…)` negado é booleano, e no ternário o `&&` é só a condição.
      "await waitFor(() => !lista.find((i) => i.id === 'x') && pronto);",
      "await waitFor(() => itens().find((i) => i.id === 'x') && ok ? a : b);",
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
