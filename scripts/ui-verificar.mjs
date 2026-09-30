#!/usr/bin/env node
/**
 * Verificação de acessibilidade do portal (axe-core) nas 9 telas e em 6 larguras. SÓ LEITURA.
 *
 * Usa as ferramentas instaladas FORA do repositório (axe-core, playwright-core, lighthouse) e o Chrome que já está
 * na máquina; nada é baixado nem instalado aqui. Não clica em ação nenhuma: navega pelo hash de cada tela, e toda
 * requisição que não seja GET/HEAD/OPTIONS é abortada no contexto do navegador (garantia de que nada escreve no
 * central). Não faz login. Uma tela por vez, sem paralelismo: a carga no central é a de uma aba do Chrome.
 *
 * Uso:
 *   node scripts/ui-verificar.mjs --saida C:\temp\ui-verificacao\rodada-1
 *   node scripts/ui-verificar.mjs --saida <pasta> --url http://127.0.0.1:8000 --telas painel,personas --larguras 1440,390
 *
 * Opções:
 *   --saida <pasta>     (obrigatória) onde gravar axe-<tela>-<largura>.json, resumo.json e resumo.md
 *   --url <url>         portal a verificar (padrão http://127.0.0.1:8000)
 *   --telas a,b,c       subconjunto das telas (padrão: as 9)
 *   --larguras 1920,... subconjunto das larguras em px (padrão: 1920,1440,1280,1024,768,390)
 *   --altura <px>       altura do viewport (padrão 900)
 *   --espera-ms <ms>    pausa depois de carregar, para a tela hidratar (padrão 2500)
 *   --chrome <caminho>  executável do Chrome (padrão: o instalado em Program Files)
 *   --falhar-em <nivel> sai com código 2 se houver violação desse nível ou pior (minor|moderate|serious|critical)
 *   --ajuda
 *
 * Ambiente: UI_VERIFICAR_DIR = pasta com node_modules/{axe-core,playwright-core} (padrão C:\temp\ui-verificar).
 * Código de saída: 0 rodou; 2 passou do limite de --falhar-em; 1 erro de execução.
 *
 * Limite, dito de frente: o axe só acha o que as regras automáticas alcançam (em geral 30–40% dos problemas de
 * acessibilidade). Contraste e nome acessível aparecem; ordem de foco, leitura por leitor de tela e texto claro não.
 *
 * O QUE ESTE SCRIPT NÃO PROVA SOZINHO (RF-10 da revisão final, 30/09):
 *  - O padrão de `--url` (http://127.0.0.1:8000) é o backend do ambiente central, que serve o `dist` da `main`
 *    implantada, NÃO o branch em que você está. Para medir um branch, suba o dev server dele
 *    (`npx vite --port <porta>`) e passe `--url http://127.0.0.1:<porta>`.
 *  - Ele não faz login (e aborta todo POST, então nem poderia). Sem uma sessão, o Chrome sem cabeça cai na tela de
 *    entrada em TODAS as células: o resultado é o axe da tela de entrada (ou erro por falta do `<header>`), não o das
 *    9 telas. Até existir uma sessão de leitura para ele, meça as telas autenticadas pelo navegador que já tem sessão
 *    (painel do navegador da IDE, injetando o axe) ou contra um backend simulado local.
 */
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const TELAS = ['painel', 'personas', 'aplicativos', 'execucoes', 'aprendizado', 'infraestrutura', 'configuracao',
  'diagnostico', 'pendencias'];
const LARGURAS = [1920, 1440, 1280, 1024, 768, 390];
const ORDEM_IMPACTO = ['minor', 'moderate', 'serious', 'critical'];
const CHROMES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
];

