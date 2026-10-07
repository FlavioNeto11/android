// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { excecoesPorApp, lerModos, lerResumo, linhaDeUso, modosDoAppEmTexto, resumoDoDeclarado } from './apps';
import type { EntradaDoLivro } from './model';

/**
 * 30.15 (primeira fatia): a visão Global → App do Aprendizado contra o contrato do adendo v0.47, com o backend
 * simulado. Prova `simulated` — nenhuma rota real foi chamada. Os pacotes abaixo são dados de teste, não do front.
 */

const ZERO = { total: 0, contagem: {} };

// Um app só declarado e com zeros (o caso do Outlook), um só aprendido e um com os dois.
const APP_DECLARADO_ZERADO = {
  pacote: 'com.exemplo.vazio', nome: 'Exemplo Vazio', existencia: 'declarado',
  declarado: { arquivos: { app: true, catalogo: true, telas: false, sessao: false }, acoes: 0, telas: 0, login_gerenciado: false },
  loja: null, aprendido: ZERO, absorvido: 0, uso: {},
};
const APP_SO_APRENDIDO = {
  pacote: 'com.exemplo.solto', nome: 'com.exemplo.solto', existencia: 'so_aprendido', declarado: null, loja: null,
  aprendido: { total: 2, contagem: { receita: { candidate: 2 } } }, absorvido: 0,
  uso: { receita: { decide_sem_ia: 2 } },
};
const APP_COMPLETO = {
  pacote: 'com.exemplo.cheio', nome: 'Exemplo Cheio', existencia: 'declarado',
  declarado: { arquivos: { app: true, catalogo: true, telas: true, sessao: true }, acoes: 12, telas: 8, login_gerenciado: true },
  loja: { nome: 'Exemplo Cheio', nav_hints: 3, known_selectors: 5 },
  aprendido: { total: 4, contagem: { receita: { published: 1 }, tela: { candidate: 3 } } }, absorvido: 2,
  uso: { receita: { decide_sem_ia: 1 }, tela: { medido_nao_usado: 3 } },
};
const VISAO = {
  apps: [APP_DECLARADO_ZERADO, APP_SO_APRENDIDO, APP_COMPLETO], total: 3,
  nao_resolvido: { pacote: 'nao_resolvido', nome: 'nao_resolvido', existencia: null, declarado: null, loja: null,
                   aprendido: { total: 1, contagem: { fluxo: { published: 1 } } }, absorvido: 0, uso: { fluxo: { vai_ao_prompt: 1 } } },
  fora_do_eixo: { memoria: { '-': 12 }, licao: { candidate: 2 } },
  modos: { receitas: 'shadow', fluxos: true, habilidades: false, licoes: 'on', telas: 'observe' },
};

function linha(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'receita', ref: '7', state: 'published', native_status: 'active', title: 'Abrir o app', app: 'com.exemplo.cheio',
    origin: 'execucao', side_effect: false, human_origin: false, requires_owner: false, created_at: null, state_at: null,
    last_used_at: null, uses: 1, evidence: { for: 1, against: 0 }, count: null, detail: null, acoes: [],
    por_que_nao_publica: null, ...over,
  };
}
const DETALHE = {
  app: APP_COMPLETO,
  declarado: [
    { tipo: 'catalogo', arquivo: 'catalogo.yaml', presente: true, quantidade: 12, uso: { camada: 'decide_sem_ia', porque: 'ai.recipes=replay' } },
    { tipo: 'telas', arquivo: 'telas.yaml', presente: false, quantidade: null, uso: null },
  ],
  aprendido: [linha({ ref: '7', title: 'Abrir o app' })],
  absorvido: [{ ...linha({ ref: '9', title: 'Receita absorvida', kind: 'fluxo' }), absorvida_em: 'abc1234', uso: { camada: 'inerte', porque: null } }],
  modos: VISAO.modos,
};

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 0 }));
  backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json(VISAO));
  backend.on('GET', /^\/api\/aprendizado\/apps\/com\.exemplo\.cheio$/, () => json(DETALHE));
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [linha({})], total: 1, contagem: { receita: { published: 1 } } }));
  useUiStore.getState().navegar({ tela: 'aprendizado' }, 'replace');
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function montar(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<AprendizadoPage />);
  });
}

const cartao = (pacote: string) => container.querySelector(`[data-app="${pacote}"]`) as HTMLElement;

