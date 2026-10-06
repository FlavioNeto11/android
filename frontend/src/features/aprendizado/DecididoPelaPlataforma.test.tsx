// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { fatosDoMotivo, lerRelatorioDaAprovacao, motivoEmPalavras, rotuloDaRegra, tituloDaDecisao } from './aprovacaoAutomatica';

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
    await waitFor(() => linha('receita:180') !== null);
    const vivo = linha('receita:180');
    expect(text(vivo)).toContain('Receita nº 180 · send message (v1)');
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
    await waitFor(() => linha('receita:180') !== null);
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
    await waitFor(() => linha('receita:1') !== null);
    expect(container.querySelectorAll('[data-decisao-da-plataforma]')).toHaveLength(5);
    await click(byRole('button', /Ver todas as 7 decisões/));
    expect(container.querySelectorAll('[data-decisao-da-plataforma]')).toHaveLength(7);
    expect(byRole('button', /Mostrar só as mais recentes/)).toBeTruthy();
  });

  it('30.63: o Ver todas diz quantas foram desfeitas', async () => {
    const muitas = Array.from({ length: 7 }, (_, i) => ({ ...DECISOES[0], item_ref: `receita:${i + 1}`, ref: String(i + 1),
                                                          titulo: `receita ${i + 1}`, estado: i === 6 ? 'disabled' : 'published' }));
    relatorio = { modo: 'on', ultima_volta: null, casos_na_sombra: 0, decididos_pela_plataforma: muitas };
    await montar();
    await waitFor(() => linha('receita:1') !== null);
    expect(byRole('button', /Ver todas as 7 decisões \(1 desfeita\)/)).toBeTruthy();
  });

  it('em shadow avisa que só observa e nomeia o que decidiria pelas filas', async () => {
    relatorio = { modo: 'shadow', casos_na_sombra: 1, decididos_pela_plataforma: [],
                  ultima_volta: { em: '2026-10-04T17:00:00Z', avaliados: 40, decidiria: ['receita:80'], decididos: [] } };
    await montar();
    await waitFor(() => secao() !== null);
    expect(text(secao()!)).toContain('Em observação');
    expect(text(secao()!)).toContain('decidiria 1 de 40 itens');
    expect(text(secao()!.querySelector('[aria-label="O que a plataforma decidiria"]')!)).toContain('Back to list (v1)');
  });

  it('em shadow com a lista vazia diz que nada foi decidido; com decisões, que são de quando ela decidia sozinha', async () => {
    relatorio = { modo: 'shadow', casos_na_sombra: 0, decididos_pela_plataforma: [], ultima_volta: null };
    await montar();
    await waitFor(() => secao() !== null);
    expect(text(secao()!)).toContain('Nada foi decidido sozinho ainda');
    await act(async () => root.unmount());
    container.remove();
    montado = false;
    relatorio = { modo: 'shadow', casos_na_sombra: 0, decididos_pela_plataforma: DECISOES, ultima_volta: null };
    await montar();
    await waitFor(() => linha('receita:180') !== null);
    expect(text(secao()!)).not.toContain('Nada foi decidido sozinho');
    expect(text(secao()!)).toContain('As decisões abaixo são de quando ela decidia.');   // a frase da main (deploy 31)
  });

  it('em off sem decisão a seção some e as filas seguem', async () => {
    relatorio = { modo: 'off', ultima_volta: null, casos_na_sombra: 0, decididos_pela_plataforma: [] };
    await montar();
    await waitFor(() => expect(text(container)).toContain('Back to list (v1)'));
    expect(secao()).toBeNull();
  });

  it('sem a rota no backend (antes do 30.55) a seção some e as filas seguem', async () => {
    backend.on('GET', /^\/api\/aprendizado\/aprovacao-automatica$/, () => json({ detail: { code: 'not_found' } }, 404));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Back to list (v1)'));
    expect(secao()).toBeNull();
  });

  it('30.63 (c): outro erro da rota aparece na seção, em vez de ela sumir', async () => {
    backend.on('GET', /^\/api\/aprendizado\/aprovacao-automatica$/,
               () => json({ detail: { code: 'internal', message: 'Falha ao ler a régua.' } }, 500));
    await montar();
    await waitFor(() => expect(secao()).not.toBeNull());
    expect(text(secao()!)).toContain('Decidido pela plataforma');
    expect(secao()!.querySelector('[role="alert"], button')).not.toBeNull();
    expect(text(container)).toContain('Back to list (v1)');                   // as filas seguem
  });

  it('30.63 (d): em shadow com decisões antigas não diz que nada foi decidido', async () => {
    relatorio = { modo: 'shadow', casos_na_sombra: 0, decididos_pela_plataforma: DECISOES,
                  ultima_volta: { em: '2026-10-04T18:00:00Z', avaliados: 40, decidiria: [], decididos: [] } };
    await montar();
    await waitFor(() => linha('receita:180') !== null);
    expect(text(secao()!)).toContain('Em observação');
    expect(text(secao()!)).not.toContain('Nada foi decidido sozinho');
    expect(text(secao()!)).toContain('de quando ela decidia');
  });
});

