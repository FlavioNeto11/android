import { describe, expect, it } from 'vitest';
import { historicoSeguro, pareceCredencial, pushHistory } from './history';

describe('pushHistory — histórico dos últimos comandos', () => {
  it('põe o comando no topo', () => {
    expect(pushHistory([], 'abrir o app')).toEqual(['abrir o app']);
    expect(pushHistory(['abrir o app'], 'enviar mensagem')).toEqual(['enviar mensagem', 'abrir o app']);
  });

  it('comando repetido move para o topo em vez de duplicar', () => {
    const list = ['c', 'b', 'a'];
    expect(pushHistory(list, 'b')).toEqual(['b', 'c', 'a']);
  });

  it('apara nos espaços antes de comparar e guardar', () => {
    expect(pushHistory(['abrir o app'], '  abrir o app  ')).toEqual(['abrir o app']);
  });

  it('comando vazio (ou só espaço) não entra', () => {
    expect(pushHistory(['a'], '')).toEqual(['a']);
    expect(pushHistory(['a'], '   ')).toEqual(['a']);
  });

  it('corta no tamanho máximo, mantendo os mais recentes', () => {
    const list = ['e', 'd', 'c', 'b', 'a'];
    expect(pushHistory(list, 'f', 3)).toEqual(['f', 'e', 'd']);
  });

  it('não muta a lista recebida', () => {
    const list = ['a'];
    pushHistory(list, 'b');
    expect(list).toEqual(['a']);
  });
});

describe('credencial no comando (ADR-025) — nunca fica no navegador', () => {
  it('reconhece senha no texto e usuário:senha@ em URL', () => {
    expect(pareceCredencial('entre no portal\nSenha: Tst-abc123')).toBe(true);
    expect(pareceCredencial('password=abc')).toBe(true);
    expect(pareceCredencial('abra https://usuario:Tst-abc@portal.exemplo.test/')).toBe(true);
  });

  it('não confunde comando comum com credencial', () => {
    expect(pareceCredencial('abra o Chrome e entre no site https://portal.exemplo.test/#/')).toBe(false);
    expect(pareceCredencial('troque a senha do perfil depois')).toBe(false);
    expect(pareceCredencial('envie "oi" para @fulano')).toBe(false);
  });

  it('comando com senha não entra no histórico, e o histórico antigo é limpo', () => {
    expect(pushHistory(['a'], 'login com senha: Tst-abc123')).toEqual(['a']);
    expect(historicoSeguro(['abrir o app', 'Senha: Tst-abc123', 'b'])).toEqual(['abrir o app', 'b']);
  });
});
