// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { isBoolean, loadJson } from '../../lib/storage';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import type { AcaoPermitida, EntradaDoLivro, EstadoDoLivro, MotivoDeNaoPublicar, RotuloDaAcao } from './model';

/**
 * Pacote A6 do ADR-054: a página Aprendizado contra o contrato do livro (A1) e o formato esperado de A3/A4, com o
 * backend simulado. Prova `simulated` — nenhuma rota real foi chamada.
 */

function entrada(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'receita', ref: '1', state: 'validated', native_status: 'validated', title: 'Item', app: 'com.whatsapp',
    origin: 'execucao', side_effect: true, human_origin: false, requires_owner: true, created_at: '2026-09-28T10:00:00Z',
    state_at: '2026-09-28T10:00:00Z', last_used_at: null, uses: 0, evidence: { for: 3, against: 0 }, count: null,
    detail: null, acoes: [], por_que_nao_publica: null, ...over,
  };
}

// As `acoes` e o motivo vêm do backend (`acoes_da_pessoa`); aqui são o que ele mandaria para cada item.
const ACAO = (to: EstadoDoLivro, rotulo: RotuloDaAcao): AcaoPermitida => ({ to, rotulo, exige_motivo: true });
const DONO = (codigo: 'efeito_externo' | 'texto_de_pessoa' | 'habilidade'): MotivoDeNaoPublicar => ({ codigo, espera_o_dono: true, detalhe: null });

const RECEITA = entrada({ kind: 'receita', ref: '12', title: 'Enviar oi para o contato',
                         acoes: [ACAO('published', 'aprovar'), ACAO('disabled', 'rejeitar')], por_que_nao_publica: DONO('efeito_externo') });
const LICAO = entrada({ kind: 'licao', ref: 'li-abc', state: 'candidate', native_status: null, side_effect: false,
                        human_origin: true, title: 'Role a lista antes de procurar o contato', state_at: '2026-09-27T10:00:00Z',
                        acoes: [ACAO('validated', 'validar'), ACAO('disabled', 'rejeitar')], por_que_nao_publica: DONO('texto_de_pessoa') });
const LEGADO = entrada({ kind: 'receita', ref: '40', state: 'published', native_status: 'active', title: 'Curtir a última foto',
                        acoes: [ACAO('deprecated', 'aposentar'), ACAO('disabled', 'desligar')], por_que_nao_publica: DONO('efeito_externo') });
const PUBLICADO = entrada({ kind: 'fluxo', ref: 'f-9', state: 'published', native_status: 'active', side_effect: false,
                            requires_owner: false, title: 'Abrir o perfil', uses: 7, acoes: [ACAO('disabled', 'desligar')] });
