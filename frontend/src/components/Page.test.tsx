// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { byRole, click, installBrowserStubs, text } from '../test/harness';
import { AutoGrid, Page, PageSection, TableWrap } from './Page';
import { Popover } from './Popover';

/**
 * Contrato de página (evolução 2, design §9.1). O jsdom não calcula layout, então o que se prova aqui é a
 * ESTRUTURA (cabeçalho, seção com rodapé, região rolável nomeada) e o CSS escrito (a página é contêiner, não há
 * variante estreita, a grade padrão desce a uma coluna). A largura de verdade se prova no navegador.
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

const css = (rel: string) => readFileSync(resolve(__dirname, rel), 'utf8');

describe('Page', () => {
  it('abre com h1 e lead, e as seções são cartões com título e rodapé preso ao cartão', async () => {
    await act(async () => root.render(
      <Page title="Configuração" lead="O que a IA opera." actions={<button type="button">Ação</button>}>
        <PageSection title="Limites" subtitle="por servidor" footer={<button type="button">Salvar limites</button>}>
          <AutoGrid min="420px"><div>um</div></AutoGrid>
        </PageSection>
      </Page>,
    ));
    expect(container.querySelector('h1')?.textContent).toBe('Configuração');
    expect(container.querySelector('section h2')?.textContent).toBe('Limites');
    expect(text(container)).toContain('O que a IA opera.');
    // O rodapé é filho do cartão (não flutua sobre a página com margem negativa, como a barra antiga de Limites).
    const salvar = byRole('button', 'Salvar limites');
    expect(salvar.closest('section')?.textContent).toContain('Limites');
    const grade = container.querySelector<HTMLElement>('[style*="--col-min"]');
    expect(grade?.style.getPropertyValue('--col-min')).toBe('420px');
  });

  it('TableWrap é uma região nomeada e focável (rola pelo teclado)', async () => {
    await act(async () => root.render(
      <TableWrap label="Modelos por função"><table><thead><tr><th>Função</th></tr></thead></table></TableWrap>,
    ));
    const r = byRole('region', 'Modelos por função');
    expect(r.tabIndex).toBe(0);
    expect(r.querySelector('th')).toBeTruthy();
  });
});

describe('CSS do contrato', () => {
  it('a página é o contêiner das faixas e não existe mais variante estreita', () => {
    const app = css('../App.module.css');
    expect(app).toMatch(/\.page \{[^}]*container-type: inline-size;[^}]*container-name: page;/);
    expect(app).not.toMatch(/\.pageNarrow/);
  });

  it('as faixas estão escritas em tokens.css, e a grade padrão nunca é mais larga que o contêiner', () => {
    expect(css('../styles/tokens.css')).toMatch(/compacto\s+< 560 px[\s\S]*médio\s+560–959 px[\s\S]*largo\s+960–1439 px[\s\S]*ultra\s+≥ 1440 px/);
    const page = css('./Page.module.css');
    expect(page).toMatch(/\.autoGrid \{[^}]*repeat\(auto-fill, minmax\(min\(100%, var\(--col-min/);
    // `sticky` precisa de um rolador vertical: sem altura máxima, `overflow: auto` prende o th numa caixa que não rola.
    expect(page).toMatch(/\.tableWrap \{[^}]*max-height:[^}]*overflow: auto;/);
    expect(page).toMatch(/\.tableWrap th \{[^}]*position: sticky;/);
  });
});

describe('Popover dentro de um contêiner', () => {
  it('o painel vai para o body (portal) e clicar DENTRO dele não o fecha', async () => {
    await act(async () => root.render(
      <Page><Popover label="Instalar app" trigger="Instalar app"><button type="button">Versão 447</button></Popover></Page>,
    ));
    await click(byRole('button', 'Instalar app'));
    const painel = byRole('dialog', 'Instalar app');
    // Fora da página: um `position: fixed` dentro de um contêiner seria posicionado e recortado por ele.
    expect(container.contains(painel)).toBe(false);
    expect(painel.parentElement).toBe(document.body);
    const item = byRole('button', 'Versão 447');
    await act(async () => { item.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); });
    expect(byRole('dialog', 'Instalar app')).toBe(painel);
    await act(async () => { document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
});
