import { describe, expect, it } from 'vitest';
import { textoDosApps } from './appsDoFluxo';

const APPS = [{ id: 'qa-messenger', name: 'QA Messenger' }, { id: 'chrome', name: 'Chrome' }];

describe('textoDosApps', () => {
  it('une os nomes na ordem recebida, sem reordenar', () => {
    expect(textoDosApps(['qa-messenger', 'chrome'], APPS)).toBe('QA Messenger → Chrome');
    expect(textoDosApps(['chrome', 'qa-messenger'], APPS)).toBe('Chrome → QA Messenger');
  });

  it('com um app, só o nome; sem app, nada', () => {
    expect(textoDosApps(['chrome'], APPS)).toBe('Chrome');
    expect(textoDosApps([], APPS)).toBe('');
    expect(textoDosApps(undefined, APPS)).toBe('');
    expect(textoDosApps(null, APPS)).toBe('');
  });

  it('app fora da lista (ou nome vazio) cai para o id', () => {
    expect(textoDosApps(['qa-messenger', 'apagado'], APPS)).toBe('QA Messenger → apagado');
    expect(textoDosApps(['chrome'], [])).toBe('chrome');
    expect(textoDosApps(['chrome'], [{ id: 'chrome', name: '  ' }])).toBe('chrome');
  });

  it('aceita o mapa id → nome que as telas já montam', () => {
    expect(textoDosApps(['qa-messenger', 'chrome'], new Map([['qa-messenger', 'QA Messenger']]))).toBe('QA Messenger → chrome');
  });
});
