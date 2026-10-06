// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, allByRole, botaoPronto, byRole, click, flush, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { AvisoDaPosCondicao, lerPosCondicoes, motivoDaPosCondicao } from './PosCondicaoQueJaVale';
import { ESPERA_DA_PREVIA_MS, TrainingReview } from './TrainingReview';

/**
 * 31.128 (adendo v1.86): a pós-condição que já vale na tela de partida vira aviso DENTRO da etapa, com as sugestões como
 * botões; na prévia (`pos_condicoes_ja_valem` ao lado de `warnings`) e na recusa 400 `pos_condicao_ja_vale`. `simulated`.
 */

const ATRASO_MAXIMO = Number(process.env.ATRASO_DO_FETCH_MS ?? 0);

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const ENTRADA = {
  session_id: 'trn-1', seq: 1, ts: '', type: 'tap', x: 10, y: 10, x2: null, y2: null, key_name: null, text: null, has_text: false,
  text_len: null, package: 'com.android.settings', app_id: null, target: { text: 'Internet', unique: ['text'] }, screen_title: 'Configurações',
  screen_lines: ['Internet'], sensitive: false,
};
const SESSAO = {
  id: 'trn-1', instance_id: 'android-01', profile_id: 'ig-1', app_id: null, intent: 'Abrir a Internet', status: 'recorded', operator: null,
  proposal: null, flow_id: null, created_at: '', finished_at: '', updated_at: '', inputs: [ENTRADA, { ...ENTRADA, seq: 2 }],
};
const etapa = (key: string, title: string, inputs: number[], valor: string) => ({
  key, title, goal: title, inputs, side_effect: false, capability: null, bindings: [], app_id: null,
  postcondition: { kind: 'text_visible', value: valor, description: 'a tela mostra' },
});
const PROPOSTA = {
  summary: 'Abrir a Internet', command_template: 'abra a internet', parameters: [], discarded: [], questions: ['O texto muda?'],
  steps: [etapa('abrir_internet', 'Abrir a Internet', [1], 'Internet'), etapa('conferir', 'Conferir a lista', [2], 'Wi-Fi')],
};
const PRONTA = (texto: string) => ({ kind: 'text_visible', value: texto, texto });
const ITEM = { etapa: 'abrir_internet', valor: 'Internet', sugestoes: ['Network & internet', 'Connected devices', 'Wi-Fi'],
  sugestoes_prontas: ['Network & internet', 'Connected devices', 'Wi-Fi'].map(PRONTA),
  message: 'Etapa “Abrir a Internet”: o texto “Internet” já aparece na tela em que ela começa, então ela passaria sem agir.' };
const PREVIA = (extra: object = {}) => ({
  steps: [{ key: 'abrir_internet', title: 'Abrir a Internet', recipe: true, reason: 'receita será gravada ao salvar' }],
  warnings: [] as string[], ...extra,
});

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training\/trn-1$/, () => json(SESSAO));
  backend.on('GET', /\/instagram\/profiles$/, () => json([]));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('POST', /\/training\/trn-1\/propose$/, () => json({ ...SESSAO, status: 'proposed', proposal: PROPOSTA }));
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA()));
  useToastStore.setState({ toasts: [] });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
});

async function abrirComProposta(): Promise<void> {
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
}
const avisos = () => allByRole('alert', /A conferência da etapa já vale/);
const salvar = () => byRole('button', /^Salvar como fluxo/);
const corpoDaUltimaPrevia = () => backend.callsTo('POST', /\/preview$/).at(-1)!.body as { proposal: { steps: { key: string; postcondition: { value: string } }[] } };

