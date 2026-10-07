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
                          requires_owner: false, title: 'Marina Bastos', count: 12 });

const FALHAS = {
  dias: 14, outro_pct: 4,
  grupos: [
    { id: 'fk-a1b2c3d4e5', app_package: 'com.instagram.android', capability: 'abrir_perfil', failure_kind: 'app_anr',
      app_nome: 'Instagram', capability_nome: 'Abrir o perfil',
      titulo: 'com.instagram.android · abrir_perfil: app sem resposta', camada: 'aparelho', onde_alterar: { arquivos: ['backend/app/devices/manager.py'], doc: 'docs/dominios/parque.md',
                                           prova: 'a mesma etapa no mesmo aparelho' },
      ocorrencias: 6, taxa: 0.25, execucoes: 4, aparelhos: 2, usd_perdido: 0.4, min_perdidos: 12, intervencoes: 1,
      custo_total: 0.65, exemplos: [{ run_id: 'r-20260928165254-e31953', attempt_id: 'a-7', erro: 'ANR' }] },
    { id: 'fk-ffffffffff', app_package: 'com.whatsapp', capability: 'enviar_mensagem',
      failure_kind: 'verificacao_falso_positivo', ocorrencias: 1, custo_total: 0, falso_positivo: true, exemplos: [] },
  ],
};

const SINAIS = [
  { id: 3, kind: 'tomou_controle', polarity: 'negative', source_ref: 'takeover:a-1', created_by: 'sistema',
    created_at: '2026-09-28T10:00:00Z', run_id: 'r-1', app_package: 'com.instagram.android', capability: 'abrir_perfil',
    app_nome: 'Instagram', capability_nome: 'Abrir o perfil', simulated: 0 },
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
  backend.on('GET', /^\/api\/aprendizado\/intencao$/, () => json({ itens: [], total: 0 }));        // 30.25, vazia
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [PUBLICADO, MEMORIA, RECEITA], total: 3,
                                                          contagem: { fluxo: { published: 1 }, memoria: { '-': 12 }, receita: { validated: 1 } } }));
  backend.on('GET', /^\/api\/aprendizado\/falhas$/, () => json(FALHAS));
  // O formato de `presentation/feedback.py` (A4): `{sinais, total, contagem, dias}`.
  backend.on('GET', /^\/api\/aprendizado\/sinais$/, (c) => json({ sinais: SINAIS, total: SINAIS.length,
                                                                  contagem: { tomou_controle: 1 }, dias: Number(c.query.get('dias')) }));
  backend.on('POST', /^\/api\/aprendizado\/[a-z]+\/[^/]+\/status$/, (c) => json({ item: { ...RECEITA, state: (c.body as { to: string }).to },
                                                                              evidencias: [], trilha: [], exposicoes: [] }));
  backend.on('POST', /^\/api\/aprendizado\/[a-z]+\/[^/]+\/confirmar$/, () => json({ item: LEGADO, evidencias: [],
                                                                                 trilha: [], exposicoes: [] }));
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
const confirmarCalls = () => backend.callsTo('POST', /\/confirmar$/);
const HAB = 'habilidade:instagram.abrir-conversa@2';
const fila = () => container.querySelector('[aria-labelledby="aprendizado-fila"]') as HTMLElement;