describe('30.63: o porquê com os rótulos da tela', () => {
  it('troca a saúde crua e o parecer com id pelos rótulos', () => {
    expect(fatosDoMotivo('auto:qa_revisar v1 — classe B; 3 a favor; saúde pouca_amostra; parecer pedir_evidencia (lr-ade33a6e8607eaeb)'))
      .toBe('classe B; 3 a favor; saúde: Pouca amostra; parecer do curador: pedir mais evidência');
    expect(fatosDoMotivo('auto:qa_revisar v1 — saúde saudavel')).toBe('saúde: Saudável');
  });
});

describe('30.66: os textos da aba Para aprovar', () => {
  it('a regra aparece com nome legível, e o id cru só no title', async () => {
    await montar();
    await waitFor(() => linha('receita:180') !== null);
    const vivo = linha('receita:180');
    expect(text(vivo)).toContain('Aprovação automática do que esperava você');
    expect(text(linha('receita:5'))).toContain('Confirmação automática do que estava em revisão');
    expect(text(secao()!)).not.toMatch(/qa_para_aprovar|qa_revisar/);
    expect(vivo.querySelector('[title="qa_para_aprovar"]')).toBeTruthy();
    expect(rotuloDaRegra('outra_regra')).toBe('Regra automática outra_regra');
  });

  it('título de chave interna com versão ou com lacuna crua fica legível', async () => {
    relatorio = { modo: 'on', ultima_volta: null, casos_na_sombra: 0, decididos_pela_plataforma: [
      ...DECISOES, { ...DECISOES[0], item_ref: 'fluxo:f-1', kind: 'fluxo', ref: 'f-1', em: '2026-10-04T17:01:00Z',
                     titulo: 'Mandar "{message_template}" para {recipient_1}' }] };
    await montar();
    await waitFor(() => linha('fluxo:f-1') !== null);
    expect(text(linha('fluxo:f-1'))).toContain('Mandar … para …');
    expect(text(linha('receita:5'))).toContain('Receita nº 5 · collect contacts (v1)');
    expect(text(secao()!)).not.toMatch(/[{}]|_i1|send_message/);
    const d = { kind: 'receita' as const, ref: '9', item_ref: 'receita:9' };
    expect(tituloDaDecisao({ ...d, titulo: null })).toBe('Receita nº 9');
    expect(tituloDaDecisao({ ...d, titulo: '{x}' })).toBe('Receita nº 9');
    expect(tituloDaDecisao({ ...d, titulo: 'send_message_i1 (v1)' }, 'Enviar a mensagem (v1)')).toBe('Enviar a mensagem (v1)');
    expect(tituloDaDecisao({ ...d, titulo: 'Enviar a mensagem (v2)' })).toBe('Enviar a mensagem (v2)');
  });

  it('com a fila vazia e itens em Revisar, o topo diz as duas coisas e cita a aprovação automática ligada', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Nada para aprovar; 1 para revisar sem pressa'));
    expect(text(container)).not.toContain('Nada aguardando você');
    await waitFor(() => expect(text(container)).toContain('até você decidir (ou a aprovação automática, que está ligada)'));
    expect(text(container)).toContain('aparece aqui, a menos que a aprovação automática o publique.');
  });

  it('com a aprovação automática desligada, o vazio não fala dela', async () => {
    relatorio = { modo: 'off', ultima_volta: null, casos_na_sombra: 0, decididos_pela_plataforma: [] };
    await montar();
    await waitFor(() => expect(text(container)).toContain('Nada para aprovar; 1 para revisar sem pressa'));
    expect(text(container)).not.toContain('aprovação automática, que está ligada');
    expect(text(container)).not.toContain('a menos que a aprovação automática');
  });

  it('com Revisar em falha, o topo não afirma o vazio', async () => {
    backend.on('GET', /^\/api\/aprendizado\/revisar$/,
               () => json({ detail: { code: 'internal', message: 'falhou' } }, 500));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Nada para aprovar; não deu para carregar Revisar'));
    expect(text(container)).not.toContain('Nada aguardando você');
  });

  it('o título usa o nome do catálogo quando o backend o manda', () => {
    const d = { kind: 'receita' as const, ref: '180', item_ref: 'receita:180', titulo: 'send_message_i1 (v1)' };
    expect(tituloDaDecisao({ ...d, capability: 'SEND_MESSAGE', capability_nome: 'Enviar a mensagem' }))
      .toBe('Enviar a mensagem (v1)');
    expect(tituloDaDecisao({ ...d, etapa: 'Digitar a mensagem' })).toBe('Digitar a mensagem (v1)');
    expect(tituloDaDecisao(d)).toBe('Receita nº 180 · send message (v1)');           // sem catálogo: o recurso de antes
  });

  it('em Revisar a frase do efeito externo aparece uma vez, no cabeçalho, e não em cada item', async () => {
    const motivo = { codigo: 'efeito_externo', espera_o_dono: true, detalhe: null };
    const itens = ['80', '81', '82'].map((ref) => ({ ...LEGADO, ref, title: `item ${ref} (v1)`, por_que_nao_publica: motivo }));
    const outro = { ...LEGADO, ref: '83', title: 'item 83 (v1)',
                    por_que_nao_publica: { codigo: 'texto_de_pessoa', espera_o_dono: true, detalhe: null } };
    backend.on('GET', /^\/api\/aprendizado\/revisar$/, () => json({ itens: [...itens, outro], total: 4 }));
    await montar();
    await waitFor(() => expect(text(container)).toContain('item 83 (v1)'));
    const revisar = container.querySelector('[aria-labelledby="aprendizado-revisar"]') as HTMLElement;
    expect(text(revisar).split('Publicados antes da regra de aprovação (tem efeito externo)')).toHaveLength(2);
    expect(text(revisar)).not.toContain('Publicado antes da regra de aprovação (tem efeito externo)');
    // o item com OUTRO motivo segue dizendo o dele
    expect(text(revisar)).toContain('Publicado antes da regra de aprovação (tem texto de pessoa): vale revisar.');
  });
});

