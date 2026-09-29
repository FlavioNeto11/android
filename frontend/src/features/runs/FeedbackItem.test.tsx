// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import type { Voto } from '../aprendizado/model';
import { FeedbackItem } from './FeedbackItem';

/**
 * Pacote A6 do ADR-054 (D2): o botão "Deu certo / Deu errado" de cada item e da execução. Nunca modal, nunca pergunta
 * sozinho; o "Deu errado" abre o motivo e a nota em linha; o voto dado aparece marcado; depois do voto, uma linha diz
 * o que mudou e oferece desfazer. Prova `simulated` (backend falso).
 */

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  installBrowserStubs();
  backend = new FakeBackend();
  backend.install();
  // O formato de `presentation/feedback.py` (A4): cada efeito com `aplicado` e, quando reativável, a chamada de volta.
  backend.on('POST', /^\/api\/runs\/[^/]+\/feedback$/, () => json({
    signal: { id: 41, kind: 'feedback', verdict: 'errado' },
    resumo: 'fluxo aprendido desta execução desligado',
    efeitos: [
      { acao: 'desligar', kind: 'fluxo', ref: 'f-1', de: 'published', para: 'disabled', aplicado: true, erro: null,
        desfazer: { method: 'POST', href: '/api/aprendizado/fluxo/f-1/status', body: { to: 'published', reason: 'reativado depois do voto' } } },
      { acao: 'backlog', kind: 'backlog', ref: 'fk-0000000001', de: null, para: 'open', aplicado: true, erro: null, desfazer: null },
    ],
  }, 201));
  backend.on('POST', /^\/api\/aprendizado\/[a-z]+\/[^/]+\/status$/, () => json({ item: {}, evidencias: [], trilha: [], exposicoes: [] }));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function montar(voto: Voto | null = null, objectiveId: string | null = 'obj-1'): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<FeedbackItem runId="r-1" objectiveId={objectiveId} voto={voto} />);
  });
}

const votos = () => backend.callsTo('POST', /\/feedback$/);

describe('FeedbackItem (D2)', () => {
  it('nunca é modal: o "Deu errado" abre o formulário em linha, sem diálogo', async () => {
    await montar();
    await click(byRole('button', /^Deu errado/, container));
    expect(document.querySelector('[role="dialog"], dialog, [aria-modal="true"]')).toBeNull();
    expect(container.querySelector('form')).not.toBeNull();
  });

  it('"Deu errado" abre o motivo (obrigatório) e a nota, e manda o voto do item', async () => {
    await montar();
    await click(byRole('button', /^Deu errado/, container));
    const enviar = byRole('button', /^Enviar/, container);
    expect(enviar.getAttribute('aria-disabled')).toBe('true');
    await click(enviar);
    expect(votos()).toHaveLength(0);                         // sem motivo, nada sai

    await setValue(byRole('combobox', /Motivo/, container) as HTMLSelectElement, 'alvo_errado');
    await setValue(byRole('textbox', /Nota/, container) as HTMLTextAreaElement, 'abriu a conversa de outra pessoa');
    await click(byRole('button', /^Enviar/, container));
    await waitFor(() => expect(votos()).toHaveLength(1));
    expect(votos()[0]?.path).toBe('/api/runs/r-1/feedback');
    expect(votos()[0]?.body).toEqual({ objective_id: 'obj-1', verdict: 'errado', reason: 'alvo_errado',
                                       note: 'abriu a conversa de outra pessoa' });
  });

  it('"Deu certo" vota na hora, sem formulário; sem item, o voto é da execução', async () => {
    await montar(null, null);
    await click(byRole('button', /^Deu certo/, container));
    await waitFor(() => expect(votos()).toHaveLength(1));
    expect(votos()[0]?.body).toEqual({ verdict: 'certo' });
  });

  it('o voto dado aparece marcado', async () => {
    await montar({ objective_id: 'obj-1', verdict: 'errado', reason: 'alvo_errado', created_by: 'ana' });
    expect(byRole('button', /^Deu errado/, container).getAttribute('aria-pressed')).toBe('true');
    expect(byRole('button', /^Deu certo/, container).getAttribute('aria-pressed')).toBe('false');
    expect(text(container)).toContain('Alvo errado');
  });

  it('depois do voto, a linha "o que mudou" oferece desfazer em um clique', async () => {
    await montar();
    await click(byRole('button', /^Deu errado/, container));
    await setValue(byRole('combobox', /Motivo/, container) as HTMLSelectElement, 'fez_outra_coisa');
    await click(byRole('button', /^Enviar/, container));
    await waitFor(() => expect(text(container)).toContain('fluxo aprendido desta execução desligado'));
    expect(byRole('button', /^Deu errado/, container).getAttribute('aria-pressed')).toBe('true');

    await click(byRole('button', /^Reativar/, container));
    await waitFor(() => expect(backend.callsTo('POST', /\/status$/)).toHaveLength(1));
    const desfazer = backend.callsTo('POST', /\/status$/)[0];
    expect(desfazer?.path).toBe('/api/aprendizado/fluxo/f-1/status');
    expect(desfazer?.body).toEqual({ to: 'published', reason: 'reativado depois do voto' });
    await waitFor(() => expect(text(container)).toContain('desfeito'));
  });

  it('nota com cara de credencial (409): nada é gravado, a nota fica para editar e o aviso é em linha', async () => {
    backend.on('POST', /^\/api\/runs\/[^/]+\/feedback$/, () => apiError(409, 'note_looks_secret', 'A nota tem formato de credencial e não foi gravada.'));
    await montar();
    await click(byRole('button', /^Deu errado/, container));
    await setValue(byRole('combobox', /Motivo/, container) as HTMLSelectElement, 'outro');
    await setValue(byRole('textbox', /Nota/, container) as HTMLTextAreaElement, 'texto que o servidor recusou');
    await click(byRole('button', /^Enviar/, container));
    await waitFor(() => expect(text(container)).toContain('parece conter uma senha ou chave'));
    expect((byRole('textbox', /Nota/, container) as HTMLTextAreaElement).value).toBe('texto que o servidor recusou');
    expect(byRole('button', /^Deu errado/, container).getAttribute('aria-pressed')).toBe('false');
  });
});
