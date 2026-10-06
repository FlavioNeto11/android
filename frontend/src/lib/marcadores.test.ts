import { expect, it } from 'vitest';
import { temMarcadorDaPersona, textoComMarcadores } from './marcadores';

// 31.134: o marcador do dado da persona em palavras; o que o painel não conhece fica como veio. Prova `simulated`.
it('troca cada marcador conhecido por palavras e mantém o resto', () => {
  expect(textoComMarcadores('{perfil_nome}')).toBe('[nome da persona]');
  expect(textoComMarcadores('O campo mostra {perfil_sobrenome} e {perfil_nome_exibicao}')).toBe('O campo mostra [sobrenome da persona] e [nome de exibição da persona]');
  expect(textoComMarcadores('{perfil_cidade}')).toBe('{perfil_cidade}');           // desconhecido: como veio
  expect(textoComMarcadores('o item {item}')).toBe('o item {item}');               // parâmetro do comando, não da persona
  expect(textoComMarcadores('sem marcador')).toBe('sem marcador');
});

it('diz se há marcador que o painel sabe nomear', () => {
  expect(temMarcadorDaPersona('oi {perfil_email}')).toBe(true);
  expect(temMarcadorDaPersona('{perfil_cidade}')).toBe(false);
  expect(temMarcadorDaPersona('{item}')).toBe(false);
});