describe('31.164: o motivo da régua nas telas que o repetem', () => {
  const CRU = 'auto:qa_revisar v1 — classe B; app com.pocqa.messenger (qa); 3 a favor, 0 contra; 0 falhas de reprodução; saúde pouca_amostra; parecer pedir_evidencia (lr-fe4a3e84376de6ac)';
  const EM_PALAVRAS = 'Confirmação automática do que estava em revisão — classe B; app com.pocqa.messenger (qa); 3 a favor, 0 contra; 0 falhas de reprodução; saúde: Pouca amostra; parecer do curador: pedir mais evidência';

  it('põe o nome da regra e os rótulos dos fatos, com ou sem o prefixo da confirmação', () => {
    expect(motivoEmPalavras(CRU)).toBe(EM_PALAVRAS);
    expect(motivoEmPalavras(`confirmado que fica: ${CRU}`)).toBe(EM_PALAVRAS);
    expect(motivoEmPalavras('auto:qa_para_aprovar v1 — saúde saudavel; parecer manter'))
      .toBe('Aprovação automática do que esperava você — saúde: Saudável; parecer do curador: manter como está');
  });

  it('o parecer sem o id da revisão também ganha o rótulo, e o motivo digitado por uma pessoa fica como está', () => {
    expect(fatosDoMotivo('auto:qa_revisar v1 — parecer pedir_evidencia')).toBe('parecer do curador: pedir mais evidência');
    expect(motivoEmPalavras('conferi o alvo; saúde pouca_amostra mesmo')).toBe('conferi o alvo; saúde pouca_amostra mesmo');
    expect(motivoEmPalavras('aprendida da IA; em prova (sombra)')).toBe('aprendida da IA; em prova (sombra)');
  });
});
