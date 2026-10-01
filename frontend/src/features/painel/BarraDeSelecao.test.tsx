// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { APPS, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { BarraDeSelecao, type BarraDeSelecaoProps } from './BarraDeSelecao';

/**
 * Tarefa 03 (revisão de UX): a barra de seleção é uma linha presa ao topo da grade. À vista ficam as ações de
 * rotina; hibernar, instalar, abrir app e resetar vão para "Mais ações", e o reset só numa "Zona de perigo" e só
 * depois da confirmação que já existia. Prova `simulated` (backend falso, nenhum aparelho real).
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.on('POST', /bulk$/, () => json({ accepted: ['android-01', 'android-02'], rejected: [], commands: [] }, 202));
  backend.install();
  const snap = makeSnapshot();
  useAppStore.setState({ ...initialDataState, settings: snap.settings, health: snap.health });
  useAppStore.setState({ apps: [{ ...APPS[0]!, promoted_version_name: '1.4', promoted_version_code: 14 }, APPS[1]!] });
  useUiStore.setState({ selectedIds: ['android-01', 'android-02'] });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const dois = [
  { id: 'android-01', supported_verbs: undefined },
  { id: 'android-02', supported_verbs: undefined },
];

async function renderBarra(over: Partial<BarraDeSelecaoProps> = {}): Promise<HTMLElement> {
  await act(async () => {
    root.render(
      <>
        <BarraDeSelecao ids={['android-01', 'android-02']} selecionados={2} selected={dois}
                        hasAbsent={false} hasHibernated={false} hibernation emFoco={false} {...over} />
        <ConfirmHost />
      </>,
    );
  });
  return byRole('toolbar', /Ação em 2 aparelhos/);
}

describe('BarraDeSelecao', () => {
  it('à vista: o contador e as ações de rotina; o resto mora em "Mais ações"', async () => {
    const barra = await renderBarra();
    expect(text(barra)).toContain('2 selecionados');
    for (const nome of ['Iniciar', 'Parar', 'Reiniciar']) expect(allByRole('button', nome, barra)).toHaveLength(1);
    for (const nome of [/^Hibernar/, /^Instalar app/, /^Abrir app/, /^Resetar dados/]) expect(allByRole('button', nome, barra)).toHaveLength(0);

    await click(byRole('button', /^Mais ações/, barra));
    const menu = await waitFor(() => byRole('dialog', 'Mais ações'));
    for (const nome of [/^Hibernar/, /^Instalar app/, /^Abrir app/]) expect(allByRole('button', nome, menu)).toHaveLength(1);
    // Zona de perigo: o reset fica sozinho, sem dividir a lista com as ações de rotina
    const zona = byRole('group', 'Zona de perigo', menu);
    expect(allByRole('button', /^Resetar dados/, zona)).toHaveLength(1);
    expect(allByRole('button', /^Resetar dados/, menu)).toHaveLength(1);
  });

  it('resetar dados pergunta antes, com a lista dos aparelhos; cancelar não envia nada', async () => {
    const barra = await renderBarra();
    await click(byRole('button', /^Mais ações/, barra));
    const menu = await waitFor(() => byRole('dialog', 'Mais ações'));
    await click(byRole('button', /^Resetar dados/, menu));
    const pergunta = await waitFor(() => byRole('dialog', /Resetar dados de 2 instâncias/));
    expect(text(pergunta)).toContain('android-01, android-02');
    await click(byRole('button', 'Cancelar', pergunta));
    await waitFor(() => expect(allByRole('dialog', /Resetar dados de 2 instâncias/)).toHaveLength(0));
    expect(backend.callsTo('POST', /bulk$/)).toHaveLength(0);
  });

  it('instalar app: a escolha do app troca o conteúdo do MESMO menu e leva o app_id ao lote', async () => {
    const barra = await renderBarra();
    await click(byRole('button', /^Mais ações/, barra));
    const menu = await waitFor(() => byRole('dialog', 'Mais ações'));
    await click(byRole('button', /^Instalar app/, menu));
    expect(text(menu)).toContain('Instalar qual aplicativo?');
    // sem versão promovida o item fica desabilitado, com o motivo no title
    expect((byRole('button', /Notas/, menu) as HTMLButtonElement).disabled).toBe(true);
    await click(byRole('button', /QA Messenger 1.4/, menu));
    await waitFor(() => expect(backend.callsTo('POST', /bulk$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /bulk$/)[0]?.body).toMatchObject({
      ids: ['android-01', 'android-02'], action: 'install_apk', params: { app_id: 'qa' },
    });
    await waitFor(() => expect(allByRole('dialog', 'Mais ações')).toHaveLength(0));
  });

  it('abrir app: lista os aplicativos e "Voltar" retorna ao menu', async () => {
    const barra = await renderBarra();
    await click(byRole('button', /^Mais ações/, barra));
    const menu = await waitFor(() => byRole('dialog', 'Mais ações'));
    await click(byRole('button', /^Abrir app/, menu));
    expect(text(menu)).toContain('Abrir qual aplicativo?');
    await click(byRole('button', /Voltar/, menu));
    expect(allByRole('button', /^Hibernar/, menu)).toHaveLength(1);
  });

  it('o verbo que algum aparelho não aceita aparece desabilitado com quem o impede, na barra e no menu', async () => {
    const barra = await renderBarra({
      selected: [dois[0]!, { id: 'android-02', supported_verbs: ['start', 'stop'] }],
    });
    const reiniciar = byRole('button', /^Reiniciar/, barra);
    expect(reiniciar.getAttribute('aria-disabled')).toBe('true');
    expect(text(reiniciar)).toContain('android-02 não aceita “Reiniciar”');
    await click(byRole('button', /^Mais ações/, barra));
    const menu = await waitFor(() => byRole('dialog', 'Mais ações'));
    const hibernar = byRole('button', /^Hibernar/, menu) as HTMLButtonElement;
    expect(hibernar.disabled).toBe(true);
    expect(text(menu)).toContain('android-02 não aceita “Hibernar”');
  });

  it('com o Foco aberto mantém o lugar e o contador, mas nenhuma ação (ela já está no drawer)', async () => {
    await renderBarra();
    await act(async () => root.render(
      <BarraDeSelecao ids={['android-01', 'android-02']} selecionados={2} selected={dois}
                      hasAbsent={false} hasHibernated={false} hibernation emFoco />));
    const barra = container.querySelector('[data-barra-de-selecao]') as HTMLElement;
    expect(text(barra)).toContain('2 selecionados');
    expect(allByRole('toolbar', /Ação em/)).toHaveLength(0);
    expect(barra.querySelectorAll('button')).toHaveLength(0);
  });

  it('uma ação só por vez: com um lote em andamento, os outros botões ficam desabilitados', async () => {
    const barra = await renderBarra();
    const { useBusyStore } = await import('../devices/actions');
    await act(async () => useBusyStore.setState({ bulkBusy: 'start' }));
    expect(byRole('button', /^Parar/, barra).hasAttribute('disabled')).toBe(true);
    expect(byRole('button', /^Mais ações/, barra).hasAttribute('disabled')).toBe(true);
    await act(async () => useBusyStore.setState({ bulkBusy: null }));
  });
});
