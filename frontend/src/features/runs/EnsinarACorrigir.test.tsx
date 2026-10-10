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
import { AtalhoParaEnsinar, EnsinarACorrigir, intencaoDaCorrecao } from './EnsinarACorrigir';

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
    causa: 'versao_nova',
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
    expect(text()).toContain('Lendo a sugestão…');                                           // enquanto a resposta não chega
    expect(text()).not.toContain('Voltar à sugestão');
    await act(async () => soltar(json(SUGESTAO)));
    await waitFor(() => expect(text()).not.toContain('Lendo a sugestão…'));
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
    backend.on('GET', /ensino-sugerido$/, () => json({ intent: 'Corrigir a etapa «Abrir o app»', pergunta: null, rotulo: null, causa: null }));
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

  it('"O que mostrar" fica em destaque ACIMA do campo, fora da dica; a dica traz só a causa', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(SUGESTAO));
    await montar();
    await abrirFormulario();
    const destaque = await waitFor(() => {
      const p = [...document.querySelectorAll('p')].find((x) => x.textContent?.startsWith('O que mostrar:'));
      expect(p).toBeTruthy();
      return p!;
    });
    expect(destaque.textContent).toContain('Mostre o caminho nesta versão do app.');
    expect(destaque.compareDocumentPosition(campo()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // vem antes do campo
    const dica = document.getElementById(campo().getAttribute('aria-describedby')!)!;
    expect(dica.textContent).toContain('Causa provável: o app mudou de versão.');
    expect(dica.textContent).not.toContain('O que mostrar');
  });

  it('o campo tem duas linhas; Enter envia como antes, Shift+Enter não, e quebra de linha colada vira espaço', async () => {
    comControleNaAba();
    await montar();
    await abrirFormulario();
    expect(campo().tagName).toBe('TEXTAREA');
    expect(campo().getAttribute('rows')).toBe('2');
    await setValue(campo(), 'Abrir o app\n  pela gaveta');
    expect(campo().value).toBe('Abrir o app pela gaveta');
    await botaoPronto(/^Assumir o controle e abrir o treino$/);                                        // com a leitura das personas em voo o envio espera (o botão diz por quê)
    await act(async () => { campo().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true })); });
    expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(0);                                   // Shift+Enter não envia
    await act(async () => { campo().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); });
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1', intent: 'Abrir o app pela gaveta' });
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBe('android-01'));
  });

  it('"Voltar à sugestão" só aparece depois de editar, devolve o texto da sugestão e então o intent some do envio', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(SUGESTAO));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(campo().value).toBe(SUGESTAO.intent));
    expect(allByRole('button', /^Voltar à sugestão$/)).toHaveLength(0);
    await setValue(campo(), 'meu texto');
    await click(byRole('button', /^Voltar à sugestão$/));
    expect(campo().value).toBe(SUGESTAO.intent);
    expect(allByRole('button', /^Voltar à sugestão$/)).toHaveLength(0);
    await enviar();
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1' });
  });

  it('sem sugestão não há "Voltar à sugestão", mesmo com o texto editado', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(null));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(backend.callsTo('GET', /ensino-sugerido$/)).toHaveLength(1));
    await flush(ATRASO_MAXIMO + 30);
    await setValue(campo(), 'meu texto');
    expect(allByRole('button', /^Voltar à sugestão$/)).toHaveLength(0);
    expect(text()).not.toContain('Lendo a sugestão…');                                          // a espera termina também sem sugestão
  });

  it('v1.82: causa indeterminada diz "não deu para saber", sem "provável", pelo código e não pela frase do rótulo', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json({ intent: 'Corrigir a etapa «Abrir o app»', pergunta: 'O que a etapa devia ter feito nesta tela?', rotulo: 'a causa não ficou clara', causa: 'indeterminada' }));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(text()).toContain('Causa: não deu para saber.'));
    expect(text()).not.toContain('Causa provável');
    expect(text()).not.toContain('a causa não ficou clara');                                    // o rótulo desse código não aparece
    expect(text()).toContain('O que mostrar: O que a etapa devia ter feito nesta tela?');
  });

  it('v1.82: causa nula (o diagnóstico falhou) não tem linha de causa, mas a pergunta do estado aparece como vem (waiting_user)', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json({ intent: 'Corrigir a etapa «Abrir o app»', pergunta: 'A etapa parou esperando você: o que ensinar, a partir desta tela, para ela seguir?', rotulo: null, causa: null }));
    await montar('waiting_user');
    await abrirFormulario();
    await waitFor(() => expect(text()).toContain('O que mostrar: A etapa parou esperando você'));
    expect(text()).not.toContain('Causa');
    expect(text()).toContain('O texto é uma sugestão');                                          // a pergunta sozinha já é sugestão
  });

  it('v1.82: o código manda — causa nula sem linha de causa mesmo que um rótulo venha junto', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json({ intent: SUGESTAO.intent, pergunta: SUGESTAO.pergunta, rotulo: SUGESTAO.rotulo, causa: null }));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(campo().value).toBe(SUGESTAO.intent));
    expect(text()).toContain('O que mostrar: Mostre o caminho nesta versão do app.');
    expect(text()).not.toContain('Causa provável');
  });

  it('backend anterior ao v1.82 (sem o campo causa): vale o rótulo como "Causa provável", como no v1.80', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json({ intent: SUGESTAO.intent, pergunta: SUGESTAO.pergunta, rotulo: SUGESTAO.rotulo }));
    await montar();
    await abrirFormulario();
    await waitFor(() => expect(text()).toContain('Causa provável: o app mudou de versão.'));
  });
});