describe('Aprendizado por aplicativo', () => {
  it('abre no Global: um cartão por app, inclusive o zerado, mais o não resolvido e o fora do eixo', async () => {
    await montar();
    expect(byRole('tab', /^Aplicativos/, container).getAttribute('aria-selected')).toBe('true');
    await waitFor(() => expect(cartao('com.exemplo.vazio')).toBeTruthy());

    const vazio = text(cartao('com.exemplo.vazio'));
    expect(vazio).toContain('Declarado');
    expect(vazio).toContain('2 arquivos · 0 ações · 0 telas');
    expect(vazio).toContain('Aprendido (0)');
    expect(vazio).toContain('Nada aprendido para usar ainda');

    const solto = text(cartao('com.exemplo.solto'));
    expect(solto).toContain('Só aprendido');
    expect(solto).toContain('2 candidatos');
    expect(solto).toContain('Receita: decide sem a IA 2');
    expect(solto).toContain('Nada declarado');

    const cheio = text(cartao('com.exemplo.cheio'));
    expect(cheio).toContain('4 arquivos · 12 ações · 8 telas');
    expect(cheio).toContain('Absorvido pelo repositório');
    expect(cheio).toContain('medido, não usado');          // shadow e observe: mede e não usa
    expect(cheio).toContain('login gerenciado');

    expect(text(cartao('nao_resolvido'))).toContain('App não identificado');
    const fora = text(cartao('fora_do_eixo'));
    expect(fora).toContain('12 lembranças');
    expect(fora).toContain('2 candidatos');
    expect(container.querySelectorAll('ul[aria-label="Aplicativos"] > li').length).toBe(5);
  });

  it('não mostra o balde nem o fora do eixo quando estão zerados, e o app zerado continua lá', async () => {
    backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ ...VISAO, nao_resolvido: { ...VISAO.nao_resolvido, aprendido: ZERO, uso: {} }, fora_do_eixo: {} }));
    await montar();
    await waitFor(() => expect(cartao('com.exemplo.vazio')).toBeTruthy());
    expect(cartao('nao_resolvido')).toBeNull();
    expect(cartao('fora_do_eixo')).toBeNull();
  });

  it('31.266: diz que o conhecimento é compartilhado (por app, vale para todas as personas) e cada cartão leva ao Livro, às receitas e ao catálogo do app', async () => {
    await montar();
    await waitFor(() => expect(cartao('com.exemplo.cheio')).toBeTruthy());
    const lead = container.querySelector('[data-conhecimento-compartilhado]') as HTMLElement;
    expect(text(lead)).toContain('por aplicativo');
    expect(text(lead)).toContain('todas as personas');
    const atalhos = cartao('com.exemplo.cheio').querySelector('[data-atalhos-do-app]') as HTMLElement;
    const href = (nome: string) => Array.from(atalhos.querySelectorAll('a')).find((a) => text(a) === nome)?.getAttribute('href');
    expect(href('Livro')).toBe('#/aprendizado?aba=aprendido&app=com.exemplo.cheio');
    expect(href('Receitas')).toBe('#/aprendizado?aba=aprendido&app=com.exemplo.cheio&tipo=receita');
    expect(href('Catálogo')).toBe('#/aprendizado?aba=apps&app=com.exemplo.cheio');
    // sem rota nova: todos os atalhos ficam no Aprendizado
    expect(Array.from(atalhos.querySelectorAll('a')).every((a) => (a.getAttribute('href') ?? '').startsWith('#/aprendizado?'))).toBe(true);
  });

  it('abre o detalhe do app pelo cartão, mostra declarado, aprendido e absorvido, e volta ao Global', async () => {
    await montar();
    await waitFor(() => expect(cartao('com.exemplo.cheio')).toBeTruthy());
    await click(byRole('button', /^Ver o app$/, cartao('com.exemplo.cheio')));
    expect(useUiStore.getState().rota.query).toMatchObject({ aba: 'apps', app: 'com.exemplo.cheio' });

    await waitFor(() => expect(text(container)).toContain('catalogo.yaml'));
    const t = text(container);
    expect(t).toContain('Quantidade: 12');
    expect(t).toContain('Uso: decide sem a IA');
    expect(t).toContain('ausente');                        // telas.yaml não existe
    expect(t).toContain('Abrir o app');                    // aprendido
    expect(t).toContain('Receita absorvida');              // absorvido
    expect(t).toContain('Uso: inerte');
    expect(t).toContain('Absorvido em abc1234');
    expect(backend.callsTo('GET', /\/aprendizado\/apps\/com\.exemplo\.cheio$/).length).toBe(1);

    await click(byRole('button', /Todos os aplicativos/, container));
    expect(useUiStore.getState().rota.query.app).toBeUndefined();
    await waitFor(() => expect(cartao('com.exemplo.vazio')).toBeTruthy());
  });

  it('30.33-C: no detalhe do app, o fluxo que atravessa apps diz todos eles pelo nome e leva a cada um', async () => {
    backend.on('GET', /^\/api\/aprendizado\/apps\/com\.exemplo\.cheio$/, () => json({ ...DETALHE, aprendido: [
      linha({ kind: 'fluxo', ref: 'f-multi', title: 'Ler no Cheio e abrir no Social', app: 'com.exemplo.social',
              apps: ['com.exemplo.cheio', 'com.exemplo.social'], apps_nomes: ['Exemplo Cheio', 'Social'] })] }));
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'apps', app: 'com.exemplo.cheio' } }, 'replace');
    await montar();
    await waitFor(() => expect(text(container)).toContain('Ler no Cheio e abrir no Social'));
    expect(text(container)).toContain('Apps: Exemplo Cheio → Social');
    await click(byRole('button', /^Social$/, container));
    expect(useUiStore.getState().rota.query).toMatchObject({ aba: 'apps', app: 'com.exemplo.social' });
  });

  it('RA-24: o detalhe diz o conhecimento em uso, com o sha de cada arquivo e o que mudou depois de subir', async () => {
    backend.on('GET', /^\/api\/apps\/com\.exemplo\.cheio\/conhecimento$/, () => json({
      app: 'com.exemplo.cheio', processo_iniciado_em: '2026-10-03T07:28:40.351Z',
      arquivos: [
        { nome: 'catalogo.yaml', sha256: '8e5c975bfdeb8677aa', git_blob: 'e112eed', modificado_em: '2026-09-29T03:54:55Z', mudou_depois_do_inicio: false },
        { nome: 'telas.yaml', sha256: '784252b0d243d768bb', git_blob: 'e277b97', modificado_em: '2026-10-03T09:00:00Z', mudou_depois_do_inicio: true },
      ],
    }));
    await montar();
    await waitFor(() => expect(cartao('com.exemplo.cheio')).toBeTruthy());
    await click(byRole('button', /^Ver o app$/, cartao('com.exemplo.cheio')));
    await waitFor(() => expect(container.querySelector('[data-conhecimento-em-uso]')).toBeTruthy());
    const linha = text(container.querySelector('[data-conhecimento-em-uso]') as HTMLElement);
    expect(linha).toContain('2 arquivos conferidos · 1 mudou depois que o servidor subiu');
    expect(linha).toContain('reinicie para valer');
    const sha = container.querySelector('[data-sha-do-arquivo="catalogo.yaml"]') as HTMLElement;
    expect(text(sha)).toBe('sha 8e5c975');
    expect(sha.title).toContain('sha256 8e5c975bfdeb8677aa');
    expect(sha.title).toContain('blob do git e112eed');
    expect(text(container)).toContain('mudou depois que o servidor subiu');   // o telas.yaml
  });

  it('RA-24: sem conhecimento declarado (404) a linha não aparece e nada vira erro', async () => {
    await montar();
    await waitFor(() => expect(cartao('com.exemplo.cheio')).toBeTruthy());
    await click(byRole('button', /^Ver o app$/, cartao('com.exemplo.cheio')));
    await waitFor(() => expect(text(container)).toContain('catalogo.yaml'));
    await waitFor(() => expect(backend.callsTo('GET', /\/apps\/com\.exemplo\.cheio\/conhecimento$/).length).toBe(1));
    expect(container.querySelector('[data-conhecimento-em-uso]')).toBeNull();
    expect(container.querySelector('[data-sha-do-arquivo]')).toBeNull();
    expect(text(container)).not.toContain('Não foi possível');
  });

  it('de um item do Livro vai ao app, e do app ao Aprendido filtrado por ele', async () => {
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido' } }, 'replace');
    await montar();
    await waitFor(() => expect(text(container)).toContain('Abrir o app'));
    await click(byRole('button', /Abrir este aplicativo|com\.exemplo\.cheio/, container));
    expect(useUiStore.getState().rota.query).toMatchObject({ aba: 'apps', app: 'com.exemplo.cheio' });

    await waitFor(() => expect(text(container)).toContain('catalogo.yaml'));
    await click(byRole('button', /Ver no catálogo Aprendido/, container));
    expect(useUiStore.getState().rota.query).toMatchObject({ aba: 'aprendido', app: 'com.exemplo.cheio' });
  });

  it('o filtro de app do Aprendido vem de /apps e passa `app` ao Livro', async () => {
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido' } }, 'replace');
    await montar();
    const select = await waitFor(() => {
      const s = Array.from(container.querySelectorAll('select')).find((x) => Array.from(x.options).some((o) => o.value === 'com.exemplo.cheio'));
      expect(s).toBeTruthy();
      return s as HTMLSelectElement;
    });
    const rotulos = Array.from(select.options).map((o) => o.textContent);
    expect(rotulos).toContain('Exemplo Cheio (com.exemplo.cheio)');
    expect(rotulos).toContain('App não identificado');
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/)[0]?.query.get('app')).toBeNull();

    await act(async () => {
      select.value = 'com.exemplo.cheio';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(useUiStore.getState().rota.query.app).toBe('com.exemplo.cheio');
    await waitFor(() => {
      const chamadas = backend.callsTo('GET', /^\/api\/aprendizado$/);
      expect(chamadas[chamadas.length - 1]?.query.get('app')).toBe('com.exemplo.cheio');
    });
  });
});

