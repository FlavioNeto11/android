// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { agruparPorCapability, contarPorRotulo, falhasPorCapability, filaDeAtencao, motivoPrincipal } from './atencao';
import type { EntradaDoLivro, RotuloDeSaude, SaudeDoItem } from './model';

/**
 * 30.15 (restante): saúde por app, fila Atenção, falhas e capability no detalhe do app, contra o contrato dos adendos
 * v0.47 (apps), v0.52 (saúde na lista) e v0.54 (obsoleto_provavel), com o backend simulado. Prova `simulated`: nenhuma
 * rota real foi chamada. Os pacotes são dados de teste, não do front.
 */

const ZERO = { total: 0, contagem: {} };
const CHEIO = 'com.exemplo.cheio';
const SOLTO = 'com.exemplo.solto';

const APP_CHEIO = {
  pacote: CHEIO, nome: 'Exemplo Cheio', existencia: 'declarado',
  declarado: { arquivos: { app: true, catalogo: true, telas: true, sessao: true }, acoes: 12, telas: 8, login_gerenciado: false },
  loja: null, aprendido: { total: 4, contagem: { receita: { published: 2 }, fluxo: { published: 1 }, licao: { published: 1 } } }, absorvido: 0, uso: {},
};
const APP_SOLTO = {
  pacote: SOLTO, nome: 'Exemplo Solto', existencia: 'so_aprendido', declarado: null, loja: null,
  aprendido: { total: 1, contagem: { receita: { candidate: 1 } } }, absorvido: 0, uso: {},
};
const APP_VAZIO = {
  pacote: 'com.exemplo.vazio', nome: 'Exemplo Vazio', existencia: 'declarado',
  declarado: { arquivos: { app: true, catalogo: false, telas: false, sessao: false }, acoes: 0, telas: 0, login_gerenciado: false },
  loja: null, aprendido: ZERO, absorvido: 0, uso: {},
};
const VISAO = {
  apps: [APP_CHEIO, APP_SOLTO, APP_VAZIO], total: 3, nao_resolvido: null, fora_do_eixo: {},
  modos: { receitas: 'replay', fluxos: true, habilidades: false, licoes: 'on', telas: 'observe' },
};

function saude(rotulo: RotuloDeSaude, codigo: string, valor: number | string | null, limite: number | null = null): SaudeDoItem {
  return { rotulo, motivos: [{ codigo, dimensao: null, valor, limite, detalhe: null }], dimensoes: [] };
}

function linha(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'receita', ref: '7', state: 'published', native_status: 'active', title: 'Abrir o app', app: CHEIO,
    origin: 'execucao', side_effect: false, human_origin: false, requires_owner: false, created_at: null, state_at: null,
    last_used_at: null, uses: 1, evidence: { for: 1, against: 0 }, count: null, detail: null, acoes: [],
    por_que_nao_publica: null, saude: null, ...over,
  };
}

const SAUDAVEL = linha({ ref: '7', title: 'Abrir o app', saude: saude('saudavel', 'amostra_suficiente', 9, 5) });
const DEGRADANDO = linha({ ref: '8', title: 'Enviar a mensagem', saude: saude('degradando', 'falhas_seguidas', 3, 2) });
const SEM_EVIDENCIA = linha({ kind: 'fluxo', ref: '3', title: 'Fluxo de login', saude: saude('sem_evidencia', 'nunca_usado', 20, 14) });
const OBSOLETO = linha({ kind: 'licao', ref: 'li-1', title: 'Tocar no ícone antigo', saude: saude('obsoleto_provavel', 'substituta_viva', '9') });
const EM_PROVA = linha({ ref: '11', title: 'Receita nova', app: SOLTO, state: 'candidate', saude: saude('em_prova', 'aguarda_repeticao', null) });
const MEMORIA = linha({ kind: 'memoria', ref: 'perfil-1', title: 'Memória', app: null, saude: null });
const LIVRO = [SAUDAVEL, DEGRADANDO, SEM_EVIDENCIA, OBSOLETO, EM_PROVA, MEMORIA];

const DETALHE = {
  app: APP_CHEIO,
  declarado: [],
  // As linhas de /apps/{pacote} chegam SEM saúde (o backend não a passa): o painel a completa pela lista do Livro.
  aprendido: [SAUDAVEL, DEGRADANDO, SEM_EVIDENCIA, OBSOLETO].map((e) => ({ ...e, saude: null })),
  absorvido: [],
  modos: VISAO.modos,
};