describe('o leitor e o motivo', () => {
  it('lerPosCondicoes: tolera o que falta, descarta o que não serve e corta em três sugestões', () => {
    expect(lerPosCondicoes(undefined)).toEqual([]);
    expect(lerPosCondicoes({ etapa: 'a' })).toEqual([]);
    const lista = lerPosCondicoes([
      { etapa: 'a', valor: 'X', sugestoes: ['1', '2', '3', '4', '', 7, '  '], message: 'm' },
      { etapa: '', valor: 'sem etapa', sugestoes: [] },
      { valor: 'sem chave' },
      'texto solto',
      null,
      { etapa: 'b', valor: 'Y' },
    ]);
    expect(lista).toEqual([
      { etapa: 'a', valor: 'X', sugestoes: ['1', '2', '3'], sugestoes_prontas: ['1', '2', '3'].map(PRONTA), message: 'm' },
      { etapa: 'b', valor: 'Y', sugestoes: [], sugestoes_prontas: [], message: '' },
    ]);
  });

  it('lerPosCondicoes (v1.91): lê as sugestões prontas com kind e value, descarta a malformada e, sem elas, cai no texto como text_visible', () => {
    const [a, b] = lerPosCondicoes([
      { etapa: 'a', valor: 'X', sugestoes: ['Back', 'Wi-Fi'], message: 'm',
        sugestoes_prontas: [{ kind: 'element_present', value: 'desc==Back', texto: 'Back' }, { kind: '', value: 'x', texto: 'y' }, { kind: 'text_visible', value: ' ', texto: 'z' }, { kind: 'text_visible', value: 'Wi-Fi', texto: 'Wi-Fi' }] },
      { etapa: 'b', valor: 'Y', sugestoes: ['Wi-Fi'], sugestoes_prontas: 'nao-lista' },
    ]);
    expect(a!.sugestoes_prontas).toEqual([{ kind: 'element_present', value: 'desc==Back', texto: 'Back' }, PRONTA('Wi-Fi')]);
    expect(b!.sugestoes_prontas).toEqual([PRONTA('Wi-Fi')]);
  });

  it('motivoDaPosCondicao: nomeia a etapa (ou as etapas) e é nulo sem ocorrência ou sem etapa conhecida', () => {
    const titulo = (k: string) => ({ a: 'Abrir', b: 'Conferir' } as Record<string, string>)[k] ?? null;
    expect(motivoDaPosCondicao([], titulo)).toBeNull();
    const sem = { sugestoes: [], sugestoes_prontas: [], message: '' };
    expect(motivoDaPosCondicao([{ etapa: 'z', valor: 'v', ...sem }], titulo)).toBeNull();
    expect(motivoDaPosCondicao([{ etapa: 'a', valor: 'v', ...sem }], titulo)).toContain('a etapa “Abrir” confere');
    expect(motivoDaPosCondicao([{ etapa: 'a', valor: 'v', ...sem }, { etapa: 'b', valor: 'w', ...sem }], titulo))
      .toContain('as etapas “Abrir”, “Conferir” conferem');
  });

  it('o aviso mostra o valor e um botão por sugestão; sem sugestão, só o aviso', async () => {
    const usadas: { kind: string; value: string }[] = [];
    await act(async () => root.render(<AvisoDaPosCondicao item={ITEM} onUsar={(t) => usadas.push(t)} />));
    expect(text()).toContain('“Internet” já aparece na tela em que esta etapa começa');
    expect(allByRole('button', /^Usar “/)).toHaveLength(3);
    await click(byRole('button', /^Usar “Connected devices”/));
    expect(usadas).toEqual([PRONTA('Connected devices')]);
    await act(async () => root.render(<AvisoDaPosCondicao item={{ ...ITEM, sugestoes: [], sugestoes_prontas: [] }} onUsar={() => {}} />));
    expect(allByRole('button', /^Usar “/)).toHaveLength(0);
    expect(text()).toContain('passaria sem agir');
  });
});

