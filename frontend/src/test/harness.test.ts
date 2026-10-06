// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { atrasoMaximoDoFetchMs, click, setValue, waitFor } from './harness';

vi.hoisted(() => { vi.stubEnv('ATRASO_DO_FETCH_MS', '75'); });

// 29.130: o harness recusa o gesto que a tela não permite, nos dois sentidos (clicar e digitar), e diz em quê.

afterEach(() => { document.body.innerHTML = ''; vi.unstubAllEnvs(); });

it('o atraso máximo devolve o teto lido pelo harness, mesmo se o ambiente mudar depois', () => {
  expect(atrasoMaximoDoFetchMs()).toBe(75);
  vi.stubEnv('ATRASO_DO_FETCH_MS', '150');
  expect(atrasoMaximoDoFetchMs()).toBe(75);
});

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

// 29.148: o prazo do `waitFor` não conta o tempo em que o processo ficou parado (host carregado), mas continua
// estourando para a condição que nunca vale.
function pararOProcesso(ms: number): void {
  const fim = Date.now() + ms;
  while (Date.now() < fim) { /* ocupa a thread: o timer do flush chega atrasado, como num worker sem CPU */ }
}

it('29.148: um processo parado por mais que o prazo não estoura a espera; a condição vale depois da parada', async () => {
  let pronto = false;
  setTimeout(() => pararOProcesso(1200), 5);          // a parada cai dentro do `flush` da espera
  setTimeout(() => { pronto = true; }, 1300);
  await expect(waitFor(() => pronto, 500)).resolves.toBe(true);
});

it('29.148: a condição que nunca vale continua estourando no prazo, sem crédito de espera em dia', async () => {
  const antes = Date.now();
  await expect(waitFor(() => false, 300)).rejects.toThrow('a condição continuou falsa');
  expect(Date.now() - antes).toBeLessThan(1500);
});
