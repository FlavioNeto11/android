// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PersonaOnDevice, RunDetail, Step, StepStatus, TrainingSession } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { RUN_ID, makeInstance, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, botaoPronto, byRole, click, flush, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { EnsinarACorrigir, ensinoDaEtapa, primeiraEtapaAEnsinar, primeiraEtapaAEnsinarDaExecucao } from './EnsinarACorrigir';
import { RunView } from './RunView';

/**
 * 31.124: o formulário "Ensinar a corrigir" avisa que a etapa já foi ensinada (ligação para a sessão em leitura e para o
 * fluxo no Livro) e pede confirmação para ensinar de novo. 31.125: o atalho "Ensinar a corrigir" no cartão de resultado e
 * na faixa do aparelho abre o mesmo formulário para a primeira etapa que falhou. Prova `simulated`.
 */

const ATRASO_MAXIMO = Number(process.env.ATRASO_DO_FETCH_MS ?? 0);
let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

const ETAPA_ID = `${RUN_ID}:android-01:v1:open_app`;
const ha = (horas: number) => new Date(Date.now() - horas * 3_600_000).toISOString();

function etapa(status: StepStatus, over: Partial<Step> = {}): Step {
  const s = makeRunDetail().steps.find((x) => x.id === ETAPA_ID)!;
  return { ...s, status, title: 'Abrir o app', ...over };
}

const persona = (profile_id: string, name: string): PersonaOnDevice => ({
  profile_id, username: null, display_name: name, name, status: 'active', app_id: null, is_primary: false, bound_at: null, session: null,
});

const sessao = (id: string, intent: string, status: TrainingSession['status'], extra: Partial<TrainingSession> = {}): TrainingSession => ({
  id, instance_id: 'android-01', profile_id: null, app_id: null, intent, status, operator: null, proposal: null, flow_id: null,
  created_at: ha(3), finished_at: ha(3), updated_at: ha(3), input_count: 2, inputs: [],
  origin: { run_id: RUN_ID, step_id: ETAPA_ID, step_key: 'open_app', attempt_id: null, motivo: 'A tela esperada não apareceu.' }, ...extra,
});

const SALVA = sessao('trn-s', 'Corrigir a etapa «Abrir o app»', 'saved', { flow_id: 'f-6d07590e1db6' });

function comControleNaAba(): void {
  useAppStore.setState({ instances: { 'android-01': makeInstance(1, { state: 'online', control: 'user' }) }, instanceOrder: ['android-01'] });
  useControlStore.setState({ leases: { 'android-01': { leaseId: 'lease-1', status: 'granted', acquiredAt: Date.now() } }, busy: {} });
}

const treinos = (...lista: TrainingSession[]) => backend.on('GET', /\/training$/, () => json(lista));

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/instances\/android-01\/personas$/, () => json([persona('p-a', 'Ana Exemplo')]));
  backend.on('GET', /\/training$/, () => json([]));
  backend.on('POST', /\/training\/from-run$/, (c) => json(sessao('trn-novo', 'x', 'recording', { origin: { run_id: (c.body as { run_id: string }).run_id, step_id: ETAPA_ID, step_key: 'open_app', attempt_id: null, motivo: null } }), 201));
  window.location.hash = '';
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
  useControlStore.setState({ leases: {}, busy: {} });
  useUiStore.setState({ focusInstanceId: null });
});

// ------------------------------------------------------------------------------------------------- 31.124
const montar = (status: StepStatus = 'failed') => act(async () => root.render(
  <>
    <EnsinarACorrigir detail={{ id: RUN_ID }} step={etapa(status)} />
    <ConfirmHost />
  </>,
));
async function abrirFormulario(): Promise<void> {
  await click(byRole('button', /^Ensinar a corrigir$/));
  await waitFor(() => expect(text()).toContain('Assumir o controle e abrir o treino'));
}
const aviso = () => document.querySelector<HTMLElement>('[aria-label="O que já foi ensinado nesta etapa"]');

