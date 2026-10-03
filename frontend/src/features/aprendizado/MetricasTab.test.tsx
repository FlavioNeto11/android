// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { atravessaAQuebra, formatHoras, formatTaxa, formatUsd, hrefDoItemDaRevisao, lerMetricas, lerPaginaDeRevisoes } from './metricas';

/**
 * 30.33: a aba Métricas contra o contrato do adendo v0.89 (rotas do 30.8), com o backend simulado. Prova `simulated`
 * — nenhuma rota real foi chamada. Os números saem do exemplo do contrato (o ensaio na cópia do central de 03/10).
 */

const METRICAS = {
  app: null, janela_dias: 14, desde: '2026-09-19T12:00:00Z', ate: '2026-10-03T12:00:00Z',
  itens: { receita: { published: 19, candidate: 1 }, fluxo: { published: 8 } }, por_origem: { execucao: 44, pessoa: 1 }, pendentes: 2,
  aprovacoes: { sistema: { validated: 1 }, pessoa: { published: 1 } },
  curador: { revisoes: 7, simuladas: 1, validade: { ok: 7, invalida: 0, recusada: 0 }, decisoes: { manter: 1, pedir_evidencia: 6 },
             aplicadas: 0, overrides: 0, usd: 0.0682 },
  refutados_depois_de_promovidos: { desligados_pelo_sistema: 0, desligados_por_pessoa: 2, publicados_com_evidencia_contra: 0 },
  sucesso_depois_de_promovido: { a_favor: 0, contra: 0, taxa: null },
  churn: { transicoes: 12, itens_com_transicao: 10, criados: 36, desligados: 1 },
  tempos: { candidate_validated: { mediana_h: 0.024, p90_h: 0.5, n: 1 }, validated_published: { mediana_h: null, p90_h: null, n: 0 } },
  saude: { saudavel: 3, pouca_amostra: 20, sem_evidencia: 4, obsoleto_provavel: 0, degradando: 0 },
  economia: { etapas: 186, elegiveis: 150, por_receita: 33, receita_mais_ia: 4, so_ia: 123, sem_ator: 20, sem_cobertura: 90,
              chamadas_evitadas_estimadas: 79.0, etapas_por_receita_sem_base: 2 },
  falhas_evitadas_proxy: { rotulo: 'proxy', etapas_comparadas: 9, com_receita: { etapas: 37, falhas: 6, taxa_de_falha: 0.162 },
                           so_ia: { etapas: 28, falhas: 1, taxa_de_falha: 0.036 } },
  orcamento_do_curador: { modo: 'on', janela_dias: 7, gasto_da_operacao: 11.48, gasto_da_curadoria: 0.29, orcamento: 1.76,
                          teto_alfa: 8.04, revisoes_na_janela: 21, uso: 0.167, aviso: false },
  sem_item: { transicoes: 3 },
};

const REVISAO = {
  id: 'lr-2', criado_em: '2026-10-03T08:29:40Z', item_ref: 'fluxo:12', item_kind: 'fluxo', app: 'com.pocqa.messenger',
  gatilho: 'pedido_da_pessoa', validade: 'ok', simulado: false, provedor: 'anthropic', modelo: 'claude', usd: 0.0123,
  classe: 'C', decisao: 'pedir_evidencia', confianca: 'alta', decisao_final: null, decidido_por: null, override: false,
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
  backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [{ pacote: 'com.pocqa.messenger', nome: 'QA Messenger' }], total: 1 }));
  backend.on('GET', /^\/api\/aprendizado\/metricas$/, () => json(METRICAS));
  backend.on('GET', /^\/api\/aprendizado\/revisoes$/, (c) => json(c.query.get('cursor')
    ? { revisoes: [{ ...REVISAO, id: 'lr-1', item_ref: 'li-9', item_kind: 'licao', decisao: 'manter', classe: 'A' }], proximo: null }
    : { revisoes: [REVISAO], proximo: '2026-10-03T08:29:40Z|lr-2' }));
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'metricas' } }, 'replace');
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

const bloco = (titulo: string) => container.querySelector(`section[aria-label="${titulo}"]`) as HTMLElement;

