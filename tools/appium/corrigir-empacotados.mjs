// Troca, DEPOIS do `npm ci`, a cópia vulnerável de um pacote que um driver traz EMPACOTADA no próprio tarball pela
// versão corrigida que já está na raiz da árvore.
//
// Por que existe (30/09/2026): o `appium-uiautomator2-driver` 8.7.0 publica as dependências dentro do tarball
// (`bundleDependencies`), e a árvore empacotada traz `brace-expansion` 5.0.9 — na faixa 4.0.0–5.0.11 dos avisos
// GHSA-q2hr-2g5m-vwhr, GHSA-qhr7-859c-m2p7 e GHSA-6j4f-fj2g-mc7p (alta). O 8.7.0 saiu horas ANTES do 5.0.12 e é a
// última versão do driver. Medido antes de escrever isto:
//   - `npm audit fix` diz "fix available" e não muda nada (dependência empacotada não é resolvida pelo registro);
//   - `overrides` no package.json não alcança o que vem dentro do tarball;
//   - editar só o package-lock deixa o `npm audit` verde e o 5.0.9 no disco: um lock que mente.
// O que corrige de verdade é o arquivo instalado. Por isso o lock declara a 5.0.12 naquele caminho E este script
// faz o disco bater com ele; `--conferir` (CI) reprova se não bater. A troca respeita o que o dependente declara
// (`minimatch` pede `^5.0.8`): mesma versão maior, só o patch de segurança.
//
// Quando o driver publicar uma versão que já empacote a corrigida, o script avisa que não há o que trocar e a
// entrada correspondente sai de CORRECOES (e o `postinstall`, quando a lista esvaziar).
//
// Uso: `node corrigir-empacotados.mjs` (o `postinstall` do npm chama) | `--conferir` (só lê; sai 1 se vulnerável)
//      `--raiz <pasta>` troca a pasta do projeto (os testes usam).
import { cpSync, existsSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const CORRECOES = [
  {
    pacote: 'brace-expansion',
    dentro_de: 'appium-uiautomator2-driver',
    minimo: '5.0.12',
    avisos: ['GHSA-q2hr-2g5m-vwhr', 'GHSA-qhr7-859c-m2p7', 'GHSA-6j4f-fj2g-mc7p'],
  },
  // 30/09/2026 (tarde): a axios 1.19.0 entrou na faixa 1.0.0–1.19.0 de sete avisos (um alto). A da raiz sobe pelo
  // `overrides` do package.json (o `appium` 3.7.0 fixa 1.19.0; o `@appium/support` publicado depois já fixa 1.20.0);
  // as duas cópias empacotadas no driver, dentro do `@appium/base-driver` e do `@appium/support` do tarball, só por
  // aqui. Mesma versão maior e as mesmas dependências declaradas, então a cópia resolve igual.
  {
    pacote: 'axios',
    dentro_de: 'appium-uiautomator2-driver/node_modules/@appium/base-driver',
    minimo: '1.20.0',
    avisos: ['GHSA-vh66-26gq-q6x8', 'GHSA-x97p-jq2g-jp4f', 'GHSA-c29m-xwm3-cm6r', 'GHSA-mghh-pgcx-3jjj'],
  },
  {
    pacote: 'axios',
    dentro_de: 'appium-uiautomator2-driver/node_modules/@appium/support',
    minimo: '1.20.0',
    avisos: ['GHSA-vh66-26gq-q6x8', 'GHSA-x97p-jq2g-jp4f', 'GHSA-c29m-xwm3-cm6r', 'GHSA-mghh-pgcx-3jjj'],
  },
];

function partes(versao) {
  const m = /^(\d+)\.(\d+)\.(\d+)$/.exec(String(versao ?? '').trim());
  if (!m) throw new Error(`versão ilegível: ${JSON.stringify(versao)}`);
  return m.slice(1).map(Number);
}

export function menor(a, b) {
  const [x, y] = [partes(a), partes(b)];
  for (let i = 0; i < 3; i += 1) {
    if (x[i] !== y[i]) return x[i] < y[i];
  }
  return false;
}

function versaoEm(pasta) {
  const manifesto = join(pasta, 'package.json');
  if (!existsSync(manifesto)) return null;
  return JSON.parse(readFileSync(manifesto, 'utf8')).version;
}

/** Uma correção. Devolve `{estado, texto}`; `estado`: ok | trocado | vulneravel | impossivel. */
export function corrigir(raiz, c, { conferir = false } = {}) {
  const modulos = join(raiz, 'node_modules');
  const empacotado = join(modulos, c.dentro_de, 'node_modules', c.pacote);
  const daRaiz = join(modulos, c.pacote);
  const onde = relative(raiz, empacotado).split(sep).join('/');
  // O pacote instalado é o primeiro trecho de `dentro_de` (o driver); o resto é caminho dentro do tarball dele. Sem o
  // driver, rodou antes do `npm ci` (erro); sem o trecho de dentro, o driver deixou de empacotá-lo (nada a fazer).
  const instalado = c.dentro_de.split('/node_modules/')[0];
  if (!existsSync(join(modulos, instalado))) {
    return { estado: 'impossivel', texto: `${instalado} não está instalado em ${modulos}: rode \`npm ci\` antes` };
  }
  const atual = versaoEm(empacotado);
  if (atual === null) {
    return { estado: 'ok', texto: `${onde}: o driver não empacota mais ${c.pacote}; a correção pode sair da lista` };
  }
  if (!menor(atual, c.minimo)) {
    return { estado: 'ok', texto: `${onde}: ${atual} (mínimo ${c.minimo})` };
  }
  if (conferir) {
    return { estado: 'vulneravel', texto: `${onde}: ${atual} no disco, abaixo de ${c.minimo} (${c.avisos.join(', ')})` };
  }
  const corrigida = versaoEm(daRaiz);
  if (corrigida === null || menor(corrigida, c.minimo) || partes(corrigida)[0] !== partes(atual)[0]) {
    return {
      estado: 'impossivel',
      texto: `${onde}: ${atual} é vulnerável e a raiz não tem ${c.pacote} >= ${c.minimo} da mesma versão maior `
        + `(tem ${corrigida ?? 'nada'}): nada foi trocado`,
    };
  }
  // A pasta apagada é montada aqui, de três nomes fixos, e conferida: nunca um caminho vindo de fora.
  if (!empacotado.startsWith(modulos + sep) || !empacotado.endsWith(join(c.dentro_de, 'node_modules', c.pacote))) {
    throw new Error(`caminho inesperado, nada apagado: ${empacotado}`);
  }
  rmSync(empacotado, { recursive: true, force: true });
  cpSync(daRaiz, empacotado, { recursive: true });
  const depois = versaoEm(empacotado);
  if (depois === null || menor(depois, c.minimo)) {
    return { estado: 'impossivel', texto: `${onde}: a troca não ficou no disco (leu ${depois ?? 'nada'})` };
  }
  return { estado: 'trocado', texto: `${onde}: ${atual} → ${depois} (${c.avisos.join(', ')})` };
}

export function executar(argv, saida = console) {
  const conferir = argv.includes('--conferir');
  const i = argv.indexOf('--raiz');
  const raiz = i >= 0 ? argv[i + 1] : dirname(fileURLToPath(import.meta.url));
  let falhou = false;
  for (const c of CORRECOES) {
    const r = corrigir(raiz, c, { conferir });
    saida.log(`[empacotados] ${r.estado}: ${r.texto}`);
    falhou ||= r.estado === 'vulneravel' || r.estado === 'impossivel';
  }
  return falhou ? 1 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = executar(process.argv.slice(2));
}
