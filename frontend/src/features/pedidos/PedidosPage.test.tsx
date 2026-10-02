// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { makeAviso, makeOcorrencia, makePedido, makePedidoDetalhe } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { PedidosPage } from './PedidosPage';
import { usePedidosStore } from './store';

/**
 * Item 28.9: a tela Pedidos (lista, detalhe, ações, caixa de avisos e navegação por hash) contra o backend simulado.
 * Prova `simulated` — nenhuma rota real foi chamada.
 */
const ATIVO = makePedido();
const PAUSADO = makePedido({ id: 'ped_b2', titulo: 'Vigiar preços', estado: 'pausado', pausado_motivo: 'Pausado pela pessoa',
                             acoes_permitidas: ['editar', 'retomar', 'cancelar'], avisos_nao_lidos: 2 });
const LISTA = { items: [ATIVO, PAUSADO], proximo_cursor: null, total_por_estado: { ativo: 1, pausado: 1 } };

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

function detalhe(p = ATIVO, over: Parameters<typeof makePedidoDetalhe>[0] = {}) {
  return makePedidoDetalhe({ ...p, ...over });
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/personas$/, () => json([]));
  backend.on('GET', /^\/api\/pedidos$/, () => json(LISTA));
  // `{id}` casa também `avisos` e `previa`: as rotas fixas entram DEPOIS (a última registrada vence), como no backend.
  backend.on('GET', /^\/api\/pedidos\/[^/]+$/, (c) => (c.path.endsWith('/ped_b2') ? json(detalhe(PAUSADO)) : json(detalhe())));
  backend.on('GET', /^\/api\/pedidos\/avisos$/, () => json({ items: [makeAviso()], nao_lidos: 1, proximo_cursor: null }));
  usePedidosStore.setState({ epoch: 0, avisos: null, naoLidos: null, falhou: false });
  useToastStore.setState({ toasts: [] });
  useUiStore.getState().navegar({ tela: 'pedidos' }, 'replace');
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function montar(): Promise<void> {
  await act(async () => { root.render(<><PedidosPage /><ConfirmHost /></>); });
}
const irPara = (tela: Parameters<ReturnType<typeof useUiStore.getState>['navegar']>[0]) => act(async () => useUiStore.getState().navegar(tela, 'replace'));
const modal = () => document.querySelector('dialog') as HTMLElement;
const toasts = () => useToastStore.getState().toasts.map((t) => `${t.title} ${t.message ?? ''}`).join(' | ');

