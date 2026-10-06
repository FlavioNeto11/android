// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PersonaOnDevice, Step, StepStatus } from '../../api/types';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { initialDataState } from '../../store/reducer';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { RUN_ID, makeInstance, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, botaoPronto, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { EnsinarACorrigir, intencaoDaCorrecao } from './EnsinarACorrigir';

/**
 * 31.111 F5 (adendo v1.75): "Ensinar a corrigir" na etapa que falhou. Pede o controle do aparelho da etapa só depois da
 * escolha da pessoa, abre o treino ligado a ela (`POST /api/training/from-run`) e leva ao Foco. Prova `simulated`.
 */

const ATRASO_MAXIMO = Number(process.env.ATRASO_DO_FETCH_MS ?? 0);
let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

const ETAPA_ID = `${RUN_ID}:android-01:v1:open_app`;

function etapa(status: StepStatus): Step {
  const s = makeRunDetail().steps.find((x) => x.id === ETAPA_ID)!;
  return { ...s, status, title: 'Abrir o app' };
}

const persona = (profile_id: string, name: string): PersonaOnDevice => ({
  profile_id, username: null, display_name: name, name, status: 'active', app_id: null, is_primary: false, bound_at: null, session: null,
});

/** O aparelho 01 com o controle desta aba (lease concedido), como depois do "Assumir controle". */
function comControleNaAba(): void {
  useAppStore.setState({ instances: { 'android-01': makeInstance(1, { state: 'online', control: 'user' }) }, instanceOrder: ['android-01'] });
  useControlStore.setState({ leases: { 'android-01': { leaseId: 'lease-1', status: 'granted', acquiredAt: Date.now() } }, busy: {} });
}

async function montar(status: StepStatus = 'failed'): Promise<void> {
  await act(async () => root.render(<EnsinarACorrigir detail={{ id: RUN_ID }} step={etapa(status)} />));
}

async function abrirFormulario(): Promise<void> {
  await click(byRole('button', /^Ensinar a corrigir$/));
  await waitFor(() => expect(text()).toContain('Assumir o controle e abrir o treino'));
}

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/instances\/android-01\/personas$/, () => json([persona('p-a', 'Ana Exemplo')]));
  backend.on('POST', /\/training\/from-run$/, (c) => json({ id: 'trn-f1', instance_id: 'android-01', profile_id: null, app_id: null, intent: 'x',
    status: 'recording', operator: null, proposal: null, flow_id: null, created_at: '', finished_at: null, updated_at: '', inputs: [],
    origin: { run_id: (c.body as { run_id: string }).run_id, step_id: ETAPA_ID, step_key: 'open_app', attempt_id: null, motivo: null } }, 201));
  useToastStore.setState({ toasts: [] });
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

