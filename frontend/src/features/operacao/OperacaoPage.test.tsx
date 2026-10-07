// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { SETTINGS } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, byRole, click, esperarElemento, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import {
  agregadoPorApp, contarPorEstado, ESTAGIOS, estagioDeParada, estagiosAlcancados, lerAlvo, lerCapacidade, lerEstagio, lerOperacao, verificacaoDoAlvo,
} from './modelo';
import { OPERACAO_DE_EXEMPLO } from './operacaoDeExemplo';
import { commitEmPalavras, lerCustoPorPasso, rotuloDoModelo } from './custoPorPasso';
import { modelosDoAlvo } from './modelosLidos';
import { rotuloDoEstagioDoCusto } from './rotuloDoEstagioDoCusto';
import { OperacaoPage } from './OperacaoPage';
import { conhecimentoEmPalavras, lerFontesDaPesquisa, lerPesquisaDaOperacao } from './pesquisaDaOperacao';

/**
 * Prova 07/10 (FULL INSTAGRAM): a tela Operação no formato do rascunho do adendo v1.94 (Jev, commit 9da5017d). Prova `simulated`:
 * o servidor é falso, e quando a rota não existe a tela lê o exemplo fixo e avisa. Os nomes dos campos são os do rascunho; o
 * leitor é tolerante.
 */

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: [], query: {} } });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [] } });
});

const ir = async (segmentos: string[]) => {
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos } });
  await act(async () => root.render(<><OperacaoPage /><ConfirmHost /></>));
};
const linhas = () => Array.from(container.querySelectorAll('tbody tr[data-alvo]')) as HTMLElement[];
const abrirExemplo = async () => {
  await ir(['op-exemplo']);
  await waitFor(() => expect(linhas()).toHaveLength(30));
};

describe('o leitor tolerante', () => {
  it('lerOperacao: sem id não é operação; campo ausente vira "não informado", nunca erro', () => {
    expect(lerOperacao(null)).toBeNull();
    expect(lerOperacao({ command: 'x' })).toBeNull();
    const op = lerOperacao({ id: 'op-1', alvos: [{}, 7, null, { run_id: 'r1', estado: 'inventado', estagio: 'inventado', resultado: 'texto' }] })!;
    expect(op.alvos).toHaveLength(2);                                  // 7 e null caem; {} fica com id derivado
    expect(op.alvos[0]!.id).toBe('alvo-1');
    expect(op.alvos[1]).toMatchObject({ id: 'r1', estado: null, estagio: null, resultado: null });
    expect(op.capacidade).toEqual(lerCapacidade(undefined));
    expect(op.capacidade.solicitados).toBeNull();
  });

  it('lerAlvo: texto vazio, número negativo e estágio sem nome não valem; o alias de estágio tem lugar fixo', () => {
    const a = lerAlvo({ run_id: 'r', persona_nome: '  ', resultado: { texto: '', conhecimento_ids: ['a', '', 3], evidencia_id: -2, acao_final: { tipo: 'CREATE_COMMENT', verificada: 'sim' } },
                        estagios: [{ estagio: 'persona', em: 'x' }, { estagio: 'inventado' }, { estagio: 'acao_bloqueada' }] }, 0)!;
    expect(a.persona).toBeNull();
    expect(a.resultado).toEqual({ texto: null, conhecimento_ids: ['a'], evidencia_id: null, acao_final: { tipo: 'CREATE_COMMENT', verificada: null, evidencia_id: null } });
    expect(a.estagios.map((e) => e.estagio)).toEqual(['persona', 'acao_executada']);
    expect(lerEstagio('app_aberto')).toBe('instagram_aberto');
    expect(lerEstagio('acao_bloqueada')).toBe('acao_executada');
    expect(lerEstagio('x')).toBeNull();
  });

  it('lerCapacidade: os motivos viram lista, do mais frequente ao menos, e valor inválido cai', () => {
    const c = lerCapacidade({ solicitados: 30, em_curso: 2, motivos: { 'sem sessão': 1, 'sem conta': 25, ruim: 'x', '': 3 } });
    expect(c).toMatchObject({ solicitados: 30, em_curso: 2, contas_existentes: null });
    expect(c.motivos).toEqual([{ motivo: 'sem conta', n: 25 }, { motivo: 'sem sessão', n: 1 }]);
  });

  it('estágios: o dono manda a ordem (14); o alcançado vem da lista `estagios` ou da posição, e a parada é o seguinte', () => {
    expect(ESTAGIOS.map((e) => e.id)).toEqual([
      'persona', 'conta', 'sessao', 'aparelho', 'instagram_aberto', 'target_localizado', 'post_localizado', 'conteudo_lido',
      'conhecimento_recuperado', 'resposta_gerada', 'interface_de_comentario_alcancada', 'acao_preparada', 'acao_executada', 'resultado_verificado',
    ]);
    expect(estagiosAlcancados({ estagio: null, estagios: [] })).toBe(0);
    expect(estagiosAlcancados({ estagio: 'aparelho', estagios: [] })).toBe(4);
    expect(estagiosAlcancados({ estagio: 'aparelho', estagios: [{ estagio: 'persona', em: null }, { estagio: 'conta', em: null }] })).toBe(2);
    expect(estagioDeParada({ estagio: 'persona', estagios: [{ estagio: 'persona', em: null }] })).toBe('conta');
    expect(estagioDeParada({ estagio: 'resultado_verificado', estagios: [] })).toBeNull();
    // o backend manda onde parou (há estágios que só existem se a Aprendizado marcar): vale o dele, não o seguinte calculado
    expect(estagioDeParada({ estagio: 'persona', estagios: [], parou_em: 'sessao' })).toBe('sessao');
    expect(lerAlvo({ profile_id: 'p', estagio: 'persona', estado: 'bloqueado', parou_em: 'conta' }, 0)!.parou_em).toBe('conta');
  });

  it('verificação: só `verificada: true` conta; tentada sem prova é "não verificada"; sem ação final não há o que verificar', () => {
    const com = (verificada: boolean | null) => ({ resultado: { texto: null, conhecimento_ids: [], evidencia_id: null, acao_final: { tipo: 'CREATE_COMMENT', verificada, evidencia_id: null } } });
    expect(verificacaoDoAlvo(com(true))).toBe('verificada');
    expect(verificacaoDoAlvo(com(false))).toBe('nao_verificada');
    expect(verificacaoDoAlvo(com(null))).toBe('nao_verificada');
    expect(verificacaoDoAlvo({ resultado: null })).toBe('sem_acao');
  });

  it('o agregado por app separa concluído de verificado', () => {
    const op = lerOperacao(OPERACAO_DE_EXEMPLO, true)!;
    expect(agregadoPorApp(op.alvos, op.app_id)).toEqual([{ app: 'com.instagram.android', alvos: 30, concluidos: 3, verificados: 2, bloqueados: 27 }]);
    expect(contarPorEstado(op.alvos)).toMatchObject({ concluido: 3, bloqueado: 27, em_curso: 0 });
  });
});

