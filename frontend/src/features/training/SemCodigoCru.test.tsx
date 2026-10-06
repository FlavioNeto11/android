// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { TrainingBar } from './TrainingBar';
import { elementoEmPalavras, textoDoConfere, TrainingReview } from './TrainingReview';

/**
 * 31.147 (parte simulada): varredura de código cru no Treino. Uma sessão com TODOS os tipos de entrada, de pós-condição e de
 * efeito é renderizada na barra (Salvas), na revisão (proposta, prévia, pacotes) e na sessão salva em leitura; o que a pessoa
 * lê e o que um leitor de tela anuncia (texto e `aria-label`) não pode ter código, chave de campo, marcador de dado, "undefined"
 * ou "null". Os ids e as chaves ficam em `title` (para quem desenvolve) e por isso não entram na varredura.
 */

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

/** O que não pode aparecer na tela. Cada item é um código que já vazou (ou quase) em alguma rodada de UX. */
const CRU: [string, RegExp][] = [
  ['kind da pós-condição', /\b(text_visible|element_present|element_absent|app_foreground|model_judged|items_collected|screen_changed)\b/],
  ['campo do contrato', /\b(side_effect|has_text|text_len|session_id|flow_id|input_count|instance_id|profile_id|origin_run_id|pacotes_aceitos)\b/],
  ['seletor da pós-condição', /\b(desc|text|id)==/],
  ['id de recurso do Android', /\bresource-id\b|\bcom\.[a-z.]+:id\//],
  ['marcador de dado da persona', /\{perfil_[a-z_]+\}/],
  ['origem crua', /\(panel\)|\bpor panel\b/],
  ['valor vazio ou quebrado', /\b(undefined|null|NaN)\b|\[object Object\]/],
  ['chave de etapa em snake_case', /\b[a-z]+(?:_[a-z0-9]+)+\b/],
];

const ENTRADA = (seq: number, type: string, extra: object = {}) => ({
  session_id: 'trn-v', seq, ts: '', type, x: 10, y: 10, x2: null, y2: null, key_name: null, text: null, has_text: false, text_len: null,
  package: 'com.android.settings', app_id: null, target: { text: 'Internet', unique: ['text'] }, screen_title: 'Configurações',
  screen_lines: ['Internet'], sensitive: false, ...extra,
});
const ENTRADAS = [
  ENTRADA(1, 'open_app', { target: null }),
  ENTRADA(2, 'tap', { target: { text: 'Search settings', id: 'com.android.settings:id/search_action_bar_title', unique: ['id', 'text'] } }),
  ENTRADA(3, 'tap', { target: { desc: 'Back', unique: ['desc'] } }),
  ENTRADA(4, 'long_press', { target: { text: 'Wi-Fi', unique: ['text'] } }),
  ENTRADA(5, 'swipe', { x2: 10, y2: 400, target: null }),
  ENTRADA(6, 'text', { text: 'wifi', has_text: true, text_len: 4 }),
  ENTRADA(7, 'text', { text: null, has_text: true, text_len: 8, sensitive: true, target: null }),
  ENTRADA(8, 'key', { key_name: 'BACK', target: null }),
  ENTRADA(9, 'tap', { x: 3, y: 4, target: null }),
];
const etapa = (key: string, title: string, inputs: number[], over: object = {}) => ({
  key, title, goal: title, inputs, side_effect: false, capability: null, bindings: [], app_id: null,
  postcondition: { kind: 'text_visible', value: 'Wi-Fi', description: 'a tela mostra o Wi-Fi' }, ...over,
});
const PROPOSTA = {
  summary: 'Abre as configurações, pesquisa um termo e confirma na tela.', command_template: 'abra as configurações e pesquise por {termo}',
  parameters: [{ name: 'termo', example: 'wifi', description: 'o texto a pesquisar' }],
  steps: [
    etapa('abrir_configuracoes', 'Abrir as Configurações', [1], { postcondition: { kind: 'app_foreground', value: 'com.android.settings', description: 'as Configurações abertas' } }),
    etapa('abrir_busca', 'Abrir a busca', [2, 3], { postcondition: { kind: 'element_present', value: 'desc==Back', description: 'o botão voltar aparece' }, pacotes_aceitos: ['com.google.android.settings.intelligence'] }),
    etapa('digitar_termo', 'Digitar o termo', [6, 7], { side_effect: true, bindings: [{ name: 'nome', value: '{perfil_nome}' }], postcondition: { kind: 'model_judged', value: 'o termo aparece', description: 'o termo digitado aparece no campo' } }),
    etapa('voltar', 'Voltar para a lista', [5, 8, 9], { postcondition: { kind: 'text_visible', value: 'Olá {perfil_nome}', description: 'a tela cumprimenta a pessoa' } }),
  ],
  discarded: [{ seq: 4, why: 'toque errado' }], questions: ['O texto muda?'],
};
const SESSAO = (status: string, over: object = {}) => ({
  id: 'trn-v', instance_id: 'android-01', profile_id: 'ig-1', app_id: null, intent: 'abra as configurações e pesquise por wifi', status,
  operator: null, proposal: status === 'recorded' ? null : PROPOSTA, flow_id: status === 'saved' ? 'f-1234' : null, created_at: '', finished_at: '',
  updated_at: '', input_count: ENTRADAS.length, inputs: ENTRADAS, nascido_de_prova: false, ...over,
});
const PREVIA = {
  steps: PROPOSTA.steps.map((s: { key: string; title: string; pacotes_aceitos?: string[] }) => ({ key: s.key, title: s.title, recipe: s.key !== 'voltar', reason: s.key !== 'voltar' ? 'receita será gravada ao salvar' : 'já havia receita ativa',
                                      pacotes_aceitos: s.pacotes_aceitos ?? [] })),
  warnings: [], pos_condicoes_ja_valem: [], code: null, message: null,
};

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training$/, () => json([{ ...SESSAO('saved'), inputs: undefined }]));
  backend.on('GET', /\/training\/trn-v$/, () => json(SESSAO('saved')));
  backend.on('GET', /\/instagram\/profiles$/, () => json([]));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('GET', /\/instances\/android-01\/personas$/, () => json([]));
  backend.on('GET', /\/aprendizado\/fluxo\//, () => json({ item: { kind: 'fluxo', ref: 'f-1234', state: 'disabled', title: 'x' }, evidencias: [], trilha: [], exposicoes: [] }));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
});

