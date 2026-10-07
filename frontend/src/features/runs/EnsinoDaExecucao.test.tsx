// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { RUN_ID, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, esperarElemento, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { EnsinoDaExecucao } from './EnsinoDaExecucao';
import { etapasProntas, lerEnsinoDaExecucao, motivoEmPalavras, receitaEmPalavras, type EtapaDoEnsino } from './ensinoLido';
import { RunView } from './RunView';

/**
 * 31.226: "Ensinar a partir desta execução", na tela da execução, contra o adendo v1.120 (31.221, Aprendizado, commit 4bceba0e). Prova
 * `simulated`: servidor falso no formato do adendo. Real = percurso sobre a execução da onda 2, depois do deploy 61.
 */

const etapaBruta = (key: string, over: Record<string, unknown> = {}) => ({
  step_id: `${RUN_ID}:android-01:v1:${key}`, key, capability: 'ler_post', status: 'succeeded', driven_by: 'ator', persona: 'p-1',
  receita: null, ensinavel: false, motivo: 'sem_receita', ...over,
});
const corpo = (etapas: unknown[], extra: Record<string, unknown> = {}) => ({ run_id: RUN_ID, status: 'succeeded', simulada: false, ensinaveis: etapas.filter((e) => (e as { ensinavel?: boolean }).ensinavel).length, etapas, ...extra });

describe('lerEnsinoDaExecucao: tolerante, sem inventar', () => {
  it('lê o corpo do adendo; id de receita numérico vira texto; campo torto vira "não informado"', () => {
    const r = lerEnsinoDaExecucao(corpo([
      etapaBruta('open_profile', { receita: { id: 40, status: 'candidate', replay_ok: true }, ensinavel: true, motivo: null }),
      etapaBruta('x', { ensinavel: 'sim', motivo: 'inventado', receita: { id: '', status: 'candidate' }, ferramentas_nao_reproduziveis: ['press_back', 3, ''] }),
    ], { ensinaveis: -1, simulada: 'nao' }))!;
    expect(r.etapas).toHaveLength(2);
    expect(r.etapas[0]).toMatchObject({ chave: 'open_profile', ensinavel: true, motivo: null, receita: { id: '40', status: 'candidate', replayOk: true } });
    expect(r.etapas[1]).toMatchObject({ ensinavel: false, receita: null, motivo: null, motivoDesconhecido: 'inventado', ferramentasNaoReproduziveis: ['press_back'] });
    expect(r.ensinaveis).toBeNull();
    expect(r.simulada).toBeNull();
    expect(r.promovidas).toBeNull();
  });
  it('uma etapa sem step_id ou key invalida o corpo inteiro (nada de lista parcial com ensinaveis apontando para o que não se vê)', () => {
    expect(lerEnsinoDaExecucao(corpo([etapaBruta('ok'), { key: 'sem_step_id' }]))).toBeNull();
    expect(lerEnsinoDaExecucao(corpo([{ step_id: 's', ensinavel: true }], { ensinaveis: 1 }))).toBeNull();
    expect(lerEnsinoDaExecucao(corpo([etapaBruta('ok'), 'lixo']))).toBeNull();
  });
  it('etapasProntas: o ensinaveis do corpo vale; sem ele, a contagem das etapas', () => {
    const base = lerEnsinoDaExecucao(corpo([etapaBruta('a', { ensinavel: true, motivo: null }), etapaBruta('b')], { ensinaveis: 5 }))!;
    expect(etapasProntas(base)).toBe(5);
    expect(etapasProntas({ ...base, ensinaveis: null })).toBe(1);
  });
  it('sem run_id ou sem a lista de etapas não é o corpo do ensino', () => {
    expect(lerEnsinoDaExecucao({ etapas: [] })).toBeNull();
    expect(lerEnsinoDaExecucao({ run_id: 'r' })).toBeNull();
    expect(lerEnsinoDaExecucao(null)).toBeNull();
  });
  it('os nove motivos têm frase própria; o desconhecido mostra o código cru; a receita diz o estado e a repetição', () => {
    const base = lerEnsinoDaExecucao(corpo([etapaBruta('a')]))!.etapas[0]!;
    const com = (motivo: EtapaDoEnsino['motivo'], extra: Partial<EtapaDoEnsino> = {}): EtapaDoEnsino => ({ ...base, motivo, ...extra });
    const frases = (['execucao_simulada', 'com_efeito', 'nao_concluida', 'ja_por_receita', 'sem_ator', 'caminho_nao_reproduzivel', 'sem_receita', 'receita_ja_vale', 'receita_fora_de_circulacao'] as const)
      .map((m) => motivoEmPalavras(com(m)));
    expect(new Set(frases).size).toBe(9);
    expect(frases.every((f) => f && !/_/.test(f))).toBe(true);                                  // nenhum código cru nas frases fechadas
    expect(motivoEmPalavras(com(null))).toBeNull();
    expect(motivoEmPalavras(com('caminho_nao_reproduzivel', { ferramentasNaoReproduziveis: ['press_back', 'commit_guard'] }))).toContain('Voltar (press_back), trava de efeito (commit_guard)');
    expect(motivoEmPalavras(com(null, { motivoDesconhecido: 'novo_motivo' }))).toContain('novo_motivo');
    expect(receitaEmPalavras({ id: '40', status: 'candidate', replayOk: false })).toBe('Receita 40: candidata, repetição falhou');
    expect(receitaEmPalavras({ id: '41', status: null, replayOk: null })).toBe('Receita 41: estado não informado, repetição não conferida');
  });
});