describe('sem a rota no central (exemplo)', () => {
  it('a lista avisa que é exemplo e leva à operação', async () => {
    await ir([]);
    await waitFor(() => expect(text(byRole('status', /Dados de exemplo/, container))).toContain('adendo v1.94'));
    const link = byRole('link', /Ler o post da loja/, container) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('#/operacoes/op-exemplo');
    expect(text(container)).toContain('30 solicitados');
  });

  it('a faixa de capacidade mostra o déficit e os motivos, sem esconder', async () => {
    await abrirExemplo();
    const faixa = container.querySelector('section[aria-labelledby="operacao-capacidade"]') as HTMLElement;
    const valores = Array.from(faixa.querySelectorAll('div > dd')).map((d) => `${d.textContent} ${d.parentElement!.querySelector('dt')!.textContent}`);
    expect(valores).toEqual(['30 solicitados', '5 contas existentes', '4 sessões válidas', '4 disponíveis', '0 em curso', '3 concluídas', '27 bloqueadas']);
    expect(text(faixa)).toContain('25 sem conta');
    expect(text(faixa)).toContain('1 limite de ações executadas');
  });

  it('uma linha por agente (30): persona, conta, aparelho, estado e o motivo do bloqueio; sem conta fica "sem conta"', async () => {
    await abrirExemplo();
    const um = linhas()[0]!;
    expect(text(um)).toContain('Persona 01');
    expect(text(um)).toContain('@exemplo_01');
    expect(text(um)).toContain('android-04');
    expect(text(um)).toContain('Concluído');
    expect(text(um)).toContain('Verificada');
    expect(text(linhas()[3]!)).toContain('Parou em Ação executada: limite de ações executadas');
    const sem = linhas()[10]!;
    expect(text(sem)).toContain('sem conta');
    expect(text(sem)).toContain('Parou em Conta: sem conta');
    expect(text(sem)).toContain('sem aparelho');
  });

  it('concluído sem prova aparece como "Não verificada", nunca como verificada', async () => {
    await abrirExemplo();
    const tres = linhas()[2]!;
    expect(text(tres)).toContain('Concluído');
    expect(text(tres)).toContain('Não verificada');
    expect(text(tres)).not.toMatch(/(^|[^o] )Verificada/);
  });

  it('o pipeline fala em palavras: quantos estágios e onde parou', async () => {
    await abrirExemplo();
    expect(byRole('img', /14 de 14 estágios; último: “Resultado verificado”/, linhas()[0]!)).toBeTruthy();
    expect(byRole('img', /12 de 14 estágios; parou em “Ação executada”/, linhas()[3]!)).toBeTruthy();
    expect(byRole('img', /1 de 14 estágios; parou em “Conta”/, linhas()[10]!)).toBeTruthy();
  });

  it('o detalhe do agente mostra o texto gerado, o conhecimento, as evidências e os estágios; fecha de novo', async () => {
    await abrirExemplo();
    await click(byRole('button', /^Abrir o detalhe de Persona 01$/, container));
    const d = container.querySelector('tbody tr:nth-child(2) td[colspan]') as HTMLElement;
    expect(text(d)).toContain('Ficou ótimo, parabéns pelo lançamento!');
    expect(text(d)).toContain('Ação final: Comentário, verificada.');
    expect(text(d)).toContain('Conhecimento usado (2)');
    expect(text(d)).toContain('fluxo:comentar-no-post');
    expect(allByRole('link', /^evidência /, d).map((a) => a.getAttribute('href'))).toEqual([
      '#/execucoes/r-exemplo-01?aba=evidencias', '#/execucoes/r-exemplo-01?aba=evidencias',
    ]);
    expect(text(d)).toContain('Resultado verificado');
    await click(byRole('button', /^Fechar o detalhe de Persona 01$/, container));
    expect(container.querySelector('tbody tr:nth-child(2) td[colspan]')).toBeNull();
  });

  it('filtra por estado e por estágio de parada', async () => {
    await abrirExemplo();
    await setValue(byRole('combobox', /^Estado/, container) as HTMLSelectElement, 'concluido');
    expect(linhas()).toHaveLength(3);
    await setValue(byRole('combobox', /^Estado/, container) as HTMLSelectElement, '');
    await setValue(byRole('combobox', /^Parou em/, container) as HTMLSelectElement, 'sessao');
    expect(linhas()).toHaveLength(1);
    expect(text(linhas()[0]!)).toContain('Persona 05');
    await setValue(byRole('combobox', /^Parou em/, container) as HTMLSelectElement, 'target_localizado');
    expect(container.textContent).toContain('Nenhum agente com este filtro');
  });

  it('o agregado por app e o cancelar desligado ("É um exemplo")', async () => {
    await abrirExemplo();
    expect(text(container.querySelector('section[aria-labelledby="operacao-por-app"]') as HTMLElement))
      .toContain('com.instagram.android: 30 alvos, 3 concluídos, 2 verificados, 27 bloqueados');
    expect(byRole('button', /^Cancelar a operação — indisponível: É um exemplo/, container).getAttribute('aria-disabled')).toBe('true');
  });
});

