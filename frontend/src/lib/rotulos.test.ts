import { describe, expect, it } from 'vitest';
import {
  duracaoHumana, evidenciaLegivel, rotuloConhecidoDoComando, rotuloDoComando, tempoRelativo,
} from './rotulos';

const AGORA = Date.parse('2026-09-30T20:00:00Z');
const ha = (ms: number) => new Date(AGORA - ms).toISOString();
const MIN = 60_000;
const H = 60 * MIN;
const D = 24 * H;

describe('rotuloDoComando — o identificador técnico vira português', () => {
  it.each([
    ['app.distribute', 'Distribuição de app'],
    ['device.network', 'Rede do aparelho'],
    ['app.canary', 'Teste de versão do app'],
    ['session.verify', 'Verificação da sessão'],
    ['store.sync', 'Sincronização da loja'],
    ['app.verify', 'Verificação de app'],
    ['restart', 'Reinício do aparelho'],
  ])('%s → %s', (verbo, esperado) => {
    expect(rotuloDoComando(verbo)).toBe(esperado);
    expect(rotuloConhecidoDoComando(verbo)).toBe(esperado);
  });

  it('verbo novo nunca aparece cru: ganha um nome legível', () => {
    expect(rotuloDoComando('app.limpar_cache')).toBe('Limpar cache (app)');
    expect(rotuloDoComando('device.proxy_novo')).toBe('Proxy novo (aparelho)');
    expect(rotuloDoComando('xyz')).toBe('Xyz');
    expect(rotuloConhecidoDoComando('app.limpar_cache')).toBeUndefined();
  });

  it('nenhum rótulo conhecido contém ponto ou sublinhado', () => {
    for (const v of ['app.install', 'app.rollback', 'session.needs_person', 'device.locked_account', 'install_apk', 'open_app']) {
      expect(rotuloDoComando(v)).not.toMatch(/[._]/);
    }
  });
});

describe('tempoRelativo — ordem de grandeza, sem precisão de máquina', () => {
  it('161 h vira "há 6 dias"', () => {
    expect(tempoRelativo(ha(161 * H), AGORA)).toBe('há 6 dias');
  });
  it('1 min 14 s vira "há 1 min"', () => {
    expect(tempoRelativo(ha(74_000), AGORA)).toBe('há 1 min');
  });
  it('escalas', () => {
    expect(tempoRelativo(ha(1000), AGORA)).toBe('agora');
    expect(tempoRelativo(ha(12_000), AGORA)).toBe('há 12 s');
    expect(tempoRelativo(ha(59 * MIN + 59_000), AGORA)).toBe('há 59 min');
    expect(tempoRelativo(ha(3 * H + 40 * MIN), AGORA)).toBe('há 3 h');
    expect(tempoRelativo(ha(24 * H), AGORA)).toBe('há 1 dia');
    expect(tempoRelativo(ha(45 * D), AGORA)).toBe('há 1 mês');
    expect(tempoRelativo(ha(75 * D), AGORA)).toBe('há 2 meses');
    expect(tempoRelativo(ha(800 * D), AGORA)).toBe('há 2 anos');
  });
  it('relógio adiantado nunca vira tempo negativo, e dado ruim vira travessão', () => {
    expect(tempoRelativo(new Date(AGORA + 60_000).toISOString(), AGORA)).toBe('agora');
    expect(tempoRelativo(null, AGORA)).toBe('—');
    expect(tempoRelativo('lixo', AGORA)).toBe('—');
    expect(duracaoHumana(-1)).toBe('—');
  });
});

describe('evidenciaLegivel', () => {
  it('seletor cru vira frase', () => {
    expect(evidenciaLegivel('seletor id=com.pocqa.messenger:id/account_label|text=qa-user-10: 1 elemento(s)'))
      .toBe('Confirmado na tela: “qa-user-10”');
  });
  it('seletor fora do padrão ainda não vaza o identificador', () => {
    expect(evidenciaLegivel('seletor xpath=//a')).toBe('Confirmado na tela pelo identificador do elemento');
  });
  it('o nível observado sai em português; o resto do texto fica', () => {
    expect(evidenciaLegivel('Mensagem "Boa tarde" com status "Entregue ✓✓" [nível observado: delivered]'))
      .toBe('Mensagem "Boa tarde" com status "Entregue ✓✓" [nível observado: entregue]');
    expect(evidenciaLegivel('texto "Conta: qa-user-03" visível na tela')).toBe('texto "Conta: qa-user-03" visível na tela');
  });
});