describe('na prévia', () => {
  it('o aviso vai para dentro da etapa citada, a frase não se repete na lista de avisos e o Salvar diz qual etapa trocar', async () => {
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA({ warnings: [ITEM.message, 'Outro aviso qualquer.'], pos_condicoes_ja_valem: [ITEM] })));
    await abrirComProposta();
    await waitFor(() => expect(avisos()).toHaveLength(1));
    let daEtapa: HTMLElement | null = avisos()[0]!;                                              // sobe até o bloco da etapa que o contém
    while (daEtapa && !/Abrir a Internet/.test(daEtapa.textContent ?? '')) daEtapa = daEtapa.parentElement;
    expect(daEtapa).not.toBeNull();
    expect(daEtapa!.textContent).not.toContain('Conferir a lista');                             // e é só o da etapa 1, não o da lista inteira
    expect(allByRole('button', /^Usar “/)).toHaveLength(3);
    const lista = document.querySelector('ul[aria-label="Avisos da prévia"]')!;
    expect(lista.textContent).toContain('Outro aviso qualquer.');
    expect(lista.textContent).not.toContain('já aparece na tela em que ela começa');            // a frase já está na etapa
    expect(salvar().textContent).toContain('Troque o que a etapa “Abrir a Internet” confere');
    expect(salvar().getAttribute('aria-disabled')).toBe('true');
  });

  it('Usar “texto” troca a conferência da etapa, relê a prévia e, sem ocorrência, o aviso some e o Salvar libera', async () => {
    let usouSugestao = false;
    backend.on('POST', /\/training\/trn-1\/preview$/, (c) => {
      const corpo = c.body as { proposal: { steps: { postcondition: { value: string } }[] } };
      usouSugestao = corpo.proposal.steps[0]!.postcondition.value === 'Network & internet';
      return json(usouSugestao ? PREVIA() : PREVIA({ warnings: [ITEM.message], pos_condicoes_ja_valem: [ITEM] }));
    });
    await abrirComProposta();
    await waitFor(() => expect(avisos()).toHaveLength(1));
    await click(byRole('button', /^Usar “Network & internet”/));
    await waitFor(() => expect(usouSugestao).toBe(true), ESPERA_DA_PREVIA_MS + 3000);      // a prévia releu com o texto novo
    await waitFor(() => expect(avisos()).toHaveLength(0));
    expect(corpoDaUltimaPrevia().proposal.steps[0]!.postcondition.value).toBe('Network & internet');
    expect(corpoDaUltimaPrevia().proposal.steps[1]!.postcondition.value).toBe('Wi-Fi');          // a outra etapa não mudou
    await botaoPronto(/^Salvar como fluxo/);
    expect(salvar().getAttribute('aria-disabled')).not.toBe('true');
  });

  it('v1.91: Usar aplica o kind e o value da sugestão pronta, mantendo o texto do botão (desc==Back é element_present)', async () => {
    const prontas = [{ kind: 'element_present', value: 'desc==Back', texto: 'Back' }, { kind: 'text_visible', value: 'Wi-Fi', texto: 'Wi-Fi' }];
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA({
      warnings: [ITEM.message], pos_condicoes_ja_valem: [{ ...ITEM, sugestoes: ['Back', 'Wi-Fi'], sugestoes_prontas: prontas }],
    })));
    await abrirComProposta();
    await waitFor(() => expect(avisos()).toHaveLength(1));
    expect(allByRole('button', /^Usar “/).map((b) => b.textContent)).toEqual(['Usar “Back”', 'Usar “Wi-Fi”']);
    await click(byRole('button', /^Usar “Back”/));
    await waitFor(() => expect(corpoDaUltimaPrevia().proposal.steps[0]!.postcondition).toMatchObject({ kind: 'element_present', value: 'desc==Back' }), ESPERA_DA_PREVIA_MS + 3000);
    expect(corpoDaUltimaPrevia().proposal.steps[1]!.postcondition).toMatchObject({ kind: 'text_visible', value: 'Wi-Fi' });   // a outra etapa não mudou
  });

  it('v1.91: Usar com text_visible aplica o texto como antes', async () => {
    const prontas = [{ kind: 'text_visible', value: 'Network & internet', texto: 'Network & internet' }];
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA({ warnings: [ITEM.message], pos_condicoes_ja_valem: [{ ...ITEM, sugestoes_prontas: prontas }] })));
    await abrirComProposta();
    await waitFor(() => expect(avisos()).toHaveLength(1));
    await click(byRole('button', /^Usar “Network & internet”/));
    await waitFor(() => expect(corpoDaUltimaPrevia().proposal.steps[0]!.postcondition).toMatchObject({ kind: 'text_visible', value: 'Network & internet' }), ESPERA_DA_PREVIA_MS + 3000);
  });

  it('v1.91: a prévia 200 com code duplicate_command trava o Salvar com a mensagem, a mostra no Comando e não a repete nos avisos', async () => {
    const MENSAGEM = 'Já existe um fluxo com este comando: mude o texto do comando.';
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA({ code: 'duplicate_command', message: MENSAGEM, warnings: [MENSAGEM, 'Aviso antigo.'] })));
    await abrirComProposta();
    await waitFor(() => expect(salvar().getAttribute('aria-disabled')).toBe('true'));
    expect(salvar().textContent).toContain('Corrija o comando');
    await waitFor(() => expect(text()).toContain(MENSAGEM));
    expect((text().match(/Já existe um fluxo com este comando/g) ?? []).length).toBe(1);
    expect(document.querySelector('ul[aria-label="Avisos da prévia"]')?.textContent).toContain('Aviso antigo.');
  });

  it('v1.91: prévia com code nulo não trava o Salvar', async () => {
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA({ code: null, message: null })));
    await abrirComProposta();
    await botaoPronto(/^Salvar como fluxo/);
    expect(salvar().getAttribute('aria-disabled')).not.toBe('true');
  });

  it('item que cita uma etapa que a proposta não tem não vira aviso na etapa e a frase fica na lista', async () => {
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA({ warnings: [ITEM.message], pos_condicoes_ja_valem: [{ ...ITEM, etapa: 'inexistente' }] })));
    await abrirComProposta();
    await waitFor(() => expect(document.querySelector('ul[aria-label="Avisos da prévia"]')).not.toBeNull());
    expect(avisos()).toHaveLength(0);
    expect(document.querySelector('ul[aria-label="Avisos da prévia"]')!.textContent).toContain('já aparece na tela');
  });

  it('prévia sem a lista (backend anterior): nada muda, os avisos seguem como estavam', async () => {
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA({ warnings: ['Aviso antigo.'] })));
    await abrirComProposta();
    await waitFor(() => expect(document.querySelector('ul[aria-label="Avisos da prévia"]')?.textContent).toContain('Aviso antigo.'));
    expect(avisos()).toHaveLength(0);
  });
});

