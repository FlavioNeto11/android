// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { AprendizadoPage } from './AprendizadoPage';
import type { EntradaDoLivro } from './model';

/**
 * Pacote A6 do ADR-054: a página Aprendizado contra o contrato do livro (A1) e o formato esperado de A3/A4, com o
 * backend simulado. Prova `simulated` — nenhuma rota real foi chamada.
 */

function entrada(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'receita', ref: '1', state: 'validated', native_status: 'validated', title: 'Item', app: 'com.whatsapp',
    origin: 'execucao', side_effect: true, human_origin: false, requires_owner: true, created_at: '2026-09-28T10:00:00Z',
    state_at: '2026-09-28T10:00:00Z', last_used_at: null, uses: 0, evidence: { for: 3, against: 0 }, count: null,
    detail: null, ...over,
  };
}

const RECEITA = entrada({ kind: 'receita', ref: '12', title: 'Enviar oi para o contato' });
const LICAO = entrada({ kind: 'licao', ref: 'li-abc', state: 'candidate', native_status: null, side_effect: false,
                        human_origin: true, title: 'Role a lista antes de procurar o contato', state_at: '2026-09-27T10:00:00Z' });
const LEGADO = entrada({ kind: 'receita', ref: '40', state: 'published', native_status: 'active', title: 'Curtir a última foto' });
const PUBLICADO = entrada({ kind: 'fluxo', ref: 'f-9', state: 'published', native_status: 'active', side_effect: false,
                            requires_owner: false, title: 'Abrir o perfil', uses: 7 });
const MEMORIA = entrada({ kind: 'memoria', ref: 'ig-1', state: null, native_status: null, side_effect: false,
                          requires_owner: false, title: 'Marina Costa', count: 12 });

const FALHAS = {
  dias: 14, outro_pct: 4,
  grupos: [
    { id: 'fk-a1b2c3d4e5', app_package: 'com.instagram.android', capability: 'abrir_perfil', failure_kind: 'app_anr',
      camada: 'aparelho', onde_alterar: { arquivos: ['backend/app/devices/manager.py'], doc: 'docs/dominios/parque.md',
                                           prova: 'a mesma etapa no mesmo aparelho' },
      ocorrencias: 6, taxa: 0.25, execucoes: 4, aparelhos: 2, usd_perdido: 0.4, min_perdidos: 12, intervencoes: 1,
      custo_total: 0.65, exemplos: [{ run_id: 'r-20260928165254-e31953', attempt_id: 'a-7', erro: 'ANR' }] },
    { id: 'fk-ffffffffff', app_package: 'com.whatsapp', capability: 'enviar_mensagem',
      failure_kind: 'verificacao_falso_positivo', ocorrencias: 1, custo_total: 0, falso_positivo: true, exemplos: [] },
  ],
};

const SINAIS = [
  { id: 3, kind: 'tomou_controle', polarity: 'negative', source_ref: 'takeover:a-1', created_by: 'sistema',
    created_at: '2026-09-28T10:00:00Z', run_id: 'r-1', app_package: 'com.instagram.android', capability: 'abrir_perfil', simulated: 0 },
];

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;
let clipboard: ReturnType<typeof vi.fn>;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [RECEITA, LICAO], total: 2 }));
  backend.on('GET', /^\/api\/aprendizado\/revisar$/, () => json({ itens: [LEGADO], total: 1 }));
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [PUBLICADO, MEMORIA, RECEITA], total: 3,
                                                          contagem: { fluxo: { published: 1 }, memoria: { '-': 12 }, receita: { validated: 1 } } }));
  backend.on('GET', /^\/api\/aprendizado\/falhas$/, () => json(FALHAS));
  // O formato de `presentation/feedback.py` (A4): `{sinais, total, contagem, dias}`.
  backend.on('GET', /^\/api\/aprendizado\/sinais$/, (c) => json({ sinais: SINAIS, total: SINAIS.length,
                                                                  contagem: { tomou_controle: 1 }, dias: Number(c.query.get('dias')) }));
  backend.on('POST', /^\/api\/aprendizado\/[a-z]+\/[^/]+\/status$/, (c) => json({ item: { ...RECEITA, state: (c.body as { to: string }).to },
                                                                              evidencias: [], trilha: [], exposicoes: [] }));
  clipboard = vi.fn(async () => undefined);
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: clipboard }, configurable: true });
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
    root.render(<AprendizadoPage />);
  });
}

const item = (ref: string) => container.querySelector(`[data-item="${ref}"]`) as HTMLElement;
const statusCalls = () => backend.callsTo('POST', /\/status$/);