describe('lista', () => {
  it('28.12: sem a data do laço mostra a PREVISTA pela agenda, e o laço desligado vira aviso no topo', async () => {
    const semData = makePedido({ id: 'ped_c3', titulo: 'Preço do café', proxima_em: null, proxima_local: null,
                                 proxima_prevista: { gatilho: 0, nominal: '2026-10-04T08:00:00', local: '2026-10-04T08:00:00-03:00',
                                                     utc: '2026-10-04T11:00:00+00:00', desviado: false, repetido: false } });
    backend.on('GET', /^\/api\/pedidos$/, () => json({ ...LISTA, items: [semData], laco: { ligado: false } }));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Preço do café'));
    expect(text(container)).toContain('O laço de pedidos está desligado nesta instalação');
    const [linha] = container.querySelectorAll('ul[aria-label="Pedidos"] > li');
    expect(text(linha as HTMLElement)).toContain('prevista');
    expect(text(linha as HTMLElement)).toContain('dom 04/10 08:00');
    expect(text(linha as HTMLElement)).toContain('(pela agenda)');
  });

  it('28.12: laço ligado não mostra aviso', async () => {
    backend.on('GET', /^\/api\/pedidos$/, () => json({ ...LISTA, laco: { ligado: true } }));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Resumo diário do feed'));
    expect(text(container)).not.toContain('laço de pedidos está desligado');
  });

  it('mostra uma linha por pedido com estado, gatilho, persona, próxima data e avisos novos; os chips contam todos', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Resumo diário do feed'));
    const linhas = container.querySelectorAll('ul[aria-label="Pedidos"] > li');
    expect(linhas).toHaveLength(2);
    expect(text(linhas[0] as HTMLElement)).toContain('Todo dia às 08:00');
    expect(text(linhas[0] as HTMLElement)).toContain('Ana Lima');
    expect(text(linhas[0] as HTMLElement)).toContain('sáb 03/10 08:00');
    expect(text(linhas[1] as HTMLElement)).toContain('2 avisos novos');
    expect(text(linhas[1] as HTMLElement)).toContain('Pausado: Pausado pela pessoa');
    // Chips de estado: contagem de TODOS (`total_por_estado`), não só dos carregados.
    expect(text(byRole('button', /^Pausado/, container.querySelector('[aria-label="Estado do pedido"]') as HTMLElement))).toContain('1');
    expect(container.querySelector('a[href="#/pedidos/ped_a1"]')).not.toBeNull();
  });

  it('o filtro mora no link e na chamada: escolher "Pausado" grava ?estado=pausado e relê com esse filtro', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Vigiar preços'));
    await click(byRole('button', /^Pausado/, container.querySelector('[aria-label="Estado do pedido"]') as HTMLElement));
    await waitFor(() => expect(window.location.hash).toBe('#/pedidos?estado=pausado'));
    await waitFor(() => expect(backend.callsTo('GET', /^\/api\/pedidos$/).some((c) => c.query.get('estado') === 'pausado')).toBe(true));
  });

  it('um link com filtro abre já filtrado (e valor desconhecido é ignorado)', async () => {
    await irPara({ tela: 'pedidos', query: { estado: 'ativo,pausado,xyz', q: 'preço', ordem: 'proxima' } });
    await montar();
    await waitFor(() => expect(backend.callsTo('GET', /^\/api\/pedidos$/).length).toBeGreaterThan(0));
    const q = backend.callsTo('GET', /^\/api\/pedidos$/)[0]?.query;
    expect(q?.get('estado')).toBe('ativo,pausado');
    expect(q?.get('q')).toBe('preço');
    expect(q?.get('ordem')).toBe('proxima');
  });

  it('lista vazia tem uma frase só e o botão "Novo pedido" leva ao Comando com o painel de pedido pedido', async () => {
    backend.on('GET', /^\/api\/pedidos$/, () => json({ items: [], proximo_cursor: null, total_por_estado: {} }));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Nenhum pedido ainda'));
    expect(text(container)).not.toContain('Nada para mostrar');
    await click(byRole('button', /Novo pedido/));
    expect(window.location.hash).toBe('#/painel');
    expect(useUiStore.getState().novoPedidoRequest).not.toBeNull();
  });

  it('cartão da lista: agenda com a hora, sem o fuso do pedido repetido e com dólar brasileiro', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Resumo diário do feed'));
    const cartao = text(container.querySelectorAll('ul[aria-label="Pedidos"] > li')[0] as HTMLElement);
    expect(cartao).not.toContain('(America/Sao_Paulo)');
    expect(cartao).toMatch(/Gasto US\$ \d+,\d{2}/);
    expect(cartao).toContain('Nenhuma ocorrência ainda');
  });
});

