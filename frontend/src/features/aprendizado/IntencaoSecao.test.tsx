// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useToastStore } from '../../store/toasts';
import { IntencaoSecao } from './IntencaoSecao';
import { filtrarCandidatos, lerPerguntas, ordenarCandidatos } from './intencao';
import { rotuloDaLinhaDeSinal } from './model';

/**
 * "Qual era o pedido?" (item 30.25) contra o formato de `presentation/intencao.py`, com o backend simulado. Prova
 * `simulated` — nenhuma rota real foi chamada.
 */

const PERGUNTA = {
  review_id: 'lr-1', run_id: 'r1', criado_em: '2026-10-03T12:00:00Z', terminou_em: '2026-10-03T11:58:00Z',
  app: 'com.instagram.android', app_nome: 'Instagram', comando: 'curta o último post de @fulano', cadeia: 'empate',
  candidatos: [{ skill_id: 'curtir_post', nome: 'Curtir o post' }, { skill_id: 'abrir_perfil', nome: 'Abrir o perfil' }],
  empatados: ['curtir_post'], decisao_final: null, decidido_por: null,
};

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;
let respondida: boolean;

beforeEach(() => {
  installBrowserStubs();
  backend = new FakeBackend();
  backend.install();
  respondida = false;
  backend.on('GET', /^\/api\/aprendizado\/intencao$/, () => json(respondida ? { itens: [], total: 0 } : { itens: [PERGUNTA], total: 1 }));
  backend.on('POST', /^\/api\/aprendizado\/execucao\/r1\/intencao$/, (c) => {
    respondida = true;
    return json({ review_id: 'lr-1', run_id: 'r1', decisao_final: (c.body as { escolha: string }).escolha });
  });
  useToastStore.setState({ toasts: [] });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function montar(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<IntencaoSecao />);
  });
}

const opcoes = () => Array.from(container.querySelectorAll<HTMLInputElement>('input[type="radio"]'));
const opcao = (i: number): HTMLInputElement => {
  const o = opcoes()[i];
  if (!o) throw new Error(`sem a opção ${i}`);
  return o;
};
const responder = () => byRole('button', /^Responder/, container);

describe('Qual era o pedido? (30.25)', () => {
  it('mostra o comando e as opções em ordem alfabética, sem o palpite, e responde uma vez', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('“curta o último post de @fulano”'));
    expect(text(container)).toContain('Instagram');
    // Ordem alfabética pelo nome (o empate não sobe ninguém) e "Nenhuma destas" por último.
    expect(opcoes().map((o) => o.value)).toEqual(['abrir_perfil', 'curtir_post', 'nenhum']);
    expect(text(container)).not.toMatch(/empate|palpite do sistema:/i);
    expect(responder().getAttribute('aria-disabled')).toBe('true');
    await click(responder());
    expect(backend.callsTo('POST', /intencao$/)).toHaveLength(0);

    await click(opcao(1));
    expect(text(container)).toContain('Escolhida: Curtir o post');
    await click(responder());
    await waitFor(() => expect(text(container)).toContain('Nenhum pedido para identificar'));
    const [chamada] = backend.callsTo('POST', /intencao$/);
    expect(chamada?.body).toEqual({ escolha: 'curtir_post' });
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain('Resposta registrada');
  });

  it('"Nenhuma destas" é uma resposta', async () => {
    await montar();
    await waitFor(() => expect(opcoes()).toHaveLength(3));
    await click(opcao(2));
    await click(responder());
    await waitFor(() => expect(backend.callsTo('POST', /intencao$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /intencao$/)[0]?.body).toEqual({ escolha: 'nenhum' });
  });

  it('já respondida (409) avisa e atualiza; fora do catálogo (422) mostra o erro na pergunta', async () => {
    backend.on('POST', /^\/api\/aprendizado\/execucao\/r1\/intencao$/, () => {
      respondida = true;
      return json({ detail: { code: 'state_conflict', message: 'já respondida' } }, 409);
    });
    await montar();
    await waitFor(() => expect(opcoes()).toHaveLength(3));
    await click(opcao(0));
    await click(responder());
    await waitFor(() => expect(text(container)).toContain('Nenhum pedido para identificar'));
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain('Esta pergunta já foi respondida');

    respondida = false;
    backend.on('POST', /^\/api\/aprendizado\/execucao\/r1\/intencao$/, () =>
      json({ detail: { code: 'invalid', message: "'x' não é uma habilidade do catálogo" } }, 422));
    await act(async () => root.unmount());
    container.remove();
    await montar();
    await waitFor(() => expect(opcoes()).toHaveLength(3));
    await click(opcao(0));
    await click(responder());
    await waitFor(() => expect(container.querySelector('[role="alert"]')?.textContent).toContain('não é uma habilidade'));
    expect(text(container)).toContain('curta o último post');
  });

  it('com muitas opções aparece o filtro, sem acento e sem caixa', async () => {
    const muitos = Array.from({ length: 10 }, (_, i) => ({ skill_id: `s${i}`, nome: `Habilidade ${i}` }));
    backend.on('GET', /^\/api\/aprendizado\/intencao$/, () => json({
      itens: [{ ...PERGUNTA, candidatos: [...muitos, { skill_id: 'seguir', nome: 'Seguir perfil público' }] }], total: 1 }));
    await montar();
    await waitFor(() => expect(opcoes()).toHaveLength(12));
    await setValue(byRole('textbox', /Filtrar as opções/, container) as HTMLInputElement, 'PUBLICO');
    expect(opcoes().map((o) => o.value)).toEqual(['seguir', 'nenhum']);
  });

  it('erro de carga mostra o aviso e "Tentar de novo" recupera', async () => {
    let falhar = true;
    backend.on('GET', /^\/api\/aprendizado\/intencao$/, () => (falhar
      ? json({ detail: { code: 'boom', message: 'falhou' } }, 500) : json({ itens: [PERGUNTA], total: 3 })));
    await montar();
    await waitFor(() => expect(text(container)).toContain('falhou'));
    falhar = false;
    await click(byRole('button', /Tentar de novo/, container));
    await waitFor(() => expect(text(container)).toContain('Mostrando 1 de 3'));
  });
});

describe('leitura tolerante e ordem', () => {
  it('descarta pergunta sem ids ou sem candidatos e ordena por nome', () => {
    const lidas = lerPerguntas({ itens: [PERGUNTA, { run_id: 'r2' }, { ...PERGUNTA, review_id: 'lr-3', candidatos: [] }], total: 1 });
    expect(lidas.itens.map((p) => p.review_id)).toEqual(['lr-1']);
    expect(lidas.total).toBe(1);
    expect(ordenarCandidatos([{ skill_id: 'b', nome: 'Écran' }, { skill_id: 'a', nome: 'abrir' }, { skill_id: 'a', nome: 'x' }])
      .map((c) => c.skill_id)).toEqual(['a', 'b']);
    expect(filtrarCandidatos([{ skill_id: 'x', nome: 'Ação rápida' }], 'acao').map((c) => c.skill_id)).toEqual(['x']);
    expect(lerPerguntas(null)).toEqual({ itens: [], total: 0 });
  });

  it('o sinal da resposta não se chama "parecer da IA"', () => {
    expect(rotuloDaLinhaDeSinal({ kind: 'parecer_decidido', template: 'intencao' })).toBe('Disse qual era o pedido');
    expect(rotuloDaLinhaDeSinal({ kind: 'parecer_decidido', template: null })).toBe('Decidiu um parecer da IA');
  });
});
