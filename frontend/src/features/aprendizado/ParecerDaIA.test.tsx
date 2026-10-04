// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { DetalheRico } from './DetalheRico';
import type { AcaoPermitida, DetalheDoLivro, EntradaDoLivro, EstadoDoLivro, RotuloDaAcao } from './model';
import {
  type BlocoDoCurador, type ParecerDaIA, type ParecerNaFila, ladoDaDecisao, rotuloDoAceite, seloDaClasse,
  textoDaDecisaoFinal, textoDaRecusa, textoDaValidade,
} from './parecer';

/**
 * 30.17: o parecer da IA no painel, contra o contrato de `presentation/livro.py` (`_revisao`, `_parecer_na_fila`,
 * `_bloco_da_ia`), com o backend simulado. Prova `simulated` — nenhuma rota real foi chamada.
 */

const ACAO = (to: EstadoDoLivro, rotulo: RotuloDaAcao): AcaoPermitida => ({ to, rotulo, exige_motivo: true });

function entrada(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'licao', ref: 'li-b', state: 'candidate', native_status: null, title: 'Role a lista antes', app: 'com.pocqa.messenger',
    origin: 'pessoa', side_effect: false, human_origin: true, requires_owner: true, created_at: '2026-10-02T10:00:00Z',
    state_at: '2026-10-02T10:00:00Z', last_used_at: null, uses: 0, evidence: { for: 2, against: 0 }, count: null,
    detail: null, acoes: [ACAO('validated', 'validar'), ACAO('disabled', 'rejeitar')],
    por_que_nao_publica: { codigo: 'texto_de_pessoa', espera_o_dono: true, detalhe: null }, ...over,
  };
}

const NA_FILA_B: ParecerNaFila = {
  id: 'lr-b', criado_em: '2026-10-02T12:00:00Z', decisao: 'aprovar', confianca: 'alta', classe: 'B', simulated: false,
  acao: { to: 'validated', rotulo: 'validar' }, recusa: null, recusa_no_lote: null,
};
const NA_FILA_C: ParecerNaFila = {
  id: 'lr-c', criado_em: '2026-10-02T12:00:00Z', decisao: 'aprovar', confianca: 'media', classe: 'C', simulated: false,
  acao: { to: 'published', rotulo: 'aprovar' }, recusa: null, recusa_no_lote: 'lote_na_classe_c',
};
const LICAO_B = entrada({ parecer: NA_FILA_B });
const RECEITA_C = entrada({
  kind: 'receita', ref: '109', state: 'validated', native_status: 'validated', title: 'Enviar a mensagem', origin: 'execucao',
  side_effect: true, human_origin: false, acoes: [ACAO('published', 'aprovar'), ACAO('disabled', 'rejeitar')],
  por_que_nao_publica: { codigo: 'efeito_externo', espera_o_dono: true, detalhe: null }, parecer: NA_FILA_C,
});

function parecer(over: Partial<ParecerDaIA>): ParecerDaIA {
  return {
    id: 'lr-b', criado_em: '2026-10-02T12:00:00Z', gatilho: 'nova_pendencia_do_dono', validade: 'ok', classe: 'B',
    simulated: false, modelo: 'modelo-x', estado_no_parecer: 'candidate',
    parecer: { decisao: 'aprovar', alvo: null, faixa: 'B', causa: 'reproduz_bem', confianca: 'alta', probabilidade: 0.9,
               evidencias_citadas: ['licao:li-b', 'run:r-20261002-abc'], riscos: [], inconsistencias: [],
               falta: ['reproducao_em_outro_aparelho'], conclusao: 'Reproduziu nas duas execuções.' },
    atual: true, acao: { to: 'validated', rotulo: 'validar' }, recusa: null, decisao_final: null, decidido_por: null,
    override: false, override_motivo: null, transicao_id: null, ...over,
  };
}

