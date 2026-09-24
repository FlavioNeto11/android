import { describe, expect, it } from 'vitest';
import { pushHistory } from './history';

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