describe('31.124: a etapa que já foi ensinada avisa antes de abrir outro treino', () => {
  it('sessão salva desta etapa: o aviso diz quando, abre a sessão em leitura e leva ao fluxo no Livro', async () => {
    treinos(SALVA, sessao('trn-d', 'descartada', 'discarded'));
    backend.on('GET', /\/training\/trn-s$/, () => json({ ...SALVA, inputs: [] }));
    await montar();
    await abrirFormulario();
    const a = await waitFor(() => aviso()!);
    expect(a.textContent).toContain('Esta etapa já foi ensinada.');
    expect(a.textContent).toContain('«Corrigir a etapa «Abrir o app»» · salvo há 3 h');
    expect(a.textContent).not.toContain('descartada');                                    // a descartada não conta: nada dela ficou
    expect(byRole('link', /^Ver o fluxo no Livro$/, a).getAttribute('href')).toBe('#/aprendizado?aba=aprendido&item=fluxo%3Af-6d07590e1db6');
    await click(byRole('button', /^Abrir o treino salvo/, a));
    const leitura = await waitFor(() => byRole('dialog', /Treinamento salvo/));
    expect(leitura.textContent).toContain('Só leitura');
    expect(backend.calls.filter((c) => c.method !== 'GET')).toHaveLength(0);              // ler nunca escreve
    await click(byRole('button', /^Fechar$/, leitura));
    await waitFor(() => expect(allByRole('dialog', /Treinamento salvo/)).toHaveLength(0));
    expect(text()).toContain('Assumir o controle e abrir o treino');                        // o formulário segue aberto
  });

  it('a etapa é a mesma por execução e chave, mesmo em outra versão do plano; outra execução, outra etapa ou descartada não avisam', async () => {
    const outraVersao = sessao('trn-v2', 'versão 2', 'saved', { origin: { run_id: RUN_ID, step_id: `${RUN_ID}:android-01:v2:open_app`, step_key: 'open_app', attempt_id: null, motivo: null } });
    treinos(outraVersao,
      sessao('trn-r', 'outra execução', 'saved', { origin: { run_id: 'run-9999', step_id: 'run-9999:android-01:v1:open_app', step_key: 'open_app', attempt_id: null, motivo: null } }),
      sessao('trn-k', 'outra etapa', 'saved', { origin: { run_id: RUN_ID, step_id: `${RUN_ID}:android-01:v1:send`, step_key: 'send', attempt_id: null, motivo: null } }),
      sessao('trn-g', 'sem origem', 'saved', { origin: null }),
      sessao('trn-x', 'descartada', 'discarded'));
    await montar();
    await abrirFormulario();
    const a = await waitFor(() => aviso()!);
    expect(a.textContent).toContain('versão 2');
    for (const fora of ['outra execução', 'outra etapa', 'sem origem', 'descartada']) expect(a.textContent).not.toContain(fora);
  });

  it('treino aberto desta etapa (gravando, só gravado, com proposta) também avisa, com o estado', async () => {
    treinos(sessao('trn-a', 'gravando agora', 'recording'), sessao('trn-b', 'falta salvar', 'proposed'));
    await montar();
    await abrirFormulario();
    const a = await waitFor(() => aviso()!);
    expect(a.textContent).toContain('Já há treino aberto desta etapa.');
    expect(a.textContent).toContain('gravando agora');
    expect(a.textContent).toContain('com proposta, falta salvar');
    expect(a.textContent).not.toContain('Esta etapa já foi ensinada.');
  });

  it('mais de três salvas: lista as três mais novas e diz "e mais N"', async () => {
    treinos(...[1, 2, 3, 4, 5].map((n) => sessao(`trn-${n}`, `ensino ${n}`, 'saved', { updated_at: ha(n) })));
    await montar();
    await abrirFormulario();
    const a = await waitFor(() => aviso()!);
    expect(a.querySelectorAll('li')).toHaveLength(3);
    expect(a.textContent).toContain('ensino 1');
    expect(a.textContent).not.toContain('ensino 4');
    expect(a.textContent).toContain('e mais 2 treinos salvos.');
  });

  it('ensinar de novo pede confirmação com o que já existe; Cancelar não abre nada, Ensinar de novo abre o treino', async () => {
    comControleNaAba();
    treinos(SALVA);
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(aviso()).not.toBeNull());
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    const dialogo = await waitFor(() => byRole('dialog', /Ensinar esta etapa de novo/));
    expect(dialogo.textContent).toContain('Esta etapa já foi ensinada em «Corrigir a etapa «Abrir o app»».');
    expect(dialogo.textContent).toContain('nada do que foi ensinado antes é apagado');
    await click(byRole('button', /^Cancelar$/, dialogo));
    await waitFor(() => expect(allByRole('dialog', /Ensinar esta etapa de novo/)).toHaveLength(0));
    await flush(ATRASO_MAXIMO + 30);
    expect(backend.callsTo('POST', /from-run$/)).toHaveLength(0);
    expect(text()).toContain('Assumir o controle e abrir o treino');                        // o formulário continua para quem desistiu

    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await click(byRole('button', /^Ensinar de novo$/, await waitFor(() => byRole('dialog', /Ensinar esta etapa de novo/))));
    await waitFor(() => expect(backend.callsTo('POST', /from-run$/)).toHaveLength(1));
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));
  });

  it('quem clica em abrir antes de o histórico chegar espera por ele: o aviso não é pulado', async () => {
    comControleNaAba();
    let soltar: () => void = () => {};
    let pedida = false;
    backend.on('GET', /\/training$/, () => { pedida = true; return new Promise((resolve) => { soltar = () => resolve(json([SALVA])); }); });
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(pedida).toBe(true));
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await flush(ATRASO_MAXIMO + 30);
    expect(backend.callsTo('POST', /from-run$/)).toHaveLength(0);                           // ainda esperando a leitura, nada abriu
    expect(allByRole('dialog', /Ensinar esta etapa de novo/)).toHaveLength(0);
    await act(async () => soltar());
    const pergunta = await waitFor(() => byRole('dialog', /Ensinar esta etapa de novo/));   // chegou: agora pergunta
    expect(backend.callsTo('POST', /from-run$/)).toHaveLength(0);
    await click(byRole('button', /^Cancelar$/, pergunta));
    await waitFor(() => expect(allByRole('dialog', /Ensinar esta etapa de novo/)).toHaveLength(0));
  });

  it('etapa sem ensino nenhum abre direto, sem confirmação e sem aviso', async () => {
    comControleNaAba();
    await montar();
    await abrirFormulario();
    await flush(ATRASO_MAXIMO + 30);
    expect(aviso()).toBeNull();
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /from-run$/)).toHaveLength(1));
    expect(allByRole('dialog', /Ensinar esta etapa de novo/)).toHaveLength(0);
  });

  it('se o histórico de treinos não carrega, o formulário segue sem o aviso e abre normalmente', async () => {
    comControleNaAba();
    backend.on('GET', /\/training$/, () => apiError(500, 'falha', 'O servidor não respondeu.'));
    await montar();
    await abrirFormulario();
    await flush(ATRASO_MAXIMO + 30);
    expect(aviso()).toBeNull();
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /from-run$/)).toHaveLength(1));
  });

  it('ensinoDaEtapa: separa salvas e em aberto, da mais nova para a mais antiga', () => {
    const r = ensinoDaEtapa([
      sessao('a', 'velha', 'saved', { updated_at: ha(9) }), sessao('b', 'nova', 'saved', { updated_at: ha(1) }),
      sessao('c', 'aberta', 'recorded'), sessao('d', 'fora', 'discarded'),
    ], RUN_ID, 'open_app');
    expect(r.salvas.map((s) => s.id)).toEqual(['b', 'a']);
    expect(r.emAberto.map((s) => s.id)).toEqual(['c']);
  });
});

