// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { fatosDoMotivo, lerRelatorioDaAprovacao } from './aprovacaoAutomatica';

/**
 * 30.55: a seção "Decidido pela plataforma" da aba Para aprovar, contra o contrato do adendo v1.24
 * (`GET /api/aprendizado/aprovacao-automatica`), com o backend simulado. Prova `simulated`: nenhuma rota real foi
 * chamada.
 */

const MOTIVO = 'auto:qa_para_aprovar v1 — classe B; app com.pocqa.messenger (qa); 2 a favor, 0 contra; 0 falhas de reprodução; saúde em_prova; sem parecer que pese (delegação do dono)';
const DECISOES = [
  { item_ref: 'receita:180', kind: 'receita', ref: '180', de: 'validated', para: 'published', motivo: MOTIVO,
    em: '2026-10-04T17:00:00Z', gesto: 'publicar', regra: 'qa_para_aprovar', versao: 1, titulo: 'send_message_i1 (v1)',
    app: 'com.pocqa.messenger', estado: 'published' },
  { item_ref: 'receita:5', kind: 'receita', ref: '5', de: 'published', para: 'published',
    motivo: `confirmado que fica: auto:qa_revisar v1 — classe B; app com.pocqa.messenger (qa); 33 a favor, 0 contra`,
    em: '2026-10-04T16:59:00Z', gesto: 'confirmar_que_fica', regra: 'qa_revisar', versao: 1, titulo: 'collect_contacts (v1)',
    app: 'com.pocqa.messenger', estado: 'disabled' },
];
const LEGADO = {
  kind: 'receita', ref: '80', state: 'published', native_status: 'active', title: 'back_to_list (v1)',
  app: 'com.pocqa.messenger', origin: 'execucao', side_effect: true, human_origin: false, requires_owner: true,
  created_at: '2026-10-02T10:00:00Z', state_at: '2026-10-02T10:00:00Z', last_used_at: null, uses: 3,
  evidence: { for: 3, against: 0 }, count: null, detail: null, acoes: [],
};

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;
let relatorio: unknown;
let montado = false;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  relatorio = { modo: 'on', ultima_volta: null, casos_na_sombra: 0, decididos_pela_plataforma: DECISOES };
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 0 }));
  backend.on('GET', /^\/api\/aprendizado\/revisar$/, () => json({ itens: [LEGADO], total: 1 }));
  backend.on('GET', /^\/api\/aprendizado\/aprovacao-automatica$/, () => json(relatorio));
  backend.on('POST', /\/status$/, () => json({ item: { ...LEGADO, state: 'disabled' } }));
  useToastStore.setState({ toasts: [] });
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprovar' } }, 'replace');
});

afterEach(async () => {
  if (!montado) return;                       // a leitura pura não monta nada
  await act(async () => root.unmount());
  container.remove();
  montado = false;
});

async function montar(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  montado = true;
  await act(async () => {
    root.render(<AprendizadoPage />);
  });
}

const secao = () => container.querySelector('[aria-labelledby="aprendizado-plataforma"]') as HTMLElement | null;
const linha = (ref: string) => container.querySelector(`[data-decisao-da-plataforma="${ref}"]`) as HTMLElement;

describe('leitura do relatório', () => {
  it('é tolerante e tira o prefixo da regra dos fatos', () => {
    const r = lerRelatorioDaAprovacao({ modo: 'estranho', decididos_pela_plataforma: [{ item_ref: 'fluxo:x-y' }, 7] });
    expect(r.modo).toBe('off');
    expect(r.decididos).toHaveLength(1);
    expect(r.decididos[0]).toMatchObject({ kind: 'fluxo', ref: 'x-y', gesto: 'confirmar_que_fica', titulo: null });
    expect(lerRelatorioDaAprovacao(null)).toEqual({ modo: 'off', ultima_volta: null, casos_na_sombra: 0, decididos: [] });
    expect(fatosDoMotivo(MOTIVO)).toMatch(/^classe B; app com\.pocqa\.messenger/);
    expect(fatosDoMotivo('confirmado que fica: auto:qa_revisar v1 — 3 a favor')).toBe('3 a favor');
  });
});

