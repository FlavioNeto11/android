// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import type { EntradaDoLivro } from './model';

/**
 * 31.131 (adendo v1.87): o fluxo que nasceu de uma prova (sessão de treino aberta como prova) leva o selo "Nascido de uma
 * prova" no Livro; o filtro "Prova" separa prova de uso real; o motivo do desligamento aparece como o backend o escreveu.
 * Prova `simulated`: nenhuma rota real foi chamada.
 */

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const MOTIVO = 'desligado de propósito: o fluxo nasceu de uma prova e não vale como uso real';

function entrada(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'fluxo', ref: 'f-1', state: 'disabled', native_status: 'disabled', title: 'Pesquise nas configurações', app: 'com.android.settings',
    origin: 'treino', side_effect: false, human_origin: false, requires_owner: false, created_at: '2026-10-06T10:00:00Z',
    state_at: '2026-10-06T10:29:00Z', last_used_at: null, uses: 0, evidence: { for: 0, against: 0 }, count: null,
    detail: null, acoes: [], por_que_nao_publica: null, ...over,
  };
}
const DE_PROVA = entrada({ ref: 'f-prova', title: 'Pesquise nas configurações por {termo}', nascido_de_prova: true,
  por_que_nao_publica: { codigo: 'vetado', espera_o_dono: false, detalhe: MOTIVO } });
const REAL = entrada({ ref: 'f-real', title: 'Abra o Wi-Fi', state: 'published', native_status: 'active' });
const DESLIGADO_POR_FALHA = entrada({ ref: 'f-falha', title: 'Abra o Bluetooth', nascido_de_prova: false,
  por_que_nao_publica: { codigo: 'vetado', espera_o_dono: false, detalhe: 'desligado depois de 3 falhas seguidas' } });
const RECEITA_COM_A_MARCA = entrada({ kind: 'receita', ref: '9', title: 'Tocar no Wi-Fi', nascido_de_prova: true });
const RELIGADO = entrada({ ref: 'f-religado', title: 'Abra a busca', state: 'published', native_status: 'active', nascido_de_prova: true, em_uso_real_desde: '2026-10-07T10:00:00Z' });
const RELIGADO_E_DESLIGADO = entrada({ ref: 'f-desligou', title: 'Abra o menu', nascido_de_prova: true, em_uso_real_desde: '2026-10-07T10:00:00Z' });

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.install();
  for (const rota of ['pendentes', 'revisar', 'intencao']) backend.on('GET', new RegExp(`^/api/aprendizado/${rota}$`), () => json({ itens: [], total: 0 }));
  backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [], sem_eixo: null, nao_resolvido: null }));
  // O backend desta prova IGNORA o parâmetro `nascido_de_prova`: quem separa é a guarda do painel.
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [DE_PROVA, REAL, DESLIGADO_POR_FALHA, RECEITA_COM_A_MARCA], total: 4, contagem: {} }));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: {} } });
});

async function montar(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<AprendizadoPage />));
  await click(byRole('tab', /^Aprendido/, container));
}

const item = (ref: string) => container.querySelector(`[data-item="${ref}"]`) as HTMLElement | null;
const selos = (el: HTMLElement) => Array.from(el.querySelectorAll('span')).filter((x) => !x.children.length && x.textContent === 'Nascido de uma prova');
const rotulosDosItens = () => Array.from(container.querySelectorAll('[data-item]')).map((e) => e.getAttribute('data-item'));