describe('31.111 F5: Ensinar a corrigir', () => {
  it('só a etapa que falhou ou ficou incerta oferece o botão', async () => {
    await montar('failed');
    expect(allByRole('button', /^Ensinar a corrigir$/)).toHaveLength(1);
    await act(async () => root.unmount());
    root = createRoot(container);
    await montar('uncertain');
    expect(allByRole('button', /^Ensinar a corrigir$/)).toHaveLength(1);
    for (const s of ['succeeded', 'running', 'pending', 'cancelled', 'skipped'] as StepStatus[]) {
      await act(async () => root.unmount());
      root = createRoot(container);
      await montar(s);
      expect(allByRole('button', /^Ensinar a corrigir$/)).toHaveLength(0);
    }
  });

  it('31.111 A: a etapa que parou esperando uma pessoa (waiting_user) também oferece o botão e abre o treino a partir dela', async () => {
    comControleNaAba();
    await montar('waiting_user');
    expect(allByRole('button', /^Ensinar a corrigir$/)).toHaveLength(1);
    await abrirFormulario();
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/training\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1' });
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));
  });

  it('com o controle desta aba: o formulário nomeia o aparelho, manda run_id, step_id e lease_id (sem intent se o texto é o padrão) e abre o Foco', async () => {
    comControleNaAba();
    await montar();
    expect(backend.callsTo('POST', /control\/take$/)).toHaveLength(0);     // o botão sozinho não pede o controle de ninguém
    await abrirFormulario();
    expect(text()).toContain('android-01');
    expect((byRole('textbox', /O que você vai ensinar/) as HTMLInputElement).value).toBe(intencaoDaCorrecao('Abrir o app'));
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/training\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1' });
    expect(backend.callsTo('POST', /control\/take$/)).toHaveLength(0);     // o lease já era desta aba: não toma de novo
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.title === 'Treino aberto a partir da falha')).toBe(true));
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));
  });

  it('o texto reescrito vai como intent', async () => {
    comControleNaAba();
    await montar();
    await abrirFormulario();
    await setValue(byRole('textbox', /O que você vai ensinar/) as HTMLInputElement, 'Abrir o app pelo ícone da gaveta');
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1', intent: 'Abrir o app pelo ícone da gaveta' });
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));  // a resposta chega depois do pedido registrado: o teste não termina antes dela
  });

  it('sem controle: toma o do aparelho da etapa e usa o lease novo', async () => {
    useAppStore.setState({ instances: { 'android-01': makeInstance(1, { state: 'online', control: 'none' }) }, instanceOrder: ['android-01'] });
    backend.on('POST', /\/instances\/android-01\/control\/take$/, () => json({ lease_id: 'lease-9', status: 'granted' }));
    await montar();
    await abrirFormulario();
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /control\/take$/)).toHaveLength(1);
    expect((backend.callsTo('POST', /\/from-run$/)[0]!.body as { lease_id: string }).lease_id).toBe('lease-9');
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));  // a resposta chega depois do pedido registrado: o teste não termina antes dela
  });

  it('lease velho no navegador, mas o aparelho já não está com você: toma de novo e usa o lease novo (não manda o velho)', async () => {
    useAppStore.setState({ instances: { 'android-01': makeInstance(1, { state: 'online', control: 'ai' }) }, instanceOrder: ['android-01'] });
    useControlStore.setState({ leases: { 'android-01': { leaseId: 'lease-velho', status: 'granted', acquiredAt: Date.now() } }, busy: {} });
    backend.on('POST', /\/instances\/android-01\/control\/take$/, () => json({ lease_id: 'lease-novo', status: 'granted' }));
    await montar();
    await abrirFormulario();
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /control\/take$/)).toHaveLength(1);
    expect((backend.callsTo('POST', /\/from-run$/)[0]!.body as { lease_id: string }).lease_id).toBe('lease-novo');
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));  // a resposta chega depois do pedido registrado: o teste não termina antes dela
  });

  it('controle só pedido (a IA termina a ação): avisa para clicar de novo e não abre o treino', async () => {
    useAppStore.setState({ instances: { 'android-01': makeInstance(1, { state: 'online', control: 'ai' }) }, instanceOrder: ['android-01'] });
    backend.on('POST', /\/instances\/android-01\/control\/take$/, () => json({ lease_id: 'lease-p', status: 'pending' }));
    await montar();
    await abrirFormulario();
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(text()).toContain('a IA termina a ação atual'));
    expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(0);
    expect(useUiStore.getState().focusInstanceId).toBeNull();
  });

  it('com duas personas pede a escolha, trava o botão com o motivo e manda profile_id', async () => {
    comControleNaAba();
    backend.on('GET', /\/instances\/android-01\/personas$/, () => json([persona('p-a', 'Ana Exemplo'), persona('p-b', 'Beto Exemplo')]));
    await montar();
    await abrirFormulario();
    const escolha = await waitFor(() => byRole('combobox', /De quem é o ensino/)) as HTMLSelectElement;
    expect(byRole('button', /^Assumir o controle e abrir o treino/).getAttribute('aria-disabled')).toBe('true');
    await click(byRole('button', /^Assumir o controle e abrir o treino/));
    expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(0);
    await setValue(escolha, 'p-b');
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1', profile_id: 'p-b' });
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));  // a resposta chega depois do pedido registrado: o teste não termina antes dela
  });

  it('recusa do backend (step_not_failed): mostra a mensagem, não abre o Foco e o formulário segue', async () => {
    comControleNaAba();
    backend.on('POST', /\/training\/from-run$/, () => apiError(409, 'step_not_failed', 'Esta etapa não falhou: só a que falhou ou ficou incerta se corrige.'));
    await montar();
    await abrirFormulario();
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(byRole('alert', /Esta etapa não falhou/)).toBeTruthy());
    expect(useUiStore.getState().focusInstanceId).toBeNull();
    expect(text()).toContain('Assumir o controle e abrir o treino');
  });

  it('Cancelar fecha o formulário sem chamar nada e devolve o foco ao botão', async () => {
    comControleNaAba();
    await montar();
    await abrirFormulario();
    await click(byRole('button', /^Cancelar$/));
    await waitFor(() => expect(allByRole('button', /^Assumir o controle e abrir o treino$/)).toHaveLength(0));
    expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(0);
    expect(document.activeElement).toBe(byRole('button', /^Ensinar a corrigir$/));
  });
});