describe('modo por app de lições e telas (30.20, só leitura)', () => {
  const PROPRIO = { licoes: { modo: 'off', origem: 'app' }, telas: { modo: 'on', origem: 'app' } };
  const GLOBAL = { licoes: { modo: 'on', origem: 'global' }, telas: { modo: 'observe', origem: 'global' } };
  const COM_EXCECAO = {
    ...VISAO,
    apps: [{ ...APP_DECLARADO_ZERADO, modos_do_app: GLOBAL }, { ...APP_SO_APRENDIDO, modos_do_app: GLOBAL },
           { ...APP_COMPLETO, modos_do_app: PROPRIO }],
    modos: { ...VISAO.modos, licoes_por_app: { 'com.exemplo.cheio': 'off' }, telas_por_app: { 'com.exemplo.cheio': 'on' } },
  };

  it('o cartão mostra só o modo próprio, e o Global lista as exceções com link para o app', async () => {
    backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json(COM_EXCECAO));
    await montar();
    await waitFor(() => expect(cartao('com.exemplo.cheio')).toBeTruthy());
    const proprio = cartao('com.exemplo.cheio').querySelector('[data-modo-proprio]') as HTMLElement;
    expect(text(proprio)).toContain('Lições: desligado');
    expect(text(proprio)).toContain('Telas aprendidas: ligado');
    expect(cartao('com.exemplo.vazio').querySelector('[data-modo-proprio]')).toBeNull();

    const resumo = Array.from(container.querySelectorAll('summary')).find((s) => text(s as HTMLElement).includes('Como o aprendizado é usado'));
    expect(text(resumo as HTMLElement)).toContain('2 exceções por app');
    await click(resumo as HTMLElement);
    const excecoes = container.querySelector('[data-excecoes-por-app]') as HTMLElement;
    expect(text(excecoes)).toContain('Exemplo Cheio — Lições: desligado');
    expect(text(excecoes)).toContain('Exemplo Cheio — Telas aprendidas: ligado');
    expect(excecoes.querySelector('a')?.getAttribute('href')).toBe('#/aprendizado?aba=apps&app=com.exemplo.cheio');
  });

  it('o detalhe diz o modo que vale, de onde vem, o que faz e como mudar', async () => {
    backend.on('GET', /^\/api\/aprendizado\/apps\/com\.exemplo\.cheio$/, () => json({ ...DETALHE, app: { ...APP_COMPLETO, modos_do_app: PROPRIO } }));
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'apps', app: 'com.exemplo.cheio' } }, 'replace');
    await montar();
    await waitFor(() => expect(container.querySelector('[data-modo-do-app]')).toBeTruthy());
    const bloco = container.querySelector('[data-modo-do-app]') as HTMLElement;
    const licoes = bloco.querySelector('[data-modo="licoes"]') as HTMLElement;
    expect(licoes.getAttribute('data-origem')).toBe('app');
    expect(text(licoes)).toContain('desligado');
    expect(text(licoes)).toContain('definido para este app');
    expect(text(licoes)).toContain('não coleta nem usa lições neste app');
    expect(text(bloco.querySelector('[data-modo="telas"]') as HTMLElement)).toContain('as telas publicadas são entregues à sessão do app');
    await click(Array.from(bloco.querySelectorAll('summary')).find((s) => text(s as HTMLElement) === 'Como mudar o modo deste app') as HTMLElement);
    const como = bloco.querySelector('[data-como-mudar]') as HTMLElement;
    expect(text(como)).toContain('config/config.yaml');
    expect(text(como)).toContain('farm-central');
    expect(text(como)).toContain('apague a linha dele');
    // o trecho traz o modo que vale hoje, no formato do bloco `aprendizado:`
    expect(como.querySelector('pre')?.textContent).toBe(
      'aprendizado:\n  licoes:\n    por_app:\n      com.exemplo.cheio: off\n  telas:\n    por_app:\n      com.exemplo.cheio: on');
    expect(text(como)).toContain('shadow: só mede (sombra)');
    expect(text(como)).toContain('observe: só observa');
  });

  it('sem override o detalhe diz "segue o global"; sem o campo (backend anterior) o bloco não aparece', async () => {
    backend.on('GET', /^\/api\/aprendizado\/apps\/com\.exemplo\.cheio$/, () => json({ ...DETALHE, app: { ...APP_COMPLETO, modos_do_app: GLOBAL } }));
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'apps', app: 'com.exemplo.cheio' } }, 'replace');
    await montar();
    await waitFor(() => expect(container.querySelector('[data-modo-do-app]')).toBeTruthy());
    const bloco = container.querySelector('[data-modo-do-app]') as HTMLElement;
    expect(Array.from(bloco.querySelectorAll('[data-origem]')).map((x) => x.getAttribute('data-origem'))).toEqual(['global', 'global']);
    expect(text(bloco)).toContain('segue o global');
    await act(async () => root.unmount());
    container.remove();

    backend.on('GET', /^\/api\/aprendizado\/apps\/com\.exemplo\.cheio$/, () => json(DETALHE));
    await montar();
    await waitFor(() => expect(text(container)).toContain('catalogo.yaml'));
    expect(container.querySelector('[data-modo-do-app]')).toBeNull();
  });

  it('funções puras: leitura tolerante, efeito por modo e exceções em ordem de app', () => {
    expect(lerModos(VISAO.modos)).toMatchObject({ licoes_por_app: {}, telas_por_app: {} });
    expect(lerResumo(APP_COMPLETO).modos_do_app).toBeNull();
    expect(modosDoAppEmTexto({ licoes: { modo: 'shadow', origem: 'global' }, telas: { modo: null, origem: 'app' } })).toEqual([
      { chave: 'licoes', tipo: 'Lições', modo: 'só mede (sombra)', efeito: 'coleta e mede as lições, mas nenhuma vai ao prompt', doApp: false },
      { chave: 'telas', tipo: 'Telas aprendidas', modo: 'não lido', efeito: null, doApp: true },
    ]);
    const m = lerModos({ licoes_por_app: { 'b.app': 'on' }, telas_por_app: { 'a.app': 'observe', 'b.app': 'off' } });
    expect(excecoesPorApp(m, new Map([['b.app', 'Bravo']])).map((x) => `${x.app}|${x.tipo}|${x.modo}`)).toEqual([
      'a.app|Telas aprendidas|só observa', 'Bravo|Lições|ligado', 'Bravo|Telas aprendidas|desligado',
    ]);
  });
});

describe('resumos puros', () => {
  it('linhaDeUso nomeia a camada e conta; vazio diz que não há o que usar', () => {
    expect(linhaDeUso({ tela: { medido_nao_usado: 3 }, receita: { decide_sem_ia: 0 } })).toBe('Tela aprendida: medido, não usado (3)');
    expect(linhaDeUso({})).toBe('Nada aprendido para usar ainda.');
  });
  it('resumoDoDeclarado é nulo fora do registro e conta os arquivos presentes', () => {
    expect(resumoDoDeclarado(null)).toBeNull();
    expect(resumoDoDeclarado({ arquivos: { app: true, catalogo: false, telas: false, sessao: false }, acoes: 1, telas: 1, login_gerenciado: false }))
      .toBe('1 arquivo · 1 ação · 1 tela');
  });
});