const grupo = (id: string, app: string, capability: string, over: Record<string, unknown> = {}) => ({
  id, app, capability, tipo: 'elemento_nao_encontrado', tela: 'inicio', camada: 'receita', titulo: `Falha ${id}`,
  ocorrencias: 5, custo_total: 1, onde_alterar: [], exemplos: [], ...over,
});
const FALHAS = {
  janela: { dias: 14 },
  grupos: [
    grupo('fk-livre', CHEIO, '*', { custo_total: 9 }),
    grupo('fk-enviar-a', CHEIO, 'enviar_mensagem', { custo_total: 5 }),
    grupo('fk-enviar-b', CHEIO, 'enviar_mensagem', { custo_total: 2 }),
    grupo('fk-abrir', CHEIO, 'abrir_conversa', { custo_total: 3 }),
  ],
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
  backend.on('GET', new RegExp(`^/api/aprendizado/apps/${CHEIO.replace(/\./g, '\\.')}$`), () => json(DETALHE));
  backend.on('GET', /^\/api\/aprendizado$/, (c) => {
    const app = c.query.get('app');
    const itens = app ? LIVRO.filter((e) => e.app === app) : LIVRO;
    return json({ itens, total: itens.length, contagem: {} });
  });
  backend.on('GET', /^\/api\/aprendizado\/falhas$/, (c) => {
    const app = c.query.get('app');
    return json({ ...FALHAS, grupos: FALHAS.grupos.filter((g) => !app || g.app === app) });
  });
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
const abrirDetalhe = async () => {
  await waitFor(() => expect(cartao(CHEIO)).toBeTruthy());
  await click(byRole('button', /^Ver o app$/, cartao(CHEIO)));
  await waitFor(() => expect(container.querySelector('[aria-label="Itens que pedem atenção"]')).toBeTruthy());
};

describe('saúde por app', () => {
  it('o cartão conta os itens do app por rótulo e avisa quantos pedem atenção; app sem item não mostra zero inventado', async () => {
    await montar();
    await waitFor(() => expect(text(cartao(CHEIO))).toContain('pedem atenção'));
    const cheio = text(cartao(CHEIO));
    expect(cheio).toContain('1 saudável');
    expect(cheio).toContain('1 degradando');
    expect(cheio).toContain('1 provavelmente obsoleto');
    expect(cheio).toContain('1 sem evidência');
    expect(cheio).toContain('3 pedem atenção');
    expect(text(cartao(SOLTO))).toContain('1 em prova');
    expect(text(cartao(SOLTO))).not.toContain('pedem atenção');
    expect(text(cartao('com.exemplo.vazio'))).toContain('Nada medido ainda');
    // Uma chamada só à lista do Livro, sem filtro: a contagem do Global não repete uma ida por app.
    const chamadas = backend.callsTo('GET', /^\/api\/aprendizado$/);
    expect(chamadas.length).toBe(1);
    expect(chamadas[0]?.query.get('app')).toBeNull();
  });

  it('se a lista do Livro falhar, os cartões continuam, sem a linha de saúde, e o erro aparece', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => apiError(500, 'boom', 'falhou'));
    await montar();
    await waitFor(() => expect(cartao(CHEIO)).toBeTruthy());
    await waitFor(() => expect(container.querySelector('[role="alert"], [role="status"]')).toBeTruthy());
    expect(text(cartao(CHEIO))).not.toContain('Saúde do aprendido');
    expect(container.querySelector('[aria-label="Atenção"]')).toBeNull();
  });

  it('o detalhe do app mostra a contagem por rótulo, pedindo ao Livro só os itens do app, e põe o selo de saúde nas linhas', async () => {
    await montar();
    await abrirDetalhe();
    const chamadas = backend.callsTo('GET', /^\/api\/aprendizado$/);
    expect(chamadas[chamadas.length - 1]?.query.get('app')).toBe(CHEIO);
    const saudeDoApp = text(container.querySelector('[data-saude-do-app]') as HTMLElement);
    expect(saudeDoApp).toContain('1 saudável');
    expect(saudeDoApp).toContain('1 degradando');
    // O selo do item vem da lista do Livro: a linha de /apps/{pacote} chegou sem `saude`.
    const lista = container.querySelector('section[aria-label="Aprendido"]') as HTMLElement;
    expect(text(lista)).toContain('Saúde: Degradando');
    expect(text(lista)).toContain('Saúde: Saudável');
  });
});