function argumentos(argv) {
  const a = { url: 'http://127.0.0.1:8000', telas: TELAS, larguras: LARGURAS, altura: 900, esperaMs: 2500 };
  for (let i = 0; i < argv.length; i += 1) {
    const k = argv[i];
    const v = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`falta o valor de ${k}`);
      return argv[i];
    };
    if (k === '--saida') a.saida = v();
    else if (k === '--url') a.url = v().replace(/\/+$/, '');
    else if (k === '--telas') a.telas = v().split(',').map((s) => s.trim()).filter(Boolean);
    else if (k === '--larguras') a.larguras = v().split(',').map((s) => Number(s.trim())).filter((n) => n > 0);
    else if (k === '--altura') a.altura = Number(v());
    else if (k === '--espera-ms') a.esperaMs = Number(v());
    else if (k === '--chrome') a.chrome = v();
    else if (k === '--falhar-em') a.falharEm = v();
    else if (k === '--ajuda' || k === '-h' || k === '--help') a.ajuda = true;
    else throw new Error(`opção desconhecida: ${k}`);
  }
  return a;
}

function ajuda() {
  const fonte = readFileSync(new URL(import.meta.url), 'utf8');
  const bloco = fonte.slice(fonte.indexOf('/**'), fonte.indexOf('*/') + 2);
  console.log(bloco.replace(/^\/\*\*\n?|\n?\s*\*\/$/g, '').replace(/^ \* ?/gm, ''));
}

function pior(impactos) {
  let m = -1;
  for (const i of impactos) m = Math.max(m, ORDEM_IMPACTO.indexOf(i));
  return m;
}

