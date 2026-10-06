// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { profileAvatarUrl } from '../api/client';
import { installBrowserStubs } from '../test/harness';
import { Avatar } from './Avatar';

/**
 * 29.26: persona sem foto gerava `GET /instagram/profiles/{id}/avatar -> 404` por cartão (cinco erros no console).
 * Agora o endereço só existe com `has_avatar`; sem ele o `Avatar` mostra as iniciais e não há `<img>` para pedir nada.
 */
let root: Root;
let container: HTMLElement;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const render = (node: React.ReactElement) => act(async () => { root.render(node); });

it('profileAvatarUrl: sem foto (ou campo ausente) não há endereço; com foto, a rota do avatar', () => {
  expect(profileAvatarUrl('ig-1', false)).toBeUndefined();
  expect(profileAvatarUrl('ig-1', undefined)).toBeUndefined();
  expect(profileAvatarUrl('ig-1', true)).toMatch(/\/instagram\/profiles\/ig-1\/avatar$/);
});

it('sem foto: iniciais no círculo e nenhuma <img> (logo, nenhuma requisição)', async () => {
  await render(<Avatar src={profileAvatarUrl('ig-1', false)} name="Luciana Bastos" />);
  expect(container.querySelector('img')).toBeNull();
  expect(container.textContent).toBe('LB');
});

it('com foto: a imagem continua, com o texto alternativo', async () => {
  await render(<Avatar src={profileAvatarUrl('ig-1', true)} name="Luciana Bastos" />);
  const img = container.querySelector('img');
  expect(img?.getAttribute('src')).toMatch(/\/instagram\/profiles\/ig-1\/avatar$/);
  expect(img?.getAttribute('alt')).toBe('Foto de Luciana Bastos');
});
