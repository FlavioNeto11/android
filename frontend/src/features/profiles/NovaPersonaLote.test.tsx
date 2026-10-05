// @vitest-environment jsdom
/**
 * Geração em lote no "Nova persona a partir de uma descrição" (v0.34): quantidade 1 continua igual; mais de uma chama
 * `POST /personas/generate/batch` com `create`, mostra o custo antes de confirmar, o progresso pelo evento (com a
 * releitura como rede de segurança) e os rascunhos com "Criar selecionadas". Backend falso: `simulated`.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PersonaBatch, PersonaBatchItem, PersonaDTO } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { startLive, stopLive } from '../../store/live';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeEvent, makeSnapshot } from '../../test/fixtures';
import {
  FakeBackend, FakeWebSocket, apiError, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor,
} from '../../test/harness';
import { LoteDePersonas } from './LoteDePersonas';
import { ProfilesPage } from './ProfilesPage';

function pessoa(over: Partial<PersonaDTO> = {}): PersonaDTO {
  return {
    id: 'ig-1', name: 'Mariana Costa', summary: null, username: 'luciana.bastos73519', display_name: 'Mariana Costa',
    first_name: 'Mariana', last_name: 'Costa', birth_date: null, email: null, persona_id: 'ig-1', persona_name: 'Mariana Costa',
    status: 'active', instance_id: null, locality: null, offline_policy: 'wait',
    credential: { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                  updated_at: null, last_used_at: null },
    session: { status: 'unknown', instance_id: null, observed_username: null, verified_at: null, detail: null, stale: false },
    last_verified_at: null, last_activity_at: null, accounts_count: 1,
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

const SOCIAL_SIMULADO = { role: 'social', provider: 'simulated', kind: 'simulated', model: 'simulado', endpoint: '',
                          sends_data_externally: false, configured: true, priced: false, vision: false, tools: false,
                          refusal_fallback: false };
const SOCIAL_PAGO = { ...SOCIAL_SIMULADO, provider: 'anthropic', kind: 'anthropic', model: 'claude-sonnet-x',
                      endpoint: 'api.anthropic.com', sends_data_externally: true, priced: true };
const IMAGEM_SIMULADA = { provider: 'simulated', model: 'degrade', quality: 'medium', configured: true, simulated: true,
                          sends_data_externally: false, per_persona: 1, on_create: true, price_per_image_usd: 0 };
const IMAGEM_PAGA = { ...IMAGEM_SIMULADA, provider: 'openai', model: 'gpt-image-2', simulated: false,
                      sends_data_externally: true, price_per_image_usd: 0.055 };
const IA_SIMULADA = { provider: 'simulated', model: null, configured: false, simulated: true, sends_data_externally: false,
                      notice: '', effort: null, roles: [SOCIAL_SIMULADO], image: IMAGEM_SIMULADA };
const IA_PAGA = { ...IA_SIMULADA, provider: 'anthropic', model: 'claude-sonnet-x', configured: true, simulated: false,
                  sends_data_externally: true, roles: [SOCIAL_PAGO], image: IMAGEM_PAGA };

const RASCUNHO = (nome: string) => ({
  name: nome, summary: `${nome}, professora em Recife.`, birth_date: '1994-03-02', gender: 'feminino',
  persona_prompt: 'Escreva com calma.', traits: { tone: 'acolhedor' },
  biography: { home: { city: 'Recife' }, work: { profession: 'Professora' } }, visual: { appearance: 'cabelo curto' },
  generation: { source: 'ai', provider: 'anthropic', model: 'claude-sonnet-x', prompt: 'professoras' },
});

function item(index: number, status: PersonaBatchItem['status'], over: Partial<PersonaBatchItem> = {}): PersonaBatchItem {
  return { index, status, name: null, persona_id: null, draft: null, error: null, ...over };
}

function lote(items: PersonaBatchItem[], over: Partial<PersonaBatch> = {}): PersonaBatch {
  return { batch_id: 'lote-1', prompt: 'professoras', count: items.length, create: false, items, done: false,
           created_at: '2026-09-28T12:00:00Z', ...over };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  const snap = makeSnapshot();
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    hydrateCount: 1,
    instances: Object.fromEntries(snap.instances.map((i) => [i.id, i])),
    instanceOrder: snap.instances.map((i) => i.id),
  });
  useUiStore.getState().navegar({ tela: 'personas', query: { foco: undefined } }, 'replace');
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(): Promise<void> {
  await act(async () => {
    root.render(<><ProfilesPage /><ConfirmHost /></>);
  });
  await waitFor(() => text().includes('Cada persona é uma pessoa'));
}

function radio(valor: 'revisar' | 'criar'): HTMLInputElement {
  const el = container.ownerDocument.querySelector<HTMLInputElement>(`input[name="modo-do-lote"][value="${valor}"]`);
  if (!el) throw new Error(`sem a opção ${valor}`);
  return el;
}

// ---------------------------------------------------------------- geração em lote (NovaPersona)
describe('nova persona em lote', () => {
  it('quantidade 1 (padrão) é o fluxo de sempre: rascunho único, sem opções de lote nem a rota de lote', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => json(IA_PAGA));
    backend.on('POST', /^\/api\/personas\/generate$/, () => json(RASCUNHO('Helena Prado')));
    await render();
    await click(byRole('button', /Nova persona a partir de uma descrição/i));
    await waitFor(() => text().includes('É uma chamada paga de IA'));
    expect((byRole('textbox', /^Quantidade/i) as HTMLInputElement).value).toBe('1');
    expect(text()).not.toContain('Custo estimado do lote');
    expect(container.ownerDocument.querySelector('input[name="modo-do-lote"]')).toBeNull();
    await setValue(byRole('textbox', /^Pedido/i) as HTMLTextAreaElement, 'professora em Recife');
    await click(byRole('button', /Gerar rascunho/i));
    await waitFor(() => text().includes('ainda não gravado'));
    expect(backend.callsTo('POST', /^\/api\/personas\/generate$/)[0]?.body)
      .toEqual({ prompt: 'professora em Recife', constraints: {} });
    expect(backend.callsTo('POST', /generate\/batch/)).toHaveLength(0);
  });

  it('mais de uma no simulado: sem custo, "Criar direto" chama o lote com create: true e o fim mostra as criadas', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => json(IA_SIMULADA));
    backend.on('POST', /^\/api\/personas\/generate\/batch$/, () => json({ batch_id: 'lote-1', count: 3 }, 202));
    backend.on('GET', /^\/api\/personas\/generate\/batch\/lote-1$/, () => json(lote([
      item(0, 'created', { name: 'Marina Lopes', persona_id: 'ig-11' }),
      item(1, 'created', { name: 'Caio Prado', persona_id: 'ig-12' }),
      item(2, 'failed', { error: 'O modelo repetiu o nome Marina Lopes, que já existe ou já saiu neste lote.' }),
    ], { create: true, done: true })));
    await render();
    await click(byRole('button', /Nova persona a partir de uma descrição/i));
    await waitFor(() => text().includes('Provedor simulado: sem custo'));
    await setValue(byRole('textbox', /^Pedido/i) as HTMLTextAreaElement, 'professoras de Recife');
    await setValue(byRole('textbox', /^Cidade/i) as HTMLInputElement, 'Recife');
    await setValue(byRole('textbox', /^Quantidade/i) as HTMLInputElement, '3');
    await waitFor(() => text().includes('Custo estimado do lote'));
    expect(text()).toContain('3 × simulada: sem custo');
    expect(text()).toContain('3 × gerador simulado: sem custo');
    await click(radio('criar'));
    await click(byRole('button', /Gerar 3 personas/i));
    // Simulado não custa: não há confirmação de gasto, o lote sai direto.
    await waitFor(() => backend.callsTo('POST', /generate\/batch$/).length === 1);
    expect(backend.callsTo('POST', /generate\/batch$/)[0]?.body)
      .toEqual({ prompt: 'professoras de Recife', constraints: { city: 'Recife' }, count: 3, create: true });
    expect(backend.callsTo('POST', /^\/api\/personas\/generate$/)).toHaveLength(0);
    await waitFor(() => text().includes('Terminado: 2 criada(s) · 0 rascunho(s) · 1 com falha.'));
    expect(text()).toContain('repetiu o nome Marina Lopes');
    expect(byRole('button', /Abrir Marina Lopes/)).toBeTruthy();
    expect(byRole('button', /Abrir Caio Prado/)).toBeTruthy();
    // No fim a lista se relê: as criadas aparecem nela.
    await waitFor(() => backend.callsTo('GET', /^\/api\/personas$/).length >= 2);
  });

  it('pago: custo do lote antes de confirmar; "Revisar antes" traz rascunhos e cria só os selecionados', async () => {
    let lista: PersonaDTO[] = [];
    backend.on('GET', /^\/api\/personas$/, () => json(lista));
    backend.on('GET', /^\/api\/ai$/, () => json(IA_PAGA));
    backend.on('GET', /\/accounts$/, () => json([]));
    backend.on('GET', /approvals/, () => json([]));
    backend.on('POST', /^\/api\/personas\/generate\/batch$/, () => json({ batch_id: 'lote-1', count: 3 }, 202));
    backend.on('GET', /^\/api\/personas\/generate\/batch\/lote-1$/, () => json(lote([
      item(0, 'ready', { name: 'Marina Lopes', draft: RASCUNHO('Marina Lopes') }),
      item(1, 'ready', { name: 'Clara Nunes', draft: RASCUNHO('Clara Nunes') }),
      item(2, 'failed', { error: 'O lote parou antes deste item: Teto de gasto de IA do dia atingido.' }),
    ], { done: true })));
    backend.on('POST', /^\/api\/personas$/, (c) => {
      const criada = pessoa({ ...(c.body as object), id: 'ig-21', username: null, persona_id: 'ig-21' });
      lista = [criada];
      return json(criada, 201);
    });
    await render();
    await click(byRole('button', /Nova persona a partir de uma descrição/i));
    await waitFor(() => text().includes('É uma chamada paga de IA'));
    await setValue(byRole('textbox', /^Pedido/i) as HTMLTextAreaElement, 'professoras');
    await setValue(byRole('textbox', /^Quantidade/i) as HTMLInputElement, '3');
    await waitFor(() => text().includes('Custo estimado do lote'));
    expect(text()).toContain('3 × ≈ US$ 0,02–0,03 = ≈ US$ 0,06–0,09 (claude-sonnet-x em anthropic)');
    expect(text()).toContain('Fotos automáticas (só das que você criar): 3 × US$ 0,055 = ≈ US$ 0,165 (openai)');
    expect(radio('revisar').checked).toBe(true);                       // o padrão é revisar

    // Pago: o primeiro clique só mostra a confirmação com o custo. Nada foi pedido ainda.
    await click(byRole('button', /Gerar 3 personas/i));
    await waitFor(() => text().includes('Confirmar 3 gerações pagas'));
    expect(backend.callsTo('POST', /generate\/batch$/)).toHaveLength(0);
    expect(text()).toContain('≈ US$ 0,06–0,09');
    await click(byRole('button', /Confirmar e gerar 3/i));
    await waitFor(() => backend.callsTo('POST', /generate\/batch$/).length === 1);
    expect(backend.callsTo('POST', /generate\/batch$/)[0]?.body)
      .toEqual({ prompt: 'professoras', constraints: {}, count: 3, create: false });

    await waitFor(() => text().includes('Terminado: 0 criada(s) · 2 rascunho(s) · 1 com falha.'));
    expect(text()).toContain('Teto de gasto de IA do dia atingido');
    expect(text()).toContain('Marina Lopes, professora em Recife.');
    expect(byRole('button', /Criar selecionadas \(2\)/)).toBeTruthy();
    await click(byRole('checkbox', /Selecionar Clara Nunes/));
    await click(byRole('button', /Criar selecionadas \(1\)/));
    await waitFor(() => backend.callsTo('POST', /^\/api\/personas$/).length === 1);
    expect(backend.callsTo('POST', /^\/api\/personas$/)[0]?.body).toEqual(RASCUNHO('Marina Lopes'));
    // A criada ganha o selo e "Abrir" (a Clara, desmarcada, continua rascunho), que relê a lista e abre a pessoa.
    await waitFor(() => byRole('button', /Abrir Marina Lopes/));
    expect(byRole('button', /Criar selecionadas \(0\)/).getAttribute('aria-disabled')).toBe('true');
    // O resumo conta a que o painel criou (o servidor não sabe dela): 1 criada, 1 rascunho, 1 falha.
    expect(text()).toContain('Terminado: 1 criada(s) · 1 rascunho(s) · 1 com falha.');
    await click(byRole('button', /Abrir Marina Lopes/));
    await waitFor(() => byRole('button', /^Visão geral/).getAttribute('aria-current') === 'page');
  });

  it('quantidade fora de 1 a 10 não gera', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    backend.on('GET', /^\/api\/ai$/, () => json(IA_SIMULADA));
    await render();
    await click(byRole('button', /Nova persona a partir de uma descrição/i));
    await setValue(byRole('textbox', /^Pedido/i) as HTMLTextAreaElement, 'professoras');
    await setValue(byRole('textbox', /^Quantidade/i) as HTMLInputElement, '11');
    await waitFor(() => text().includes('Use um número inteiro de 1 a 10.'));
    const gerar = byRole('button', /Gerar rascunho/i);
    expect(gerar.getAttribute('aria-disabled')).toBe('true');
    await click(gerar);
    expect(backend.calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });
});

describe('progresso do lote', () => {
  async function abrir(releituraMs: number, onLote: () => void = () => {}): Promise<void> {
    await act(async () => {
      root.render(<LoteDePersonas batchId="lote-1" onClose={() => {}} onLote={onLote} releituraMs={releituraMs} />);
    });
  }

  it('anda pelo evento DESTE lote, sem polling; o fim relê a lista uma vez', async () => {
    backend.on('GET', /^\/api\/session$/, () => json({ operator: 'Ana', token_required: false, expires_at: null }));
    backend.on('GET', /^\/api\/snapshot$/, () => json(makeSnapshot({ last_event_id: 7 })));
    let estado = lote([item(0, 'generating'), item(1, 'generating'), item(2, 'pending')], { create: true });
    backend.on('GET', /^\/api\/personas\/generate\/batch\/lote-1$/, () => json(estado));
    let relidas = 0;
    await abrir(60_000, () => { relidas += 1; });
    await waitFor(() => text().includes('0/3'));
    const parar = startLive();
    try {
      await waitFor(() => expect(FakeWebSocket.instances.length).toBeGreaterThan(0));
      const ws = FakeWebSocket.last;
      await act(async () => {
        ws.serverOpen();
        ws.serverSend({ type: 'hello', server_time: new Date().toISOString(), last_event_id: 7 });
      });
      const antes = backend.callsTo('GET', /batch\/lote-1$/).length;
      await act(async () => ws.serverSend({ type: 'event', event: makeEvent(8, 'persona.batch.updated',
        { batch_id: 'lote-9', index: 0, status: 'ready', done: false }) }));
      await flush(50);
      expect(backend.callsTo('GET', /batch\/lote-1$/).length).toBe(antes);   // outro lote: nada

      estado = lote([item(0, 'created', { name: 'Marina Lopes', persona_id: 'ig-11' }), item(1, 'generating'),
                     item(2, 'pending')], { create: true });
      await act(async () => ws.serverSend({ type: 'event', event: makeEvent(9, 'persona.batch.updated',
        { batch_id: 'lote-1', index: 0, status: 'created', done: false }) }));
      await waitFor(() => text().includes('1/3') && text().includes('Marina Lopes'));
      expect(relidas).toBe(0);

      estado = lote([item(0, 'created', { name: 'Marina Lopes', persona_id: 'ig-11' }),
                     item(1, 'created', { name: 'Caio Prado', persona_id: 'ig-12' }),
                     item(2, 'failed', { error: 'O modelo recusou o pedido.' })], { create: true, done: true });
      await act(async () => ws.serverSend({ type: 'event', event: makeEvent(10, 'persona.batch.updated',
        { batch_id: 'lote-1', index: null, status: null, done: true }) }));
      await waitFor(() => text().includes('Terminado: 2 criada(s)'));
      expect(text()).toContain('O modelo recusou o pedido.');
      await waitFor(() => relidas === 1);
    } finally {
      parar();
      stopLive();
    }
  });

  it('sem evento, relê o estado de tempos em tempos até terminar', async () => {
    let leituras = 0;
    backend.on('GET', /^\/api\/personas\/generate\/batch\/lote-1$/, () => {
      leituras += 1;
      return json(leituras < 3 ? lote([item(0, 'generating'), item(1, 'pending')])
        : lote([item(0, 'ready', { name: 'Marina Lopes', draft: RASCUNHO('Marina Lopes') }),
                item(1, 'ready', { name: 'Clara Nunes', draft: RASCUNHO('Clara Nunes') })], { done: true }));
    });
    await abrir(30);
    await waitFor(() => text().includes('Terminado: 0 criada(s) · 2 rascunho(s)'));
    // Conta os pedidos que a tela mandou, não as respostas: com o atraso, uma releitura mandada antes do fim ainda
    // chega ao handler depois do "Terminado" (29.104).
    const relidas = () => backend.callsTo('GET', /^\/api\/personas\/generate\/batch\/lote-1$/).length;
    const depois = relidas();
    await flush(120);
    expect(relidas()).toBe(depois);                                     // terminou: parou de reler
  });

  it('lote perdido num reinício (404) vira aviso, não erro', async () => {
    backend.on('GET', /^\/api\/personas\/generate\/batch\/lote-1$/,
               () => apiError(404, 'not_found', 'Lote não encontrado: os lotes vivem na memória do servidor.'));
    await abrir(60_000);
    await waitFor(() => text().includes('O lote se perdeu'));
    expect(text()).toContain('vivem na memória do servidor');
  });
});