describe('fila Atenção', () => {
  it('no Global lista só degradando, obsoleto provável e sem evidência, do mais grave ao menos, com o motivo e o link do item', async () => {
    await montar();
    const fila = await waitFor(() => {
      const f = container.querySelector('[aria-label="Itens que pedem atenção"]') as HTMLElement;
      expect(f).toBeTruthy();
      return f;
    });
    const itens = Array.from(fila.querySelectorAll('li')).map((li) => li.getAttribute('data-atencao'));
    expect(itens).toEqual(['receita:8', 'licao:li-1', 'fluxo:3']);
    expect(text(container.querySelector('[aria-label="Atenção"]') as HTMLElement)).toContain('Atenção (3)');

    const degradando = text(fila.querySelector('[data-atencao="receita:8"]') as HTMLElement);
    expect(degradando).toContain('3 falhas seguidas (o limite é 2)');
    expect(degradando).toContain('Exemplo Cheio');                      // no Global a fila mostra o app
    expect(text(fila.querySelector('[data-atencao="licao:li-1"]') as HTMLElement)).toContain('Há uma versão mais nova em uso (9)');
    expect(text(fila.querySelector('[data-atencao="fluxo:3"]') as HTMLElement)).toContain('nunca usado');

    const link = byRole('link', /Abrir o item/, fila.querySelector('[data-atencao="receita:8"]') as HTMLElement);
    expect(link.getAttribute('href')).toBe('#/aprendizado?aba=aprendido&item=receita%3A8');
    // Saudável, em prova e memória (sem saúde) nunca entram.
    expect(fila.textContent).not.toContain('Receita nova');
    expect(fila.textContent).not.toContain('Memória');
  });

  it('no detalhe do app a fila é só daquele app, sem repetir o nome do app', async () => {
    await montar();
    await abrirDetalhe();
    const fila = container.querySelector('[aria-label="Itens que pedem atenção"]') as HTMLElement;
    expect(Array.from(fila.querySelectorAll('li')).length).toBe(3);
    expect(text(fila)).not.toContain('Exemplo Cheio');
  });

  it('é só leitura: nenhuma chamada de escrita sai de nenhuma das duas telas', async () => {
    await montar();
    await abrirDetalhe();
    expect(backend.calls.filter((c) => c.method !== 'GET')).toEqual([]);
  });
});

describe('falhas e capability no detalhe do app', () => {
  it('pede as falhas do app (janela de 14 dias) e as agrupa por capability, a etapa livre por último', async () => {
    await montar();
    await abrirDetalhe();
    await waitFor(() => expect(container.querySelector('[aria-label="O que falha neste app"] li[data-item]')).toBeTruthy());
    const chamada = backend.callsTo('GET', /^\/api\/aprendizado\/falhas$/)[0];
    expect(chamada?.query.get('app')).toBe(CHEIO);
    expect(chamada?.query.get('dias')).toBe('14');

    const secao = container.querySelector('[aria-label="O que falha neste app"]') as HTMLElement;
    expect(text(secao)).toContain('O que falha (4)');
    const subtitulos = Array.from(secao.querySelectorAll('h4')).map((h) => h.textContent);
    expect(subtitulos).toEqual(['abrir_conversa (1)', 'enviar_mensagem (2)', 'Etapa livre (sem capability) (1)']);
    // Dentro do grupo vale a ordem do custo (a do backend): o de custo 5 antes do de custo 2.
    const enviar = secao.querySelector('ol[aria-label="Falhas de enviar_mensagem"]') as HTMLElement;
    expect(Array.from(enviar.querySelectorAll('li[data-item]')).map((li) => li.getAttribute('data-item'))).toEqual(['fk-enviar-a', 'fk-enviar-b']);
  });

  it('sem falha no app diz que não há grupo; "Ver todas as falhas" abre a aba de falhas', async () => {
    backend.on('GET', /^\/api\/aprendizado\/falhas$/, () => json({ janela: { dias: 14 }, grupos: [] }));
    await montar();
    await abrirDetalhe();
    await waitFor(() => expect(text(container)).toContain('Nenhum grupo de falha neste app'));
    await click(byRole('button', /Ver todas as falhas/, container));
    expect(useUiStore.getState().rota.query).toMatchObject({ aba: 'falhas' });
  });

  it('sem a capability na lista (backend anterior), o aprendido é agrupado por tipo e nunca fica plano', async () => {
    await montar();
    await abrirDetalhe();
    const secao = container.querySelector('section[aria-label="Aprendido"]') as HTMLElement;
    expect(Array.from(secao.querySelectorAll('[data-grupos] > details > summary')).map((s) => text(s as HTMLElement))).toEqual([
      expect.stringContaining('Fluxo'), expect.stringContaining('Lição'), expect.stringContaining('Receita'),
    ]);
    // O grupo com item pedindo atenção já abre; o resto fica recolhido até a pessoa abrir.
    expect(container.querySelector('ul[aria-label="Aprendido: Receita"]')).toBeTruthy();
  });

  it('quando a linha traz `capability`, o aprendido é agrupado por ela', async () => {
    const comCapability = {
      ...DETALHE,
      aprendido: [
        { ...DETALHE.aprendido[0], capability: 'abrir_conversa' },
        { ...DETALHE.aprendido[1], capability: 'enviar_mensagem' },
        { ...DETALHE.aprendido[2] },
      ],
    };
    backend.on('GET', new RegExp(`^/api/aprendizado/apps/${CHEIO.replace(/\./g, '\\.')}$`), () => json(comCapability));
    await montar();
    await abrirDetalhe();
    await waitFor(() => expect(container.querySelector('ul[aria-label="Aprendido: enviar_mensagem"]')).toBeTruthy());
    const secao = container.querySelector('section[aria-label="Aprendido"]') as HTMLElement;
    const resumos = Array.from(secao.querySelectorAll('[data-grupos] > details > summary')).map((s) => text(s as HTMLElement));
    // Capability primeiro, em ordem; o fluxo (comando inteiro) no seu bloco, por último.
    expect(resumos).toEqual([
      expect.stringContaining('abrir_conversa'), expect.stringContaining('enviar_mensagem'),
      expect.stringContaining('Fluxos (o comando inteiro)'),
    ]);
    expect(resumos[1]).toContain('1 pedem atenção');
    // Sem item pedindo atenção, o bloco fica recolhido.
    expect(container.querySelector('ul[aria-label="Aprendido: abrir_conversa"]')).toBeNull();
  });
});

