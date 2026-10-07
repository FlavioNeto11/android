// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { PersonaOnDevice } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, byRole, click, flush, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { TrainingBar } from './TrainingBar';

/**
 * 31.120: a sessão de treino salva abre em LEITURA a partir de "Salvas": entradas, proposta, origem (execução, etapa e
 * tentativa, só ids), diagnóstico e a persona só como marca. Prova `simulated`.
 */

const ATRASO_MAXIMO = Number(process.env.ATRASO_DO_FETCH_MS ?? 0);

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const RUN = 'r-20261006053318-c04149';
const ORIGEM = {
  run_id: RUN, step_id: `${RUN}:android-01:v1:check_item`, step_key: 'check_item', attempt_id: `${RUN}:android-01:v1:check_item:a2`,
  motivo: 'A tela esperada não apareceu.',
  context: { disponivel: false },
  diagnostico: { causa: 'teto_de_ia', rotulo: 'a IA gastou o orçamento da etapa', pergunta: 'Mostre o caminho curto, toque a toque, para a etapa não depender da IA.',
                 fatos: [{ codigo: 'tipo', valor: 'ia_orcamento' }], proposta: null, amostra: 1 },
};
const ENTRADA = (seq: number, type: string, extra: object = {}) => ({
  session_id: 'trn-s', seq, ts: '', type, x: 10, y: 10, x2: null, y2: null, key_name: null, text: null, has_text: false, text_len: null,
  package: 'com.android.settings', app_id: null, target: null, screen_title: null, screen_lines: [], sensitive: false, ...extra,
});
const PROPOSTA = {
  summary: 'Abre as configurações e confirma um item na tela.', command_template: 'abra as configurações e confirme o item {item} na tela',
  parameters: [{ name: 'item', example: 'Safety & emergency', description: 'o item a confirmar' }],
  steps: [
    { key: 'rolar', title: 'Rolar a lista para ver o item', goal: 'rolar', inputs: [1, 2], side_effect: false, capability: null, bindings: [], app_id: null,
      postcondition: { kind: 'app_foreground', value: 'com.android.settings', description: 'As Configurações continuam em primeiro plano' } },
  ],
  discarded: [{ seq: 3, why: 'toque errado' }], questions: [],
};
const SALVA = {
  id: 'trn-s', instance_id: 'android-01', profile_id: 'ig-1', app_id: null, intent: 'Corrigir a etapa «Confirmar item na tela»', status: 'saved',
  operator: null, proposal: PROPOSTA, flow_id: 'f-455f91437856', created_at: '', finished_at: '', updated_at: '', input_count: 3,
  inputs: [ENTRADA(1, 'swipe'), ENTRADA(2, 'tap'), ENTRADA(3, 'tap')], origin: ORIGEM,
};
const persona: PersonaOnDevice = { profile_id: 'ig-1', username: null, display_name: 'Ana Exemplo', name: 'Ana Exemplo', status: 'active', app_id: null, is_primary: true, bound_at: null, session: null };

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training$/, () => json([{ ...SALVA, inputs: undefined }]));
  backend.on('GET', /\/training\/trn-s$/, () => json(SALVA));
  backend.on('GET', /\/training\/trn-s\/rendimento$/, () => json({ sessao: 'trn-s', receitas: [], resumo: { usado_de_verdade: false }, fluxo: null, execucoes_do_fluxo: { real: 0, prova: 0, simulada: 0 }, licoes: [], vizinhos: [] }));   // 31.203
  backend.on('GET', /\/instances\/android-01\/personas$/, () => json([persona]));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
});

const barra = () => <TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />;

async function abrirSalva(): Promise<HTMLElement> {
  await act(async () => root.render(barra()));
  await waitFor(() => expect(text()).toContain('Salvas (1)'));
  await click(await waitFor(() => byRole('button', /^Ver o treinamento salvo “Corrigir a etapa «Confirmar item na tela»”$/)));
  return waitFor(() => byRole('dialog', /Treinamento salvo/));
}