describe('página Aprendizado', () => {
  it('tem as sete abas, e cada uma lê a sua rota (a Métricas e a Validação têm o teste delas)', async () => {
    await montar();
    const nomes = allByRole('tab', /.*/, container).map((t) => t.textContent?.replace(/\d+$/, '').trim());
    expect(nomes).toEqual(['Aplicativos', 'Para aprovar', 'Aprendido', 'O que mais falha', 'Sinais', 'Métricas', 'Validação']);
    await waitFor(() => expect(text(container)).toContain('Enviar oi para o contato'));

    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(text(container)).toContain('Abrir o perfil'));
    expect(text(container)).toContain('12 lembranças');          // memória: só a contagem

    await click(byRole('tab', /^O que mais falha/, container));
    await waitFor(() => expect(text(container)).toContain('App sem resposta (ANR)'));
    // P2 do deploy 3: o motivo e onde, com os nomes; o título de quem desenvolve não aparece na linha.
    expect(text(container)).toContain('App sem resposta (ANR) — Instagram · Abrir o perfil');
    expect(text(container)).not.toContain('com.instagram.android · abrir_perfil: app sem resposta');

    await click(byRole('tab', /^Sinais/, container));
    await waitFor(() => expect(text(container)).toContain('Tomou o controle'));
    // Os nomes vêm do catálogo, outro fetch: espera os nomes, não só o sinal (29.104).
    await waitFor(() => expect(text(container)).toContain('Instagram › Abrir o perfil'));    // P3: os nomes, com o código no `title`
    expect(text(container)).not.toContain('com.instagram.android ›');
    expect(backend.callsTo('GET', /^\/api\/aprendizado\/sinais$/)[0]?.query.get('dias')).toBe('14');
  });

  it('a decisão de aprovação sem app diz o nome do catálogo ou "decisão de aprovação", nunca o código cru (deploy 12)', async () => {
    const decisao = { kind: 'aprovacao_decidida', polarity: 'positive', created_by: 'painel', created_at: '2026-10-03T10:00:00Z',
                      app_package: null, app_nome: null, simulated: 0 };
    backend.on('GET', /^\/api\/aprendizado\/sinais$/, () => json({
      sinais: [{ ...decisao, id: 7, source_ref: 'approval:a-7', capability: 'CREATE_COMMENT', capability_nome: 'Comentar na publicação' },
               { ...decisao, id: 8, source_ref: 'approval:a-8', capability: 'REPLY_COMMENT', capability_nome: null }],
      total: 2, contagem: { aprovacao_decidida: 2 }, dias: 14 }));
    await montar();
    await click(byRole('tab', /^Sinais/, container));
    await waitFor(() => expect(container.querySelector('[data-item="sinal:8"]')).not.toBeNull());
    expect(text(container.querySelector('[data-item="sinal:7"]') as HTMLElement)).toContain('Comentar na publicação');
    const semNome = container.querySelector('[data-item="sinal:8"]') as HTMLElement;
    expect(text(semNome)).toContain('decisão de aprovação');
    expect(text(container)).not.toMatch(/CREATE_COMMENT|REPLY_COMMENT/);
    expect(semNome.querySelector('[title="REPLY_COMMENT"]')).not.toBeNull();       // o código fica na dica
  });

  it('Para aprovar diz o nome do app (o pacote no title) e dá nome à receita de app sem catálogo (deploy 4)', async () => {
    const qa = entrada({ ref: '70', title: 'fill_message (v1)', app: 'com.pocqa.messenger', app_nome: 'QA Messenger',
                         etapa: 'Digitar a mensagem', capability: null, capability_nome: null });
    const semNome = entrada({ ref: '71', title: 'open_app (v1)', app: 'com.exemplo.sem.nome', app_nome: null });
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [qa, semNome], total: 2 }));
    await montar();
    await waitFor(() => expect(item('receita:70')).toBeTruthy());
    expect(text(item('receita:70'))).toContain('Digitar a mensagem (v1)');
    // UX dos deploys 7 e 8: a chave da etapa e a referência no Livro saem do texto do cartão e ficam no `title`.
    expect(text(item('receita:70'))).not.toContain('fill_message');
    expect(text(item('receita:70'))).not.toContain('receita:70');
    expect(item('receita:70').querySelector('[title*="fill_message (v1) · receita:70"]')).not.toBeNull();
    expect(text(item('receita:70'))).toContain('App: QA Messenger');
    expect(text(item('receita:70'))).not.toContain('com.pocqa.messenger');
    expect(byRole('button', /^QA Messenger$/, item('receita:70')).getAttribute('title')).toContain('com.pocqa.messenger');
    expect(text(item('receita:71'))).toContain('App: com.exemplo.sem.nome');   // sem nome, o pacote
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

  it('no Aprendido, o legado confirmado perde o "vale revisar" e diz quem confirmou (30.24)', async () => {
    const confirmado = { ...LEGADO, em_revisar: false,
                         confirmado: { por: 'Ana Ribeiro', em: '2026-10-03T05:00:00Z', motivo: 'conferi o alvo' } };
    const naFila = { ...LEGADO, ref: '41', em_revisar: true, confirmado: null };
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [confirmado, naFila], total: 2,
                                                            contagem: { receita: { published: 2 } } }));
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(item('receita:40')).toBeTruthy());
    expect(text(item('receita:40'))).not.toContain('vale revisar');
    expect(text(item('receita:40'))).toContain('Confirmado que fica por Ana Ribeiro');
    expect(text(item('receita:40'))).toContain('conferi o alvo');
    expect(text(item('receita:41'))).toContain('vale revisar');
    expect(text(item('receita:41'))).not.toContain('Confirmado que fica');
  });

  it('31.164: a confirmação automática da régua mostra o nome da regra e os fatos em palavras, com o texto cru só no title', async () => {
    const cru = 'auto:qa_revisar v1 — classe B; app com.pocqa.messenger (qa); 3 a favor, 0 contra; 0 falhas de reprodução; saúde pouca_amostra; parecer pedir_evidencia (lr-fe4a3e84376de6ac)';
    const auto = { ...LEGADO, em_revisar: false, confirmado: { por: 'sistema', em: '2026-10-03T05:00:00Z', motivo: cru } };
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [auto], total: 1, contagem: { receita: { published: 1 } } }));
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(item('receita:40')).toBeTruthy());
    const nota = text(item('receita:40'));
    expect(nota).toContain('Confirmação automática do que estava em revisão — classe B');
    expect(nota).toContain('saúde: Pouca amostra; parecer do curador: pedir mais evidência');
    expect(nota).not.toMatch(/pouca_amostra|pedir_evidencia|auto:qa_revisar|lr-fe4a/);
    expect(item('receita:40')!.querySelector(`[title="${cru}"]`)).not.toBeNull();
  });

  it('31.145: a confirmação feita pelo painel diz "pelo painel", e a receita ensinada no treino mostra o nome, não a chave', async () => {
    const doPainel = { ...LEGADO, em_revisar: false, title: 'abrir_busca (v1)', capability: null, capability_nome: null, etapa: null,
                       confirmado: { por: 'panel', em: '2026-10-03T05:00:00Z', motivo: null } };
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [doPainel], total: 1, contagem: { receita: { published: 1 } } }));
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(item('receita:40')).toBeTruthy());
    expect(text(item('receita:40'))).toContain('Confirmado que fica pelo painel');
    expect(text(item('receita:40'))).not.toContain('por panel');
    expect(text(item('receita:40'))).toContain('Abrir busca (v1)');
    expect(item('receita:40')!.querySelector('[title^="abrir_busca (v1)"]')).toBeTruthy();       // a chave crua fica no title
  });

  it('"Revisar" diz por que o item confirmado voltou: chegou evidência contrária (30.24)', async () => {
    backend.on('GET', /^\/api\/aprendizado\/revisar$/, () => json({ itens: [{ ...LEGADO, em_revisar: true, confirmado: null,
      confirmacao_contestada: { por: 'Ana Ribeiro', em: '2026-10-03T05:00:00Z', motivo: null } }], total: 1 }));
    await montar();
    await waitFor(() => expect(item('receita:40')).toBeTruthy());
    expect(text(item('receita:40'))).toContain('Voltou para revisar: confirmado que fica por Ana Ribeiro');
    expect(text(item('receita:40'))).toContain('chegou evidência contrária');
  });

  it('no Aprendido, o filtro de apps é segmentado (Produto · QA · Todos), Produto por padrão, com os ocultos ao lado', async () => {
    // RA-19: o servidor aplica `produto` quando o painel não pede; o painel mostra o que valeu e quantos ficaram fora.
    backend.on('GET', /^\/api\/aprendizado$/, (c) => {
      const r = c.query.get('rotulo');
      if (r === 'todos') return json({ itens: [PUBLICADO, RECEITA], total: 2, contagem: {}, rotulo: 'todos', ocultos: 0 });
      if (r === 'qa') return json({ itens: [RECEITA], total: 1, contagem: {}, rotulo: 'qa', ocultos: 1 });
      return json({ itens: [PUBLICADO], total: 1, contagem: {}, rotulo: 'produto', ocultos: 94 });
    });
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    const grupo = await waitFor(() => byRole('radiogroup', /^Apps$/, container));
    const marcado = () => allByRole('radio', /./, grupo).filter((b) => b.getAttribute('aria-checked') === 'true').map(text);
    expect(allByRole('radio', /./, grupo).map(text)).toEqual(['Produto', 'QA', 'Todos']);
    await waitFor(() => expect(text(container)).toContain('94 do QA ocultos'));
    expect(marcado()).toEqual(['Produto']);
    // a primeira leitura não pede rótulo: o padrão é do servidor (e com um app escolhido, ele vale "todos")
    const [primeira] = backend.callsTo('GET', /^\/api\/aprendizado$/);
    expect(primeira).toBeTruthy();
    expect(primeira?.query.get('rotulo')).toBeNull();

    await click(byRole('radio', /^Todos$/, grupo));
    await waitFor(() => expect(item('receita:12')).toBeTruthy());
    expect(marcado()).toEqual(['Todos']);
    expect(text(container)).not.toContain('ocultos');

    await click(byRole('radio', /^QA$/, grupo));
    await waitFor(() => expect(text(container)).toContain('1 de produto oculto'));
    expect(marcado()).toEqual(['QA']);

    // a escolha vale para o app em que foi feita: escolher um app volta ao padrão do servidor (que mostra o que ele tem)
    await act(async () => { useUiStore.getState().trocarQuery({ app: 'com.pocqa.messenger' }); });
    await waitFor(() => expect(backend.callsTo('GET', /^\/api\/aprendizado$/).some((c) => c.query.get('app') === 'com.pocqa.messenger')).toBe(true));
    const doApp = backend.callsTo('GET', /^\/api\/aprendizado$/).filter((c) => c.query.get('app') === 'com.pocqa.messenger');
    expect(doApp.map((c) => c.query.get('rotulo'))).toEqual([null]);
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
    // 30.66: o motivo comum ("tem efeito externo") é dito uma vez, no cabeçalho de Revisar, e não em cada item.
    expect(text(legado)).not.toContain('Publicado antes da regra de aprovação');
    const revisar = container.querySelector('[aria-labelledby="aprendizado-revisar"]') as HTMLElement;
    expect(text(revisar)).toContain('Publicados antes da regra de aprovação (tem efeito externo) e ainda ativos');
    await click(byRole('button', /^Desligar$/, legado));
    await setValue(byRole('textbox', /Motivo/, legado) as HTMLInputElement, 'comentário automático não');
    await click(byRole('button', /^Confirmar desligamento/, legado));
    await waitFor(() => expect(statusCalls()).toHaveLength(1));
    expect(statusCalls()[0]?.body).toEqual({ to: 'disabled', reason: 'comentário automático não' });
  });

  it('"Revisar": "Confirmar que fica" vale sem motivo e vai pela rota própria (30.24)', async () => {
    await montar();
    await waitFor(() => expect(item('receita:40')).toBeTruthy());
    const legado = item('receita:40');
    await click(byRole('button', /^Confirmar que fica$/, legado));
    expect(byRole('textbox', /Motivo \(opcional\)/, legado)).toBeTruthy();
    await click(byRole('button', /^Confirmar que fica$/, legado));
    await waitFor(() => expect(confirmarCalls()).toHaveLength(1));
    expect(confirmarCalls()[0]?.path).toBe('/api/aprendizado/receita/40/confirmar');
    expect(confirmarCalls()[0]?.body).toEqual({});
    expect(statusCalls()).toHaveLength(0);
  });

  it('"Revisar": confirmar em lote leva o mesmo motivo a cada item', async () => {
    await montar();
    await waitFor(() => expect(item('receita:40')).toBeTruthy());
    const secao = container.querySelector('[aria-labelledby="aprendizado-revisar"]') as HTMLElement;
    await click(byRole('button', /^Selecionar todos$/, secao));
    await click(byRole('button', /^Confirmar selecionados \(1\)/, secao));
    await setValue(byRole('textbox', /Motivo da confirmação em lote/, secao) as HTMLInputElement, 'conferi os dois prints');
    await click(byRole('button', /^Confirmar que fica$/, secao));
    await waitFor(() => expect(confirmarCalls()).toHaveLength(1));
    expect(confirmarCalls()[0]?.body).toEqual({ motivo: 'conferi os dois prints' });
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

  it('30.81: o fluxo ensinado que espera a pessoa mostra o porquê, oferece "Confirmar que fica" na frente e usa a rota própria', async () => {
    const ENSINADO = entrada({ kind: 'fluxo', ref: 'f-30', state: 'published', native_status: 'active', side_effect: false,
                               requires_owner: false, title: 'Mandar mensagem', acoes: [ACAO('disabled', 'desligar')],
                               espera_a_pessoa: 'tentativas_esgotadas' });
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [ENSINADO, PUBLICADO], total: 2, contagem: {} }));
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(item('fluxo:f-30')).toBeTruthy());
    const ensinado = item('fluxo:f-30');
    expect(text(ensinado)).toContain('Ensinado, ainda em prova: o uso fica restrito até ela passar');
    // o Livro não sabe se a gravação tinha persona: a frase cobre os dois casos, sem afirmar que existe uma que ensinou
    expect(text(ensinado)).toContain('sem persona na gravação, nenhum aparelho');
    expect(text(ensinado)).toContain('tentou 3 vezes sem veredito');
    expect(text(ensinado)).not.toContain('tentativas_esgotadas');           // o código fica no title, nunca na tela
    expect(allByRole('button', /^(Confirmar que fica|Desligar)$/, ensinado).map(text)).toEqual(['Confirmar que fica', 'Desligar']);
    // O fluxo comum, sem o campo, segue só com o que o backend ofereceu.
    expect(text(item('fluxo:f-9'))).not.toContain('Ensinado, ainda em prova');
    expect(allByRole('button', /^Confirmar que fica$/, item('fluxo:f-9'))).toHaveLength(0);

    await click(byRole('button', /^Confirmar que fica$/, ensinado));
    expect(byRole('textbox', /Motivo \(opcional\)/, ensinado)).toBeTruthy();
    await click(byRole('button', /^Confirmar que fica$/, ensinado));
    await waitFor(() => expect(confirmarCalls()).toHaveLength(1));
    expect(confirmarCalls()[0]?.path).toBe('/api/aprendizado/fluxo/f-30/confirmar');
    expect(statusCalls()).toHaveLength(0);
  });

  it('30.81: `espera_a_pessoa: null` (o ensinado ainda na prova automática) não oferece o botão', async () => {
    const NA_PROVA = entrada({ kind: 'fluxo', ref: 'f-31', state: 'published', native_status: 'active', side_effect: false,
                               requires_owner: false, title: 'Mandar mensagem', acoes: [ACAO('disabled', 'desligar')],
                               espera_a_pessoa: null });
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [NA_PROVA], total: 1, contagem: {} }));
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(item('fluxo:f-31')).toBeTruthy());
    expect(allByRole('button', /^Confirmar que fica$/, item('fluxo:f-31'))).toHaveLength(0);
    expect(text(item('fluxo:f-31'))).not.toContain('Ensinado, ainda em prova');
  });

  // 30.85 (adendo v1.73): o fluxo ensinado em prova segue "Publicado", e a lista mostra o selo do 30.81 ao lado do estado.
  // O selo é o elemento de texto exato "em prova" (a nota também diz "sem prova", que contém a mesma sequência).
  const selo = (el: HTMLElement) => Array.from(el.querySelectorAll('span')).find((x) => x.textContent === 'em prova') ?? null;
  const EM_PROVA = (ref: string, over: object = {}) => entrada({
    kind: 'fluxo', ref, state: 'published', native_status: 'active', side_effect: false, requires_owner: false,
    title: 'Voltar para a lista', acoes: [ACAO('disabled', 'desligar')], ...over });

  it('30.85: o fluxo ensinado em prova mostra o selo "em prova" ao lado de "Publicado", com a nota de quem pode usar', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [
      EM_PROVA('f-40', { ensinado_em_prova: { persona: 'ig-1', sessao: 'trn-1' } }),
      EM_PROVA('f-41', { ensinado_em_prova: { persona: null, sessao: 'trn-2' } }),
      EM_PROVA('f-42'),
    ], total: 3, contagem: {} }));
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(item('fluxo:f-40')).toBeTruthy());
    const comPersona = item('fluxo:f-40');
    expect(selo(comPersona)).not.toBeNull();
    expect(text(comPersona)).toContain('Publicado');                                    // o estado não muda: o selo é a prova que falta
    expect(text(comPersona)).toContain('só vale para a persona que ensinou');
    expect(allByRole('button', /^Confirmar que fica$/, comPersona)).toHaveLength(0);    // sem `espera_a_pessoa`, nada de botão novo
    expect(selo(item('fluxo:f-41'))).not.toBeNull();
    expect(text(item('fluxo:f-41'))).toContain('a gravação não tinha persona: não vale em aparelho nenhum');
    expect(selo(item('fluxo:f-42'))).toBeNull();                         // campo ausente: provado, confirmado, desligado ou sem treino
  });

  it('30.85: com `espera_a_pessoa` o selo aparece e a nota é uma só (a do "Confirmar que fica")', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [
      EM_PROVA('f-43', { espera_a_pessoa: 'classe_c', ensinado_em_prova: { persona: 'ig-1', sessao: 'trn-1' } }),
    ], total: 1, contagem: {} }));
    await montar();
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(item('fluxo:f-43')).toBeTruthy());
    const t = text(item('fluxo:f-43'));
    expect(selo(item('fluxo:f-43'))).not.toBeNull();
    expect(t).toContain('Ensinado, ainda em prova: o uso fica restrito até ela passar');
    expect(t).not.toContain('Ensinado e ainda sem prova');                              // a explicação curta não repete a nota
    expect(allByRole('button', /^Confirmar que fica$/, item('fluxo:f-43'))).toHaveLength(1);
  });
});