describe('funções puras', () => {
  it('contarPorRotulo conta o que o backend rotulou, na ordem de gravidade, e ignora item sem saúde', () => {
    expect(contarPorRotulo(LIVRO)).toEqual([
      { rotulo: 'degradando', n: 1 }, { rotulo: 'obsoleto_provavel', n: 1 }, { rotulo: 'sem_evidencia', n: 1 },
      { rotulo: 'em_prova', n: 1 }, { rotulo: 'saudavel', n: 1 },
    ]);
    expect(contarPorRotulo([])).toEqual([]);
    // Rótulo de um backend mais novo entra no fim, como veio.
    expect(contarPorRotulo([linha({ saude: { rotulo: 'novo' as RotuloDeSaude, motivos: [], dimensoes: [] } })])).toEqual([{ rotulo: 'novo', n: 1 }]);
  });

  it('filaDeAtencao ordena por gravidade e mantém a ordem do backend dentro do rótulo', () => {
    const outro = linha({ ref: '20', title: 'Outra', saude: saude('degradando', 'falhas_seguidas', 4, 2) });
    expect(filaDeAtencao([SEM_EVIDENCIA, outro, SAUDAVEL, DEGRADANDO]).map((e) => e.ref)).toEqual(['20', '8', '3']);
  });

  it('motivoPrincipal é o primeiro motivo em português, ou nulo sem saúde', () => {
    expect(motivoPrincipal(DEGRADANDO)).toBe('3 falhas seguidas (o limite é 2)');
    expect(motivoPrincipal(MEMORIA)).toBeNull();
    expect(motivoPrincipal(linha({ saude: { rotulo: 'degradando', motivos: [], dimensoes: [] } }))).toBeNull();
  });

  it('agruparPorCapability devolve nulo sem o campo e põe a etapa livre por último', () => {
    expect(agruparPorCapability([{ a: 1 }, { a: 2 }])).toBeNull();
    const g = agruparPorCapability([{ capability: 'b' }, { x: 1 }, { capability: 'a' }, { capability: '*' }]);
    expect(g?.map((x) => [x.capability, x.itens.length])).toEqual([['a', 1], ['b', 1], ['*', 2]]);
  });

  it('falhasPorCapability junta pelo nome e deixa `*` (e o vazio) por último', () => {
    const g = falhasPorCapability([{ capability: '*' }, { capability: 'z' }, { capability: 'a' }, { capability: 'z' }, { capability: '' }]);
    expect(g.map((x) => [x.capability, x.itens.length])).toEqual([['a', 1], ['z', 2], ['*', 2]]);
  });
});