it('"Salvas" ganha o Ver, que abre a sessão em leitura com entradas, proposta, origem em ids e diagnóstico', async () => {
  const d = await abrirSalva();
  await waitFor(() => expect(d.textContent).toContain('3 entradas'));
  expect(d.textContent).toContain('Só leitura: nada daqui roda nem muda.');
  expect(d.textContent).toContain('f-455f91437856');
  expect(d.textContent).toContain('salvo');
  // entradas
  const entradas = d.querySelector('ol[aria-label="Entradas gravadas"]')!;
  expect(entradas.querySelectorAll('li')).toHaveLength(3);
  expect(entradas.querySelectorAll('li')[2]!.className).toMatch(/discarded/);                 // a descartada da proposta vem riscada
  // proposta
  expect(d.textContent).toContain('Abre as configurações e confirma um item na tela.');
  expect(d.textContent).toContain('abra as configurações e confirme o item {item} na tela');
  expect(d.textContent).toContain('{item}');
  expect(d.textContent).toContain('Rolar a lista para ver o item');
  expect(d.textContent).toContain('#1, #2');
  expect(d.textContent).toContain('1 entrada descartada da proposta.');
  // origem: só ids + diagnóstico + link da execução
  const origem = d.querySelector<HTMLElement>('section[aria-label="Origem do treino"]')!;
  expect(origem.textContent).toContain(`${RUN}:android-01:v1:check_item`);
  expect(origem.textContent).toContain(`${RUN}:android-01:v1:check_item:a2`);
  expect(origem.textContent).toContain('Causa provável: a IA gastou o orçamento da etapa');
  expect(origem.textContent).toContain('O que mostrar: Mostre o caminho curto');
  expect(byRole('link', /corrige uma falha/, origem).getAttribute('href')).toBe(`#/execucoes/${RUN}`);
});

it('a leitura não oferece ação nenhuma: só o Fechar; a persona aparece só como marca, com o nome no rótulo', async () => {
  const d = await abrirSalva();
  await waitFor(() => expect(d.querySelector('[aria-label="Persona: Ana Exemplo"]')).not.toBeNull());
  expect(d.querySelector('[aria-label="Persona: Ana Exemplo"]')!.textContent).toBe('AE');        // a marca são as iniciais
  expect(d.textContent).not.toContain('Ana Exemplo');                                              // o nome fica só no rótulo acessível, não impresso
  const botoes = [...d.querySelectorAll('button')].map((b) => b.textContent!.trim()).filter(Boolean);
  expect(botoes.filter((t) => !/^(Fechar|Mostrar|Ocultar|Por que a plataforma|O que a execução fez)/.test(t) && !/^×$/.test(t))).toEqual([]);
  expect(allByRole('button', /Salvar|Descartar|Pedir proposta|Refazer/, d)).toHaveLength(0);
  await click(byRole('button', /^Fechar$/, d));
  await waitFor(() => expect(allByRole('dialog', /Treinamento salvo/)).toHaveLength(0));
  expect(backend.calls.filter((c) => c.method !== 'GET')).toHaveLength(0);                       // ler nunca escreve
});

it('sessão sem entradas e sem proposta diz isso; a recusa do backend mostra o erro e "Tentar de novo" lê outra vez', async () => {
  let tentativas = 0;
  backend.on('GET', /\/training\/trn-s$/, () => {
    tentativas += 1;
    return tentativas === 1 ? apiError(500, 'falha', 'O servidor não respondeu.') : json({ ...SALVA, proposal: null, inputs: [], input_count: 0, origin: null, flow_id: null });
  });
  await act(async () => root.render(barra()));
  await waitFor(() => expect(text()).toContain('Salvas (1)'));
  await click(await waitFor(() => byRole('button', /^Ver o treinamento salvo/)));
  await waitFor(() => expect(text()).toContain('Não foi possível abrir o treinamento'));
  await click(byRole('button', /^Tentar de novo$/));
  const d = await waitFor(() => byRole('dialog', /Treinamento salvo/));
  await waitFor(() => expect(d.textContent).toContain('Nada foi gravado nesta sessão.'));
  expect(d.textContent).toContain('Esta sessão não chegou a ter proposta.');
  expect(d.querySelector('section[aria-label="Origem do treino"]')).toBeNull();
  await flush(ATRASO_MAXIMO + 30);
});

