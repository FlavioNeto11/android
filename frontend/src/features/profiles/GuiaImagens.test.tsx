// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { InstagramProfile, PersonaImage } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { startLive, stopLive } from '../../store/live';
import { makeEvent, makeSnapshot } from '../../test/fixtures';
import {
  FakeBackend, FakeWebSocket, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor,
} from '../../test/harness';
import { ProfileDetail } from './ProfileDetail';

function perfil(over: Partial<InstagramProfile> = {}): InstagramProfile {
  return {
    id: 'ig-1', username: 'mariana.costa91182', display_name: 'Mariana Costa', first_name: 'Mariana',
    last_name: 'Costa', birth_date: null, email: null, persona_id: 'ig-1', persona_name: 'Mariana Costa',
    status: 'active', instance_id: null, locality: null, offline_policy: 'wait',
    credential: { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                  updated_at: null, last_used_at: null },
    session: { status: 'unknown', instance_id: null, observed_username: null, verified_at: null, detail: null, stale: false },
    last_verified_at: null, last_activity_at: null,
    created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
    visual: { appearance: 'cabelo cacheado, óculos redondos' },
    ...over,
  };
}

function imagem(over: Partial<PersonaImage> = {}): PersonaImage {
  return {
    id: 'img-1', persona_id: 'ig-1', status: 'ready', source: 'generated', is_primary: true, width: 1024, height: 1024,
    provider: 'simulated', model: 'simulado-v1', seed: 123456, aspect: '1:1', cost_usd: 0, error: null,
    created_at: '2026-09-28T10:00:00Z', url: '/api/personas/ig-1/images/img-1', ...over,
  };
}

const IA_SIMULADA = {
  provider: 'simulated', model: null, configured: false, simulated: true, sends_data_externally: false, notice: '',
  effort: null,
  image: { provider: 'simulated', model: 'simulado-v1', quality: 'low', configured: true, simulated: true,
           sends_data_externally: false, per_persona: 1, on_create: true, price_per_image_usd: 0 },
};

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /approvals/, () => json([]));
  backend.on('GET', /\/accounts$/, () => json([]));
  backend.on('GET', /^\/api\/ai$/, () => json(IA_SIMULADA));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function abrirImagens(onChanged: () => Promise<void> = async () => {}): Promise<void> {
  await act(async () => {
    root.render(<><ProfileDetail profile={perfil()} abaInicial="imagens" onBack={() => {}} onChanged={onChanged} />
      <ConfirmHost /></>);
  });
  await waitFor(() => text().includes('Gerar mais'));
}

it('a galeria mostra a principal, o selo "simulado", a receita e o custo', async () => {
  backend.on('GET', /\/personas\/ig-1\/images$/, () => json([
    imagem(),
    imagem({ id: 'img-2', is_primary: false, status: 'refused', provider: 'openai', model: 'gpt-image-1-mini',
             error: 'O provedor recusou o pedido.', cost_usd: 0.011, url: '/api/personas/ig-1/images/img-2' }),
  ]));
  await abrirImagens();
  await waitFor(() => text().includes('principal'));
  expect(text()).toContain('simulado');
  expect(text()).toContain('semente 123456');
  expect(text()).toContain('1:1');
  expect(text()).toContain('sem custo');
  expect(text()).toContain('recusada pelo provedor');
  expect(text()).toContain('O provedor recusou o pedido.');
  expect(text()).toContain('custo US$ 0.011');
  expect(text()).toContain('Simulado: sem custo');
  // "Tornar principal" só para imagem pronta que ainda não é a principal: nenhuma aqui.
  expect(() => byRole('button', /Tornar principal/i)).toThrow();
});

