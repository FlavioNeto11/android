// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useUiStore } from '../../store/ui';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import {
  agregadoPorApp, contarPorEstado, ESTAGIOS, estagioDeParada, estagiosAlcancados, lerAlvo, lerCapacidade, lerEstagio, lerOperacao, verificacaoDoAlvo,
} from './modelo';
import { OPERACAO_DE_EXEMPLO } from './operacaoDeExemplo';
import { OperacaoPage } from './OperacaoPage';

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
    const d = container.querySelector('tbody tr:nth-child(2) td') as HTMLElement;
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
    created_at: '2026-10-06T17:00:00Z', finished_at: null, custo_usd: 0.02,
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

  it('"Liberar": com alvos parados no limite pede confirmação, diz quantos e chama a rota; sem eles fica desligado', async () => {
    const NO_LIMITE = { ...OPERACAO.alvos[1], estado: 'bloqueado', motivo: 'limite de ações executadas', estagio: 'acao_preparada', estagios: [] };
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, alvos: [OPERACAO.alvos[0], NO_LIMITE] }));
    backend.on('POST', /^\/api\/operacoes\/op-1\/liberar$/, () => json(OPERACAO));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    await click(byRole('button', /^Liberar$/, container));
    const d = await waitFor(() => byRole('dialog', /Liberar as ações paradas\?/));
    expect(text(d)).toContain('1 agente parou no limite de contas que executam a ação final');
    await click(byRole('button', /^Voltar$/, d));
    expect(backend.callsTo('POST', /liberar$/)).toHaveLength(0);
    await click(byRole('button', /^Liberar$/, container));
    await click(byRole('button', /^Liberar$/, await waitFor(() => byRole('dialog', /Liberar as ações paradas\?/))));
    await waitFor(() => expect(backend.callsTo('POST', /liberar$/)).toHaveLength(1));
  });

  it('"Liberar" sem ninguém parado no limite fica desligado, com o motivo', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(OPERACAO));
    await ir(['op-1']);
    await waitFor(() => expect(linhas()).toHaveLength(2));
    expect(byRole('button', /^Liberar — indisponível: Nenhum agente parou no limite/, container).getAttribute('aria-disabled')).toBe('true');
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
