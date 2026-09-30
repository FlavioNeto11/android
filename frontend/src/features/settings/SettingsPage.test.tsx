// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { AiStatus } from '../../api/types';
import { useAppStore } from '../../store/app';
import { useUiStore } from '../../store/ui';
import { initialDataState } from '../../store/reducer';
import { APPS, makeInstance, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { SettingsPage } from './SettingsPage';

// Evolução 2, design §9.2: Configuração deixou de ser um Card com as abas dentro (a única tela encaixotada) e passou
// ao contrato de página — abas NA página e cada seção num `PageSection`. Os rótulos das abas NÃO mudam: os testes
// de integração e quem já usa a tela os procuram pelo nome.

const IA: AiStatus = {
  provider: 'anthropic', model: 'claude-opus-5', configured: true, simulated: false, sends_data_externally: true,
  notice: '', effort: 'medium',
  roles: [{
    role: 'plan', provider: 'anthropic', kind: 'anthropic', model: 'claude-opus-5', endpoint: 'api.anthropic.com',
    sends_data_externally: true, configured: true, priced: true, vision: true, tools: true,
    refusal_fallback: false, fallback_provider: null, timeout_s: 120, concurrency: 4, effort: 'medium',
  }],
};

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  // A guia vem do link (`?aba=`): sem voltar para `#/configuracao`, um caso herdaria a guia do anterior.
  localStorage.clear();
  useUiStore.getState().navegar({ tela: 'configuracao' }, 'replace');
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/ai$/, () => json(IA));
  backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([]));
  backend.on('GET', /^\/api\/servers\/limits$/, () => json([]));
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    hydrateCount: 1,
    apps: APPS,
    settings: makeSnapshot().settings,
    instances: { 'android-01': makeInstance(1) },
    instanceOrder: ['android-01'],
  });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(): Promise<void> {
  await act(async () => { root.render(<SettingsPage />); });
}

/** Títulos dos cartões (`CardHeader`) visíveis agora. */
function titulosDeCartao(): string[] {
  return Array.from(container.querySelectorAll('header h2, header h3')).map((h) => (h.textContent ?? '').trim());
}

describe('Configuração no contrato de página', () => {
  it('as cinco abas continuam role=tab com os mesmos nomes, direto na página (sem Card em volta)', async () => {
    await render();
    const abas = allByRole('tab', /.+/, container).map((t) => (t.textContent ?? '').trim());
    expect(abas).toEqual(['Aplicativos', 'Instâncias e contas', 'IA', 'Fluxos e receitas', 'Limites']);
    const lista = byRole('tablist', 'Seções de configuração', container);
    // A lista de abas é filha da página, como em Aplicativos — não mais de um cartão (`section`) que envolvia tudo.
    expect(lista.closest('section')).toBeNull();
    expect(lista.parentElement?.querySelector('h1')?.textContent).toBe('Configuração');
    expect(allByRole('tabpanel', /.*/, container)).toHaveLength(1);
  });

  it('Aplicativos é um cartão que leva a "Gerenciar em Aplicativos"; o cadastro novo mora só lá', async () => {
    await render();
    expect(titulosDeCartao()).toContain('Aplicativos cadastrados');
    const cabecalho = container.querySelector('header') as HTMLElement;
    const link = Array.from(cabecalho.querySelectorAll('a')).find((a) => /Gerenciar em Aplicativos/.test(a.textContent ?? ''));
    expect(link?.getAttribute('href')).toBe('#/aplicativos');
    expect(allByRole('button', /Novo aplicativo|Cadastrar aplicativo/, container)).toHaveLength(0);
  });

  it('IA: "Por função" é cabeçalho de cartão e a tabela fica numa região rolável nomeada', async () => {
    await render();
    await click(byRole('tab', /^IA$/, container));
    await waitFor(() => expect(text(container)).toContain('api.anthropic.com'));
    expect(titulosDeCartao()).toEqual(expect.arrayContaining(['Situação', 'Onde fica a chave', 'Por função']));
    const regiao = byRole('region', 'Funções da IA', container);
    expect(regiao.getAttribute('tabindex')).toBe('0');
    expect(regiao.querySelector('table')).not.toBeNull();
    expect(text(container)).toContain('nunca no navegador');
  });

  it('Instâncias e contas: um cartão por servidor, com o nome da máquina no cabeçalho', async () => {
    await render();
    await click(byRole('tab', /^Instâncias e contas/, container));
    await waitFor(() => expect(text(container)).toContain('Aplicar app a todas'));
    expect(titulosDeCartao()).toEqual(expect.arrayContaining(['Aparelhos, apps e contas', 'Este servidor']));
  });

  it('Limites: "Por servidor" e "Parque" são cartões, e "Salvar limites" mora no rodapé do cartão do parque', async () => {
    await render();
    await click(byRole('tab', /^Limites/, container));
    await waitFor(() => expect(titulosDeCartao()).toEqual(expect.arrayContaining(['Por servidor', 'Parque — vale para todos os servidores'])));
    const salvar = byRole('button', /^Salvar limites/, container);
    const cartao = salvar.closest('section') as HTMLElement;
    expect(cartao.getAttribute('aria-labelledby')).toBe('limites-do-parque');
    // Cabeçalho, corpo e rodapé: o botão está no rodapé, preso ao cartão — não numa barra fixa sobre a página.
    const [, corpo, rodape] = Array.from(cartao.children);
    expect(cartao.children).toHaveLength(3);
    expect(rodape?.contains(salvar)).toBe(true);
    expect(corpo?.contains(salvar)).toBe(false);
  });

  it('a guia vai para o link e volta dele: #/configuracao?aba=fluxos abre Fluxos e receitas', async () => {
    await render();
    expect(byRole('tab', /^Aplicativos/, container).getAttribute('aria-selected')).toBe('true');
    backend.on('GET', /^\/api\/(flows|recipes)$/, () => json([]));
    backend.on('GET', /^\/api\/flows\/cobertura$/, () => json([]));
    await click(byRole('tab', /^Fluxos e receitas/, container));
    expect(useUiStore.getState().rota.query.aba).toBe('fluxos');
    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    expect(byRole('tab', /^Fluxos e receitas/, container).getAttribute('aria-selected')).toBe('true');
    // "Aplicativos" é a padrão: não aparece no link, e o link antigo `#/configuracao` continua abrindo nela.
    await click(byRole('tab', /^Aplicativos/, container));
    expect(useUiStore.getState().rota.query.aba).toBeUndefined();
  });
});
