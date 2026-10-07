// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, openDetails, setValue, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { ACAO_PUBLICAR_O_FATO, acaoDePublicarOFato, confiancaEmPalavras, fatoDaOperacaoDe, frescorEmPalavras } from './fatoDaOperacao';
import type { DetalheDoLivro, EntradaDoLivro } from './model';

/**
 * 31.214: no Livro, a lição candidata que veio de fato da pesquisa de uma operação (Aprendizado 31.190, `operation_fact`) mostra de onde
 * veio (operação, assunto, fontes), confiança, frescor, uso e evidência, e tem o Publicar com confirmação (só uma pessoa publica). Prova
 * `simulated`: servidor falso no formato que o Aprendizado combinou (a proveniência e o `source_kind` ainda não saem da API).
 */

function entrada(over: Partial<EntradaDoLivro> = {}): EntradaDoLivro {
  return {
    kind: 'licao', ref: 'li-fato', state: 'candidate', native_status: null, title: 'O festival de inverno começa em julho e tem 40 atrações.', app: 'com.instagram.android',
    origin: 'sistema', side_effect: false, human_origin: true, requires_owner: true, created_at: '2026-10-07T10:00:00Z', state_at: '2026-10-07T10:00:00Z',
    last_used_at: null, uses: 0, evidence: { for: 0, against: 0 }, count: null, detail: null,
    acoes: [{ to: 'validated', rotulo: 'validar', exige_motivo: true }, { to: 'disabled', rotulo: 'rejeitar', exige_motivo: true }], por_que_nao_publica: null, ...over,
  };
}
const PROVENIENCIA = {
  regra: '31.190-v1', operacao: 'op-20261007-1', chave: 'pesquisa.festival', assunto: 'festival de inverno', fontes: ['exemplo.com.br', 'outro.com.br'],
  frescor_ate: '2999-01-01T00:00:00Z', usado_em: 3, execucoes: ['r-20261007100000-aaaa', 'r-20261007100100-bbbb'], confianca: 'confirmado',
};
const CONTEUDO = { tipo: 'licao' as const, texto: 'O festival de inverno começa em julho e tem 40 atrações.', modelo: 'fato_da_operacao', acao: null, alvo: null,
  escopo: { app: 'com.instagram.android', capability: null, step_hash: null, role: 'writer' }, tokens: 18 };
const detalhe = (over: Partial<DetalheDoLivro> = {}, item: Partial<EntradaDoLivro> = {}): DetalheDoLivro =>
  ({ item: entrada(item), evidencias: [], trilha: [], exposicoes: [], conteudo: CONTEUDO, proveniencia: PROVENIENCIA, ...over });

describe('fatoDaOperacaoDe (puro)', () => {
  it('reconhece pelo source_kind, pelo modelo do conteúdo ou pela regra da proveniência; outra lição é null', () => {
    expect(fatoDaOperacaoDe({ source_kind: 'operation_fact' })).not.toBeNull();
    expect(fatoDaOperacaoDe({}, { conteudo: CONTEUDO })).not.toBeNull();
    expect(fatoDaOperacaoDe({}, { proveniencia: { regra: '31.190-v2' } })).not.toBeNull();
    expect(fatoDaOperacaoDe({ source_kind: 'manual' }, { conteudo: { ...CONTEUDO, modelo: 'outra' } })).toBeNull();
    expect(fatoDaOperacaoDe({})).toBeNull();
  });
  it('lê a proveniência; sem ela, tudo "não informado" e a confiança é a única que nasce (confirmado)', () => {
    const f = fatoDaOperacaoDe({}, { proveniencia: PROVENIENCIA, conteudo: CONTEUDO })!;
    expect(f).toMatchObject({ operacaoId: 'op-20261007-1', assunto: 'festival de inverno', fontes: ['exemplo.com.br', 'outro.com.br'], usadoEm: 3, confianca: 'confirmado', frescorInformado: true });
    expect(f.execucoes).toHaveLength(2);
    const vazio = fatoDaOperacaoDe({ source_kind: 'operation_fact' })!;
    expect(vazio).toMatchObject({ operacaoId: null, assunto: null, fontes: [], usadoEm: null, frescorInformado: false, confianca: 'confirmado' });
    expect(fatoDaOperacaoDe({}, { proveniencia: { regra: '31.190-v1', usado_em: -1, confianca: 'hipotese' } })).toMatchObject({ usadoEm: null, confianca: 'hipotese' });
  });
  it('frescor: vale até, vencido (com os dias), sem prazo (null informado) e não informado (campo ausente)', () => {
    const agora = Date.parse('2026-10-10T12:00:00Z');
    expect(frescorEmPalavras({ frescorAte: '2026-10-12T00:00:00Z', frescorInformado: true }, agora).texto).toMatch(/^Vale até /);
    expect(frescorEmPalavras({ frescorAte: '2026-10-07T12:00:00Z', frescorInformado: true }, agora)).toEqual({ texto: 'Vencido há 3 dias', vencido: true });
    expect(frescorEmPalavras({ frescorAte: '2026-10-10T08:00:00Z', frescorInformado: true }, agora)).toEqual({ texto: 'Vencido hoje', vencido: true });
    expect(frescorEmPalavras({ frescorAte: null, frescorInformado: true }, agora)).toEqual({ texto: 'Sem prazo de validade', vencido: false });
    expect(frescorEmPalavras({ frescorAte: null, frescorInformado: false }, agora)).toEqual({ texto: 'Frescor não informado', vencido: false });
  });
  it('a confiança em palavras', () => {
    expect(confiancaEmPalavras('confirmado')).toContain('Confirmado');
    expect(confiancaEmPalavras('hipotese')).toContain('não confirmada');
    expect(confiancaEmPalavras(null)).toBe('Confiança não informada');
  });
  it('o Publicar só na candidata que é fato, e só quando nenhuma ação já publica', () => {
    const fato = { source_kind: 'operation_fact' };
    expect(acaoDePublicarOFato({ ...entrada(), ...fato })).toBe(ACAO_PUBLICAR_O_FATO);
    expect(acaoDePublicarOFato({ ...entrada({ state: 'published' }), ...fato })).toBeNull();
    expect(acaoDePublicarOFato(entrada())).toBeNull();                                        // sem sinal de fato
    expect(acaoDePublicarOFato({ ...entrada({ acoes: [{ to: 'published', rotulo: 'aprovar', exige_motivo: true }] }), ...fato })).toBeNull();
  });
});

