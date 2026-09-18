import { expect, it } from 'vitest';
import { makeInstance } from '../test/fixtures';
import { selectInstanceList, selectStoreInstance, selectTaskInstances, selectTaskOrder } from './app';

/**
 * Quem escolhe ALVO lê de `selectTask*`; quem só LISTA aparelhos usa `selectInstanceList`. Com a loja como 11º
 * aparelho, "selecionar todas" pela lista inteira mandaria 11 alvos e o backend responderia 422 (teto de 10) —
 * e a loja, de todo modo, nunca executa tarefa.
 */
function estado() {
  const lista = [makeInstance(1), makeInstance(2), makeInstance(11, { kind: 'store' })];
  return { instances: Object.fromEntries(lista.map((i) => [i.id, i])), instanceOrder: lista.map((i) => i.id) };
}

it('a lista completa inclui a loja, para ela aparecer na grade', () => {
  expect(selectInstanceList(estado()).map((i) => i.id)).toEqual(['android-01', 'android-02', 'android-11']);
});

it('os aparelhos de tarefa são todos menos a loja, na mesma ordem', () => {
  expect(selectTaskOrder(estado())).toEqual(['android-01', 'android-02']);
  expect(selectTaskInstances(estado()).every((i) => i.kind !== 'store')).toBe(true);
});

it('a loja é encontrada pelo papel, não pelo nome', () => {
  expect(selectStoreInstance(estado())?.id).toBe('android-11');
});

it('sem loja configurada nada muda', () => {
  const lista = [makeInstance(1), makeInstance(2)];
  const s = { instances: Object.fromEntries(lista.map((i) => [i.id, i])), instanceOrder: lista.map((i) => i.id) };
  expect(selectTaskOrder(s)).toEqual(['android-01', 'android-02']);
  expect(selectStoreInstance(s)).toBeNull();
});