describe('Aprendizado: a aba Métricas', () => {
  it('mostra cada bloco com ausente como "sem amostra" (nunca 0%), o proxy rotulado e o orçamento global', async () => {
    await montar();
    expect(byRole('tab', /^Métricas/, container).getAttribute('aria-selected')).toBe('true');
    await waitFor(() => expect(bloco('O livro agora')).toBeTruthy());

    expect(text(bloco('O livro agora'))).toContain('28itens no livro');
    expect(text(bloco('O livro agora'))).toContain('Receita 20 · Fluxo 8');
    expect(text(bloco('Movimento na janela'))).toContain('Validado 1');
    expect(text(bloco('Orçamento do curador'))).toContain('Curador ligado · 21 revisões na janela');
    expect(text(bloco('Tempo até subir'))).toContain('1 min');                      // 0,024 h em minutos
    expect(text(bloco('Tempo até subir'))).toContain('validado → publicado · n = 0');
    expect(text(bloco('Depois de publicado'))).toContain('sem amostra');
    expect(text(bloco('Depois de publicado'))).not.toContain('0%');
    expect(text(bloco('Receita × só IA (comparação, não prova)'))).toContain('Não mede falha evitada');
    expect(text(bloco('Receita × só IA (comparação, não prova)'))).toContain('16,2%');
    expect(text(bloco('Economia de IA'))).toContain('chamadas de IA evitadas (estimativa)');
    expect(text(bloco('Curador na janela'))).toContain('US$ 0,0682');
    expect(text(bloco('Curador na janela'))).toContain('1 simuladas');
    expect(text(bloco('Orçamento do curador'))).toContain('janela do curador (7 dias), não a escolhida acima');
    expect(text(container)).toContain('Esta janela atravessa uma mudança de medida');
    expect(text(container)).toContain('3 transições');
    // Saúde: os rótulos zerados não aparecem como chip.
    expect(text(bloco('Saúde dos publicados'))).not.toContain('degradando');
  });

  it('o filtro de app e a janela vão na consulta', async () => {
    await montar();
    await waitFor(() => expect(bloco('O livro agora')).toBeTruthy());
    await setValue(byRole('combobox', /^Aplicativo/, container) as HTMLSelectElement, 'com.pocqa.messenger');
    await setValue(byRole('combobox', /^Janela/, container) as HTMLSelectElement, '7');
    await waitFor(() => {
      const ultima = backend.callsTo('GET', /^\/api\/aprendizado\/metricas$/).at(-1);
      expect(ultima?.query.get('app')).toBe('com.pocqa.messenger');
      expect(ultima?.query.get('dias')).toBe('7');
    });
    const revisoes = backend.callsTo('GET', /^\/api\/aprendizado\/revisoes$/).at(-1);
    expect(revisoes?.query.get('app')).toBe('com.pocqa.messenger');
  });

  it('lista os pareceres com link para o item e carrega a página seguinte pelo cursor', async () => {
    await montar();
    await waitFor(() => expect(container.querySelector('[data-revisao="lr-2"]')).toBeTruthy());
    const linha = container.querySelector('[data-revisao="lr-2"]') as HTMLElement;
    expect(text(linha)).toContain('Pedir mais evidência');
    expect(text(linha)).toContain('Classe C');
    expect(text(linha)).toContain('pedido de uma pessoa');
    expect(linha.querySelector('a')?.getAttribute('href')).toBe('#/aprendizado?aba=aprendido&item=fluxo%3A12');

    await click(byRole('button', /^Carregar mais$/, container));
    await waitFor(() => expect(container.querySelector('[data-revisao="lr-1"]')).toBeTruthy());
    expect(backend.callsTo('GET', /^\/api\/aprendizado\/revisoes$/).at(-1)?.query.get('cursor')).toBe('2026-10-03T08:29:40Z|lr-2');
    expect(container.querySelector('[data-revisao="lr-2"]')).toBeTruthy();       // a página nova soma, não troca
    expect((container.querySelector('[data-revisao="lr-1"] a') as HTMLAnchorElement).getAttribute('href'))
      .toBe('#/aprendizado?aba=aprendido&item=licao%3Ali-9');
    expect(text(container)).toContain('Fim da lista.');
  });

  it('sem revisões e sem curador: estados vazios que dizem o porquê', async () => {
    backend.on('GET', /^\/api\/aprendizado\/metricas$/, () => json({ ...METRICAS, curador: { revisoes: 0, simuladas: 0, validade: {}, decisoes: {} },
                                                                      orcamento_do_curador: null, economia: null,
                                                                      falhas_evitadas_proxy: { rotulo: 'proxy', etapas_comparadas: 0 } }));
    backend.on('GET', /^\/api\/aprendizado\/revisoes$/, () => json({ revisoes: [], proximo: null }));
    await montar();
    await waitFor(() => expect(bloco('O livro agora')).toBeTruthy());
    expect(text(bloco('Curador na janela'))).toContain('Nenhuma revisão do curador nesta janela');
    expect(text(bloco('Orçamento do curador'))).toContain('O curador não está composto neste servidor');
    expect(text(bloco('Economia de IA'))).toContain('não está ligada');
    expect(text(bloco('Receita × só IA (comparação, não prova)'))).toContain('Nenhuma etapa rodou dos dois jeitos');
    await waitFor(() => expect(text(container)).toContain('Nenhum parecer do curador ainda'));
  });

  it('o banco vazio (a resposta do backend sem nada): uma frase por bloco, não uma grade de zeros', async () => {
    // A resposta da rota num banco novo (backend simulado do worktree, 03/10), sem edição.
    const vazio = {
      app: null, janela_dias: 14, desde: '2026-09-19T12:27:27.919Z', ate: '2026-10-03T12:27:27.919Z', itens: {}, por_origem: {}, pendentes: 0,
      aprovacoes: { sistema: {}, pessoa: {} },
      curador: { revisoes: 0, simuladas: 0, validade: { ok: 0, invalida: 0, recusada: 0 }, decisoes: {}, aplicadas: 0, overrides: 0, usd: 0 },
      refutados_depois_de_promovidos: { desligados_pelo_sistema: 0, desligados_por_pessoa: 0, publicados_com_evidencia_contra: 0 },
      sucesso_depois_de_promovido: { a_favor: 0, contra: 0, taxa: null },
      churn: { transicoes: 0, itens_com_transicao: 0, criados: 0, desligados: 0 },
      tempos: { candidate_validated: { mediana_h: null, p90_h: null, n: 0 }, validated_published: { mediana_h: null, p90_h: null, n: 0 } },
      saude: { inativo: 0, em_prova: 0, degradando: 0, obsoleto_provavel: 0, sem_evidencia: 0, parado: 0, pouca_amostra: 0, saudavel: 0, indeterminado: 0 },
      economia: { etapas: 0, elegiveis: 0, por_receita: 0, receita_mais_ia: 0, so_ia: 0, sem_ator: 0, sem_cobertura: 0,
                  chamadas_evitadas_estimadas: 0.0, etapas_por_receita_sem_base: 0 },
      falhas_evitadas_proxy: { rotulo: 'proxy', etapas_comparadas: 0, com_receita: { etapas: 0, falhas: 0, taxa_de_falha: null },
                               so_ia: { etapas: 0, falhas: 0, taxa_de_falha: null } },
      orcamento_do_curador: { modo: 'off', janela_dias: 7, gasto_da_operacao: 0.0, gasto_da_curadoria: 0.0, orcamento: 0.0, teto_alfa: 0.0,
                              revisoes_na_janela: 0, uso: null, aviso: false },
      sem_item: {},
    };
    backend.on('GET', /^\/api\/aprendizado\/metricas$/, () => json(vazio));
    await montar();
    await waitFor(() => expect(bloco('O livro agora')).toBeTruthy());
    expect(text(bloco('O livro agora'))).toContain('O livro está vazio neste recorte.');
    expect(text(bloco('Movimento na janela'))).toContain('Nenhum item nasceu nem mudou de estado');
    expect(text(bloco('Tempo até subir'))).toContain('Nenhum item subiu de estado');
    expect(text(bloco('Depois de publicado'))).toContain('Nenhuma evidência real nem desligamento');
    expect(text(bloco('Saúde dos publicados'))).toContain('Nenhum item publicado.');
    expect(text(bloco('Economia de IA'))).toContain('Nenhuma etapa executada');
    expect(text(bloco('Orçamento do curador'))).toContain('não houve gasto de IA na janela do curador');
    expect(text(bloco('Orçamento do curador'))).not.toContain('US$');
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
  });

  it('503 not_ready vira o aviso de serviço não ligado; outro erro, o estado de erro com nova tentativa', async () => {
    backend.on('GET', /^\/api\/aprendizado\/metricas$/, () => apiError(503, 'not_ready', 'As métricas do aprendizado não foram compostas.'));
    await montar();
    await waitFor(() => expect(text(container)).toContain('As métricas não estão ligadas neste servidor'));
    expect(container.querySelector('[data-revisao]')).toBeNull();
    await act(async () => root.unmount());
    container.remove();

    backend.on('GET', /^\/api\/aprendizado\/metricas$/, () => apiError(500, 'erro', 'Falhou.'));
    await montar();
    await waitFor(() => expect(byRole('button', /Tentar de novo/, container)).toBeTruthy());
  });

  it('o aviso a 80% do orçamento aparece no topo e no cartão', async () => {
    backend.on('GET', /^\/api\/aprendizado\/metricas$/, () => json({ ...METRICAS, orcamento_do_curador: { ...METRICAS.orcamento_do_curador, uso: 0.85, aviso: true } }));
    await montar();
    await waitFor(() => expect(text(container)).toContain('O curador passou de 80% do orçamento da janela dele'));
    expect(text(bloco('Orçamento do curador'))).toContain('passou de 80% do orçamento');
  });

  it('30.33-C: o orçamento diz qual ramo manda, sem "piso", e avisa que o uso fica perto de 1/k', async () => {
    backend.on('GET', /^\/api\/aprendizado\/metricas$/, () => json({ ...METRICAS, orcamento_do_curador: {
      ...METRICAS.orcamento_do_curador, orcamento: 1.17, teto_alfa: 3.52, pelas_revisoes: 1.17, ramo: 'revisoes', k: 1.5,
      uso: 0.667 } }));
    await montar();
    await waitFor(() => expect(text(bloco('Orçamento do curador'))).toContain('ramo das revisões · manda'));
    const o = text(bloco('Orçamento do curador'));
    expect(o).not.toContain('piso');
    expect(o).toContain('ramo da operação');
    expect(o).not.toContain('ramo da operação · manda');
    expect(o).toContain('o uso fica perto de 67%');
  });

  it('30.33-C: a sombra da autopublicação aparece no painel, com a regra para ligar; sem ela, o bloco some', async () => {
    backend.on('GET', /^\/api\/aprendizado\/metricas$/, () => json({ ...METRICAS, curador: { ...METRICAS.curador, autopublicacao: {
      modo: 'shadow', casos: 3, abertos: 2, limpos: 1, regrediram: 0, taxa_sem_regressao: 1.0, libera: false,
      limiares: { casos_fechados: 30, taxa_sem_regressao: 0.9, janela_dias: 7 } } } }));
    await montar();
    await waitFor(() => expect(bloco('Autopublicação (sombra)')).toBeTruthy());
    const s = text(bloco('Autopublicação (sombra)'));
    expect(s).toContain('3casos');
    expect(s).toContain('2ainda abertos');
    expect(s).toContain('Sem regressão100%');
    expect(s).toContain('em sombra · ainda não cumpre a regra para publicar de verdade: pelo menos 30 casos fechados e 90% sem regressão em 7 dias.');
  });

  it('30.33-C: sem o ramo (backend anterior) os dois números de antes aparecem e a nota não', async () => {
    expect(METRICAS.curador).not.toHaveProperty('autopublicacao');
    await montar();
    await waitFor(() => expect(text(bloco('Orçamento do curador'))).toContain('ramo da operação'));
    const o = text(bloco('Orçamento do curador'));
    expect(bloco('Autopublicação (sombra)')).toBeNull();
    expect(o).not.toContain('ramo das revisões');
    expect(o).not.toContain('não mede folga');
  });

  it('30.33-C: o parecer mostra o título do item e os apps pelo nome; sem título, a referência', async () => {
    backend.on('GET', /^\/api\/aprendizado\/revisoes$/, () => json({ revisoes: [
      { ...REVISAO, id: 'lr-m', item_ref: 'fluxo:ler-no-correio', app: 'com.exemplo.social', app_nome: 'Social',
        titulo: 'No Correio, leia o assunto e abra o perfil no Social', apps: ['com.exemplo.correio', 'com.exemplo.social'],
        apps_nomes: ['Correio', 'Social'] },
      { ...REVISAO, id: 'lr-r', item_ref: 'receita:10', item_kind: 'receita', app_nome: 'QA Messenger',
        titulo: 'send_message (v2)', capability: 'SEND_MESSAGE', capability_nome: 'Enviar a mensagem',
        resultado_posterior: 'rebaixar', resultado_em: '2026-10-17T07:10:00Z' },
      { ...REVISAO, id: 'lr-x', item_ref: 'fluxo:sumiu', app_nome: 'QA Messenger', titulo: null }], proximo: null }));
    await montar();
    await waitFor(() => expect(container.querySelector('[data-revisao="lr-m"]')).toBeTruthy());
    const multi = container.querySelector('[data-revisao="lr-m"]') as HTMLElement;
    expect(text(multi)).toContain('Fluxo: No Correio, leia o assunto e abra o perfil no Social');
    expect(text(multi)).toContain('Correio → Social');
    expect(text(multi)).not.toContain('com.exemplo');
    expect(multi.querySelector('a')?.getAttribute('title')).toBe('Fluxo ler-no-correio');
    const receita = text(container.querySelector('[data-revisao="lr-r"]') as HTMLElement);
    expect(receita).toContain('Receita: Enviar a mensagem (v2)');
    expect(receita).toContain('QA Messenger');
    expect(receita).not.toContain('com.pocqa.messenger');
    expect(receita).toContain('Em 14 dias, o item foi rebaixado.');
    expect(text(multi)).not.toContain('Em 14 dias');
    expect(text(container.querySelector('[data-revisao="lr-x"]') as HTMLElement)).toContain('Fluxo sumiu');
  });
});

