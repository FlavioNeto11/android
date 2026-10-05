// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { installBrowserStubs } from '../../test/harness';
import { SeloRotuloIa } from './SeloRotuloIa';

/**
 * 29.81: o item que pede o sim do dono distingue os três estados do rótulo de IA. "Foto real" foi o dono quem disse
 * (neutro, nada a fazer); "não informado" ninguém disse (aviso, com o caminho para a guia Imagens da persona).
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
const link = () => container.querySelector('a');

it('com rótulo, sem rótulo por foto real e sem rótulo porque ninguém disse', async () => {
  await render(<SeloRotuloIa rotulo motivo="ia" profileId="p-1" longo />);
  expect(container.textContent).toBe('Sai com o rótulo de IA do Instagram');
  expect(link()).toBeNull();

  await render(<SeloRotuloIa rotulo={false} motivo="foto_real" profileId="p-1" />);
  expect(container.textContent).toBe('sem rótulo de IA (foto real, informado por você)');
  expect(link()).toBeNull();

  await render(<SeloRotuloIa rotulo={false} motivo="nao_informado" profileId="p-1" />);
  expect(container.textContent).toContain('sem rótulo de IA: ninguém informou se a foto é de IA');
  expect(link()?.getAttribute('href')).toBe('#/personas/p-1/imagens');
});

it('sem o porquê (etapa de antes do 29.81) o "sem rótulo" fica em aviso; sem imagem, nada', async () => {
  await render(<SeloRotuloIa rotulo={false} profileId="p-1" />);
  expect(container.textContent).toContain('ninguém informou');
  await render(<SeloRotuloIa rotulo={null} motivo={null} profileId="p-1" />);
  expect(container.textContent).toBe('');
});