// Habilidade validada: o livro põe TODA versão validada na fila do D1 (publicar é sempre de uma pessoa), mas a
// transição vai pela rota das habilidades — a do livro devolve 409 `use_skills_route`.
const HABILIDADE = entrada({ kind: 'habilidade', ref: 'instagram.abrir-conversa@2', native_status: 'validated',
                             side_effect: false, origin: 'ensino', app: 'com.instagram.android',
                             title: 'Abrir a conversa com o contato', state_at: '2026-09-26T10:00:00Z',
                             por_que_nao_publica: DONO('habilidade') });
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
  backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [RECEITA, LICAO, HABILIDADE], total: 3 }));
  backend.on('GET', /^\/api\/aprendizado\/revisar$/, () => json({ itens: [LEGADO], total: 1 }));
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [PUBLICADO, MEMORIA, RECEITA], total: 3,
                                                          contagem: { fluxo: { published: 1 }, memoria: { '-': 12 }, receita: { validated: 1 } } }));
  backend.on('GET', /^\/api\/aprendizado\/falhas$/, () => json(FALHAS));
  // O formato de `presentation/feedback.py` (A4): `{sinais, total, contagem, dias}`.
  backend.on('GET', /^\/api\/aprendizado\/sinais$/, (c) => json({ sinais: SINAIS, total: SINAIS.length,
                                                                  contagem: { tomou_controle: 1 }, dias: Number(c.query.get('dias')) }));
  backend.on('POST', /^\/api\/aprendizado\/[a-z]+\/[^/]+\/status$/, (c) => json({ item: { ...RECEITA, state: (c.body as { to: string }).to },
                                                                              evidencias: [], trilha: [], exposicoes: [] }));
  // A rota das habilidades (§10.3): o livro a recusa com 409 `use_skills_route`, então o painel nem tenta o livro.
  backend.on('POST', /^\/api\/aprendizado\/habilidade\//, () => json({ detail: {
    code: 'use_skills_route', message: 'use a rota das habilidades', href: '/api/skills/instagram.abrir-conversa/versions/2/status',
  } }, 409));
  backend.on('POST', /^\/api\/skills\/[^/]+\/versions\/\d+\/status$/, (c) => json({
    ref: 'instagram.abrir-conversa@2', skill_id: 'instagram.abrir-conversa', version: 2,
    state: (c.body as { to: string }).to, history: [],
  }));
  useToastStore.setState({ toasts: [] });
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprovar' } }, 'replace');
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
const HAB = 'habilidade:instagram.abrir-conversa@2';
const fila = () => container.querySelector('[aria-labelledby="aprendizado-fila"]') as HTMLElement;