/** O que a pessoa lê e o que o leitor de tela anuncia; `title` fica de fora de propósito (é onde moram ids e chaves). */
function visivel(el: HTMLElement): string {
  const rotulos = Array.from(el.querySelectorAll('[aria-label]')).map((x) => x.getAttribute('aria-label') ?? '');
  return `${text(el)}\n${rotulos.join('\n')}`;
}
function cru(el: HTMLElement): string[] {
  const t = visivel(el);
  return CRU.flatMap(([nome, re]) => {
    const g = new RegExp(re.source, 'g');
    return Array.from(t.matchAll(g)).map((m) => `${nome}: “${m[0]}” em …${t.slice(Math.max(0, (m.index ?? 0) - 40), (m.index ?? 0) + 50).replace(/\n/g, ' ')}…`);
  });
}

describe('31.147: o Treino não mostra código cru', () => {
  it('a revisão da proposta (etapas, pós-condições, efeito, parâmetro, prévia e pacotes) só tem palavras', async () => {
    backend.on('GET', /\/training\/trn-v$/, () => json(SESSAO('proposed')));
    backend.on('POST', /\/training\/trn-v\/preview$/, () => json(PREVIA));
    await act(async () => root.render(<TrainingReview sessionId="trn-v" onClose={() => {}} />));
    await waitFor(() => expect(text(container)).toContain('Abrir as Configurações'));
    await waitFor(() => expect(text(container)).toContain('a receita desta etapa é gravada'));
    expect(text(container)).toContain('Também aceita concluir em');        // a seção dos pacotes está na tela que se varre
    expect(text(container)).toContain('[nome da persona]');                  // o marcador do dado da persona virou palavras
    expect(cru(container)).toEqual([]);
  });

  it('a barra com a sessão gravada e a lista de salvas só tem palavras', async () => {
    backend.on('GET', /\/training$/, () => json([{ ...SESSAO('saved'), inputs: undefined }, { ...SESSAO('recorded', { id: 'trn-w' }), inputs: undefined }]));
    await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
    await waitFor(() => expect(text(container)).toContain('Salvas'));
    expect(cru(container)).toEqual([]);
  });

  it('a sessão salva em leitura (entradas, proposta, pacotes, estado do fluxo) só tem palavras', async () => {
    await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
    await waitFor(() => expect(text(container)).toContain('Salvas'));
    await click(await waitFor(() => allByRole('button', /^Ver o treinamento salvo/)[0]!));
    const d = await waitFor(() => byRole('dialog', /Treinamento salvo/));
    await waitFor(() => expect(text(d)).toContain(`${ENTRADAS.length} entradas`));
    await waitFor(() => expect(text(d)).toContain('No Livro agora'));
    expect(cru(d)).toEqual([]);
  });

  it('elementoEmPalavras: o seletor vira "com a descrição/texto/identificador", e o valor sem seletor fica entre aspas', () => {
    expect(elementoEmPalavras('desc==Back')).toBe('com a descrição “Back”');
    expect(elementoEmPalavras('text==Wi-Fi')).toBe('com o texto “Wi-Fi”');
    expect(elementoEmPalavras('id==search_action_bar_title')).toBe('com o identificador “search_action_bar_title”');
    expect(elementoEmPalavras('desc==Olá {perfil_nome}')).toBe('com a descrição “Olá [nome da persona]”');
    expect(elementoEmPalavras('Botão voltar')).toBe('“Botão voltar”');
    expect(elementoEmPalavras('')).toBe('');
    expect(textoDoConfere({ kind: 'element_present', value: 'desc==Back', description: '' })).toBe('existe o elemento com a descrição “Back”');
    expect(textoDoConfere({ kind: 'element_present', value: '', description: '' })).toBe('existe o elemento esperado');
  });

  it('a varredura pega o que procura: um texto com código cru é apontado', () => {
    const el = document.createElement('div');
    el.innerHTML = '<p>Confere: text_visible desc==Back {perfil_nome} (panel) undefined</p>';
    expect(cru(el).map((x) => x.split(':')[0])).toEqual(expect.arrayContaining([
      'kind da pós-condição', 'seletor da pós-condição', 'marcador de dado da persona', 'origem crua', 'valor vazio ou quebrado',
    ]));
  });
});
