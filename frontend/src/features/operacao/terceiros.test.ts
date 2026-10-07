import { expect, it } from 'vitest';
import { mascararTerceiros, usuariosConhecidosDaOperacao } from './terceiros';

const MOTIVO = 'Etapa \'Comentar na publicação\' falhou: A tela mostra o feed "Posts" com o post de astro_jessica em foco (o de nasawebb …';

it('o motivo de falha perde o dono do post (usuário com sublinhado) e o perfil alvo conhecido, e o resto do texto fica (31.254)', () => {
  const r = mascararTerceiros(MOTIVO, ['nasawebb']);
  expect(r).toBe('Etapa \'Comentar na publicação\' falhou: A tela mostra o feed "Posts" com o post de [usuário omitido] em foco (o de [usuário omitido] …');
  expect(r).not.toMatch(/astro_jessica|nasawebb/);
});

it('sem o perfil alvo conhecido, o usuário simples depois de "o de" fica (limite conhecido), mas o com sublinhado, ponto ou dígito sai', () => {
  expect(mascararTerceiros('o post de baixo e o de nasawebb')).toBe('o post de baixo e o de nasawebb');
  expect(mascararTerceiros('o perfil de ana.souza e a conta de fulano99')).toBe('o perfil de [usuário omitido] e a conta de [usuário omitido]');
});

it('o @, o "x said" e o conhecido (com ou sem arroba, qualquer caixa) saem; palavra comum e nome parecido ficam', () => {
  expect(mascararTerceiros('a frota já mexeu com @loja.exemplo')).toBe('a frota já mexeu com @[omitido]');
  expect(mascararTerceiros('space.girl.ma said Lindo!')).toBe('[usuário omitido] said Lindo!');
  expect(mascararTerceiros('NASA publicou; nasa2 não é o alvo; sem conta', ['@nasa'])).toBe('[usuário omitido] publicou; nasa2 não é o alvo; sem conta');
  expect(mascararTerceiros('sem conta')).toBe('sem conta');
  expect(mascararTerceiros('x', ['ab'])).toBe('x');                       // conhecido curto demais não vale (evita apagar letras soltas)
});

it('os conhecidos da operação são o perfil alvo, sem arroba; sem ele, nenhum', () => {
  expect(usuariosConhecidosDaOperacao({ username: '@nasawebb' })).toEqual(['nasawebb']);
  expect(usuariosConhecidosDaOperacao({ caption_contains: 'x' })).toEqual([]);
  expect(usuariosConhecidosDaOperacao(null)).toEqual([]);
});