const ON: BlocoDoCurador = { modo: 'on', pendentes_ocultos: 0, pode_pedir_revisao: true };

function detalhe(pareceres: ParecerDaIA[], curador: BlocoDoCurador | null): DetalheDoLivro {
  return { item: entrada({}), evidencias: [], trilha: [], exposicoes: [], pareceres, curador };
}

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/aprendizado\/revisar$/, () => json({ itens: [], total: 0, curador: { modo: 'on' } }));
  backend.on('GET', /^\/api\/aprendizado\/[a-z]+\/[^/]+$/, () => json(detalhe([], ON)));
  backend.on('POST', /\/status$/, () => json(detalhe([], ON)));
  backend.on('POST', /\/parecer\/[^/]+$/, () => json(detalhe([], ON)));
  useToastStore.setState({ toasts: [] });
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprovar' } }, 'replace');
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function montar(no: React.ReactElement): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(no);
  });
}

const item = (ref: string) => container.querySelector(`[data-item="${ref}"]`) as HTMLElement;
const secao = () => container.querySelector('[data-secao-parecer]') as HTMLElement;
const chamadas = (re: RegExp) => backend.callsTo('POST', re);

describe('parecer da IA na fila', () => {
  it('a linha mostra o que a IA sugere, e decidir pela linha leva o review_id', async () => {
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [LICAO_B], total: 1, curador: { modo: 'on' } }));
    await montar(<AprendizadoPage />);
    await waitFor(() => expect(item('licao:li-b')).toBeTruthy());
    expect(text(item('licao:li-b'))).toContain('Parecer do curador: aprovar · confiança alta');
    expect(text(item('licao:li-b'))).toContain('Classe B · aceite em lote');
    await click(byRole('button', /^Validar$/, item('licao:li-b')));
    await setValue(byRole('textbox', /Motivo/, item('licao:li-b')) as HTMLInputElement, 'conferi');
    await click(byRole('button', /^Confirmar validação/, item('licao:li-b')));
    await waitFor(() => expect(chamadas(/\/status$/)).toHaveLength(1));
    expect(chamadas(/\/status$/)[0]?.body).toEqual({ to: 'validated', reason: 'conferi', review_id: 'lr-b' });
  });

  it('aceite em lote: só os pareceres da classe B entram; a C fica fora com a razão', async () => {
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [RECEITA_C, LICAO_B], total: 2, curador: { modo: 'on' } }));
    await montar(<AprendizadoPage />);
    await waitFor(() => expect(item('licao:li-b')).toBeTruthy());
    const botao = () => byRole('button', /^Aceitar pareceres do curador/, container);
    expect(botao().getAttribute('aria-disabled')).toBe('true');          // nada selecionado
    await click(byRole('checkbox', /Selecionar/, item('receita:109')));
    expect(text(botao())).toContain('(0)');                             // a C não entra no lote
    await click(byRole('checkbox', /Selecionar/, item('licao:li-b')));
    expect(text(botao())).toContain('(1)');
    await click(botao());
    // 30.54: antes do motivo, o que o curador sugere para cada item e quem fica de fora; o botão apagado diz por quê.
    const resumo = container.querySelector('[data-resumo-do-lote]') as Element;
    expect(text(resumo)).toContain('O curador sugere:');
    expect(text(resumo)).toContain('1 selecionado fica de fora (classe C ou sem parecer)');
    expect(text(container.querySelector('ul[aria-label="O que cada aceite faz"]') as Element)).toContain('validado (ainda não publicado)');
    expect(text(container.querySelector('[data-por-que-apagado]') as Element)).toContain('Diga o motivo');
    expect(byRole('button', /^Aprovar selecionados/, container).className).not.toMatch(/primary/i);
    await setValue(byRole('textbox', /Motivo do aceite em lote/, container) as HTMLInputElement, 'pareceres conferidos');
    expect(container.querySelector('[data-por-que-apagado]')).toBeNull();
    await click(byRole('button', /^Aceitar 1 parecer/, container));
    await waitFor(() => expect(chamadas(/\/parecer\//)).toHaveLength(1));
    expect(chamadas(/\/parecer\//)[0]?.path).toBe('/api/aprendizado/licao/li-b/parecer/lr-b');
    expect(chamadas(/\/parecer\//)[0]?.body).toEqual({ resposta: 'aceitar', motivo: 'pareceres conferidos', em_lote: true });
    const [aviso] = useToastStore.getState().toasts;
    expect(aviso?.title).toContain('1 parecer aceito de 2 itens selecionados');
    expect(JSON.stringify(aviso?.details)).toContain('Classe C: decida item a item');
    expect(aviso?.details).toContain('Role a lista antes: validado (ainda não publicado)');   // validar não é publicar
    expect(chamadas(/\/status$/)).toHaveLength(0);                       // aceitar o parecer não é o /status
  });

  it('30.38-c: o parecer gravado como B que hoje é C mostra C, diz que era B e não entra no lote', async () => {
    const era = entrada({ ref: 'li-era', parecer: { ...NA_FILA_C, id: 'lr-era', classe_no_parecer: 'B' } });
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [era], total: 1, curador: { modo: 'on' } }));
    await montar(<AprendizadoPage />);
    await waitFor(() => expect(item('licao:li-era')).toBeTruthy());
    expect(text(item('licao:li-era'))).toContain('Classe C · item a item (era B no parecer)');
    expect(text(item('licao:li-era'))).not.toContain('aceite em lote');
  });

  it('fora do modo ligado não há selo nem aceite em lote', async () => {
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [entrada({})], total: 1, curador: { modo: 'shadow' } }));
    await montar(<AprendizadoPage />);
    await waitFor(() => expect(item('licao:li-b')).toBeTruthy());
    expect(text(item('licao:li-b'))).not.toContain('Parecer do curador');
    expect(container.querySelector('[data-parecer-na-linha]')).toBeNull();
    expect(text(container)).not.toContain('Aceitar pareceres do curador');
  });
});