describe('a seção na tela da execução', () => {
  let backend: FakeBackend;
  let root: Root;
  let container: HTMLDivElement;

  beforeAll(() => installBrowserStubs());
  beforeEach(() => {
    backend = new FakeBackend();
    backend.install();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    useToastStore.setState({ toasts: [] });
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    useAppStore.setState({ ...initialDataState });
  });

  const ENSINO = /\/aprendizado\/execucao\/run-0001\/ensino$/;
  const montar = async (status: 'completed' | 'running' = 'completed') => {
    await act(async () => root.render(<><EnsinoDaExecucao run={{ id: RUN_ID, status }} /><ConfirmHost /></>));
  };
  const secao = () => container.querySelector('[data-ensino-da-execucao]') as HTMLElement | null;
  const etapa = (k: string) => container.querySelector(`li[data-etapa="${k}"]`) as HTMLElement;

  const COM_CANDIDATA = () => corpo([
    etapaBruta('open_profile', { receita: { id: 40, status: 'candidate', replay_ok: true }, ensinavel: true, motivo: null }),
    etapaBruta('comentar', { motivo: 'com_efeito' }),
    etapaBruta('abrir_post', { motivo: 'caminho_nao_reproduzivel', ferramentas_nao_reproduziveis: ['press_back'] }),
    etapaBruta('ler_legenda', { motivo: 'ja_por_receita', receita: { id: 12, status: 'published', replay_ok: true } }),
  ]);

  it('por etapa, a receita candidata que nasceu dela ou o motivo de não ter nascido, em palavras', async () => {
    backend.on('GET', ENSINO, () => json(COM_CANDIDATA()));
    await montar();
    await waitFor(() => expect(secao()).not.toBeNull());
    expect(text(secao()!)).toContain('1 etapa pronta para ensinar, de 4.');
    expect(text(etapa('open_profile'))).toContain('Receita 40: candidata, repetição conferida');
    expect(text(etapa('open_profile'))).toContain('Pronta para ensinar.');
    expect(etapa('open_profile').querySelector('a')!.getAttribute('href')).toBe('#/aprendizado?aba=aprendido&item=receita%3A40');
    expect(text(etapa('comentar'))).toContain('tem efeito no app');
    expect(text(etapa('abrir_post'))).toContain('Voltar (press_back)');
    expect(text(etapa('ler_legenda'))).toContain('já rodou por uma receita');
    expect(text(etapa('ler_legenda'))).toContain('Receita 12: publicada');
    expect(secao()!.textContent).not.toMatch(/com_efeito|caminho_nao_reproduzivel|ja_por_receita/);   // nenhum código cru
  });

  it('o botão confirma antes e o POST é sem corpo; o resultado diz, por receita, o que foi promovido e o que foi recusado', async () => {
    backend.on('GET', ENSINO, () => json(COM_CANDIDATA()));
    backend.on('POST', ENSINO, () => json({
      ...corpo([etapaBruta('open_profile', { receita: { id: 40, status: 'published', replay_ok: true }, motivo: 'receita_ja_vale' }), etapaBruta('outra', { receita: { id: 41, status: 'candidate', replay_ok: false } })]),
      promovidas: [{ recipe_id: 40, step_key: 'open_profile' }],
      recusadas: [{ recipe_id: 41, step_key: 'outra', code: 'replay_falhou', message: 'A repetição não passou.' }],
    }));
    await montar();
    await waitFor(() => expect(secao()).not.toBeNull());
    await click(byRole('button', /^Ensinar a partir da execução/, container));
    const d = await waitFor(() => byRole('dialog', /Ensinar a partir desta execução\?/));
    expect(text(d)).toContain('1 receita candidata vai passar por candidata → validada → publicada pelo Livro.');
    expect(backend.callsTo('POST', ENSINO)).toHaveLength(0);                                    // nada sai antes da confirmação
    await click(byRole('button', /^Ensinar$/, d));
    await waitFor(() => expect(backend.callsTo('POST', ENSINO)).toHaveLength(1));
    expect(backend.callsTo('POST', ENSINO)[0]!.body).toBeUndefined();
    const r = await esperarElemento('[data-resultado-do-ensino]', container);
    expect(text(container.querySelector('[data-promovida="40"]')!)).toContain('Promovida: receita 40 (etapa open_profile), agora publicada.');
    expect(text(container.querySelector('[data-recusada="41"]')!)).toContain('Recusada: receita 41 (etapa outra): A repetição não passou. [replay_falhou].');
    expect(text(r)).not.toContain('Nenhuma receita');
    expect(text(etapa('open_profile'))).toContain('Receita 40: publicada');                  // o corpo relido substitui o da leitura
    expect(useToastStore.getState().toasts.some((t) => t.title === 'Ensino feito')).toBe(true);
  });

  it('ensinaveis omitido: o botão e a confirmação usam a MESMA contagem das etapas (nunca "0 receitas" com o POST habilitado)', async () => {
    const c = COM_CANDIDATA() as Record<string, unknown>;
    delete c.ensinaveis;
    backend.on('GET', ENSINO, () => json(c));
    await montar();
    await waitFor(() => expect(secao()).not.toBeNull());
    await click(byRole('button', /^Ensinar a partir da execução/, container));
    const d = await waitFor(() => byRole('dialog', /Ensinar a partir desta execução\?/));
    expect(text(d)).toContain('1 receita candidata vai passar');
    expect(text(d)).not.toContain('0 receitas');
  });

  it('"Voltar" na confirmação não promove nada', async () => {
    backend.on('GET', ENSINO, () => json(COM_CANDIDATA()));
    await montar();
    await waitFor(() => expect(secao()).not.toBeNull());
    await click(byRole('button', /^Ensinar a partir da execução/, container));
    const d = await waitFor(() => byRole('dialog', /Ensinar a partir desta execução\?/));
    await click(byRole('button', /^Voltar$/, d));
    expect(backend.callsTo('POST', ENSINO)).toHaveLength(0);
  });

  it('sem candidata o POST seria vazio: o botão fica indisponível com o motivo; execução simulada diz isso', async () => {
    backend.on('GET', ENSINO, () => json(corpo([etapaBruta('a', { motivo: 'sem_receita' })])));
    await montar();
    await waitFor(() => expect(secao()).not.toBeNull());
    const b = byRole('button', /^Ensinar a partir da execução/, container);
    expect(b.getAttribute('aria-disabled')).toBe('true');
    expect(b.textContent).toContain('Nenhuma etapa desta execução está pronta para ensinar.');
  });

  it('execução simulada: o botão explica que não há o que ensinar', async () => {
    backend.on('GET', ENSINO, () => json(corpo([etapaBruta('a', { motivo: 'execucao_simulada' })], { simulada: true })));
    await montar();
    await waitFor(() => expect(secao()).not.toBeNull());
    expect(byRole('button', /^Ensinar a partir da execução/, container).textContent).toContain('A execução foi simulada');
    expect(text(etapa('a'))).toContain('A execução foi simulada');
  });

  it('recusa do POST aparece na tela e a lista fica como estava', async () => {
    backend.on('GET', ENSINO, () => json(COM_CANDIDATA()));
    backend.on('POST', ENSINO, () => apiError(409, 'sem_operador', 'Entre como operador para ensinar.'));
    await montar();
    await waitFor(() => expect(secao()).not.toBeNull());
    await click(byRole('button', /^Ensinar a partir da execução/, container));
    const d = await waitFor(() => byRole('dialog', /Ensinar a partir desta execução\?/));
    await click(byRole('button', /^Ensinar$/, d));
    await waitFor(() => expect(text(container)).toContain('Entre como operador para ensinar.'));
    expect(container.querySelector('[data-resultado-do-ensino]')).toBeNull();
    expect(text(etapa('open_profile'))).toContain('Receita 40: candidata');
  });

  it('central anterior (a rota não existe): uma linha diz isso, sem botão; execução desconhecida é erro, não "sem módulo"', async () => {
    backend.on('GET', ENSINO, () => apiError(404, 'not_found', 'Rota inexistente.'));
    await montar();
    await waitFor(() => expect(container.querySelector('[data-ensino-sem-modulo]')).not.toBeNull());
    expect(secao()).toBeNull();
    await act(async () => { root.unmount(); root = createRoot(container); });
    backend.on('GET', ENSINO, () => apiError(404, 'execucao_desconhecida', 'A execução não existe.'));
    await montar();
    await waitFor(() => expect(text(container)).toContain('A execução não existe.'));
    expect(container.querySelector('[data-ensino-sem-modulo]')).toBeNull();
  });

  it('execução ainda em andamento: nada é lido nem mostrado', async () => {
    backend.on('GET', ENSINO, () => json(COM_CANDIDATA()));
    await montar('running');
    expect(secao()).toBeNull();
    expect(backend.callsTo('GET', ENSINO)).toHaveLength(0);
  });

  it('o corpo em formato inesperado é erro de leitura, não uma lista vazia', async () => {
    backend.on('GET', ENSINO, () => json({ run_id: RUN_ID, itens: [] }));
    await montar();
    await waitFor(() => expect(text(container)).toContain('formato inesperado'));
    expect(secao()).toBeNull();
  });

  it('a tela da execução terminada traz a seção', async () => {
    backend.on('GET', ENSINO, () => json(COM_CANDIDATA()));
    const detail = makeRunDetail();
    useAppStore.setState({
      ...initialDataState, hydrated: true, hydrateCount: 1, runs: [{ ...detail, status: 'completed' as const }],
      detail: { runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready', data: { ...detail, status: 'completed' as const }, events: [] },
    });
    useUiStore.setState({ selectedRunId: RUN_ID });
    await act(async () => root.render(<><RunView /><ConfirmHost /></>));
    await waitFor(() => expect(secao()).not.toBeNull());
    expect(backend.callsTo('GET', ENSINO)).toHaveLength(1);
  });
});