describe('31.131: o fluxo nascido de uma prova no Livro', () => {
  it('só o fluxo com a marca leva o selo, e o motivo do desligamento aparece como o backend o escreveu', async () => {
    await montar();
    await waitFor(() => expect(item('fluxo:f-prova')).toBeTruthy());
    expect(selos(item('fluxo:f-prova')!)).toHaveLength(1);
    expect(item('fluxo:f-prova')!.querySelector('[title^="Nasceu de uma prova"]')).toBeTruthy();   // a explicação está no selo
    expect(text(item('fluxo:f-prova')!)).toContain(MOTIVO);                                          // o motivo vai como veio
    expect(text(item('fluxo:f-prova')!)).toContain('Desligado');
    expect(selos(item('fluxo:f-real')!)).toHaveLength(0);                                            // sem o campo (backend anterior)
    expect(selos(item('fluxo:f-falha')!)).toHaveLength(0);                                           // `false`: desligado por falha, não por prova
    expect(text(item('fluxo:f-falha')!)).toContain('desligado depois de 3 falhas seguidas');
    expect(selos(item('receita:9')!)).toHaveLength(0);                                               // a marca é do fluxo
  });

  it('o filtro "Prova" manda o parâmetro e separa prova de uso real, mesmo com um backend que ainda ignora o parâmetro', async () => {
    await montar();
    await waitFor(() => expect(rotulosDosItens()).toHaveLength(4));
    const filtro = () => byRole('combobox', /^Prova/, container) as HTMLSelectElement;
    expect(Array.from(filtro().options).map((o) => o.textContent)).toEqual(['Todos', 'Só os nascidos de uma prova', 'Sem os de prova (uso real)']);
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/).at(-1)!.query.has('nascido_de_prova')).toBe(false);

    await setValue(filtro(), 'so_prova');
    await waitFor(() => expect(rotulosDosItens()).toEqual(['fluxo:f-prova', 'receita:9']));
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/).at(-1)!.query.get('nascido_de_prova')).toBe('true');

    await setValue(filtro(), 'sem_prova');
    await waitFor(() => expect(rotulosDosItens()).toEqual(['fluxo:f-real', 'fluxo:f-falha']));
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/).at(-1)!.query.get('nascido_de_prova')).toBe('false');

    await setValue(filtro(), '');
    await waitFor(() => expect(rotulosDosItens()).toHaveLength(4));
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/).at(-1)!.query.has('nascido_de_prova')).toBe(false);
  });

  it('o filtro "Só os nascidos de uma prova" sem nenhum diz que não há, e não mostra a lista inteira', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [REAL, DESLIGADO_POR_FALHA], total: 2, contagem: {} }));
    await montar();
    await waitFor(() => expect(rotulosDosItens()).toHaveLength(2));
    await setValue(byRole('combobox', /^Prova/, container) as HTMLSelectElement, 'so_prova');
    await waitFor(() => expect(text(container)).toContain('Nada aprendido com este filtro'));
    expect(allByRole('listitem', /.*/, container).filter((e) => e.hasAttribute('data-item'))).toHaveLength(0);
  });
});

// 31.146: a contagem "N de prova" ao lado de Fluxo na faixa do Livro.
describe('31.146: a contagem "N de prova" na faixa do Livro', () => {
  const COM_FLUXO = { fluxo: { disabled: 2, published: 1 } };
  const chip = () => byRole('button', /de prova$/, container);

  it('mostra quantos fluxos nasceram de prova (só fluxo, não a receita) e, ao clicar, aplica o filtro Prova; clicar de novo tira', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [DE_PROVA, REAL, DESLIGADO_POR_FALHA, RECEITA_COM_A_MARCA], total: 4, contagem: COM_FLUXO }));
    backend.on('GET', /^\/api\/flows$/, () => json([{ id: 'f-prova', nascido_de_prova: true }, { id: 'f-real', nascido_de_prova: false }]));
    await montar();
    await waitFor(() => expect(chip()).toBeTruthy());
    expect(text(chip())).toBe('1 de prova');
    expect(chip().getAttribute('aria-pressed')).toBe('false');
    expect(backend.callsTo('GET', /^\/api\/flows$/)[0]!.query.get('nascido_de_prova')).toBe('true');
    await click(chip());
    await waitFor(() => expect(rotulosDosItens()).toEqual(['fluxo:f-prova', 'receita:9']));
    expect((byRole('combobox', /^Prova/, container) as HTMLSelectElement).value).toBe('so_prova');
    expect(chip().getAttribute('aria-pressed')).toBe('true');
    await click(chip());
    await waitFor(() => expect(rotulosDosItens()).toHaveLength(4));
    expect((byRole('combobox', /^Prova/, container) as HTMLSelectElement).value).toBe('');
  });

  it('some quando nenhum fluxo nasceu de prova, com o backend sem a marca e sem a rota', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [REAL, DESLIGADO_POR_FALHA, RECEITA_COM_A_MARCA], total: 3, contagem: COM_FLUXO }));
    backend.on('GET', /^\/api\/flows$/, () => json([{ id: 'f-real' }, { id: 'f-falha', nascido_de_prova: false }]));
    await montar();
    await waitFor(() => expect(rotulosDosItens()).toHaveLength(3));
    expect(container.textContent).toContain('2 desligados');     // a faixa de contagem está lá
    expect(allByRole('button', /de prova$/, container)).toHaveLength(0);
    // com a rota ausente (404 do FakeBackend) também some, sem erro na tela
    backend.on('GET', /^\/api\/flows$/, () => apiError(404, 'not_found', 'sem rota'));
    await click(byRole('button', /^Atualizar$/, container));
    await waitFor(() => expect(backend.callsTo('GET', /^\/api\/flows$/).length).toBeGreaterThan(1));
    expect(allByRole('button', /de prova$/, container)).toHaveLength(0);
  });

  it('o número é o dos fluxos de prova do servidor, não o da lista na tela', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [REAL], total: 1, contagem: COM_FLUXO }));
    backend.on('GET', /^\/api\/flows$/, () => json([1, 2, 3].map((n) => ({ id: `f${n}`, nascido_de_prova: true }))));
    await montar();
    await waitFor(() => expect(chip()).toBeTruthy());
    expect(text(chip())).toBe('3 de prova');
    expect(rotulosDosItens()).toEqual(['fluxo:f-real']);
  });
});