describe('com a rota no central', () => {
  const OPERACAO = {
    id: 'op-1', command: 'Comentar no post da loja', app_id: 'com.instagram.android', acao_final: 'preparar', status: 'em_curso',
    created_at: '2026-10-06T17:00:00Z', finished_at: null,
    max_usd: 1.5, assunto: 'A embalagem nova da loja.', fontes: ['https://exemplo.com.br/a', 'http://exemplo.com.br/b', 'https://u:p@exemplo.com.br/c', 'https://exemplo.com.br/d?x=1'],
    custo: { pesquisa_usd: 0.01, alvos_usd: 0.02, total_usd: 0.03 },
    capacidade: { solicitados: 2, contas_existentes: 2, sessoes_validas: 2, contas_disponiveis: 2, concluidas: 0, bloqueadas: 0, em_curso: 2, motivos: {} },
    alvos: [
      { profile_id: 'p1', persona_nome: 'Ana', app_id: 'com.instagram.android', account_id: 'a1', conta: '@ana', instance_id: 'android-04', run_id: 'r1',
        estagio: 'conteudo_lido', estagios: [{ estagio: 'persona', em: null }], estado: 'em_curso', motivo: null, resultado: null },
      { profile_id: 'p2', persona_nome: 'Bia', app_id: 'com.instagram.android', account_id: 'a2', conta: '@bia', instance_id: 'android-05', run_id: 'r2',
        estagio: 'conta', estagios: [], estado: 'pendente', motivo: null, resultado: null },
    ],
  };

  it('lê a operação real: sem aviso de exemplo, e o cancelar pede confirmação e chama a rota', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(OPERACAO));
    backend.on('POST', /^\/api\/operacoes\/op-1\/cancelar$/, () => json({ ...OPERACAO, status: 'cancelada' }));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    expect(container.textContent).not.toContain('Dados de exemplo');
    expect(text(linhas()[0]!)).toContain('Ana');
    expect(text(linhas()[0]!)).toContain('Em andamento');
    expect(text(linhas()[1]!)).toContain('Na fila');
    await click(byRole('button', /^Cancelar a operação/, container));
    const d1 = await waitFor(() => byRole('dialog', /Cancelar a operação\?/));
    await click(byRole('button', /^Voltar$/, d1));
    expect(backend.callsTo('POST', /cancelar$/)).toHaveLength(0);          // "Voltar" não cancela
    await click(byRole('button', /^Cancelar a operação/, container));
    const d2 = await waitFor(() => byRole('dialog', /Cancelar a operação\?/));
    await click(byRole('button', /^Cancelar a operação$/, d2));
    await waitFor(() => expect(backend.callsTo('POST', /cancelar$/)).toHaveLength(1));
  });

  const PREPARADO = (p: string, nome: string, texto: string) => ({ ...OPERACAO.alvos[1], profile_id: p, persona_nome: nome, estado: 'bloqueado', motivo: 'limite de ações executadas',
    estagio: 'acao_preparada', estagios: [], resultado: { texto, conhecimento_ids: [], evidencia_id: null, acao_final: null } });

  it('mostra o custo (total, teto e a divisão), o assunto e as fontes; só vira link a que o backend aceita (https, sem usuário nem query)', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(OPERACAO));
    await ir(['op-1']);
    const faixa = await waitFor(() => {
      const e = container.querySelector('section[aria-label="Custo e assunto"]');
      if (!e) throw new Error('a faixa de custo ainda não apareceu');
      return e as HTMLElement;
    });
    expect(text(faixa)).toContain('Custo de IA US$ 0,0300 de um teto de US$ 1,5000');
    expect(text(faixa)).toContain('pesquisa US$ 0,0100 · agentes US$ 0,0200');
    expect(text(faixa)).toContain('Assunto: A embalagem nova da loja.');
    const links = Array.from(faixa.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(links).toEqual(['https://exemplo.com.br/a']);
    expect(text(faixa)).toContain('http://exemplo.com.br/b');          // as outras aparecem como texto, sem link
    expect(text(faixa)).toContain('https://u:p@exemplo.com.br/c');
  });

  it('a tabela mostra o custo de IA de cada agente (do alvo; do resultado só como reserva) e "—" sem execução', async () => {
    const alvos = [{ ...OPERACAO.alvos[0], custo_usd: 0.0123 }, { ...OPERACAO.alvos[1], custo_usd: undefined, resultado: { texto: 'x', custo_usd: 0.5 } }, { ...OPERACAO.alvos[1], profile_id: 'p3', run_id: 'r3' }];
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, alvos }));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(3));
    expect(container.querySelector('thead')!.textContent).toContain('Custo de IA');
    const celulas = linhas().map((l) => Array.from(l.querySelectorAll('td'))[7]!.textContent);
    expect(celulas).toEqual(['US$ 0,0123', 'US$ 0,5000', '—']);
  });

  it('uma recarga que falha depois de uma carga boa mantém a operação, MOSTRA o erro e deixa tentar de novo', async () => {
    let falhar = false;
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => (falhar ? apiError(500, 'erro_interno', 'quebrou') : json(OPERACAO)));
    backend.on('POST', /^\/api\/operacoes\/op-1\/cancelar$/, () => { falhar = true; return json({ ...OPERACAO, status: 'cancelada' }); });
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    expect(text(container)).not.toContain('Mostrando a última leitura');
    await click(byRole('button', /^Cancelar a operação/, container));
    const d = await waitFor(() => byRole('dialog', /Cancelar a operação\?/));
    await click(byRole('button', /^Cancelar a operação$/, d));
    await waitFor(() => expect(text(container)).toContain('Mostrando a última leitura'));
    expect(linhas()).toHaveLength(2);                                  // a leitura anterior segue na tela, avisada como velha
    expect(text(container).split('Mostrando a última leitura')).toHaveLength(2);   // um aviso só, não dois
    falhar = false;
    await click(byRole('button', /Tentar de novo/, container));
    await waitFor(() => expect(text(container)).not.toContain('Mostrando a última leitura'));
  });

  it('a lista sem `items` (formato inesperado) é erro de leitura, não "Nenhuma operação ainda"', async () => {
    backend.on('GET', /^\/api\/operacoes$/, () => json({ itens: [] }));
    await ir([]);
    await waitFor(() => expect(text(container)).toContain('Não foi possível'));
    expect(text(container)).not.toContain('Nenhuma operação ainda');
  });

  it('a operação sem a lista de alvos é erro de leitura, não "0 agentes"', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, alvos: undefined }));
    await ir(['op-1']);
    await waitFor(() => expect(text(container)).toContain('Não foi possível'));
    expect(linhas()).toHaveLength(0);
    expect(allByRole('button', /^Relatório$/, container)).toHaveLength(0);
  });

  it('custo parcial: a parte que falta aparece como "não informada", nunca como US$ 0,0000', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, custo: { total_usd: 0.03 } }));
    await ir(['op-1']);
    const faixa = await waitFor(() => {
      const e = container.querySelector('section[aria-label="Custo e assunto"]');
      if (!e) throw new Error('a faixa de custo ainda não apareceu');
      return e as HTMLElement;
    });
    expect(text(faixa)).toContain('US$ 0,0300');
    expect(text(faixa)).toContain('pesquisa não informada · agentes não informado');
    expect(text(faixa)).not.toContain('0,0000');
  });

  it('sem custo, teto, assunto nem fontes a faixa não aparece', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, custo: undefined, max_usd: undefined, assunto: undefined, fontes: undefined }));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    expect(container.querySelectorAll('section[aria-label="Custo e assunto"]')).toHaveLength(0);
  });

  it('"Liberar": mostra os textos parados, começa tudo desmarcado, respeita o limite que sobra e envia exatamente o texto lido', async () => {
    useAppStore.setState({ settings: { ...SETTINGS, operacao_max_acoes_executadas: 2 } });
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, alvos: [OPERACAO.alvos[0], PREPARADO('p2', 'Bia', 'Texto da Bia.'), PREPARADO('p3', 'Caio', 'Texto do Caio.'), PREPARADO('p4', 'Dani', 'Texto da Dani.')] }));
    backend.on('POST', /^\/api\/operacoes\/op-1\/liberar$/, () => json({ liberados: ['p2', 'p3'], recusados: [], operacao: OPERACAO }));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(4));
    await click(byRole('button', /^Liberar$/, container));
    const d = await waitFor(() => byRole('dialog', /Liberar as ações paradas\?/));
    expect(text(d)).toContain('Texto da Bia.');
    expect(text(d)).toContain('Ainda cabem 2 contas');
    const caixas = allByRole('checkbox', /./, d) as HTMLInputElement[];
    expect(caixas.map((c) => c.checked)).toEqual([false, false, false]);                    // nenhuma aprovação automática
    expect(byRole('button', /^Liberar — indisponível: Marque ao menos um agente/, d).getAttribute('aria-disabled')).toBe('true');
    await click(caixas[0]!);
    await click(caixas[1]!);
    expect(caixas[2]!.disabled).toBe(true);                                                  // o limite que sobra
    await click(byRole('button', /^Liberar 2$/, d));
    await waitFor(() => expect(backend.callsTo('POST', /liberar$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /liberar$/)[0]!.body).toEqual({ itens: [{ profile_id: 'p2', texto: 'Texto da Bia.' }, { profile_id: 'p3', texto: 'Texto do Caio.' }] });
  });

  it('"Liberar": a decisão é item a item: o recusado (texto mudou) é avisado com o motivo e a operação é relida', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, alvos: [OPERACAO.alvos[0], PREPARADO('p2', 'Bia', 'Texto da Bia.')] }));
    backend.on('POST', /^\/api\/operacoes\/op-1\/liberar$/, () => json({ liberados: [], recusados: [{ profile_id: 'p2', motivo: 'texto_divergente' }], operacao: OPERACAO }));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    await click(byRole('button', /^Liberar$/, container));
    const d = await waitFor(() => byRole('dialog', /Liberar as ações paradas\?/));
    await click(allByRole('checkbox', /./, d)[0]!);
    await click(byRole('button', /^Liberar 1$/, d));
    await waitFor(() => expect(backend.callsTo('GET', /operacoes\/op-1$/).length).toBeGreaterThan(1));
    const aviso = useToastStore.getState().toasts.find((x) => x.title === 'Nada foi liberado');
    expect(aviso?.message).toContain('o texto mudou depois que você o leu');
    expect(container.querySelector('dialog[open]')).toBeNull();
  });

  const FEITO = (p: string, nome: string, verificada: boolean | null) => ({ ...OPERACAO.alvos[0], profile_id: p, persona_nome: nome, estado: 'concluido', estagio: 'resultado_verificado', estagios: [],
    resultado: { texto: `Texto de ${nome}.`, conhecimento_ids: [], evidencia_id: 9, acao_final: { tipo: 'CREATE_COMMENT', verificada, evidencia_id: 10 } } });

  it('o cabeçalho diz quantas ações a operação JÁ executou e quantas foram verificadas, também com o modo "Só preparar" (depois da liberação)', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, status: 'concluida', alvos: [FEITO('p1', 'Ana', true), FEITO('p2', 'Bia', false), PREPARADO('p3', 'Caio', 'Texto do Caio.')] }));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(3));
    const cab = container.querySelector('[data-acoes-feitas]') as HTMLElement;
    expect(text(cab)).toBe('· 2 ações executadas, 1 verificada');
    expect(text(container)).toContain('Ação final: Só preparar');                    // o modo da operação segue como o backend o mandou
  });

  it('singular e sem nada executado: uma ação aparece no singular, e o cabeçalho não afirma nada quando nenhum alvo executou', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, alvos: [FEITO('p1', 'Ana', true), { ...FEITO('p9', 'Zeca', true), estado: 'cancelado' }, OPERACAO.alvos[1]] }));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(3));
    expect(text(container.querySelector('[data-acoes-feitas]') as HTMLElement)).toBe('· 1 ação executada, 1 verificada');      // o alvo cancelado depois não conta como executado
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, alvos: [PREPARADO('p3', 'Caio', 'Texto do Caio.'), OPERACAO.alvos[1]] }));
    await ir([]);
    await ir(['op-1']);
    await waitFor(() => expect(text(container)).toContain('Caio'));
    expect(container.querySelector('[data-acoes-feitas]')).toBeNull();               // texto preparado e parado não é ação executada
  });

  it('liberar numa operação que o "preparar" já fechou a REABRE: a tela e o relatório seguem o estado de agora (idempotente, sem eventos)', async () => {
    const fechada = { ...OPERACAO, status: 'concluida', finished_at: '2026-10-06T17:30:00Z', alvos: [OPERACAO.alvos[0], PREPARADO('p2', 'Bia', 'Texto da Bia.')] };
    const reaberta = { ...fechada, status: 'em_curso', finished_at: null, acao_final: 'executar' };
    let liberou = false;
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(liberou ? reaberta : fechada));
    backend.on('POST', /^\/api\/operacoes\/op-1\/liberar$/, () => { liberou = true; return json({ liberados: ['p2'], recusados: [], operacao: reaberta }); });
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => apiError(404, 'not_found', 'sem rota'));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    // fechada: o Liberar segue ligado (há texto esperando) e o Cancelar fica desligado
    expect(text(container)).toContain('Concluída');
    expect(byRole('button', /^Cancelar a operação — indisponível: A operação já terminou/, container).getAttribute('aria-disabled')).toBe('true');
    await click(byRole('button', /^Liberar$/, container));
    const d = await waitFor(() => byRole('dialog', /Liberar as ações paradas\?/));
    await click(allByRole('checkbox', /./, d)[0]!);
    await click(byRole('button', /^Liberar 1$/, d));
    // reaberta: o cabeçalho passa a "Em andamento", o Cancelar liga, e há UM selo de estado (nada duplicado)
    await waitFor(() => expect(byRole('button', /^Cancelar a operação$/, container).getAttribute('aria-disabled')).not.toBe('true'));
    expect(text(container)).toContain('Em andamento');
    expect(text(container)).not.toContain('Concluída');
    expect(text(container).split('Ação final: Preparar e executar')).toHaveLength(2);
    expect(backend.callsTo('POST', /liberar$/)).toHaveLength(1);
    // o relatório da operação reaberta: "em aberto", sem encerramento
    const { montarRelatorio, relatorioEmMarkdown } = await import('./relatorio');
    const r = montarRelatorio(lerOperacao(reaberta)!);
    expect(r.operacao.encerrada_em).toBeNull();
    expect(relatorioEmMarkdown(r)).toContain('**Encerrada em:** em aberto');
  });

  it('"Liberar" sem ninguém esperando fica desligado, com o motivo', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(OPERACAO));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    expect(byRole('button', /^Liberar — indisponível: Nenhum agente espera a liberação/, container).getAttribute('aria-disabled')).toBe('true');
  });

  it('operação inexistente (404 operacao_inexistente) é erro, nunca o exemplo; o status encerrado desliga o cancelar', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-x$/, () => apiError(404, 'operacao_inexistente', 'Operação inexistente.'));
    await ir(['op-x']);
    await waitFor(() => expect(container.textContent).toContain('operação'));
    expect(container.textContent).not.toContain('Dados de exemplo');
    expect(linhas()).toHaveLength(0);
  });

  it('a lista lê o `items` da rota real', async () => {
    backend.on('GET', /^\/api\/operacoes$/, () => json({ items: [{ ...OPERACAO, alvos: undefined }] }));
    await ir([]);
    await waitFor(() => expect(text(container)).toContain('Comentar no post da loja'));
    expect(text(container)).toContain('Em andamento · 2 solicitados');
    expect(container.textContent).not.toContain('Dados de exemplo');
  });
});