describe('detalhe e navegação por hash', () => {
  it('#/pedidos/<id> abre o detalhe; o motivo da pulada e a execução purgada aparecem; Voltar leva à lista', async () => {
    backend.on('GET', /^\/api\/pedidos\/ped_a1$/, () => json(detalhe(ATIVO, {
      ocorrencias_recentes: [
        makeOcorrencia({ id: 'oc_p', estado: 'pulada', motivo: 'A anterior ainda rodava', run_id: null, run: null, previsto_para: '2026-10-02T12:00:00Z' }),
        makeOcorrencia({ id: 'oc_x', estado: 'concluida', run_id: 'run-77', run: null, run_disponivel: false, resumo: 'Feed resumido.' }),
        makeOcorrencia({ id: 'oc_r', estado: 'falhou', motivo: 'Aparelho sem sessão', run_id: 'run-0002', previsto_para: '2026-10-01T11:00:00Z' }),
      ],
    })));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'], query: { aba: 'ocorrencias' } });
    await montar();
    await waitFor(() => expect(text(container)).toContain('Motivo: A anterior ainda rodava'));
    expect(text(container)).toContain('A execução foi purgada; o resumo e o custo ficaram aqui.');
    // Execução disponível tem link para a tela de Execuções; a purgada não tem.
    expect(container.querySelector('a[href="#/execucoes/run-0002"]')).not.toBeNull();
    expect(container.querySelector('a[href="#/execucoes/run-77"]')).toBeNull();
    await click(byRole('button', /Todos os pedidos/));
    await waitFor(() => expect(window.location.hash).toBe('#/pedidos'));
  });

  it('memória, relatórios e observações: null diz "ainda não disponível", [] diz "vazio"', async () => {
    backend.on('GET', /^\/api\/pedidos\/ped_a1$/, () => json(detalhe(ATIVO, { memoria: null, relatorios_recentes: [], observacoes_recentes: null })));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'], query: { aba: 'memoria' } });
    await montar();
    await waitFor(() => expect(text(container.querySelector('section[aria-label="Memória"]') as HTMLElement)).toContain('Ainda não disponível'));
    expect(text(container.querySelector('section[aria-label="Relatórios recentes"]') as HTMLElement)).toContain('Vazio');
    expect(text(container.querySelector('section[aria-label="Observações recentes"]') as HTMLElement)).toContain('Ainda não disponível');
  });

  it('cabeçalho curto e Resumo em cartões: Agenda com a hora, aparelhos do alvo e próxima calculada', async () => {
    const objetivo = 'Resuma o feed do Instagram e me conte as novidades mais importantes dos perfis que sigo, com tudo detalhado e organizado por assunto';
    backend.on('GET', /^\/api\/pedidos\/ped_a1$/, () => json(detalhe(ATIVO, {
      titulo: objetivo, objetivo, personas: [], proxima_em: null, proxima_local: null,
      alvos: { targets: [{ instance_id: 'android-01', profile_id: 'p1', app_id: null, origem: 'ui' }], device_policy: 'one' },
      gatilhos_resumo: [{ tipo: 'recorrencia', descricao: 'Todo dia (America/Sao_Paulo)' }],
      gatilhos: [{ id: 'g1', tipo: 'recorrencia', ativo: true, criado_em: '2026-10-01T10:00:00Z', cursor: null, spec: { dtstart: '2026-10-02T19:00:00', rrule: 'FREQ=DAILY' } }],
      proximas: [{ gatilho: 0, nominal: '2026-10-03T19:00:00', local: '2026-10-03 19:00 -03:00', utc: '2026-10-03T22:00:00Z', desviado: false, repetido: false }],
    })));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(container.querySelector('section[aria-label="Agenda"]')).not.toBeNull());
    expect(text(container.querySelector('h1') as HTMLElement).length).toBeLessThan(100);
    expect(text(container.querySelector('h1') as HTMLElement)).toContain('…');
    const agenda = text(container.querySelector('section[aria-label="Agenda"]') as HTMLElement);
    expect(agenda).toContain('Todo dia às 19:00');
    expect(agenda).toContain('sáb, 03/10 às 19:00');
    expect(agenda).toContain('calculada pela agenda');
    expect(text(container.querySelector('section[aria-label="Quem faz"]') as HTMLElement)).toContain('android-01');
    expect(text(container.querySelector('section[aria-label="Objetivo"]') as HTMLElement)).toContain(objetivo);
    for (const nome of ['Custos e limites', 'Comportamento', 'Autoria']) expect(container.querySelector(`section[aria-label="${nome}"]`)).not.toBeNull();
  });

  it('aguardando você: aviso acima das guias com o motivo da ocorrência incerta, o link e o Retomar ali perto', async () => {
    backend.on('GET', /^\/api\/pedidos\/ped_a1$/, () => json(detalhe(ATIVO, {
      estado: 'aguardando_pessoa', acoes_permitidas: ['retomar', 'cancelar'],
      ocorrencias_recentes: [makeOcorrencia({ id: 'oc_i', estado: 'incerta', run_id: 'run-5', previsto_para: '2026-10-02T22:00:00Z' })],
      pendencias: [{ tipo: 'ocorrencia_incerta', ref: 'oc_i', run_id: null, ocorrencia_id: 'oc_i', desde: '2026-10-02T22:10:00Z' }],
    })));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(text(container)).toContain('Este pedido espera você'));
    const aviso = container.querySelector('[role="status"]') as HTMLElement;
    expect(text(aviso)).toContain('A ocorrência de 02/10 terminou incerta');
    expect(text(aviso)).toContain('confira no aparelho se a ação aconteceu');
    expect(aviso.querySelector('a[href="#/execucoes/run-5"]')).not.toBeNull();
    expect(aviso.querySelector('button')?.textContent).toContain('Retomar');
    // o aviso fica antes das guias
    expect(aviso.compareDocumentPosition(container.querySelector('[role="tablist"]') as HTMLElement) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('o botão "Novo pedido" está sempre no cabeçalho da página', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Resumo diário do feed'));
    expect(text(container.querySelector('h1')?.parentElement?.parentElement as HTMLElement)).toContain('Novo pedido');
    await click(byRole('button', /Novo pedido/));
    expect(window.location.hash).toBe('#/painel');
  });

  it('pedido inexistente: 404 vira "este pedido não existe"', async () => {
    backend.on('GET', /^\/api\/pedidos\/ped_zzz$/, () => apiError(404, 'not_found', 'Pedido não encontrado'));
    await irPara({ tela: 'pedidos', segmentos: ['ped_zzz'] });
    await montar();
    await waitFor(() => expect(text(container)).toContain('Este pedido não existe'));
  });

  it('o pedido aguardando você mostra o que espera, com link para a execução', async () => {
    backend.on('GET', /^\/api\/pedidos\/ped_a1$/, () => json(detalhe(ATIVO, {
      estado: 'aguardando_pessoa', acoes_permitidas: ['retomar', 'cancelar'],
      pendencias: [{ tipo: 'pergunta', ref: 'q1', run_id: 'run-9', ocorrencia_id: 'oc_1', desde: '2026-10-02T09:00:00Z' }],
    })));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(text(container)).toContain('Pergunta do planejador'));
    expect(container.querySelector('a[href="#/execucoes/run-9"]')).not.toBeNull();
  });
});

