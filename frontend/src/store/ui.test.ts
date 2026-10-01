// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parseHash } from '../lib/rotas';
import { aplicarHash, bindHashRouting, menuRecolhidoInicial, podeVoltarPara, useUiStore } from './ui';

/**
 * Revisão de UX de 30/09 (tarefa 01): a URL passou a dizer onde a pessoa está — tela, objeto, guia e aparelho em
 * Foco. Abrir empilha (Voltar fecha); guia, canonização e seleção automática substituem; fechar volta pelo histórico
 * quando a entrada anterior é o destino.
 */

function irPara(hash: string): void {
  window.history.replaceState(null, '', hash);
  aplicarHash(true);
}

let desligar: (() => void) | null = null;

beforeEach(() => {
  irPara('#/painel');
  useUiStore.setState({ selectedRunId: null });
});

afterEach(() => {
  desligar?.();
  desligar = null;
  vi.restoreAllMocks();
});

describe('podeVoltarPara (critério de history.back)', () => {
  it('só volta por uma entrada que nós empilhamos e cuja anterior é o destino', () => {
    expect(podeVoltarPara(null, '#/painel')).toBe(false);
    expect(podeVoltarPara({ outro: 1 }, '#/painel')).toBe(false);
    expect(podeVoltarPara({ cda: 1, de: '#/painel' }, '#/painel')).toBe(true);
    expect(podeVoltarPara({ cda: 1, de: '#/personas' }, '#/painel')).toBe(false);
  });

  it('com critério, aceita a lista com qualquer filtro', () => {
    const lista = (de: { tela: string; segmentos: string[] }) => de.tela === 'personas' && de.segmentos.length === 0;
    expect(podeVoltarPara({ cda: 1, de: '#/personas?situacao=bloqueada' }, '#/personas', lista)).toBe(true);
    expect(podeVoltarPara({ cda: 1, de: '#/personas/x' }, '#/personas', lista)).toBe(false);
  });
});

describe('nome antigo e hash desconhecido', () => {
  it('#/perfis redireciona para #/personas sem empilhar e preserva objeto, guia e filtros', () => {
    window.history.replaceState(null, '', '#/perfis/ig-1/memoria?foco=android-02');
    const antes = window.history.length;
    desligar = bindHashRouting();
    expect(window.location.hash).toBe('#/personas/ig-1/memoria?foco=android-02');
    expect(window.history.length).toBe(antes);
    const s = useUiStore.getState();
    expect(s.view).toBe('personas');
    expect(s.rota.segmentos).toEqual(['ig-1', 'memoria']);
    expect(s.focusInstanceId).toBe('android-02');
  });

  it('hash que não nomeia tela volta à rota atual', () => {
    irPara('#/aplicativos?aba=versoes');
    window.history.replaceState(null, '', '#/nada');
    aplicarHash();
    expect(window.location.hash).toBe('#/aplicativos?aba=versoes');
    expect(useUiStore.getState().view).toBe('aplicativos');
  });

  it('Voltar/Avançar do navegador (hashchange) aplica a rota', () => {
    desligar = bindHashRouting();
    window.history.replaceState(null, '', '#/execucoes/r-9?aba=linha-do-tempo');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    const s = useUiStore.getState();
    expect(s.view).toBe('execucoes');
    expect(s.selectedRunId).toBe('r-9');
    expect(s.rota.query.aba).toBe('linha-do-tempo');
  });
});

describe('painel de Foco na URL', () => {
  it('abrir empilha ?foco= preservando os filtros da tela; fechar volta pelo histórico', () => {
    irPara('#/personas?q=ana&situacao=bloqueada');
    const antes = window.history.length;
    useUiStore.getState().openFocus('android-01');
    expect(window.location.hash).toBe('#/personas?foco=android-01&q=ana&situacao=bloqueada');
    expect(window.history.length).toBe(antes + 1);
    expect(useUiStore.getState().focusInstanceId).toBe('android-01');

    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {});
    useUiStore.getState().closeFocus();
    expect(back).toHaveBeenCalledTimes(1);
    // A tela responde já, sem esperar a travessia assíncrona do histórico.
    expect(useUiStore.getState().focusInstanceId).toBeNull();
    expect(useUiStore.getState().rota.query).toEqual({ q: 'ana', situacao: 'bloqueada' });
  });

  it('trocar de aparelho com o painel aberto substitui (não empilha)', () => {
    useUiStore.getState().openFocus('android-01');
    const antes = window.history.length;
    useUiStore.getState().openFocus('android-02');
    expect(window.location.hash).toBe('#/painel?foco=android-02');
    expect(window.history.length).toBe(antes);
  });

  it('link colado com ?foco=: fechar substitui a entrada, sem voltar para fora do portal', () => {
    irPara('#/painel?estado=desconhecido&foco=android-03');
    expect(useUiStore.getState().focusInstanceId).toBe('android-03');
    const back = vi.spyOn(window.history, 'back');
    const antes = window.history.length;
    useUiStore.getState().closeFocus();
    expect(back).not.toHaveBeenCalled();
    expect(window.location.hash).toBe('#/painel?estado=desconhecido');
    expect(window.history.length).toBe(antes);
    expect(useUiStore.getState().focusInstanceId).toBeNull();
  });

  it('trocar de tela leva o foco junto', () => {
    useUiStore.getState().openFocus('android-01');
    useUiStore.getState().setView('infraestrutura');
    expect(window.location.hash).toBe('#/infraestrutura?foco=android-01');
    expect(useUiStore.getState().focusInstanceId).toBe('android-01');
  });
});