// 31.134: cada botão de decisão no Livro diz o efeito (no title e, ao abrir, acima do motivo).
describe('31.134: o efeito de cada botão do Livro', () => {
  const ACAO = (to: string, rotulo: string) => ({ to, rotulo, exige_motivo: true });
  const TEXTOS: Record<string, string> = {
    reativar: 'Publica o item de novo, já valendo, sem uma nova prova.',
    devolver: 'Tira o fluxo de desligado sem publicar: ele fica parado e volta a se provar, com a evidência contada de novo.',
  };

  it('"Reativar" e "Devolver à prova" explicam o efeito no title, e a decisão aberta repete a frase antes do motivo', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [{ ...DE_PROVA, acoes: [ACAO('published', 'reativar'), ACAO('candidate', 'devolver')] }], total: 1, contagem: {} }));
    await montar();
    await waitFor(() => expect(item('fluxo:f-prova')).toBeTruthy());
    const linha = item('fluxo:f-prova')!;
    expect(byRole('button', /^Reativar$/, linha).getAttribute('title')).toBe(TEXTOS.reativar);
    expect(byRole('button', /^Devolver à prova$/, linha).getAttribute('title')).toBe(TEXTOS.devolver);
    await click(byRole('button', /^Devolver à prova$/, linha));
    expect(text(linha)).toContain(TEXTOS.devolver);
    expect(allByRole('button', /^Confirmar volta à prova/, linha)).toHaveLength(1);
  });

  it('cada rótulo de ação do backend tem a sua frase de efeito', async () => {
    const rotulos = ['validar', 'aprovar', 'rejeitar', 'aposentar', 'desligar', 'reativar', 'devolver'];
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [{ ...REAL, acoes: rotulos.map((r, i) => ACAO(['validated', 'published', 'disabled', 'deprecated', 'disabled', 'published', 'candidate'][i]!, r)) }], total: 1, contagem: {} }));
    await montar();
    await waitFor(() => expect(item('fluxo:f-real')).toBeTruthy());
    const botoes = allByRole('button', /^(Validar|Aprovar|Rejeitar|Aposentar|Desligar|Reativar|Devolver à prova)$/, item('fluxo:f-real')!);
    expect(botoes).toHaveLength(7);
    for (const b of botoes) expect((b.getAttribute('title') ?? '').length).toBeGreaterThan(20);
    expect(new Set(botoes.map((b) => b.getAttribute('title'))).size).toBe(7);
  });
});

describe('31.168: o selo "Em uso real desde" na linha do Livro', () => {
  const emUsoReal = (el: HTMLElement) => Array.from(el.querySelectorAll('span')).filter((x) => /^Em uso real desde /.test(x.textContent ?? '') && !x.children.length);

  it('o fluxo religado e ligado leva o selo com a data (a explicação no title); desligado de novo, sem a marca ou sem data, não', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [DE_PROVA, REAL, RELIGADO, RELIGADO_E_DESLIGADO], total: 4, contagem: {} }));
    await montar();
    await waitFor(() => expect(item('fluxo:f-religado')).toBeTruthy());
    expect(emUsoReal(item('fluxo:f-religado')!)).toHaveLength(1);
    expect(item('fluxo:f-religado')!.querySelector('[title^="Nasceu de uma prova e uma pessoa o religou"]')).toBeTruthy();
    expect(selos(item('fluxo:f-religado')!)).toHaveLength(1);                  // a marca de origem não se apaga
    expect(emUsoReal(item('fluxo:f-desligou')!)).toHaveLength(0);              // voltou a desligado: o backend zera, e a tela não insiste
    expect(emUsoReal(item('fluxo:f-prova')!)).toHaveLength(0);
    expect(emUsoReal(item('fluxo:f-real')!)).toHaveLength(0);
  });
});