describe('o que a tela nunca mostra', () => {
  it('sem credencial, e-mail, código cru nem valor quebrado, no texto e nos rótulos de leitura de tela', async () => {
    await abrirExemplo();
    await click(byRole('button', /^Abrir o detalhe de Persona 01$/, container));
    const t = `${text(container)}\n${Array.from(container.querySelectorAll('[aria-label],[title]')).map((e) => `${e.getAttribute('aria-label') ?? ''} ${e.getAttribute('title') ?? ''}`).join('\n')}`;
    expect(t).not.toMatch(/senha|password|token|credencial|login_identifier|[\w.+-]+@[\w-]+\.[a-z]{2,}/i);
    expect(t).not.toMatch(/\b(undefined|null|NaN)\b|\[object Object\]/);
    expect(t).not.toMatch(/\b(em_curso|pendente|acao_preparada|interface_de_comentario_alcancada|resultado_verificado|conhecimento_ids|evidencia_id)\b/);
  });
});

describe('31.225: o laço do sistema na lista de operações', () => {
  const listaVazia = () => backend.on('GET', /^\/api\/operacoes$/, () => json({ items: [] }));
  const indicador = () => container.querySelector('[data-laco-do-sistema]');

  it('desligado (0): diz desligado e que a operação só anda com a tela aberta', async () => {
    listaVazia();
    useAppStore.setState({ settings: { ...SETTINGS, operacao_laco_s: 0 } });
    await ir([]);
    await waitFor(() => expect(indicador()).not.toBeNull());
    expect(indicador()!.getAttribute('data-laco-do-sistema')).toBe('desligado');
    expect(text(indicador()!)).toContain('Laço do sistema: desligado.');
    expect(text(indicador()!)).toContain('só avança quando alguém abre a tela dela');
  });

  it('ligado: diz de quanto em quanto tempo e que anda sozinho', async () => {
    listaVazia();
    useAppStore.setState({ settings: { ...SETTINGS, operacao_laco_s: 15 } });
    await ir([]);
    await waitFor(() => expect(indicador()).not.toBeNull());
    expect(indicador()!.getAttribute('data-laco-do-sistema')).toBe('ligado');
    expect(text(indicador()!)).toContain('Laço do sistema: ligado, a cada 15 s.');
  });

  it('central anterior (sem o campo): não afirma ligado nem desligado', async () => {
    listaVazia();
    const sem = { ...SETTINGS } as Record<string, unknown>;
    delete sem.operacao_laco_s;
    useAppStore.setState({ settings: sem as unknown as typeof SETTINGS });
    await ir([]);
    await waitFor(() => expect(text(container)).toContain('Nenhuma operação ainda'));
    expect(indicador()).toBeNull();
  });
});