describe('metricas.ts: leitura tolerante e texto', () => {
  it('ausente continua null, nunca zero', () => {
    const m = lerMetricas({ sucesso_depois_de_promovido: { a_favor: 0, contra: 0, taxa: null }, tempos: {}, economia: null });
    expect(m.sucesso_depois_de_promovido.taxa).toBeNull();
    expect(m.tempos.validated_published).toEqual({ mediana_h: null, p90_h: null, n: 0 });
    expect(m.economia).toBeNull();
    expect(m.orcamento_do_curador).toBeNull();
    expect(m.pendentes).toBeNull();
    expect(lerMetricas(null).itens).toEqual({});
    expect(lerMetricas({ economia: { etapas: 3, so_ia: 'x' } }).economia).toEqual({ etapas: 3, so_ia: null });
  });

  it('a página de revisões descarta linha sem id e mantém o cursor', () => {
    const p = lerPaginaDeRevisoes({ revisoes: [REVISAO, { item_ref: 'x' }, 'lixo'], proximo: 'a|b' });
    expect(p.revisoes.map((r) => r.id)).toEqual(['lr-2']);
    expect(p.proximo).toBe('a|b');
    expect(lerPaginaDeRevisoes({}).proximo).toBeNull();
  });

  it('o link do item segue a leitura do backend (nativo com tipo no ref; item li- com o item_kind)', () => {
    expect(hrefDoItemDaRevisao({ item_ref: 'receita:7', item_kind: null })).toBe('#/aprendizado?aba=aprendido&item=receita%3A7');
    expect(hrefDoItemDaRevisao({ item_ref: 'li-3', item_kind: 'tela' })).toBe('#/aprendizado?aba=aprendido&item=tela%3Ali-3');
    expect(hrefDoItemDaRevisao({ item_ref: 'execucao:run-1', item_kind: null })).toBeNull();
    expect(hrefDoItemDaRevisao({ item_ref: 'li-3', item_kind: null })).toBeNull();
  });

  it('horas na unidade que se lê, taxa e US$ sem inventar zero', () => {
    expect(formatHoras(0.024)).toBe('1 min');
    expect(formatHoras(0)).toBe('0 min');
    expect(formatHoras(0.001)).toBe('menos de 1 min');
    expect(formatHoras(5.25)).toBe('5,3 h');
    expect(formatHoras(72)).toBe('3 dias');
    expect(formatHoras(null)).toBe('—');
    expect(formatTaxa(null)).toBe('sem amostra');
    expect(formatTaxa(0)).toBe('0%');
    expect(formatUsd(0.0068)).toBe('US$ 0,0068');
    expect(formatUsd(1.5)).toBe('US$ 1,50');
    expect(formatUsd(0.29)).toBe('US$ 0,29');
    expect(formatUsd(0)).toBe('US$ 0,00');
    expect(formatUsd(null)).toBe('—');
  });

  it('a quebra de série do deploy 8 só vale quando a janela a atravessa', () => {
    expect(atravessaAQuebra('2026-09-19T12:00:00Z', '2026-10-03T12:00:00Z')).toBe(true);
    expect(atravessaAQuebra('2026-10-03T10:00:00Z', '2026-10-03T12:00:00Z')).toBe(false);
    expect(atravessaAQuebra(null, '2026-10-03T12:00:00Z')).toBe(false);
  });
});
