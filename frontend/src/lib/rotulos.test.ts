import { describe, expect, it } from 'vitest';
import { evidenciaLegivel, rotuloConhecidoDoComando, rotuloDoComando } from './rotulos';

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