describe('página Aprendizado', () => {
  it('tem as quatro abas, e cada uma lê a sua rota', async () => {
    await montar();
    const nomes = allByRole('tab', /.*/, container).map((t) => t.textContent?.replace(/\d+$/, '').trim());
    expect(nomes).toEqual(['Para aprovar', 'Aprendido', 'O que mais falha', 'Sinais']);
    await waitFor(() => expect(text(container)).toContain('Enviar oi para o contato'));

    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(text(container)).toContain('Abrir o perfil'));
    expect(text(container)).toContain('12 lembranças');          // memória: só a contagem

    await click(byRole('tab', /^O que mais falha/, container));
    await waitFor(() => expect(text(container)).toContain('App sem resposta (ANR)'));

    await click(byRole('tab', /^Sinais/, container));
    await waitFor(() => expect(text(container)).toContain('Tomou o controle'));
    expect(backend.callsTo('GET', /^\/api\/aprendizado\/sinais$/)[0]?.query.get('dias')).toBe('14');
  });

  it('aprovar exige motivo e chama POST status com o próximo estado', async () => {
    await montar();
    await waitFor(() => expect(item('receita:12')).toBeTruthy());
    await click(byRole('button', /^Aprovar$/, item('receita:12')));
    const confirmar = byRole('button', /^Confirmar aprovação/, item('receita:12'));
    expect(confirmar.getAttribute('aria-disabled')).toBe('true');
    await click(confirmar);
    expect(statusCalls()).toHaveLength(0);                       // sem motivo, nada sai

    await setValue(byRole('textbox', /Motivo/, item('receita:12')) as HTMLInputElement, 'conferi a evidência no print');
    await click(byRole('button', /^Confirmar aprovação/, item('receita:12')));
    await waitFor(() => expect(statusCalls()).toHaveLength(1));
    expect(statusCalls()[0]?.path).toBe('/api/aprendizado/receita/12/status');
    expect(statusCalls()[0]?.body).toEqual({ to: 'published', reason: 'conferi a evidência no print' });
  });

  it('rejeitar exige motivo e desliga', async () => {
    await montar();
    await waitFor(() => expect(item('licao:li-abc')).toBeTruthy());
    await click(byRole('button', /^Rejeitar$/, item('licao:li-abc')));
    await click(byRole('button', /^Confirmar rejeição/, item('licao:li-abc')));
    expect(statusCalls()).toHaveLength(0);
    await setValue(byRole('textbox', /Motivo/, item('licao:li-abc')) as HTMLInputElement, 'a dica está errada');
    await click(byRole('button', /^Confirmar rejeição/, item('licao:li-abc')));
    await waitFor(() => expect(statusCalls()).toHaveLength(1));
    expect(statusCalls()[0]?.path).toBe('/api/aprendizado/licao/li-abc/status');
    expect(statusCalls()[0]?.body).toEqual({ to: 'disabled', reason: 'a dica está errada' });
  });

  it('aprovação em lote: um motivo, uma transição por item, cada uma para o próximo estado dele', async () => {
    await montar();
    await waitFor(() => expect(item('receita:12')).toBeTruthy());
    await click(byRole('checkbox', /Selecionar/, item('receita:12')));
    await click(byRole('checkbox', /Selecionar/, item('licao:li-abc')));
    await click(byRole('button', /^Aprovar selecionados \(2\)/, container));
    await click(byRole('button', /^Confirmar aprovação de 2/, container));
    expect(statusCalls()).toHaveLength(0);
    await setValue(byRole('textbox', /Motivo da aprovação em lote/, container) as HTMLInputElement, 'revisado em lote');
    await click(byRole('button', /^Confirmar aprovação de 2/, container));
    await waitFor(() => expect(statusCalls()).toHaveLength(2));
    const porCaminho = Object.fromEntries(statusCalls().map((c) => [c.path, c.body]));
    expect(porCaminho['/api/aprendizado/receita/12/status']).toEqual({ to: 'published', reason: 'revisado em lote' });
    expect(porCaminho['/api/aprendizado/licao/li-abc/status']).toEqual({ to: 'validated', reason: 'revisado em lote' });
  });

  it('"Revisar" mostra o legado ativo com efeito, que só a pessoa rebaixa', async () => {
    await montar();
    await waitFor(() => expect(item('receita:40')).toBeTruthy());
    const legado = item('receita:40');
    expect(text(legado)).toContain('Curtir a última foto');
    expect(text(legado)).toContain('tem efeito externo');
    await click(byRole('button', /^Rebaixar$/, legado));
    await setValue(byRole('textbox', /Motivo/, legado) as HTMLInputElement, 'comentário automático não');
    await click(byRole('button', /^Confirmar rebaixamento/, legado));
    await waitFor(() => expect(statusCalls()).toHaveLength(1));
    expect(statusCalls()[0]?.body).toEqual({ to: 'disabled', reason: 'comentário automático não' });
  });

  it('"Copiar para sessão" copia o md do item de falha', async () => {
    await montar();
    await click(byRole('tab', /^O que mais falha/, container));
    await waitFor(() => expect(item('fk-a1b2c3d4e5')).toBeTruthy());
    // o falso positivo do verificador vem primeiro, mesmo sem custo
    const linhas = Array.from(container.querySelectorAll('[data-item^="fk-"]')).map((el) => el.getAttribute('data-item'));
    expect(linhas).toEqual(['fk-ffffffffff', 'fk-a1b2c3d4e5']);
    await click(byRole('button', /^Copiar para sessão/, item('fk-a1b2c3d4e5')));
    await waitFor(() => expect(clipboard).toHaveBeenCalledTimes(1));
    const md = String(clipboard.mock.calls[0]?.[0]);
    expect(md).toContain('fk-a1b2c3d4e5');
    expect(md).toContain('backend/app/devices/manager.py');
    expect(md).toContain('r-20260928165254-e31953');
  });
});