describe('a tela', () => {
  let root: Root;
  let container: HTMLElement;
  let backend: FakeBackend;
  const statusCalls = () => backend.callsTo('POST', /\/status$/);

  beforeEach(() => {
    installBrowserStubs();
    window.localStorage.clear();
    backend = new FakeBackend();
    backend.install();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    for (const rota of ['pendentes', 'revisar', 'intencao']) backend.on('GET', new RegExp(`^/api/aprendizado/${rota}$`), () => json({ itens: [], total: 0 }));
    backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [], sem_eixo: null, nao_resolvido: null }));
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: {} } });
  });

  const abrir = async (item: Partial<EntradaDoLivro> = {}, det: Partial<DetalheDoLivro> = {}) => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [entrada(item)], total: 1, contagem: {} }));
    backend.on('GET', /^\/api\/aprendizado\/licao\/li-fato$/, () => json(detalhe(det, item)));
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido' } }, 'replace');
    await act(async () => root.render(<AprendizadoPage />));
    await waitFor(() => expect(container.querySelector('[data-item="licao:li-fato"]')).not.toBeNull());
    const linha = container.querySelector('[data-item="licao:li-fato"]') as HTMLElement;
    await openDetails(/Detalhes, evidência e trilha/, linha);
    await waitFor(() => expect(linha.querySelector('h4')).not.toBeNull());
    return linha;
  };

  it('o detalhe mostra a origem (link da operação), o assunto, as fontes, a confiança, o frescor, o uso e a evidência', async () => {
    const linha = await abrir();
    const bloco = linha.querySelector('[data-fato-da-operacao]') as HTMLElement;
    const t = text(bloco);
    expect(t).toContain('operação op-20261007-1');
    expect(bloco.querySelector('a')!.getAttribute('href')).toBe('#/operacoes/op-20261007-1');
    expect(t).toContain('festival de inverno');
    expect(t).toContain('exemplo.com.br, outro.com.br');
    expect(t).toContain('Confirmado');
    expect(text(bloco.querySelector('[data-frescor]')!)).toMatch(/^Vale até /);
    expect(bloco.querySelector('[data-frescor]')!.getAttribute('data-vencido')).toBe('nao');
    expect(t).toContain('3 alvos da operação');
    expect(t).toContain('0 a favor · 0 contra · saiu de 2 execuções');
    expect(text(linha)).toContain('só uma pessoa o publica');
  });

  it('o Publicar abre a confirmação com o motivo e leva a candidata a publicada (to=published, num gesto só)', async () => {
    backend.on('POST', /^\/api\/aprendizado\/licao\/li-fato\/status$/, () => json(detalhe({}, { state: 'published' })));
    const linha = await abrir();
    await click(byRole('button', /^Publicar$/, linha));
    expect(text(linha)).toContain('Só uma pessoa publica');
    expect(statusCalls()).toHaveLength(0);                                                     // abrir não publica
    const confirmar = byRole('button', /^Confirmar publicação/, linha);
    expect(confirmar.getAttribute('aria-disabled')).toBe('true');                               // sem motivo, não
    await setValue(byRole('textbox', /Motivo/, linha) as HTMLInputElement, 'fato conferido nas duas fontes');
    await click(byRole('button', /^Confirmar publicação/, linha));
    await waitFor(() => expect(statusCalls()).toHaveLength(1));
    expect(statusCalls()[0]!.body).toEqual({ to: 'published', reason: 'fato conferido nas duas fontes' });
    expect(statusCalls()[0]!.path).toBe('/api/aprendizado/licao/li-fato/status');
  });

  it('cancelar a confirmação não publica; recusa do servidor aparece e nada some', async () => {
    backend.on('POST', /^\/api\/aprendizado\/licao\/li-fato\/status$/, () => apiError(409, 'transicao_invalida', 'A lição já mudou de estado.'));
    const linha = await abrir();
    await click(byRole('button', /^Publicar$/, linha));
    await click(byRole('button', /^Cancelar/, linha));
    expect(statusCalls()).toHaveLength(0);
    expect(() => byRole('button', /^Confirmar publicação/, linha)).toThrow();
    await click(byRole('button', /^Publicar$/, linha));
    await setValue(byRole('textbox', /Motivo/, linha) as HTMLInputElement, 'confere');
    await click(byRole('button', /^Confirmar publicação/, linha));
    await waitFor(() => expect(text(linha)).toContain('A lição já mudou de estado.'));
    expect(byRole('button', /^Confirmar publicação/, linha)).toBeTruthy();
  });

  it('já publicada: mostra de onde veio mas não oferece o Publicar; fato vencido avisa; sem proveniência fica "não informado"', async () => {
    const publicada = await abrir({ state: 'published', acoes: [] });
    expect(() => byRole('button', /^Publicar$/, publicada)).toThrow();
    expect(text(publicada.querySelector('[data-fato-da-operacao]')!)).toContain('operação op-20261007-1');
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /^\/api\/aprendizado\/licao\/li-fato$/, () => json(detalhe({ proveniencia: { ...PROVENIENCIA, frescor_ate: '2020-01-01T00:00:00Z' } })));
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido' } }, 'replace');
    await act(async () => root.render(<AprendizadoPage />));
    await waitFor(() => expect(container.querySelector('[data-item="licao:li-fato"]')).not.toBeNull());
    const linha = container.querySelector('[data-item="licao:li-fato"]') as HTMLElement;
    await openDetails(/Detalhes, evidência e trilha/, linha);
    await waitFor(() => expect(linha.querySelector('[data-frescor]')).not.toBeNull());
    expect(linha.querySelector('[data-frescor]')!.getAttribute('data-vencido')).toBe('sim');
    expect(text(linha)).toContain('O prazo deste fato passou');
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /^\/api\/aprendizado\/licao\/li-fato$/, () => json(detalhe({ proveniencia: undefined })));
    await act(async () => root.render(<AprendizadoPage />));
    await waitFor(() => expect(container.querySelector('[data-item="licao:li-fato"]')).not.toBeNull());
    const sem = container.querySelector('[data-item="licao:li-fato"]') as HTMLElement;
    await openDetails(/Detalhes, evidência e trilha/, sem);
    await waitFor(() => expect(sem.querySelector('[data-fato-da-operacao] [data-frescor]')).not.toBeNull());
    expect(text(sem.querySelector('[data-fato-da-operacao]')!)).toContain('operação não informada');
    expect(text(sem.querySelector('[data-fato-da-operacao]')!)).toContain('Frescor não informado');
  });

  it('com o source_kind na entrada o Publicar já está na linha (um botão só) e o detalhe não o repete', async () => {
    backend.on('POST', /^\/api\/aprendizado\/licao\/li-fato\/status$/, () => json(detalhe({}, { state: 'published' })));
    const linha = await abrir({ source_kind: 'operation_fact' });
    expect(container.querySelectorAll('[data-item="licao:li-fato"] button').length).toBeGreaterThan(0);
    const publicar = Array.from(linha.querySelectorAll('button')).filter((b) => /^Publicar$/.test(text(b).trim()));
    expect(publicar).toHaveLength(1);
    await click(publicar[0] as HTMLElement);
    await setValue(byRole('textbox', /Motivo/, linha) as HTMLInputElement, 'ok');
    await click(byRole('button', /^Confirmar publicação/, linha));
    await waitFor(() => expect(statusCalls()).toHaveLength(1));
    expect(statusCalls()[0]!.body).toEqual({ to: 'published', reason: 'ok' });
  });

  it('lição comum (sem sinal de fato) não ganha a seção nem o Publicar', async () => {
    const linha = await abrir({}, { conteudo: { ...CONTEUDO, modelo: 'outra' }, proveniencia: undefined });
    expect(linha.querySelector('[data-fato-da-operacao]')).toBeNull();
    expect(() => byRole('button', /^Publicar$/, linha)).toThrow();
  });
});