it('31.129: a leitura da sessão salva diz em qual pacote vizinho a etapa também conclui', async () => {
  const comPacote = { ...PROPOSTA, steps: [{ ...PROPOSTA.steps[0]!, pacotes_aceitos: ['com.google.android.googlequicksearchbox', 'com.exemplo.vizinho'] }] };
  backend.on('GET', /\/training\/trn-s$/, () => json({ ...SALVA, proposal: comPacote }));
  const d = await abrirSalva();
  await waitFor(() => expect(d.textContent).toContain('Também aceita concluir em: com.google.android.googlequicksearchbox, com.exemplo.vizinho'));
});

it('31.129: sem o campo (etapa antiga) ou com a lista vazia, nenhuma linha', async () => {
  backend.on('GET', /\/training\/trn-s$/, () => json({ ...SALVA, proposal: { ...PROPOSTA, steps: [{ ...PROPOSTA.steps[0]!, pacotes_aceitos: [] }] } }));
  const d = await abrirSalva();
  await waitFor(() => expect(d.textContent).toContain('Rolar a lista para ver o item'));
  expect(d.textContent).not.toContain('Também aceita concluir em');
});

// 31.132: o diálogo da sessão salva diz o estado de hoje do fluxo (o Livro manda) e leva ao item.
it('31.132: o fluxo da sessão salva mostra o estado no Livro (Desligado) e o link para o item', async () => {
  backend.on('GET', /\/aprendizado\/fluxo\/f-455f91437856$/, () => json({
    item: { kind: 'fluxo', ref: 'f-455f91437856', state: 'disabled', title: 'Confirmar item', nascido_de_prova: true },
    evidencias: [], trilha: [], exposicoes: [],
  }));
  const d = await abrirSalva();
  const estado = await waitFor(() => { const e = d.querySelector('[aria-label="Estado do fluxo no Livro"]'); expect(e).toBeTruthy(); return e as HTMLElement; });
  expect(estado.textContent).toContain('No Livro agora: Desligado');
  expect(estado.textContent).toContain('só uma pessoa o reativa');
  expect(byRole('link', /Abrir no Livro/, d).getAttribute('href')).toBe('#/aprendizado?aba=aprendido&item=fluxo%3Af-455f91437856');
  await flush(ATRASO_MAXIMO + 30);
});

// 31.134: o "confere" da proposta salva também fala em palavras, sem o marcador cru do dado da persona.
it('31.134: o confere da etapa na sessão salva troca o marcador da persona por palavras', async () => {
  const marcada = { ...SALVA, proposal: { ...PROPOSTA, steps: [{ ...PROPOSTA.steps[0]!, postcondition: { kind: 'text_visible', value: 'x', description: 'O campo mostra o texto {perfil_nome}' } }] } };
  backend.on('GET', /\/training\/trn-s$/, () => json(marcada));
  const d = await abrirSalva();
  await waitFor(() => expect(d.textContent).toContain('confere: O campo mostra o texto [nome da persona]'));
  expect(d.textContent).not.toContain('{perfil_nome}');
  await flush(ATRASO_MAXIMO + 30);
});

it('31.189: com a cópia mascarada (v1.109), a leitura mostra a cópia e não a proposta crua', async () => {
  const crua = { ...PROPOSTA, summary: 'Abre o perfil de Marina Souza.', command_template: 'abra o perfil de Marina Souza' };
  const mascarada = { ...crua, summary: 'Abre o perfil de {contato}.', command_template: 'abra o perfil de {contato}' };
  backend.on('GET', /\/training\/trn-s$/, () => json({ ...SALVA, proposal: crua, proposal_exibicao: mascarada }));
  const d = await abrirSalva();
  await waitFor(() => expect(d.textContent).toContain('Abre o perfil de {contato}.'));
  expect(d.textContent).toContain('abra o perfil de {contato}');
  expect(d.textContent).not.toContain('Marina Souza');
});