describe('na recusa do salvar', () => {
  const recusa = (lista?: unknown[]) => json({ detail: { code: 'pos_condicao_ja_vale', message: ITEM.message, ...(lista ? { pos_condicoes_ja_valem: lista } : {}) } }, 400);

  it('400 com a lista: o aviso entra na etapa, sem toast repetindo a frase; Usar troca o texto e libera o Salvar', async () => {
    backend.on('POST', /\/training\/trn-1\/save$/, () => recusa([ITEM, { ...ITEM, etapa: 'conferir', valor: 'Wi-Fi', sugestoes: ['Wi-Fi calling'], sugestoes_prontas: [PRONTA('Wi-Fi calling')] }]));
    await abrirComProposta();
    await click(await botaoPronto(/^Salvar como fluxo/));
    await waitFor(() => expect(avisos()).toHaveLength(2));
    await flush(ATRASO_MAXIMO + 30);
    expect(useToastStore.getState().toasts.some((t) => t.title === 'Não foi possível salvar o fluxo')).toBe(false);
    expect(salvar().textContent).toContain('as etapas “Abrir a Internet”, “Conferir a lista” conferem');
    await click(byRole('button', /^Usar “Wi-Fi calling”/));
    await waitFor(() => expect(corpoDaUltimaPrevia().proposal.steps[1]!.postcondition.value).toBe('Wi-Fi calling'), ESPERA_DA_PREVIA_MS + 3000);
    await waitFor(() => expect(avisos()).toHaveLength(0));
  });

  it('400 sem a lista (backend anterior ao v1.86): segue o caminho de sempre, com o toast e a frase no Salvar', async () => {
    backend.on('POST', /\/training\/trn-1\/save$/, () => recusa());
    await abrirComProposta();
    await click(await botaoPronto(/^Salvar como fluxo/));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.title === 'Não foi possível salvar o fluxo')).toBe(true));
    expect(avisos()).toHaveLength(0);
    expect(salvar().textContent).toContain('já aparece na tela em que ela começa');
  });
});