describe('31.116 parte 2 (adendo v1.80): a intenção sugerida pelo diagnóstico', () => {
  const SUGESTAO = {
    intent: 'Corrigir a etapa «Abrir o app»: o app mudou de versão',
    pergunta: 'Mostre o caminho nesta versão do app.',
    rotulo: 'o app mudou de versão',
  };
  const campo = () => byRole('textbox', /O que você vai ensinar/) as HTMLInputElement;
  const enviar = async () => {
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));  // a resposta chega depois do pedido registrado: o teste não termina antes dela
  };

  it('pré-preenche o campo, mostra a causa e o que mostrar, lê a etapa certa e, sem mexer, não manda intent', async () => {
    comControleNaAba();
    backend.on('GET', /\/runs\/[^/]+\/steps\/[^/]+\/ensino-sugerido$/, () => json(SUGESTAO));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(campo().value).toBe(SUGESTAO.intent));
    expect(text()).toContain('Causa provável: o app mudou de versão.');
    expect(text()).toContain('O que mostrar: Mostre o caminho nesta versão do app.');
    expect(campo().getAttribute('aria-describedby')).toBeTruthy();                       // a dica é lida junto do campo
    expect(backend.callsTo('GET', /ensino-sugerido$/)).toHaveLength(1);
    expect(backend.callsTo('GET', /ensino-sugerido$/)[0]!.path).toContain(`/runs/${RUN_ID}/steps/${encodeURIComponent(ETAPA_ID)}/ensino-sugerido`);
    expect(backend.callsTo('POST', /control\/take$/)).toHaveLength(0);                    // ler a sugestão não toma o controle
    await enviar();
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1' });   // a sugestão vale sozinha: sem intent
  });

  it('a intenção da pessoa vence: o texto reescrito vai como intent mesmo havendo sugestão', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(SUGESTAO));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(campo().value).toBe(SUGESTAO.intent));
    await setValue(campo(), 'Abrir o app pela gaveta, na versão nova');
    await enviar();
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1', intent: 'Abrir o app pela gaveta, na versão nova' });
  });

  it('quem já escreveu antes de a sugestão chegar não tem o texto trocado; o que escreveu vai como intent', async () => {
    comControleNaAba();
    let soltar: (r: Response) => void = () => {};
    let pedida = false;                                  // com o fetch atrasado, o pedido só chega ao handler depois
    backend.on('GET', /ensino-sugerido$/, () => new Promise<Response>((ok) => { soltar = ok; pedida = true; }));
    await montar();
    await abrirFormulario();
    await setValue(campo(), 'Ensinar a abrir o app');
    await waitFor(() => expect(pedida).toBe(true));
    await act(async () => soltar(json(SUGESTAO)));
    await waitFor(() => expect(text()).toContain('Causa provável: o app mudou de versão.'));   // a dica chega...
    expect(campo().value).toBe('Ensinar a abrir o app');                                      // ...e o campo segue da pessoa
    await enviar();
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1', intent: 'Ensinar a abrir o app' });
  });

  it('sem tentativa (resposta null) ou com a rota recusando: fica o texto padrão, sem dica e sem intent', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(null));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(backend.callsTo('GET', /ensino-sugerido$/)).toHaveLength(1));
    await flush(ATRASO_MAXIMO + 30);
    expect(campo().value).toBe(intencaoDaCorrecao('Abrir o app'));
    expect(text()).not.toContain('Causa provável');
    expect(text()).not.toContain('O texto é uma sugestão');
    await act(async () => root.unmount());
    root = createRoot(container);

    backend.on('GET', /ensino-sugerido$/, () => apiError(409, 'step_not_failed', 'Esta etapa não falhou.'));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(backend.callsTo('GET', /ensino-sugerido$/)).toHaveLength(2));
    await flush(ATRASO_MAXIMO + 30);
    expect(campo().value).toBe(intencaoDaCorrecao('Abrir o app'));
    expect(text()).not.toContain('Causa provável');
    expect(allByRole('alert', /Esta etapa não falhou/)).toHaveLength(0);                       // a leitura que falha não vira erro na tela
    await enviar();
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1' });
  });

  it('diagnóstico que falhou (pergunta e rótulo nulos): o campo vem com a intenção, sem dica', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json({ intent: 'Corrigir a etapa «Abrir o app»', pergunta: null, rotulo: null }));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(campo().value).toBe('Corrigir a etapa «Abrir o app»'));
    expect(text()).not.toContain('Causa provável');
    expect(text()).not.toContain('O texto é uma sugestão');
    await enviar();
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1' });
  });

  it('fechar e abrir de novo lê a sugestão outra vez e volta ao texto dela, não ao que a pessoa tinha escrito', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(SUGESTAO));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(campo().value).toBe(SUGESTAO.intent));
    await setValue(campo(), 'outro texto');
    await click(byRole('button', /^Cancelar$/));
    await abrirFormulario();
    await waitFor(() => expect(backend.callsTo('GET', /ensino-sugerido$/)).toHaveLength(2));
    await waitFor(() => expect(campo().value).toBe(SUGESTAO.intent));
  });
});
