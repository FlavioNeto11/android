// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { click, setValue } from './harness';

// 29.130: o harness recusa o gesto que a tela não permite, nos dois sentidos (clicar e digitar), e diz em quê.

afterEach(() => { document.body.innerHTML = ''; });

it('click recusa controle desabilitado, também dentro de fieldset desabilitado; aria-disabled segue clicável', async () => {
  document.body.innerHTML = `
    <button id="nativo" disabled>Salvar</button>
    <fieldset disabled><input type="checkbox" aria-label="Sob demanda"></fieldset>
    <button aria-disabled="true">Explica o motivo</button>`;
  await expect(click(document.getElementById('nativo')!)).rejects.toThrow('click: nativo está desabilitado');
  await expect(click(document.querySelector('fieldset input')!)).rejects.toThrow('click: Sob demanda está desabilitado');
  let cliques = 0;
  const explica = document.querySelector('[aria-disabled]')!;
  explica.addEventListener('click', () => { cliques += 1; });
  await click(explica);
  expect(cliques).toBe(1);
});

it('o clique no ícone de um botão desabilitado também é recusado; um link dentro de fieldset desabilitado, não', async () => {
  document.body.innerHTML = `
    <button aria-label="Criar" disabled><svg></svg></button>
    <fieldset disabled><a href="#ajuda">ajuda</a></fieldset>`;
  await expect(click(document.querySelector('svg')!)).rejects.toThrow('click: Criar está desabilitado');
  let cliques = 0;
  const link = document.querySelector('a')!;
  link.addEventListener('click', (e) => { e.preventDefault(); cliques += 1; });
  await click(link);
  expect(cliques).toBe(1);
});

it('sem aria-label nem id, a mensagem nomeia a tag (id vazio não vale como nome)', async () => {
  document.body.innerHTML = '<input disabled><button disabled>x</button>';
  await expect(setValue(document.querySelector('input')!, 'a')).rejects.toThrow('o campo INPUT está desabilitado');
  await expect(click(document.querySelector('button')!)).rejects.toThrow('click: BUTTON está desabilitado');
});