it('gerar chama POST …/images {count} e a galeria se atualiza com o evento DESTA persona, sem polling', async () => {
  backend.on('GET', /^\/api\/session$/, () => json({ operator: 'Ana', token_required: false, expires_at: null }));
  backend.on('GET', /^\/api\/snapshot$/, () => json(makeSnapshot({ last_event_id: 7 })));
  let galeria: PersonaImage[] = [];
  backend.on('GET', /\/personas\/ig-1\/images$/, () => json(galeria));
  backend.on('POST', /\/personas\/ig-1\/images$/, () => json({ accepted: true, persona_id: 'ig-1', count: 2,
                                                               provider: 'simulated', simulated: true }, 202));
  let relida = 0;
  await abrirImagens(async () => { relida += 1; });
  await waitFor(() => text().includes('Nenhuma foto ainda'));

  const parar = startLive();
  try {
    await waitFor(() => expect(FakeWebSocket.instances.length).toBeGreaterThan(0));
    const ws = FakeWebSocket.last;
    await act(async () => {
      ws.serverOpen();
      ws.serverSend({ type: 'hello', server_time: new Date().toISOString(), last_event_id: 7 });
    });

    await setValue(byRole('combobox', /Quantas gerar/i) as HTMLSelectElement, '2');
    await click(byRole('button', /Gerar mais/i));
    await waitFor(() => backend.callsTo('POST', /\/personas\/ig-1\/images$/).length === 1);
    expect(backend.callsTo('POST', /\/personas\/ig-1\/images$/)[0]?.body).toEqual({ count: 2 });
    await waitFor(() => text().includes('Gerando 2 imagem(ns)'));

    // Evento de OUTRA persona não relê nada.
    const antes = backend.callsTo('GET', /\/images$/).length;
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(8, 'persona.image.updated',
      { profile_id: 'ig-9', image_id: 'x', status: 'ready' }) }));
    await new Promise((r) => setTimeout(r, 50));
    expect(backend.callsTo('GET', /\/images$/).length).toBe(antes);

    galeria = [imagem()];
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(9, 'persona.image.updated',
      { profile_id: 'ig-1', image_id: 'img-1', status: 'ready' }) }));
    await waitFor(() => text().includes('principal'));
    expect(backend.callsTo('GET', /\/images$/).length).toBe(antes + 1);
    await waitFor(() => text().includes('Gerando 1 imagem(ns)'));
    await waitFor(() => relida >= 1);                          // a principal é o avatar: a persona se relê
  } finally {
    parar();
    stopLive();
  }
});

it('tornar principal e apagar (com confirmação) usam as rotas da imagem', async () => {
  let galeria = [imagem(), imagem({ id: 'img-2', is_primary: false, url: '/api/personas/ig-1/images/img-2' })];
  backend.on('GET', /\/personas\/ig-1\/images$/, () => json(galeria));
  backend.on('PUT', /\/images\/img-2\/primary$/, () => {
    galeria = [imagem({ is_primary: false }), imagem({ id: 'img-2', is_primary: true })];
    return json(perfil());
  });
  backend.on('DELETE', /\/images\/img-1$/, () => json(null, 204));
  await abrirImagens();
  await waitFor(() => text().includes('Tornar principal'));
  await click(byRole('button', /Tornar principal/i));
  await waitFor(() => backend.callsTo('PUT', /\/personas\/ig-1\/images\/img-2\/primary$/).length === 1);

  await waitFor(() => text().includes('Tornar principal'));   // agora a img-1 deixou de ser a principal
  await click(byRole('button', /^Apagar$/i));
  await waitFor(() => text().includes('Apagar esta foto?'));
  await click(byRole('button', /^Apagar$/i, byRole('dialog', /Apagar esta foto/)));
  await waitFor(() => backend.callsTo('DELETE', /\/personas\/ig-1\/images\/img-1$/).length === 1);
});

it('gerador pago sem chave: o erro 409 aparece na guia com o motivo', async () => {
  backend.on('GET', /\/personas\/ig-1\/images$/, () => json([]));
  backend.on('GET', /^\/api\/ai$/, () => json({ ...IA_SIMULADA, image: { ...IA_SIMULADA.image, provider: 'openai',
    model: 'gpt-image-1-mini', simulated: false, configured: true, sends_data_externally: true, price_per_image_usd: 0.011 } }));
  backend.on('POST', /\/personas\/ig-1\/images$/, () => apiError(409, 'ai_budget', 'O teto de gasto de hoje foi atingido.'));
  await abrirImagens();
  await waitFor(() => text().includes('Chamada paga: ≈ US$ 0.011 por 1 imagem(ns).'));
  expect(text()).toContain('nunca o nome');
  await click(byRole('button', /Gerar mais/i));
  await waitFor(() => text().includes('Teto de gasto de IA atingido'));
  expect(text()).toContain('O teto de gasto de hoje foi atingido.');
});

it('a identidade visual salva só o bloco visual', async () => {
  backend.on('GET', /\/personas\/ig-1\/images$/, () => json([]));
  backend.on('PATCH', /\/personas\/ig-1$/, () => json(perfil()));
  await abrirImagens();
  await setValue(byRole('textbox', /^Paleta/i) as HTMLInputElement, 'tons terrosos');
  await click(byRole('button', /Salvar identidade visual/i));
  await waitFor(() => backend.callsTo('PATCH', /\/personas\/ig-1$/).length === 1);
  const corpo = backend.callsTo('PATCH', /\/personas\/ig-1$/)[0]?.body as { visual: Record<string, unknown> };
  expect(Object.keys(corpo)).toEqual(['visual']);
  expect(corpo.visual).toMatchObject({ appearance: 'cabelo cacheado, óculos redondos', palette: 'tons terrosos' });
});