describe('ações', () => {
  it('só mostra o que acoes_permitidas diz (o painel não reescreve a tabela de estados)', async () => {
    backend.on('GET', /^\/api\/pedidos\/ped_a1$/, () => json(detalhe(ATIVO, { acoes_permitidas: ['pausar'] })));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(allByRole('button', 'Pausar').length).toBe(1));
    expect(allByRole('button', /Editar/)).toHaveLength(0);
    expect(allByRole('button', /Cancelar pedido/)).toHaveLength(0);
    expect(allByRole('button', /Retomar/)).toHaveLength(0);
  });

  it('pausar pede o motivo e manda; 200 sem_mudanca vira aviso de que já estava assim', async () => {
    backend.on('POST', /^\/api\/pedidos\/ped_a1\/pausar$/, () => json({ pedido: { ...ATIVO, estado: 'pausado' }, sem_mudanca: true }));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(allByRole('button', 'Pausar').length).toBe(1));
    await click(byRole('button', 'Pausar'));
    await waitFor(() => expect(modal()).not.toBeNull());
    await click(byRole('button', 'Pausar', modal()));
    await waitFor(() => expect(backend.callsTo('POST', /pausar$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /pausar$/)[0]?.body).toEqual({});
    await waitFor(() => expect(toasts()).toContain('Nada mudou'));
    expect(toasts()).toContain('O pedido já estava pausado.');
  });

  it('cancelar é em duas etapas: a primeira chamada só pergunta (409), a confirmada leva confirmar: true', async () => {
    let chamadas = 0;
    backend.on('POST', /^\/api\/pedidos\/ped_a1\/cancelar$/, (c) => {
      chamadas += 1;
      if ((c.body as { confirmar?: boolean }).confirmar !== true) {
        return json({ detail: { code: 'confirmacao_necessaria', message: 'confirme', execucoes_em_curso: [{ run_id: 'r1' }], ocorrencias_futuras: 3 } }, 409);
      }
      return json({ pedido: { ...ATIVO, estado: 'cancelado' }, sem_mudanca: false, execucoes_em_curso: [{ run_id: 'r1', entregue: true }], ocorrencias_canceladas: 3 });
    });
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(allByRole('button', /Cancelar pedido/).length).toBe(1));
    await click(byRole('button', /Cancelar pedido/));
    await waitFor(() => expect(text(modal())).toContain('3 ocorrências futuras serão canceladas'));
    expect(text(modal())).toContain('1 execução em curso recebe');
    expect(chamadas).toBe(1);
    expect(backend.callsTo('POST', /cancelar$/)[0]?.body).toEqual({ confirmar: false });
    await click(byRole('button', 'Cancelar o pedido', modal()));
    await waitFor(() => expect(chamadas).toBe(2));
    expect(backend.callsTo('POST', /cancelar$/)[1]?.body).toEqual({ confirmar: true });
    await waitFor(() => expect(toasts()).toContain('3 ocorrências canceladas'));
  });

  it('voltar na confirmação do cancelamento não cancela nada', async () => {
    backend.on('POST', /^\/api\/pedidos\/ped_a1\/cancelar$/, () => json({ detail: { code: 'confirmacao_necessaria', message: 'x', execucoes_em_curso: [], ocorrencias_futuras: 1 } }, 409));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(allByRole('button', /Cancelar pedido/).length).toBe(1));
    await click(byRole('button', /Cancelar pedido/));
    await waitFor(() => expect(modal()).not.toBeNull());
    await click(byRole('button', 'Voltar', modal()));
    expect(backend.callsTo('POST', /cancelar$/)).toHaveLength(1);
  });

  it('retomar um pausado pergunta o modo; "daqui para frente" manda modo: daqui e mostra as contagens', async () => {
    backend.on('POST', /^\/api\/pedidos\/ped_b2\/retomar$/, () => json({ pedido: { ...PAUSADO, estado: 'ativo' }, sem_mudanca: false, puladas: 2, recuperadas: 0 }));
    await irPara({ tela: 'pedidos', segmentos: ['ped_b2'] });
    await montar();
    await waitFor(() => expect(allByRole('button', /Retomar/).length).toBe(1));
    await click(byRole('button', /Retomar/));
    await click(byRole('button', 'Daqui para frente', modal()));
    await waitFor(() => expect(backend.callsTo('POST', /retomar$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /retomar$/)[0]?.body).toEqual({ modo: 'daqui' });
    await waitFor(() => expect(toasts()).toContain('2 puladas, 0 recuperadas'));
  });

  it('retomar de aguardando_pessoa vai sem modo; com pendência aberta (409) a tela explica', async () => {
    backend.on('GET', /^\/api\/pedidos\/ped_a1$/, () => json(detalhe(ATIVO, { estado: 'aguardando_pessoa', acoes_permitidas: ['retomar'] })));
    backend.on('POST', /^\/api\/pedidos\/ped_a1\/retomar$/, () => json({ detail: { code: 'pendencia_aberta', message: 'aberta', pendencias: [{ tipo: 'pergunta' }] } }, 409));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(allByRole('button', /Retomar/).length).toBe(1));
    await click(byRole('button', /Retomar/));
    await waitFor(() => expect(backend.callsTo('POST', /retomar$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /retomar$/)[0]?.body).toEqual({});
    await waitFor(() => expect(toasts()).toContain('decisão sua pendente'));
  });

  it('409 invalid_state relê o pedido e diz o que vale agora', async () => {
    backend.on('POST', /^\/api\/pedidos\/ped_a1\/pausar$/, () => json({ detail: { code: 'invalid_state', message: 'x', estado: 'concluido', acoes_permitidas: [] } }, 409));
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(allByRole('button', 'Pausar').length).toBe(1));
    const antes = backend.callsTo('GET', /^\/api\/pedidos\/ped_a1$/).length;
    await click(byRole('button', 'Pausar'));
    await click(byRole('button', 'Pausar', modal()));
    await waitFor(() => expect(toasts()).toContain('o estado do pedido mudou'));
    await waitFor(() => expect(backend.callsTo('GET', /^\/api\/pedidos\/ped_a1$/).length).toBeGreaterThan(antes));
  });

  it('editar: primeiro a prévia (dry_run), depois aplicar com a versão e o selo que a prévia devolveu', async () => {
    backend.on('PATCH', /^\/api\/pedidos\/ped_a1$/, (c) => {
      const b = c.body as { dry_run?: boolean };
      return json({
        aplicado: !b.dry_run, pedido: { ...ATIVO, max_ocorrencias: 5, versao: b.dry_run ? 1 : 2 },
        mudancas: [{ campo: 'max_ocorrencias', de: null, para: 5 }], proximas_antes: [], proximas_depois: [],
        ocorrencias_refeitas: 2, custo: { base: 'sem_base', por_ocorrencia_usd: null, ocorrencias_por_mes: null, por_mes_usd: null },
        confirmacao: 'sha256:abc',
      });
    });
    await irPara({ tela: 'pedidos', segmentos: ['ped_a1'] });
    await montar();
    await waitFor(() => expect(allByRole('button', /Editar/).length).toBe(1));
    await click(byRole('button', /^Editar$/));
    await setValue(byRole('textbox', /Máximo de ocorrências/, modal()) as HTMLInputElement, '5');
    await click(byRole('button', 'Ver o que muda', modal()));
    await waitFor(() => expect(text(modal())).toContain('2 ocorrências são refeitas'));
    await click(byRole('button', 'Aplicar edição', modal()));
    await waitFor(() => expect(backend.callsTo('PATCH', /ped_a1$/)).toHaveLength(2));
    const [seco, real] = backend.callsTo('PATCH', /ped_a1$/).map((c) => c.body);
    expect(seco).toEqual({ max_ocorrencias: 5, versao: 1, dry_run: true });
    expect(real).toEqual({ max_ocorrencias: 5, versao: 1, confirmacao: 'sha256:abc' });
  });
});