describe('parecer da IA no detalhe', () => {
  it('em on: a sugestão, a classe, a conclusão e o que a IA citou; aceitar exige motivo e chama a rota do parecer', async () => {
    const mudou = vi.fn();
    await montar(<DetalheRico detalhe={detalhe([parecer({})], ON)} onMudou={mudou} />);
    const sec = secao();
    expect(text(sec)).toContain('O curador sugere: Aprovar');
    expect(text(sec)).toContain('confiança alta');
    expect(text(sec)).toContain('Classe B · aceite em lote');
    expect(text(sec)).toContain('Reproduziu nas duas execuções.');
    expect(text(sec)).toContain('reproduzir em outro aparelho');
    const run = sec.querySelector('a[href*="r-20261002-abc"]');
    expect(run).toBeTruthy();
    await click(byRole('button', /^Aceitar e validar$/, sec));
    await click(byRole('button', /^Confirmar: validar/, sec));
    expect(chamadas(/\/parecer\//)).toHaveLength(0);                     // sem motivo, nada sai
    await setValue(byRole('textbox', /Motivo do aceite/, sec) as HTMLInputElement, 'a IA viu bem');
    await click(byRole('button', /^Confirmar: validar/, sec));
    await waitFor(() => expect(chamadas(/\/parecer\//)).toHaveLength(1));
    expect(chamadas(/\/parecer\//)[0]?.path).toBe('/api/aprendizado/licao/li-b/parecer/lr-b');
    expect(chamadas(/\/parecer\//)[0]?.body).toEqual({ resposta: 'aceitar', motivo: 'a IA viu bem', em_lote: false });
    await waitFor(() => expect(mudou).toHaveBeenCalledTimes(1));
  });

  it('recusar o parecer pede o porquê; a recusa do backend aparece em português', async () => {
    backend.on('POST', /\/parecer\/[^/]+$/, () => json({ detail: { code: 'parecer_desatualizado', message: 'mudou' } }, 409));
    await montar(<DetalheRico detalhe={detalhe([parecer({})], ON)} />);
    const sec = secao();
    await click(byRole('button', /^Recusar o parecer$/, sec));
    // A recusa não toca o item: o aviso do botão não promete a trilha.
    expect(text(byRole('button', /^Confirmar recusa/, sec))).toContain('fica no registro do parecer');
    await setValue(byRole('textbox', /Por que você recusa/, sec) as HTMLInputElement, 'a nota fala de outra tela');
    await click(byRole('button', /^Confirmar recusa/, sec));
    await waitFor(() => expect(text(sec)).toContain('O item mudou depois do parecer'));
    expect(chamadas(/\/parecer\//)[0]?.body).toEqual({ resposta: 'recusar', motivo: 'a nota fala de outra tela', em_lote: false });
  });

  it('parecer simulado e o da classe A ficam só de registro: sem botões, com a razão', async () => {
    await montar(<DetalheRico detalhe={detalhe([parecer({ simulated: true, recusa: 'parecer_simulado' })], ON)} />);
    let sec = secao();
    expect(text(sec)).toContain('simulado');
    expect(text(sec)).toContain('Parecer de teste');
    expect(text(sec)).not.toContain('aceite em lote');                 // o selo não promete o aceite que não há
    expect(sec.querySelector('[title^="Efeito médio."]')?.getAttribute('title')).toContain('Parecer de teste');
    expect(text(sec)).not.toContain('Aceitar e validar');
    await act(async () => root.render(<DetalheRico detalhe={detalhe([parecer({ classe: 'A', recusa: 'so_registro_na_classe_a' })], ON)} />));
    sec = secao();
    expect(text(sec)).toContain('Classe A');
    expect(text(sec)).not.toContain('Classe A · só registro · ');
    expect(text(sec)).toContain('quem decide é a regra automática');
    expect(text(sec)).not.toContain('Recusar o parecer');
  });

  it('em sombra o parecer pendente não aparece: só o aviso e os já decididos', async () => {
    const decidido = parecer({ id: 'lr-velho', atual: false, decisao_final: 'validar', decidido_por: 'panel', override: false });
    await montar(<DetalheRico detalhe={detalhe([decidido], { modo: 'shadow', pendentes_ocultos: 1, pode_pedir_revisao: false })} />);
    const sec = secao();
    expect(text(sec)).toContain('Há um parecer do curador sobre este item. Ele aparece depois da sua decisão');
    expect(text(sec)).not.toContain('O curador sugere:');
    expect(text(sec)).toContain('panel decidiu sem ver o parecer (validar) e concordou com o curador.');
    expect(text(sec)).not.toContain('Pedir revisão');
  });

  it('com o curador desligado e nada registrado, a seção não existe', async () => {
    await montar(<DetalheRico detalhe={detalhe([], { modo: 'off', pendentes_ocultos: 0, pode_pedir_revisao: false })} />);
    expect(text(container)).not.toContain('Parecer do curador');
    await act(async () => root.render(<DetalheRico detalhe={detalhe([], null)} />));
    expect(text(container)).not.toContain('Parecer do curador');
  });

  it('pedir revisão: pedido registrado, já revisado, ou a recusa do modo', async () => {
    let resposta: Response = json({ pedido: true, revisao: null }, 202);
    backend.on('POST', /\/revisao$/, () => resposta);
    const mudou = vi.fn();
    await montar(<DetalheRico detalhe={detalhe([], ON)} onMudou={mudou} />);
    const sec = secao();
    expect(text(sec)).toContain('O curador ainda não revisou este item.');
    await click(byRole('button', /Pedir revisão ao curador/, sec));
    await waitFor(() => expect(text(sec)).toContain('Pedido registrado'));
    expect(chamadas(/\/revisao$/)[0]?.path).toBe('/api/aprendizado/licao/li-b/revisao');
    resposta = json({ pedido: false, revisao: parecer({ atual: false }) });
    await click(byRole('button', /Pedir revisão ao curador/, sec));
    await waitFor(() => expect(text(sec)).toContain('O item já foi revisado como está agora'));
    expect(mudou).toHaveBeenCalledTimes(1);
    resposta = json({ detail: { code: 'curador_fora_do_on', message: 'fora' } }, 409);
    await click(byRole('button', /Pedir revisão ao curador/, sec));
    await waitFor(() => expect(text(sec)).toContain('Pedir revisão só com o curador ligado.'));
  });

  it('os pareceres anteriores dizem o que a IA sugeriu e quem decidiu', async () => {
    const anteriores = [
      parecer({ id: 'lr-1', atual: false, decisao_final: 'recusou', decidido_por: 'dono', override: true, override_motivo: 'outra tela' }),
      parecer({ id: 'lr-2', atual: false, validade: 'invalida:sem_citacao', parecer: null }),
      parecer({ id: 'lr-3', atual: false, validade: 'recusada:custo', parecer: null }),
    ];
    await montar(<DetalheRico detalhe={detalhe(anteriores, ON)} />);
    const sec = secao();
    expect(text(sec)).toContain('sugeriu aprovar — dono recusou: outra tela');
    expect(text(sec)).toContain('fora do contrato e foi descartada');
    expect(text(sec)).toContain('caro demais para o orçamento');
  });
});

describe('textos do parecer', () => {
  it('30.38-c: a classe é a de agora; se o parecer foi gravado com outra, o selo diz', () => {
    expect(seloDaClasse('C', null, 'B')).toMatchObject({ selo: 'Classe C · item a item (era B no parecer)', registro: null });
    expect(seloDaClasse('C', null, 'B')?.explica).toContain('gravado como classe B; vale a de agora');
    expect(seloDaClasse('B', null, null)?.selo).toBe('Classe B · aceite em lote');
    expect(seloDaClasse('B', null, 'B')?.selo).toBe('Classe B · aceite em lote');
    expect(seloDaClasse('A', 'so_registro_na_classe_a', 'B')?.selo).toBe('Classe A (era B no parecer)');
  });

  it('o botão do aceite diz o passo; sem passo, é concordar', () => {
    expect(rotuloDoAceite({ to: 'validated', rotulo: 'validar' }).label).toBe('Aceitar e validar');
    expect(rotuloDoAceite({ to: 'disabled', rotulo: 'desligar' })).toMatchObject({ label: 'Aceitar e desligar', perigo: true });
    expect(rotuloDoAceite(null).label).toBe('Concordar');
  });

  it('o lado, a decisão final, a validade e a recusa desconhecida', () => {
    expect([ladoDaDecisao('aprovar'), ladoDaDecisao('desativar'), ladoDaDecisao('observar')]).toEqual(['sobe', 'desce', 'espera']);
    expect(textoDaDecisaoFinal({ decisao_final: 'aceitou', decidido_por: 'dono', override: false, override_motivo: null })).toBe('dono aceitou.');
    expect(textoDaDecisaoFinal({ decisao_final: 'aprovar', decidido_por: 'dono', override: true, override_motivo: null }))
      .toBe('dono decidiu sem ver o parecer (aprovar) e foi para outro lado.');
    expect(textoDaDecisaoFinal({ decisao_final: null, decidido_por: null, override: false, override_motivo: null })).toBeNull();
    expect(textoDaValidade('ok')).toBeNull();
    expect(textoDaValidade('recusada:triagem')).toContain('credencial');
    expect(textoDaRecusa('codigo_novo')).toBe('codigo_novo');
    expect(textoDaRecusa(null)).toBeNull();
  });
});