describe('31.313 (adendo v1.138): ensinar onde a exploração da IA parou', () => {
  const PEDIDO = 'Crie uma regra no Outlook para a caixa de Fulana Exemplo';
  const FRASE = 'Ensinar à IA como fazer: criar regra de e-mail';
  const SUGESTAO_EXPLORACAO = {
    intent: FRASE,
    pergunta: 'A IA explorou e parou no teto da exploração sem chegar lá. Mostre, a partir desta tela, o caminho toque a toque: ensinado uma vez, ele serve a todas as personas.',
    rotulo: null, causa: null, exploracao: true, parou_no_teto: true,
  };
  const campo = () => byRole('textbox', /O que você vai ensinar/) as HTMLTextAreaElement;
  const exploratoria = (status: StepStatus): Step => ({ ...etapa(status), key: 'explorar_criar_regra_email', title: PEDIDO, goal: PEDIDO, exploratoria: true });
  const montarExploratoria = async (status: StepStatus = 'failed') => {
    await act(async () => root.render(<EnsinarACorrigir detail={{ id: RUN_ID }} step={exploratoria(status)} />));
    await click(byRole('button', /^Ensinar a corrigir$/));
    await waitFor(() => expect(text()).toContain('Assumir o controle e abrir o treino'));
  };

  it('usa o intent e a pergunta do servidor, diz que parou no teto e não manda intent se o texto é a sugestão', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(SUGESTAO_EXPLORACAO));
    await montarExploratoria('uncertain');
    await waitFor(() => expect(campo().value).toBe(FRASE));
    expect(text(container.querySelector('[aria-label="Exploração da IA"]') as HTMLElement)).toContain('parou no teto da exploração');
    expect(text()).toContain('O que mostrar: A IA explorou e parou no teto');
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1' });
  });

  it('o pedido de origem nunca entra no campo nem no corpo, nem quando a sugestão não chega', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(null, 404));
    await montarExploratoria();
    await waitFor(() => expect(campo().getAttribute('aria-busy')).toBeNull());
    expect(campo().value).toBe('');                                       // sem a frase do servidor o campo fica vazio, sem o título da etapa
    expect(container.textContent).not.toContain(PEDIDO);
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /\/from-run$/)[0]!.body as Record<string, unknown>;
    expect(corpo).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1' });
    expect(JSON.stringify(corpo)).not.toContain('Fulana');
  });

  it('exploração que falhou sem teto diz "não chegou lá"; etapa comum não ganha o aviso', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json({ ...SUGESTAO_EXPLORACAO, parou_no_teto: false }));
    await montarExploratoria();
    await waitFor(() => expect(campo().value).toBe(FRASE));
    expect(text(container.querySelector('[aria-label="Exploração da IA"]') as HTMLElement)).toContain('não chegou lá');
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /ensino-sugerido$/, () => json({ intent: 'Corrigir a etapa «Abrir o app»', pergunta: null, rotulo: null }));
    await montar('failed');
    await abrirFormulario();
    await waitFor(() => expect(campo().value).toBe('Corrigir a etapa «Abrir o app»'));
    expect(container.querySelector('[aria-label="Exploração da IA"]')).toBeNull();
  });

  it('a frase do servidor reescrita pela pessoa vai como intent', async () => {
    comControleNaAba();
    backend.on('GET', /ensino-sugerido$/, () => json(SUGESTAO_EXPLORACAO));
    await montarExploratoria();
    await waitFor(() => expect(campo().value).toBe(FRASE));
    await setValue(campo(), 'Ensinar à IA a criar a regra pela tela de filtros');
    await click(await botaoPronto(/^Assumir o controle e abrir o treino$/));
    await waitFor(() => expect(backend.callsTo('POST', /\/from-run$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/from-run$/)[0]!.body).toEqual({ run_id: RUN_ID, step_id: ETAPA_ID, lease_id: 'lease-1', intent: 'Ensinar à IA a criar a regra pela tela de filtros' });
  });
});

it('31.313: o atalho da etapa exploratória não leva o pedido (título da etapa) ao nome do botão nem ao título do diálogo', async () => {
  const pedido = 'Crie uma regra no Outlook para a caixa de Fulana Exemplo';
  backend.on('GET', /ensino-sugerido$/, () => json(null, 404));
  const exploratoria: Step = { ...etapa('failed'), key: 'explorar_criar_regra_email', title: pedido, goal: pedido, exploratoria: true };
  await act(async () => root.render(<AtalhoParaEnsinar detail={{ id: RUN_ID }} step={exploratoria} />));
  const botao = byRole('button', /^Ensinar a corrigir a exploração da IA em android-01$/);
  expect(botao.getAttribute('aria-label')).not.toContain('Fulana');
  await click(botao);
  await waitFor(() => expect(document.body.textContent).toContain('Ensinar a corrigir: onde a IA parou'));
  expect(document.body.textContent).not.toContain('Fulana');
  // A etapa comum segue com o título dela.
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => root.render(<AtalhoParaEnsinar detail={{ id: RUN_ID }} step={etapa('failed')} />));
  expect(byRole('button', /^Ensinar a corrigir a etapa «Abrir o app» em android-01$/)).toBeTruthy();
});
