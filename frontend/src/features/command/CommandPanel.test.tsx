// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeRun, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { CommandPanel, SENHA_NO_COMANDO } from './CommandPanel';

/**
 * ADR-040 (evolução 2, onda E1): a execução NÃO carrega credencial. O campo "Senha para a automação" saiu, o corpo de
 * `POST /runs` não leva `credentials` nem `consent_credentials` (o backend responde 422 a quem ainda os mande), e a
 * senha escrita no texto continua barrada — agora apontando para a conta da persona. Prova `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(async () => {
  backend = new FakeBackend();
  backend
    .on('GET', /^\/api\/flows\/match$/, () => json(null))
    .on('POST', /^\/api\/runs$/, () => json(makeRun()));
  backend.install();
  window.localStorage.clear();
  useAppStore.setState({ ...initialDataState });
  useAppStore.getState().hydrate(makeSnapshot());
  useUiStore.setState({ selectedIds: ['android-01'] });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<CommandPanel />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('Comando sem credencial (ADR-040)', () => {
  it('não há campo de senha no comando', () => {
    expect(text(container)).not.toContain('Senha para a automação');
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });

  it('o corpo de POST /runs não leva credentials nem consent_credentials', async () => {
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'Abra o app e envie a mensagem');
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /^\/api\/runs$/)[0]!.body as Record<string, unknown>;
    expect(corpo).toMatchObject({ command: 'Abra o app e envie a mensagem', instance_ids: ['android-01'], mode: 'execute' });
    expect(corpo).not.toHaveProperty('credentials');
    expect(corpo).not.toHaveProperty('consent_credentials');
  });

  it('senha no texto continua barrada, e o motivo manda guardá-la na conta da persona', async () => {
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'Entre no portal com senha: hunter2');
    expect(text(container)).toContain(SENHA_NO_COMANDO);
    expect(SENHA_NO_COMANDO).toContain('Contas e acesso');
    await click(byRole('button', /^Executar/));
    expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(0);
    // Nem o rascunho com a senha vai ao navegador: depois do atraso do rascunho (400 ms), o gravado é vazio.
    await flush(500);
    expect(window.localStorage.getItem('cda.commandDraft')).toBe('""');
  });
});