// ------------------------------------------------------------------------------------------------- 31.125
/** A execução do fixture com os status das etapas trocados pelos pedidos (`id da etapa → status`). */
function execucao(status: Record<string, StepStatus>): RunDetail {
  const base = makeRunDetail();
  return { ...base, steps: base.steps.map((s) => (status[s.id] ? { ...s, status: status[s.id]! } : s)) };
}
const E1_ABRIR = `${RUN_ID}:android-01:v1:open_app`;
const E1_ENVIAR = `${RUN_ID}:android-01:v1:send`;
const E2_ABRIR = `${RUN_ID}:android-02:v1:open_app`;
const tituloDe = (id: string) => makeRunDetail().steps.find((s) => s.id === id)!.title;

describe('31.125: o atalho "Ensinar a corrigir" na execução, sem descer até a etapa', () => {
  it('primeiraEtapaAEnsinar pega a primeira etapa corrigível na ordem do plano atual; a execução, o primeiro objetivo que tem uma', () => {
    const d = execucao({ [E1_ABRIR]: 'succeeded', [E1_ENVIAR]: 'failed', [E2_ABRIR]: 'waiting_user' });
    const e1 = d.steps.filter((s) => s.instance_id === 'android-01');
    expect(primeiraEtapaAEnsinar(e1)?.key).toBe('send');
    expect(primeiraEtapaAEnsinar([])).toBeNull();
    expect(primeiraEtapaAEnsinarDaExecucao(d)?.id).toBe(E1_ENVIAR);
    // sem falha no primeiro aparelho, vale a do segundo
    expect(primeiraEtapaAEnsinarDaExecucao(execucao({ [E1_ENVIAR]: 'succeeded', [E2_ABRIR]: 'uncertain' }))?.id).toBe(E2_ABRIR);
    // etapa de versão anterior do plano não conta (a atual a substituiu)
    const antiga = { ...d, steps: d.steps.map((s) => (s.id === E1_ENVIAR ? { ...s, plan_version: 0 } : s)) };
    expect(primeiraEtapaAEnsinarDaExecucao({ ...antiga, steps: antiga.steps.map((s) => (s.id === E2_ABRIR ? { ...s, status: 'succeeded' as const } : s)) })).toBeNull();
  });

  async function montarRunView(detail: RunDetail): Promise<void> {
    useAppStore.setState({
      ...initialDataState, hydrated: true, hydrateCount: 1, runs: [detail],
      detail: { runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready', data: detail, events: [] },
    });
    useUiStore.setState({ selectedRunId: RUN_ID });
    await act(async () => root.render(<><RunView /><ConfirmHost /></>));
    await waitFor(() => expect(text()).toContain('Resultado'));
  }
  const abrirPorAparelho = () => click(byRole('tab', /Por aparelho/));
  const atalhos = () => allByRole('button', /^Ensinar a corrigir a etapa/);

  it('o cartão de resultado e a faixa de cada aparelho com etapa corrigível ganham o atalho, com o nome da etapa e do aparelho', async () => {
    await montarRunView(execucao({ [E1_ABRIR]: 'succeeded', [E1_ENVIAR]: 'failed', [E2_ABRIR]: 'waiting_user' }));
    const resumo = document.querySelector<HTMLElement>('dl[aria-label="Resumo da execução"]')!;
    const noResumo = allByRole('button', /^Ensinar a corrigir a etapa/, resumo);
    expect(noResumo).toHaveLength(1);
    expect(noResumo[0]!.getAttribute('aria-label')).toBe(`Ensinar a corrigir a etapa «${tituloDe(E1_ENVIAR)}» em android-01`);
    expect(resumo.textContent).toContain('em android-01');                                // a 1ª etapa, não a do segundo aparelho
    // uma faixa por aparelho, fora do botão que abre o objetivo (na guia "Por aparelho")
    await abrirPorAparelho();
    const faixas = document.querySelectorAll('section[aria-label^="Objetivo em"]');
    expect(faixas).toHaveLength(2);
    expect(allByRole('button', /^Ensinar a corrigir a etapa/, faixas[0] as HTMLElement)).toHaveLength(1);
    expect(allByRole('button', /^Ensinar a corrigir a etapa/, faixas[1] as HTMLElement)).toHaveLength(1);
    expect(atalhos()).toHaveLength(3);
    expect(faixas[1]!.textContent).toContain(`«${tituloDe(E2_ABRIR)}»`);
  });

  it('execução sem etapa corrigível não mostra o atalho', async () => {
    await montarRunView(execucao({ [E2_ABRIR]: 'succeeded' }));
    expect(atalhos()).toHaveLength(0);
  });

  it('o atalho abre o MESMO formulário num diálogo; cancelar fecha, e abrir o treino fecha o diálogo e leva ao Foco', async () => {
    comControleNaAba();
    backend.on('GET', /\/training$/, () => json([SALVA]));
    await montarRunView(execucao({ [E1_ABRIR]: 'failed', [E2_ABRIR]: 'succeeded' }));
    await click(atalhos()[0]!);
    const d = await waitFor(() => byRole('dialog', /^Ensinar a corrigir: /));
    await waitFor(() => expect(d.textContent).toContain('Assumir o controle e abrir o treino'));
    expect(d.textContent).toContain('android-01');
    await waitFor(() => expect(d.querySelector('[aria-label="O que já foi ensinado nesta etapa"]')).not.toBeNull());   // o aviso do 31.124 vem junto
    await click(byRole('button', /^Cancelar$/, d));
    await waitFor(() => expect(allByRole('dialog', /^Ensinar a corrigir:/)).toHaveLength(0));
    expect(backend.callsTo('POST', /from-run$/)).toHaveLength(0);

    await click(atalhos()[0]!);
    const d2 = await waitFor(() => byRole('dialog', /^Ensinar a corrigir:/));
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/, d2));
    await click(byRole('button', /^Ensinar de novo$/, await waitFor(() => byRole('dialog', /Ensinar esta etapa de novo/))));
    await waitFor(() => expect(backend.callsTo('POST', /from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /from-run$/)[0]!.body).toMatchObject({ run_id: RUN_ID, step_id: E1_ABRIR, lease_id: 'lease-1' });
    await waitFor(() => expect(allByRole('dialog', /^Ensinar a corrigir:/)).toHaveLength(0));
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));
  });
});