describe('caixa de avisos', () => {
  it('lista só os informativos não lidos (requer_pessoa=0) e marca como lido', async () => {
    backend.on('POST', /^\/api\/pedidos\/avisos\/ler$/, () => json({ lidos: 1, nao_lidos: 0 }));
    await irPara({ tela: 'pedidos', query: { aba: 'avisos' } });
    await montar();
    await waitFor(() => expect(text(container)).toContain('O pedido gastou 80% do orçamento.'));
    const q = backend.callsTo('GET', /^\/api\/pedidos\/avisos$/)[0]?.query;
    expect(q?.get('requer_pessoa')).toBe('0');
    expect(q?.get('lido')).toBe('0');
    expect(text(container)).toContain('Orçamento a 80%');
    // A caixa de avisos nunca usa a palavra reservada às Pendências.
    expect(text(container).toLowerCase()).not.toContain('pendência');
    await click(byRole('button', /Marcar como lido: Resumo diário do feed/));
    await waitFor(() => expect(backend.callsTo('POST', /avisos\/ler$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /avisos\/ler$/)[0]?.body).toEqual({ ids: ['av_1'] });
  });

  it('o contador do store vem dos avisos não lidos da caixa', async () => {
    await act(async () => { await usePedidosStore.getState().atualizar(); });
    expect(usePedidosStore.getState().naoLidos).toBe(1);
  });

  it('o selo vem de `nao_lidos` do backend (o total real), não do tamanho da página', async () => {
    backend.on('GET', /^\/api\/pedidos\/avisos$/, () => json({ items: [makeAviso()], nao_lidos: 7, proximo_cursor: null }));
    await act(async () => { await usePedidosStore.getState().atualizar(); });
    expect(usePedidosStore.getState().naoLidos).toBe(7);
  });
});