describe('execuções na URL', () => {
  it('setView("execucoes") leva a execução selecionada no link', () => {
    useUiStore.getState().selectRun('r-1');
    expect(window.location.hash).toBe('#/painel'); // fora de Execuções a seleção não mexe no link
    useUiStore.getState().setView('execucoes');
    expect(window.location.hash).toBe('#/execucoes/r-1');
  });

  it('#/execucoes do menu com execução selecionada vira #/execucoes/<id> sem empilhar', () => {
    useUiStore.setState({ selectedRunId: 'r-2' });
    const antes = window.history.length;
    irPara('#/execucoes');
    expect(window.location.hash).toBe('#/execucoes/r-2');
    expect(useUiStore.getState().rota.segmentos).toEqual(['r-2']);
    expect(window.history.length).toBe(antes);
  });

  it('seleção automática substitui e tira a guia da execução anterior; clique da pessoa empilha', () => {
    irPara('#/execucoes/r-1?aba=relatorio');
    const antes = window.history.length;
    useUiStore.getState().selectRun('r-2');
    expect(window.location.hash).toBe('#/execucoes/r-2');
    expect(window.history.length).toBe(antes);
    useUiStore.getState().abrirExecucao('r-3');
    expect(window.location.hash).toBe('#/execucoes/r-3');
    expect(window.history.length).toBe(antes + 1);
    expect(useUiStore.getState().selectedRunId).toBe('r-3');
  });
});

describe('personas e trocas de parâmetro', () => {
  it('openPersona empilha #/personas/<id>/<guia>', () => {
    const antes = window.history.length;
    useUiStore.getState().openPersona('ig-1', 'memoria');
    expect(window.location.hash).toBe('#/personas/ig-1/memoria');
    expect(window.history.length).toBe(antes + 1);
    expect(useUiStore.getState().view).toBe('personas');
  });

  it('trocarQuery preserva os outros parâmetros e substitui', () => {
    irPara('#/personas?ordem=nome&q=ana');
    const antes = window.history.length;
    useUiStore.getState().trocarQuery({ visao: 'tabela', q: undefined });
    expect(parseHash(window.location.hash)?.query).toEqual({ ordem: 'nome', visao: 'tabela' });
    expect(window.history.length).toBe(antes);
  });

  it('voltarPara sem entrada anterior compatível vai ao destino pelo modo pedido', () => {
    irPara('#/aplicativos/instagram');
    const antes = window.history.length;
    useUiStore.getState().voltarPara({ tela: 'aplicativos', query: { aba: 'apps' } }, 'push',
      (de) => de.tela === 'aplicativos' && de.segmentos.length === 0);
    expect(window.location.hash).toBe('#/aplicativos?aba=apps');
    expect(window.history.length).toBe(antes + 1);
  });
});

/**
 * Tarefa 13 da rodada 2 (acessibilidade): sem preferência guardada, o menu abre EXPANDIDO (ícone + rótulo) a partir de
 * 1280 px e recolhido abaixo; a escolha da pessoa, uma vez guardada, vale em qualquer largura.
 */
describe('menuRecolhidoInicial (padrão por largura, preferência vence)', () => {
  const larguraOriginal = window.innerWidth;
  const largura = (px: number) => Object.defineProperty(window, 'innerWidth', { configurable: true, value: px });

  beforeEach(() => window.localStorage.removeItem('cda.menuRecolhido'));
  afterEach(() => {
    largura(larguraOriginal);
    window.localStorage.removeItem('cda.menuRecolhido');
  });

  it('sem preferência: expandido em 1280 px ou mais, recolhido abaixo', () => {
    largura(1440);
    expect(menuRecolhidoInicial()).toBe(false);
    largura(1280);
    expect(menuRecolhidoInicial()).toBe(false);
    largura(1279);
    expect(menuRecolhidoInicial()).toBe(true);
    largura(1024);
    expect(menuRecolhidoInicial()).toBe(true);
  });

  it('a preferência guardada vence o padrão, nos dois sentidos', () => {
    largura(1440);
    useUiStore.getState().setMenuRecolhido(true);
    expect(window.localStorage.getItem('cda.menuRecolhido')).toBe('true');
    expect(menuRecolhidoInicial()).toBe(true);
    largura(1100);
    useUiStore.getState().setMenuRecolhido(false);
    expect(menuRecolhidoInicial()).toBe(false);
  });

  it('armazenamento que falha não derruba: cai no padrão da largura', () => {
    largura(1440);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('bloqueado'); });
    expect(menuRecolhidoInicial()).toBe(false);
    largura(800);
    expect(menuRecolhidoInicial()).toBe(true);
  });
});
