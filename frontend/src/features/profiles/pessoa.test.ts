import { expect, it } from 'vitest';
import type { PersonaOnDevice } from '../../api/types';
import { compartilhadoCom, compartilhadoEmPalavras, ehEndereco, numerosDasPersonas, rotuloDoIdentificador } from './pessoa';

it('identificador de e-mail aparece como endereço, sem o arroba de nome de usuário na frente (29.15)', () => {
  expect(ehEndereco('fulano@outlook.com')).toBe(true);
  expect(ehEndereco('@fulano')).toBe(false);          // o arroba inicial é nome de usuário, não endereço
  expect(ehEndereco('fulano')).toBe(false);
  expect(ehEndereco(null)).toBe(false);
  expect(rotuloDoIdentificador('fulano@outlook.com')).toBe('fulano@outlook.com');
  expect(rotuloDoIdentificador('fulano')).toBe('@fulano');
  expect(rotuloDoIdentificador('@fulano')).toBe('@fulano');                  // um arroba só
});

it('o Nº da persona é a ordem de criação, com desempate pelo id, e não muda com a ordem em que a lista chega (31.245)', () => {
  const n = numerosDasPersonas([
    { id: 'ig-z', created_at: '2026-09-18T10:00:00Z' }, { id: 'ig-a', created_at: '2026-09-17T10:00:00Z' },
    { id: 'ig-m', created_at: '2026-09-18T10:00:00Z' }, { id: 'ig-x', created_at: '' },
  ]);
  expect([n.get('ig-x'), n.get('ig-a'), n.get('ig-m'), n.get('ig-z')]).toEqual([1, 2, 3, 4]);   // sem data vem primeiro; empate pelo id
  expect(numerosDasPersonas([]).size).toBe(0);
});

it('aparelho dividido: os Nº das outras personas, só informativo; sozinha, nada (31.245)', () => {
  const noAparelho = (id: string): PersonaOnDevice => ({ profile_id: id, username: null, display_name: null, name: id, status: 'active', app_id: null, is_primary: true, bound_at: null } as PersonaOnDevice);
  const mapa = new Map([['android-04', [noAparelho('b'), noAparelho('c'), noAparelho('d')]], ['android-01', [noAparelho('a')]]]);
  const numeros = new Map([['a', 1], ['b', 2], ['c', 3], ['d', 4]]);
  const com = (id: string, aparelho: string) => compartilhadoCom({ id, instance_id: aparelho, devices: undefined } as unknown as Parameters<typeof compartilhadoCom>[0], mapa, numeros);
  expect(com('b', 'android-04')).toEqual([3, 4]);
  expect(com('a', 'android-01')).toEqual([]);
  expect(compartilhadoEmPalavras([])).toBe('');
  expect(compartilhadoEmPalavras([3])).toBe('compartilhado com Nº 3');
  expect(compartilhadoEmPalavras([3, 4])).toBe('compartilhado com Nº 3 e Nº 4');
  expect(compartilhadoEmPalavras([2, 3, 4])).toBe('compartilhado com Nº 2, Nº 3 e Nº 4');
});