async function main() {
  const args = argumentos(process.argv.slice(2));
  if (args.ajuda) return ajuda(), 0;
  if (!args.saida) throw new Error('informe --saida <pasta> (ver --ajuda)');
  for (const t of args.telas) if (!TELAS.includes(t)) throw new Error(`tela desconhecida: ${t} (válidas: ${TELAS.join(', ')})`);
  if (args.falharEm && !ORDEM_IMPACTO.includes(args.falharEm)) throw new Error(`--falhar-em: use ${ORDEM_IMPACTO.join('|')}`);

  const dir = process.env.UI_VERIFICAR_DIR || 'C:\\temp\\ui-verificar';
  if (!existsSync(path.join(dir, 'package.json'))) throw new Error(`UI_VERIFICAR_DIR sem package.json: ${dir}`);
  const req = createRequire(path.join(dir, 'package.json'));
  const { chromium } = req('playwright-core');
  const axeFonte = readFileSync(req.resolve('axe-core/axe.min.js'), 'utf8');
  const chrome = args.chrome || CHROMES.find((c) => existsSync(c));
  if (!chrome) throw new Error('Chrome não encontrado; use --chrome <caminho>');

  mkdirSync(args.saida, { recursive: true });
  const navegador = await chromium.launch({ executablePath: chrome, headless: true });
  const resumo = [];
  let piorGeral = -1;
  try {
    for (const tela of args.telas) {
      for (const largura of args.larguras) {
        const contexto = await navegador.newContext({ viewport: { width: largura, height: args.altura }, reducedMotion: 'reduce' });
        let bloqueadas = 0;
        await contexto.route('**/*', (rota) => {
          const m = rota.request().method();
          if (m === 'GET' || m === 'HEAD' || m === 'OPTIONS') return rota.continue();
          bloqueadas += 1;
          return rota.abort();
        });
        const pagina = await contexto.newPage();
        const errosDeConsole = [];
        pagina.on('console', (msg) => { if (msg.type() === 'error') errosDeConsole.push(msg.text().slice(0, 200)); });
        const t0 = Date.now();
        let registro;
        try {
          await pagina.goto(`${args.url}/#/${tela}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
          await pagina.waitForSelector('header', { timeout: 15000 });
          await pagina.waitForTimeout(args.esperaMs);
          await pagina.addScriptTag({ content: axeFonte });
          const axe = await pagina.evaluate(async () => {
            // eslint-disable-next-line no-undef
            const r = await axe.run(document, { resultTypes: ['violations', 'incomplete'] });
            const enxuga = (l) => l.map((v) => ({
              id: v.id, impact: v.impact, help: v.help, helpUrl: v.helpUrl, tags: v.tags.filter((t) => t.startsWith('wcag') || t === 'best-practice'),
              nodes: v.nodes.length, exemplos: v.nodes.slice(0, 5).map((n) => ({ alvo: n.target.join(' '), resumo: (n.failureSummary || '').split('\n').slice(0, 2).join(' ').slice(0, 220) })),
            }));
            return { violations: enxuga(r.violations), incomplete: enxuga(r.incomplete), passes: r.passes.length };
          });
          const transbordo = await pagina.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth,
          }));
          registro = {
            tela, largura, url: `${args.url}/#/${tela}`, ms: Date.now() - t0, requisicoesEscritaBloqueadas: bloqueadas,
            rolagemHorizontal: transbordo.scrollWidth > transbordo.innerWidth + 1, ...transbordo,
            errosDeConsole: errosDeConsole.length, amostraErrosDeConsole: errosDeConsole.slice(0, 3), ...axe,
          };
        } catch (e) {
          registro = { tela, largura, url: `${args.url}/#/${tela}`, erro: String(e).slice(0, 300), ms: Date.now() - t0 };
        } finally {
          await contexto.close();
        }
        writeFileSync(path.join(args.saida, `axe-${tela}-${largura}.json`), JSON.stringify(registro, null, 2));
        resumo.push(registro);
        if (registro.violations) piorGeral = Math.max(piorGeral, pior(registro.violations.map((v) => v.impact)));
        const n = registro.violations ? registro.violations.length : 'erro';
        console.log(`${tela} @${largura}: ${n} violação(ões)${registro.rolagemHorizontal ? ' · ROLAGEM HORIZONTAL' : ''}${registro.erro ? ` · ${registro.erro}` : ''}`);
      }
    }
  } finally {
    await navegador.close();
  }

  const contagem = {};
  for (const r of resumo) for (const v of r.violations || []) {
    const c = (contagem[v.id] ||= { id: v.id, impact: v.impact, help: v.help, telas: new Set(), nos: 0 });
    c.telas.add(`${r.tela}@${r.largura}`);
    c.nos += v.nodes;
  }
  const regras = Object.values(contagem).sort((a, b) => ORDEM_IMPACTO.indexOf(b.impact) - ORDEM_IMPACTO.indexOf(a.impact) || b.nos - a.nos);
  writeFileSync(path.join(args.saida, 'resumo.json'), JSON.stringify({ geradoEm: new Date().toISOString(), url: args.url, resumo }, null, 2));
  const md = [
    `# Verificação de acessibilidade (axe-core) — ${new Date().toISOString()}`,
    '', `Portal: ${args.url} · telas: ${args.telas.length} · larguras: ${args.larguras.join(', ')} · SOMENTE LEITURA (escritas bloqueadas: ${resumo.reduce((s, r) => s + (r.requisicoesEscritaBloqueadas || 0), 0)}).`,
    '', '| Tela | Largura | Violações | Pior impacto | Rolagem horizontal | Erros de console |', '|---|---|---|---|---|---|',
    ...resumo.map((r) => `| ${r.tela} | ${r.largura} | ${r.violations ? r.violations.length : `erro: ${r.erro}`} | ${r.violations && r.violations.length ? ORDEM_IMPACTO[pior(r.violations.map((v) => v.impact))] : '—'} | ${r.rolagemHorizontal ? 'SIM' : 'não'} | ${r.errosDeConsole ?? '—'} |`),
    '', '## Regras violadas (pior primeiro)', '', '| Regra | Impacto | Nós | Onde | O quê |', '|---|---|---|---|---|',
    ...regras.map((c) => `| \`${c.id}\` | ${c.impact} | ${c.nos} | ${[...c.telas].slice(0, 6).join(', ')}${c.telas.size > 6 ? ` (+${c.telas.size - 6})` : ''} | ${c.help} |`),
    '', 'O axe só acha o que as regras automáticas alcançam; ordem de foco, leitor de tela e clareza do texto exigem revisão à mão.', '',
  ].join('\n');
  writeFileSync(path.join(args.saida, 'resumo.md'), md);
  console.log(`\nGravado em ${args.saida} (resumo.md, resumo.json e ${resumo.length} arquivos axe-*.json).`);
  if (args.falharEm && piorGeral >= ORDEM_IMPACTO.indexOf(args.falharEm)) return 2;
  return 0;
}

main().then((c) => process.exit(c), (e) => { console.error(`ui-verificar: ${e.message}`); process.exit(1); });