describe('Decidido pela plataforma', () => {
  it('em on lista as decisões com o gesto, os fatos e o Desligar só no que segue vivo', async () => {
    await montar();
    await waitFor(() => linha('receita:180'));
    const vivo = linha('receita:180');
    expect(text(vivo)).toContain('send_message_i1 (v1)');
    expect(text(vivo)).toContain('Publicou');
    expect(text(vivo)).toContain('classe B; app com.pocqa.messenger (qa)');
    expect(text(vivo)).not.toContain('auto:');
    expect(byRole('button', /^Desligar$/, vivo)).toBeTruthy();
    const desligado = linha('receita:5');
    expect(text(desligado)).toContain('Confirmou que fica');
    expect(text(desligado)).toContain('Desligado depois');
    expect(desligado.querySelector('button')).toBeNull();
    // A seção fica depois das filas: o dono vê primeiro o que sobra para ele.
    const titulos = [...container.querySelectorAll('h2')].map((h) => h.textContent?.trim());
    expect(titulos.indexOf('Decidido pela plataforma')).toBeGreaterThan(titulos.indexOf('Revisar'));
    expect(text(container)).toContain('a plataforma decide sozinha');
  });

  it('desligar pede o motivo, chama a rota de sempre e relê', async () => {
    await montar();
    await waitFor(() => linha('receita:180'));
    await click(byRole('button', /^Desligar$/, linha('receita:180')));
    const campo = linha('receita:180').querySelector('textarea, input[type="text"], input:not([type])') as HTMLInputElement;
    await setValue(campo, 'prefiro olhar este');
    relatorio = { ...(relatorio as object), decididos_pela_plataforma: [{ ...DECISOES[0], estado: 'disabled' }, DECISOES[1]] };
    await click(byRole('button', /Confirmar desligamento/, linha('receita:180')));
    await waitFor(() => expect(backend.callsTo('POST', /\/aprendizado\/receita\/180\/status$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /\/status$/)[0]?.body).toEqual({ to: 'disabled', reason: 'prefiro olhar este' });
    await waitFor(() => expect(text(linha('receita:180'))).toContain('Desligado depois'));
    expect(useToastStore.getState().toasts.some((t) => /desligado/.test(t.title))).toBe(true);
  });

  it('mostra as 5 mais recentes e o resto sob demanda', async () => {
    const muitas = Array.from({ length: 7 }, (_, i) => ({ ...DECISOES[0], item_ref: `receita:${i + 1}`, ref: String(i + 1),
                                                          titulo: `receita ${i + 1}` }));
    relatorio = { modo: 'on', ultima_volta: null, casos_na_sombra: 0, decididos_pela_plataforma: muitas };
    await montar();
    await waitFor(() => linha('receita:1'));
    expect(container.querySelectorAll('[data-decisao-da-plataforma]')).toHaveLength(5);
    await click(byRole('button', /Ver todas as 7 decisões/));
    expect(container.querySelectorAll('[data-decisao-da-plataforma]')).toHaveLength(7);
    expect(byRole('button', /Mostrar só as mais recentes/)).toBeTruthy();
  });

  it('em shadow avisa que só observa e nomeia o que decidiria pelas filas', async () => {
    relatorio = { modo: 'shadow', casos_na_sombra: 1, decididos_pela_plataforma: [],
                  ultima_volta: { em: '2026-10-04T17:00:00Z', avaliados: 40, decidiria: ['receita:80'], decididos: [] } };
    await montar();
    await waitFor(() => secao());
    expect(text(secao()!)).toContain('Em observação');
    expect(text(secao()!)).toContain('decidiria 1 de 40 itens');
    expect(text(secao()!.querySelector('[aria-label="O que a plataforma decidiria"]')!)).toContain('back_to_list (v1)');
  });

  it('em off sem decisão a seção some e as filas seguem', async () => {
    relatorio = { modo: 'off', ultima_volta: null, casos_na_sombra: 0, decididos_pela_plataforma: [] };
    await montar();
    await waitFor(() => expect(text(container)).toContain('back_to_list (v1)'));
    expect(secao()).toBeNull();
  });

  it('sem a rota no backend (antes do 30.55) a seção some e as filas seguem', async () => {
    backend.on('GET', /^\/api\/aprendizado\/aprovacao-automatica$/, () => json({ detail: { code: 'not_found' } }, 404));
    await montar();
    await waitFor(() => expect(text(container)).toContain('back_to_list (v1)'));
    expect(secao()).toBeNull();
  });
});
