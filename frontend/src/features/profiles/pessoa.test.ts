import { expect, it } from 'vitest';
import { ehEndereco, rotuloDoIdentificador } from './pessoa';

it('identificador de e-mail aparece como endereço, sem o arroba de nome de usuário na frente (29.15)', () => {
  expect(ehEndereco('fulano@outlook.com')).toBe(true);
  expect(ehEndereco('@fulano')).toBe(false);          // o arroba inicial é nome de usuário, não endereço
  expect(ehEndereco('fulano')).toBe(false);
  expect(ehEndereco(null)).toBe(false);
  expect(rotuloDoIdentificador('fulano@outlook.com')).toBe('fulano@outlook.com');
  expect(rotuloDoIdentificador('fulano')).toBe('@fulano');
  expect(rotuloDoIdentificador('@fulano')).toBe('@fulano');                  // um arroba só
});