describe('página Aprendizado', () => {
  it('tem as cinco abas, e cada uma lê a sua rota', async () => {
    await montar();
    const nomes = allByRole('tab', /.*/, container).map((t) => t.textContent?.replace(/\d+$/, '').trim());
    expect(nomes).toEqual(['Aplicativos', 'Para aprovar', 'Aprendido', 'O que mais falha', 'Sinais']);
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

  it('habilidade validada na fila: publicar exige motivo e vai pela rota das habilidades, nunca pela do livro', async () => {
    await montar();
    await waitFor(() => expect(item(HAB)).toBeTruthy());
    const hab = item(HAB);
    expect(text(hab)).toContain('publicar é sempre de uma pessoa');
    await click(byRole('button', /^Publicar$/, hab));
    await click(byRole('button', /^Confirmar publicação/, hab));
    expect(statusCalls()).toHaveLength(0);                       // sem motivo, nada sai

    await setValue(byRole('textbox', /Motivo/, hab) as HTMLInputElement, 'conferi os casos da habilidade');
    await click(byRole('button', /^Confirmar publicação/, hab));
    await waitFor(() => expect(statusCalls()).toHaveLength(1));
    expect(statusCalls()[0]?.path).toBe('/api/skills/instagram.abrir-conversa/versions/2/status');
    expect(statusCalls()[0]?.body).toEqual({ to: 'published', reason: 'conferi os casos da habilidade' });
    expect(backend.callsTo('POST', /^\/api\/aprendizado\//)).toHaveLength(0);
  });

  it('habilidade validada na fila: rejeitar desabilita pela rota das habilidades', async () => {
    await montar();
    await waitFor(() => expect(item(HAB)).toBeTruthy());
    await click(byRole('button', /^Rejeitar$/, item(HAB)));
    await setValue(byRole('textbox', /Motivo/, item(HAB)) as HTMLInputElement, 'o comando casa com o fluxo antigo');
    await click(byRole('button', /^Confirmar rejeição/, item(HAB)));
    await waitFor(() => expect(statusCalls()).toHaveLength(1));
    expect(statusCalls()[0]?.path).toBe('/api/skills/instagram.abrir-conversa/versions/2/status');
    expect(statusCalls()[0]?.body).toEqual({ to: 'disabled', reason: 'o comando casa com o fluxo antigo' });
  });

  it('"Selecionar todos → Aprovar selecionados" decide também a habilidade, pela rota dela, sem falha falsa', async () => {
    await montar();
    await waitFor(() => expect(item(HAB)).toBeTruthy());
    await click(byRole('button', /^Selecionar todos$/, fila()));
    await click(byRole('button', /^Aprovar selecionados \(3\)/, fila()));
    await setValue(byRole('textbox', /Motivo da aprovação em lote/, fila()) as HTMLInputElement, 'revisado em lote');
    await click(byRole('button', /^Confirmar aprovação de 3/, fila()));
    await waitFor(() => expect(statusCalls()).toHaveLength(3));
    const porCaminho = Object.fromEntries(statusCalls().map((c) => [c.path, c.body]));
    expect(porCaminho['/api/aprendizado/receita/12/status']).toEqual({ to: 'published', reason: 'revisado em lote' });
    expect(porCaminho['/api/aprendizado/licao/li-abc/status']).toEqual({ to: 'validated', reason: 'revisado em lote' });
    expect(porCaminho['/api/skills/instagram.abrir-conversa/versions/2/status']).toEqual({ to: 'published', reason: 'revisado em lote' });
    await waitFor(() => expect(useToastStore.getState().toasts.length).toBeGreaterThan(0));
    const aviso = useToastStore.getState().toasts[0];
    expect(aviso?.tone).toBe('success');
    expect(aviso?.title).toBe('3 de 3 item(ns) decidido(s)');
    expect(aviso?.details ?? []).toEqual([]);
  });

  it('a habilidade na fila leva ao ciclo completo dela (Configuração → Fluxos e receitas → Habilidades)', async () => {
    await montar();
    await waitFor(() => expect(item(HAB)).toBeTruthy());
    await click(byRole('button', /Configuração → Fluxos e receitas → Habilidades/, item(HAB)));
    expect(useUiStore.getState().view).toBe('configuracao');
    expect(useUiStore.getState().rota.query.aba).toBe('fluxos');
    expect(loadJson('settings.section.habilidades', isBoolean)).toBe(true);
  });

  it('no Aprendido, a habilidade aponta para onde o ciclo dela fica, sem botão do livro', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [{ ...HABILIDADE, state: 'published', native_status: 'published' }],
                                                            total: 1, contagem: { habilidade: { published: 1 } } }));
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(item(HAB)).toBeTruthy());
    const hab = item(HAB);
    expect(text(hab)).not.toContain('aba Habilidades da persona');
    expect(allByRole('button', /^(Aposentar|Desligar|Reativar|Publicar)$/, hab)).toHaveLength(0);
    await click(byRole('button', /Configuração → Fluxos e receitas → Habilidades/, hab));
    expect(useUiStore.getState().view).toBe('configuracao');
  });

  it('"Revisar" mostra o legado ativo com efeito, que só a pessoa desliga', async () => {
    await montar();
    await waitFor(() => expect(item('receita:40')).toBeTruthy());
    const legado = item('receita:40');
    expect(text(legado)).toContain('Curtir a última foto');
    expect(text(legado)).toContain('tem efeito externo');
    await click(byRole('button', /^Desligar$/, legado));
    await setValue(byRole('textbox', /Motivo/, legado) as HTMLInputElement, 'comentário automático não');
    await click(byRole('button', /^Confirmar desligamento/, legado));
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
    // O botão é de quem desenvolve: fica no bloco recolhido "Para quem desenvolve".
    const tecnico = item('fk-a1b2c3d4e5').querySelector('details') as HTMLDetailsElement;
    expect(text(tecnico.querySelector('summary') as HTMLElement)).toContain('Para quem desenvolve');
    await act(async () => { tecnico.open = true; tecnico.dispatchEvent(new Event('toggle')); });
    await click(byRole('button', /^Copiar para sessão/, item('fk-a1b2c3d4e5')));
    await waitFor(() => expect(clipboard).toHaveBeenCalledTimes(1));
    const md = String(clipboard.mock.calls[0]?.[0]);
    expect(md).toContain('fk-a1b2c3d4e5');
    expect(md).toContain('backend/app/devices/manager.py');
    expect(md).toContain('r-20260928165254-e31953');
  });
});