describe('31.228: modelos e custo no detalhe do agente', () => {
  const OP = {
    id: 'op-m', command: 'Comentar no post', app_id: 'instagram', acao_final: 'executar', status: 'em_curso', created_at: '2026-10-07T10:00:00Z', finished_at: null,
    capacidade: { solicitados: 2, contas_existentes: 2, sessoes_validas: 2, contas_disponiveis: 2, concluidas: 0, bloqueadas: 0, em_curso: 2, motivos: {} },
    alvos: [
      { profile_id: 'p1', persona_nome: 'Ana', app_id: 'instagram', account_id: 'a1', conta: 'c1', instance_id: 'android-01', run_id: 'r-m1', estagio: 'conteudo_lido', estagios: [], estado: 'em_curso', motivo: null, resultado: null },
      { profile_id: 'p2', persona_nome: 'Bia', app_id: 'instagram', account_id: 'a2', conta: 'c2', instance_id: 'android-03', run_id: null, estagio: 'conta', estagios: [], estado: 'bloqueado', motivo: 'sem sessão', resultado: null },
    ],
  };
  const GRUPO = (role: string, model: string, tier: 0 | 1, calls: number, usd: number | null, errors = 0) =>
    ({ role, model, tier, calls, fresh: 1, cache_read: 0, cache_write: 0, output: 1, with_image: 0, errors, avg_ms: 10, usd });
  const USO = (groups: unknown[], total = 0.12) => ({ scope: { run_id: 'r-m1', days: null }, groups, total_usd: total, objectives_with_ai: 1, calls_per_objective: 5, usd_per_objective: total, steps_driven_by: {}, unpriced_models: [] });
  const abrirAlvo = async (nome: string) => {
    backend.on('GET', /^\/api\/operacoes\/op-m$/, () => json(OP));
    await ir(['op-m']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    await click(byRole('button', new RegExp(`^Abrir o detalhe de ${nome}$`), container));
  };

  it('modelosLidos: soma por função, o total só do que tem preço (parcial) e a divisão das decisões entre os modelos', () => {
    const m = modelosDoAlvo({ groups: [GRUPO('verify', 'claude-opus', 1, 1, 0.05), GRUPO('decide', 'claude-sonnet', 0, 6, 0.04), GRUPO('decide', 'claude-opus', 1, 2, 0.06), GRUPO('plan', 'claude-sonnet', 0, 1, null)] as never });
    expect(m.linhas.map((l) => `${l.funcao}:${l.modelo}`)).toEqual(['plan:claude-sonnet', 'decide:claude-opus', 'decide:claude-sonnet', 'verify:claude-opus']);   // Planejar, Decidir, Verificar
    expect(m.somas.find((s) => s.funcao === 'decide')).toMatchObject({ chamadas: 8, parcial: false });
    expect(m.somas.find((s) => s.funcao === 'decide')!.usd).toBeCloseTo(0.1, 6);
    expect(m.somas.find((s) => s.funcao === 'plan')).toMatchObject({ usd: null, parcial: false });         // modelo sem preço: nunca zero
    expect(m.usd).toBeCloseTo(0.15, 6);
    expect(m.parcial).toBe(true);
    expect(m.chamadas).toBe(10);
    expect(m.decisoes).toEqual([{ modelo: 'claude-sonnet', chamadas: 6, parte: 0.75 }, { modelo: 'claude-opus', chamadas: 2, parte: 0.25 }]);
    expect(modelosDoAlvo({ groups: [] as never })).toMatchObject({ linhas: [], usd: null, chamadas: 0, decisoes: [] });
    expect(modelosDoAlvo({ groups: [{ role: 3 }, null, GRUPO('decide', 'm', 0, -2, Number.NaN)] as never }).linhas).toMatchObject([{ chamadas: 0, usd: null }]);   // campo torto
  });

  it('abrir o detalhe lê GET /api/usage?run_id= desse agente e mostra função, modelo, chamadas, custo e a divisão das decisões', async () => {
    backend.on('GET', /^\/api\/usage$/, () => json(USO([GRUPO('decide', 'claude-sonnet-5-5', 0, 6, 0.04), GRUPO('decide', 'claude-opus-5-5', 1, 2, 0.06, 1), GRUPO('verify', 'claude-opus-5-5', 0, 1, 0.02)], 0.12)));
    await abrirAlvo('Ana');
    const bloco = await esperarElemento('[data-modelos-do-alvo]', container);
    expect(backend.callsTo('GET', /^\/api\/usage$/)[0]!.query.get('run_id')).toBe('r-m1');
    expect(text(bloco)).toContain('claude-sonnet-5-5');
    expect(text(bloco.querySelector('tr[data-funcao="decide"]:nth-child(1)')!)).toContain('claude-opus-5-5');
    expect(text(bloco)).toContain('escalonado');
    expect(text(bloco)).toContain('(1 com erro)');
    expect(text(bloco.querySelector('[data-soma-da-funcao="decide"]')!)).toContain('8');
    expect(text(bloco.querySelector('[data-soma-total]')!)).toContain('US$ 0,12');
    expect(text(bloco.querySelector('[data-divisao-das-decisoes]')!)).toBe('Quem decidiu: claude-sonnet-5-5 6 (75%) · claude-opus-5-5 2 (25%).');
    expect(bloco.textContent).not.toMatch(/\bdecide\b|\bverify\b/);                          // nenhuma função em código cru
  });

  it('só um modelo decidiu: não há divisão a mostrar; sem chamada de IA, diz isso; sem execução, nada é lido', async () => {
    backend.on('GET', /^\/api\/usage$/, () => json(USO([GRUPO('decide', 'claude-sonnet-5-5', 0, 3, 0.03)])));
    await abrirAlvo('Ana');
    const bloco = await esperarElemento('[data-modelos-do-alvo]', container);
    expect(bloco.querySelector('[data-divisao-das-decisoes]')).toBeNull();
    await click(byRole('button', /^Fechar o detalhe de Ana$/, container));
    backend.on('GET', /^\/api\/usage$/, () => json(USO([], 0)));
    await click(byRole('button', /^Abrir o detalhe de Ana$/, container));
    await esperarElemento('[data-modelos-vazio]', container);
    const antes = backend.callsTo('GET', /^\/api\/usage$/).length;
    await click(byRole('button', /^Abrir o detalhe de Bia$/, container));
    await waitFor(() => expect(text(container)).toContain('Este agente não tem execução'));
    expect(backend.callsTo('GET', /^\/api\/usage$/)).toHaveLength(antes);
  });

  it('falha ao ler o uso: o detalhe segue, com o aviso; nunca zero', async () => {
    backend.on('GET', /^\/api\/usage$/, () => apiError(500, 'erro_interno', 'Falhou.'));
    await abrirAlvo('Ana');
    const aviso = await esperarElemento('[data-modelos-erro]', container);
    expect(text(aviso)).toContain('Não foi possível ler os modelos desta execução');
    expect(text(container)).toContain('Resposta gerada');
  });
});

describe('31.228 (parte 2): a linha do tempo do agente por passo (v1.124, 31.229)', () => {
  const PASSOS = {
    passos: [
      { step_id: 's1', seq: 1, key: 'open_profile', capability: 'OPEN_PROFILE', efeito: false, estagio: 'target_localizado', modelo: 'claude-sonnet-5-5', commit: null,
        chamadas: 3, custo_usd: 0.0123, por_modelo: [{ modelo: 'claude-sonnet-5-5', chamadas: 3, custo_usd: 0.0123 }] },
      { step_id: 's2', seq: 2, key: 'comentar', capability: 'COMMENT', efeito: true, estagio: 'acao_preparada', modelo: 'claude-sonnet-5-5',
        commit: { fonte: 'ai', modelo: 'claude-opus-5-5', tier: 1, escalate: 'efeito' }, chamadas: 4, custo_usd: 0.11, por_modelo: [{ modelo: 'claude-sonnet-5-5', chamadas: 3, custo_usd: 0.03 }, { modelo: 'claude-opus-5-5', chamadas: 1, custo_usd: 0.08 }] },
      { step_id: 's3', seq: 3, key: 'ver', capability: null, efeito: false, estagio: null, modelo: null, commit: null, chamadas: 0, custo_usd: 0, por_modelo: [] },
      { step_id: 's4', seq: 4, key: 'curtir', capability: 'LIKE', efeito: true, estagio: 'nova_etapa_do_futuro', modelo: 'claude-haiku-4-5-20251001', commit: { fonte: 'recipe', modelo: null, tier: null, escalate: null }, chamadas: 1, custo_usd: null, por_modelo: [] },
      { step_id: 's5', seq: 5, key: 'enviar', capability: 'SEND', efeito: true, estagio: null, modelo: 'claude-sonnet-5-5', commit: null, chamadas: 1, custo_usd: 0.01, por_modelo: [] },
    ],
    sem_passo: { chamadas: 1, custo_usd: 0.004, por_modelo: [{ modelo: 'claude-sonnet-5-5', chamadas: 1, custo_usd: 0.004 }] },
    por_modelo: [{ modelo: 'claude-sonnet-5-5', chamadas: 8, custo_usd: 0.0563 }, { modelo: 'claude-opus-5-5', chamadas: 1, custo_usd: 0.08 }],
    por_estagio: { target_localizado: { chamadas: 3, custo_usd: 0.0123 }, acao_preparada: { chamadas: 4, custo_usd: 0.11 }, sem_estagio: { chamadas: 2, custo_usd: 0.01 }, sem_passo: { chamadas: 1, custo_usd: 0.004 } },
  };
  const OP = {
    id: 'op-p', command: 'Comentar no post', app_id: 'instagram', acao_final: 'executar', status: 'em_curso', created_at: '2026-10-07T10:00:00Z', finished_at: null,
    capacidade: { solicitados: 2, contas_existentes: 2, sessoes_validas: 2, contas_disponiveis: 2, concluidas: 0, bloqueadas: 0, em_curso: 2, motivos: {} },
    custo: { pesquisa_usd: 0.01, alvos_usd: 0.14, total_usd: 0.15 }, max_usd: 2,
    custo_por_modelo: [{ modelo: 'claude-sonnet-5-5', chamadas: 14, custo_usd: 0.09 }, { modelo: 'claude-opus-5-5', chamadas: 2, custo_usd: 0.16 }],
    custo_por_estagio: { acao_preparada: { chamadas: 6, custo_usd: 0.2 }, sem_passo: { chamadas: 2, custo_usd: 0.01 } },
    alvos: [
      { profile_id: 'p1', persona_nome: 'Ana', app_id: 'instagram', account_id: 'a1', conta: 'c1', instance_id: 'android-01', run_id: 'r-p1', estagio: 'acao_preparada', estagios: [], estado: 'em_curso', motivo: null, resultado: null, custo_por_passo: PASSOS },
      { profile_id: 'p2', persona_nome: 'Bia', app_id: 'instagram', account_id: 'a2', conta: 'c2', instance_id: 'android-03', run_id: 'r-p2', estagio: 'conta', estagios: [], estado: 'em_curso', motivo: null, resultado: null, custo_por_passo: null },
    ],
  };
  const abrir = async (nome: string, op: unknown = OP) => {
    backend.on('GET', /^\/api\/operacoes\/op-p$/, () => json(op));
    await ir(['op-p']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    await click(byRole('button', new RegExp(`^Abrir o detalhe de ${nome}$`), container));
  };

  it('os leitores: o exemplo do contrato; um passo inválido invalida o objeto; a chave ausente não entra, a nula vira null', () => {
    const c = lerCustoPorPasso(PASSOS)!;
    expect(c.passos.map((p) => p.chave)).toEqual(['open_profile', 'comentar', 'ver', 'curtir', 'enviar']);
    expect(c.passos[1]!.commit).toEqual({ fonte: 'ai', modelo: 'claude-opus-5-5', tier: 1, escalate: 'efeito' });
    expect(c.passos[3]!.custoUsd).toBeNull();                                                   // sem preço: nunca zero
    expect(c.semPasso).toMatchObject({ chamadas: 1, custoUsd: 0.004 });
    expect(c.porEstagio.map((e) => e.estagio)).toEqual(['target_localizado', 'acao_preparada', 'sem_estagio', 'sem_passo']);   // ordem do servidor
    expect(lerCustoPorPasso({ passos: [PASSOS.passos[0], { key: 'sem_step_id' }] })).toBeNull();
    expect(lerCustoPorPasso({ passos: 'x' })).toBeNull();
    expect(lerCustoPorPasso(null)).toBeNull();
    const base = { profile_id: 'p', run_id: 'r' };
    expect('custo_por_passo' in lerAlvo(base, 0)!).toBe(false);
    expect(lerAlvo({ ...base, custo_por_passo: null }, 0)!.custo_por_passo).toBeNull();
    expect(lerAlvo({ ...base, custo_por_passo: PASSOS }, 0)!.custo_por_passo!.passos).toHaveLength(5);
    const op = lerOperacao({ id: 'o', command: 'c', alvos: [], custo_por_modelo: OP.custo_por_modelo, custo_por_estagio: OP.custo_por_estagio })!;
    expect(op.custo_por_modelo).toHaveLength(2);
    expect(op.custo_por_estagio).toHaveLength(2);
    expect('custo_por_modelo' in lerOperacao({ id: 'o', command: 'c', alvos: [] })!).toBe(false);
  });

  it('os rótulos: modelo em palavras (o cru no title), estágio, e quem executou o commit', () => {
    expect(rotuloDoModelo('claude-sonnet-5-5')).toBe('Sonnet 5.5');
    expect(rotuloDoModelo('claude-opus-5')).toBe('Opus 5');
    expect(rotuloDoModelo('claude-haiku-4-5-20251001')).toBe('Haiku 4.5');
    expect(rotuloDoModelo('gpt-novo')).toBe('gpt-novo');                                        // fora do padrão: como veio
    expect(rotuloDoEstagioDoCusto('acao_preparada')).toBe('Ação preparada');
    expect(rotuloDoEstagioDoCusto('sem_estagio')).toBe('Sem estágio');
    expect(rotuloDoEstagioDoCusto('sem_passo')).toBe('Planejamento e chamadas sem etapa');
    expect(rotuloDoEstagioDoCusto('estagio_do_futuro')).toBe('estagio_do_futuro');
    const p = (efeito: boolean, commit: unknown) => ({ efeito, commit: commit as never });
    expect(commitEmPalavras(p(true, null))).toBe('não chegou ao commit');
    expect(commitEmPalavras(p(false, null))).toBeNull();
    expect(commitEmPalavras(p(true, { fonte: 'recipe', modelo: null, tier: null, escalate: null }))).toBe('por receita, sem modelo');
    expect(commitEmPalavras(p(true, { fonte: 'ai', modelo: 'claude-opus-5-5', tier: 1, escalate: 'efeito' }))).toBe('Opus 5.5, escalonado pelo efeito (forte)');
    expect(commitEmPalavras(p(true, { fonte: 'ai', modelo: 'claude-sonnet-5-5', tier: 0, escalate: null }))).toBe('Sonnet 5.5');
    expect(commitEmPalavras(p(true, { fonte: 'ai', modelo: null, tier: null, escalate: null }))).toBe('modelo não informado');
  });

  it('o detalhe do agente mostra, passo a passo, o estágio, o modelo que decidiu, o commit, as chamadas e o custo; sem GET /api/usage', async () => {
    await abrir('Ana');
    const bloco = await esperarElemento('[data-linha-do-tempo-do-alvo]', container);
    expect(backend.callsTo('GET', /^\/api\/usage$/)).toHaveLength(0);                           // o custo por passo dispensa a segunda leitura
    const linha = (k: string) => bloco.querySelector(`tr[data-passo="${k}"]`) as HTMLElement;
    expect(text(linha('open_profile'))).toContain('1. open_profile');
    expect(text(linha('open_profile'))).toContain('Perfil-alvo localizado');
    expect(text(linha('open_profile'))).toContain('Sonnet 5.5');
    expect(text(linha('open_profile'))).toContain('US$ 0,0123');
    expect(linha('open_profile').getAttribute('data-efeito')).toBe('nao');
    expect(text(linha('comentar'))).toContain('com efeito');
    expect(text(linha('comentar').querySelector('[data-commit]')!)).toBe('Opus 5.5, escalonado pelo efeito (forte)');   // o forte só no commit
    expect(linha('comentar').querySelector('td:nth-child(3) span')!.getAttribute('title')).toBe('claude-sonnet-5-5');
    expect(text(linha('ver'))).toContain('só receita');
    expect(text(linha('curtir').querySelector('[data-commit]')!)).toBe('por receita, sem modelo');
    expect(text(linha('curtir'))).toContain('Haiku 4.5');
    expect(text(linha('curtir'))).toContain('não informado');                                    // sem preço: nunca zero
    expect(text(linha('curtir'))).toContain('nova_etapa_do_futuro');                            // estágio que o painel não conhece: cru
    expect(text(linha('enviar').querySelector('[data-commit]')!)).toBe('não chegou ao commit');
    expect(text(bloco.querySelector('tr[data-sem-passo]')!)).toContain('Planejamento e chamadas sem etapa');
    expect(text(bloco.querySelector('[data-por-modelo]')!)).toBe('Neste agente, por modelo: Sonnet 5.5 8 chamadas, US$ 0,0563 · Opus 5.5 1 chamada, US$ 0,08');
    expect(text(bloco.querySelector('[data-por-estagio]')!)).toContain('Ação preparada 4 chamadas, US$ 0,11');
    expect(text(bloco.querySelector('[data-por-estagio]')!)).toContain('Planejamento e chamadas sem etapa 1 chamada, US$ 0,004');
    expect(bloco.textContent).not.toMatch(/\bsem_estagio\b|\bsem_passo\b|\bclaude-/);           // nenhum id cru à vista (só no title)
  });

  it('alvo sem custo_por_passo (null ou central anterior) cai no uso por função e modelo', async () => {
    backend.on('GET', /^\/api\/usage$/, () => json({ scope: { run_id: 'r-p2', days: null }, groups: [], total_usd: 0, objectives_with_ai: 0, calls_per_objective: 0, usd_per_objective: 0, steps_driven_by: {}, unpriced_models: [] }));
    await abrir('Bia');
    await esperarElemento('[data-modelos-vazio]', container);
    expect(backend.callsTo('GET', /^\/api\/usage$/)[0]!.query.get('run_id')).toBe('r-p2');
  });

  it('a operação mostra o custo somado por modelo e por estágio; sem os campos, a faixa de custo fica como era', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-p$/, () => json(OP));
    await ir(['op-p']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    const faixa = container.querySelector('section[aria-label="Custo e assunto"]') as HTMLElement;
    expect(text(faixa.querySelector('[data-por-modelo]')!)).toBe('Na operação, por modelo: Sonnet 5.5 14 chamadas, US$ 0,09 · Opus 5.5 2 chamadas, US$ 0,16');
    expect(text(faixa.querySelector('[data-por-estagio]')!)).toContain('Ação preparada 6 chamadas, US$ 0,2');
    await act(async () => { root.unmount(); root = createRoot(container); });
    const { custo_por_modelo: _m, custo_por_estagio: _e, ...semSomas } = OP;
    backend.on('GET', /^\/api\/operacoes\/op-p$/, () => json(semSomas));
    await ir(['op-p']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    expect(container.querySelector('[data-somas-do-custo]')).toBeNull();
  });
});

describe('31.234: a pesquisa da operação (reaproveitada do Livro ou paga) e os fatos por alvo', () => {
  const FATOS = [
    { item: 'operation_fact:12', origem: 'pesquisa', frescor_ate: '2026-10-14T12:00:00Z', confianca: 'confirmado' },
    { item: 'operation_fact:15', origem: 'pesquisa', frescor_ate: '2026-10-10T08:30:00Z', confianca: 'hipotese' },
  ];
  const REAPROVEITADA = { estado: 'reaproveitada_do_livro', criterio: 'dois fatos vivos e dentro do frescor cobrem o pedido', minimo_fatos: 2, frescor_ate: '2026-10-10T08:30:00Z', custo_usd: 0, fatos: FATOS };
  const BASE = {
    id: 'op-q', command: 'Comentar no post', app_id: 'instagram', acao_final: 'executar', status: 'em_curso', created_at: '2026-10-07T10:00:00Z', finished_at: null,
    capacidade: { solicitados: 1, contas_existentes: 1, sessoes_validas: 1, contas_disponiveis: 1, concluidas: 0, bloqueadas: 0, em_curso: 1, motivos: {} },
    assunto: 'Lançamento de outubro', custo: { pesquisa_usd: 0, alvos_usd: 0.1, total_usd: 0.1 }, max_usd: 2,
    alvos: [{ profile_id: 'p1', persona_nome: 'Ana', app_id: 'instagram', account_id: 'a1', conta: 'c1', instance_id: 'android-01', run_id: 'r-q1', estagio: 'resposta_gerada', estagios: [], estado: 'em_curso', motivo: null,
      resultado: { texto: 'Ótimo post!', conhecimento_ids: ['fato:livro.operation_fact:12', 'fato:alvo.conteudo', 'fonte:https://exemplo.com.br/a', 'registro:conta', 'fluxo:comentar-no-post'] } }],
  };
  const abrir = async (op: unknown) => {
    backend.on('GET', /^\/api\/operacoes\/op-q$/, () => json(op));
    await ir(['op-q']);
    await waitFor(() => expect(linhas()).toHaveLength(1));
  };
  const secao = () => container.querySelector('section[aria-label="Pesquisa da operação"]') as HTMLElement | null;

  it('os leitores: estado conhecido, estado novo vira "não informado", fato sem item cai, fontes sem repetição; o conhecimento em palavras', () => {
    const r = lerPesquisaDaOperacao({ ...REAPROVEITADA, fatos: [...FATOS, { origem: 'x' }, 'lixo'] })!;
    expect(r).toMatchObject({ estado: 'reaproveitada_do_livro', minimoDeFatos: 2, custoUsd: 0, frescorAte: '2026-10-10T08:30:00Z' });
    expect(r.fatos.map((f) => f.item)).toEqual(['operation_fact:12', 'operation_fact:15']);
    expect(r.fatos[1]!.confianca).toBe('hipotese');
    expect(lerPesquisaDaOperacao({ estado: 'estado_novo', custo_usd: -1 })).toMatchObject({ estado: null, custoUsd: null, fatos: [] });
    expect(lerPesquisaDaOperacao(null)).toBeNull();
    expect(lerPesquisaDaOperacao([])).toBeNull();
    expect(lerFontesDaPesquisa(['https://a.com', 'https://a.com', '', 3])).toEqual(['https://a.com']);
    expect(lerFontesDaPesquisa('x')).toBeNull();
    expect('pesquisa' in lerOperacao({ id: 'o', command: 'c', alvos: [] })!).toBe(false);
    expect(lerOperacao({ id: 'o', command: 'c', alvos: [], pesquisa: REAPROVEITADA, fontes_da_pesquisa: ['https://a.com'] })!.pesquisa!.estado).toBe('reaproveitada_do_livro');
    expect(conhecimentoEmPalavras('fato:livro.operation_fact:12')).toEqual({ tipo: 'Fato do Livro', chave: 'operation_fact:12' });
    expect(conhecimentoEmPalavras('fato:alvo.conteudo')).toEqual({ tipo: 'Fato da operação', chave: 'alvo.conteudo' });
    expect(conhecimentoEmPalavras('fonte:k')).toEqual({ tipo: 'Fonte da pesquisa', chave: 'k' });
    expect(conhecimentoEmPalavras('registro:k')).toEqual({ tipo: 'Registro da operação', chave: 'k' });
    expect(conhecimentoEmPalavras('fluxo:comentar')).toEqual({ tipo: null, chave: 'fluxo:comentar' });
  });

  it('reaproveitada do Livro: diz que não houve chamada paga, o critério, até quando vale e cada fato com origem e frescor', async () => {
    await abrir({ ...BASE, pesquisa: REAPROVEITADA, fontes_da_pesquisa: [] });
    const s = secao()!;
    expect(s.getAttribute('data-pesquisa-da-operacao')).toBe('reaproveitada_do_livro');
    expect(text(s)).toContain('Pesquisa da operação: Reaproveitada do Livro, sem chamada paga');
    expect(text(s)).toContain('custo da pesquisa: US$ 0,0000');
    expect(text(s)).toContain('Critério: dois fatos vivos e dentro do frescor cobrem o pedido (mínimo de 2 fatos).');
    expect(text(s.querySelector('[data-frescor]')!)).toContain('o primeiro fato que vence reabre a pesquisa paga');
    const f12 = s.querySelector('[data-fato-do-livro="operation_fact:12"]') as HTMLElement;
    expect(text(f12)).toContain('origem pesquisa');
    expect(text(f12)).toContain('confirmado');
    expect(text(f12)).toContain('vale até');
    expect(text(s.querySelector('[data-fato-do-livro="operation_fact:15"]')!)).toContain('hipótese');
    expect(s.textContent).not.toMatch(/reaproveitada_do_livro|hipotese/);                      // nenhum código cru
    expect(s.querySelector('[data-sem-estado-da-pesquisa]')).toBeNull();
  });

  it('paga: o custo, o estado em palavras e as fontes que achou (link só se https sem parâmetros)', async () => {
    await abrir({ ...BASE, custo: { pesquisa_usd: 0.043, alvos_usd: 0.1, total_usd: 0.143 },
      pesquisa: { estado: 'paga', custo_usd: 0.043, fatos: [] }, fontes_da_pesquisa: ['https://exemplo.com.br/a', 'https://exemplo.com.br/b?x=1'] });
    const s = secao()!;
    expect(text(s)).toContain('Paga: o Livro não cobria o assunto');
    expect(text(s)).toContain('US$ 0,0430');
    const links = Array.from(s.querySelectorAll('[data-fontes-da-pesquisa] a')).map((a) => a.getAttribute('href'));
    expect(links).toEqual(['https://exemplo.com.br/a']);                                         // o da query fica como texto
    expect(text(s.querySelector('[data-fontes-da-pesquisa]')!)).toContain('exemplo.com.br · exemplo.com.br');
    expect(s.querySelector('[data-pesquisa-reaproveitada]')).toBeNull();
  });

  it('central sem o campo: só o que ele registra (custo pago e fontes); nunca "reaproveitada" por dedução', async () => {
    await abrir({ ...BASE, custo: { pesquisa_usd: 0.043, alvos_usd: 0.1, total_usd: 0.143 }, fontes_da_pesquisa: ['https://exemplo.com.br/a'] });
    let s = secao()!;
    expect(s.getAttribute('data-pesquisa-da-operacao')).toBe('sem_estado');
    expect(text(s)).toContain('Pesquisa da operação: paga, US$ 0,0430');
    expect(text(s.querySelector('[data-sem-estado-da-pesquisa]')!)).toContain('ainda não diz se o Livro bastou');
    await act(async () => { root.unmount(); root = createRoot(container); });
    await abrir(BASE);                                                                         // custo 0 e sem fontes, mas com assunto
    s = secao()!;
    expect(text(s)).toContain('sem custo de pesquisa registrado');
    expect(text(s)).not.toMatch(/[Rr]eaproveitada/);
    await act(async () => { root.unmount(); root = createRoot(container); });
    await abrir({ ...BASE, pesquisa: null });                                                  // central novo, operação sem pesquisa: ele SABE
    s = secao()!;
    expect(text(s)).toContain('não houve pesquisa nesta operação');
    expect(s.querySelector('[data-sem-estado-da-pesquisa]')).toBeNull();
    await act(async () => { root.unmount(); root = createRoot(container); });
    const { assunto: _a, ...semAssunto } = BASE;
    await abrir({ ...semAssunto, custo: null });                                               // nada a dizer: a seção não aparece
    expect(secao()).toBeNull();
  });

  it('os fatos da operação por alvo, em palavras (o do Livro, o da operação, a fonte, o registro; prefixo desconhecido como veio)', async () => {
    await abrir({ ...BASE, pesquisa: REAPROVEITADA });
    await click(byRole('button', /^Abrir o detalhe de Ana$/, container));
    const lista = await esperarElemento('li[data-conhecimento="Fato do Livro"]', container);
    expect(text(lista)).toBe('Fato do Livro: operation_fact:12');
    expect(text(container.querySelector('li[data-conhecimento="Fato da operação"]')!)).toBe('Fato da operação: alvo.conteudo');
    expect(text(container.querySelector('li[data-conhecimento="Fonte da pesquisa"]')!)).toBe('Fonte da pesquisa: https://exemplo.com.br/a');
    expect(text(container.querySelector('li[data-conhecimento="Registro da operação"]')!)).toBe('Registro da operação: conta');
    expect(text(container.querySelector('li[data-conhecimento="outro"]')!)).toBe('fluxo:comentar-no-post');
  });
});
