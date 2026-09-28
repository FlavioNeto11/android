// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { App } from './App';
import { stopLive } from './store/live';
import {
  DIAGNOSTICS, FLOWS, RECIPES, REPORT, RUN_EVENTS, RUN_ID, USAGE_SIMULATED, makeEvent, makeInstance, makeRun, makeRunDetail, makeSnapshot,
} from './test/fixtures';
import {
  FakeBackend, FakeWebSocket, allByRole, apiError, byRole, click, flush, installBrowserStubs, json, pointer, setValue, text, waitFor,
} from './test/harness';

/**
 * Teste de integração da UI inteira contra um backend falso (fetch + WebSocket simulados).
 * Os blocos rodam em sequência sobre a MESMA aplicação montada, como uma sessão de uso real.
 */

const backend = new FakeBackend();
let root: Root;
let snapshotLastEventId = 100;
let frameSeq = 0;

function snapshotCalls(): number {
  return backend.callsTo('GET', /^\/api\/snapshot$/).length;
}

function runPosts() {
  return backend.callsTo('POST', /^\/api\/runs$/);
}

async function goTo(hash: string): Promise<void> {
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}

beforeAll(async () => {
  installBrowserStubs();
  backend
    // O painel pergunta quem está operando antes de qualquer outra coisa (item 9.1): aqui a sessão já existe,
    // que é o estado em que a pessoa usa o sistema. O gate em si é testado em features/login.
    .on('GET', /^\/api\/session$/, () => json({ operator: 'Ana Ribeiro', token_required: false, expires_at: null }))
    .on('POST', /^\/api\/logout$/, () => json({ ended: true }))
    .on('GET', /^\/api\/snapshot$/, () => json(makeSnapshot({ last_event_id: snapshotLastEventId })))
    .on('GET', /^\/api\/runs$/, () => json([makeRun()]))
    .on('GET', new RegExp(`^/api/runs/${RUN_ID}$`), () => json(makeRunDetail()))
    .on('GET', new RegExp(`^/api/runs/${RUN_ID}/events$`), () => json(RUN_EVENTS))
    .on('GET', new RegExp(`^/api/runs/${RUN_ID}/report$`), () => json(REPORT))
    .on('GET', /^\/api\/diagnostics$/, () => json(DIAGNOSTICS))
    .on('GET', /^\/api\/ai$/, () => json(makeSnapshot().health.ai))
    .on('GET', /^\/api\/instances\/[^/]+\/frame$/, () => {
      frameSeq += 1;
      // Bytes, não `new Blob(...)`: sob jsdom o `Blob` global é o do jsdom, sem `.stream()`, e o `Response` do
      // undici do Node 22 (o do CI) falha com "object.stream is not a function" — o Foco mostrava "Não foi possível
      // carregar a tela". O Node 24 tolera; o teste não pode depender disso (backlog B13).
      return new Response(new TextEncoder().encode('jpeg'), {
        status: 200,
        headers: {
          'Content-Type': 'image/jpeg', 'X-Frame-Id': `full-${frameSeq}`, 'X-Frame-Ts': new Date().toISOString(),
          'X-Frame-Width': '1080', 'X-Frame-Height': '2400', 'X-Frame-Orientation': 'portrait',
        },
      });
    })
    .on('GET', /^\/api\/instances\/[^/]+\/hierarchy$/, () => json({ ts: new Date().toISOString(), elements: [] }))
    .on('POST', /^\/api\/instances\/[^/]+\/actions\/[^/]+$/, () => json({ accepted: true }, 202))
    .on('GET', /^\/api\/usage$/, (c) => json({ ...USAGE_SIMULATED, scope: { run_id: c.query.get('run_id'), days: c.query.get('run_id') ? null : Number(c.query.get('days')) } }))
    .on('GET', /^\/api\/flows$/, () => json(FLOWS))
    .on('GET', /^\/api\/recipes$/, () => json(RECIPES))
    .on('PUT', /^\/api\/flows\/[^/]+$/, (c) => json({ ...FLOWS.find((f) => c.path.endsWith(`/${f.id}`)), ...(c.body as object) }))
    .on('PUT', /^\/api\/recipes\/\d+$/, (c) => json({ id: Number(c.path.split('/').pop()), ...(c.body as object) }))
    .on('DELETE', /^\/api\/(flows|recipes)\/[^/]+$/, () => new Response(null, { status: 204 }))
    .on('POST', /^\/api\/instances\/bulk$/, (c) => json({ accepted: (c.body as { ids: string[] }).ids.slice(1), rejected: [{ id: (c.body as { ids: string[] }).ids[0], reason: 'já está online' }] }, 202))
    .on('POST', /^\/api\/instances\/[^/]+\/control\/take$/, () => json({ status: 'granted', lease_id: 'lease-1' }))
    .on('POST', /^\/api\/instances\/[^/]+\/control\/release$/, () => json({ status: 'released' }))
    .on('POST', /^\/api\/instances\/[^/]+\/input$/, () => json({ ok: true }))
    .on('POST', new RegExp(`^/api/runs/${RUN_ID}/cancel$`), () => json(makeRun({ status: 'cancelling' })));
  backend.install();

  window.localStorage.clear();
  // Estas suítes cobrem os modos MANUAIS; o Automático (ADR-050, o padrão) tem os seus testes em SugestaoDeAlvos.
  window.localStorage.setItem('cda.commandTargetV2', JSON.stringify('selecao'));
  const container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<App />);
  });
});

afterAll(async () => {
  await act(async () => root.unmount());
  stopLive();
});

describe('Central de Aparelhos — sessão completa', () => {
  it('faz GET /snapshot, hidrata e só então abre o WS com last_event_id', async () => {
    await waitFor(() => expect(text()).toContain('android-10'));
    expect(snapshotCalls()).toBe(1);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.last.url).toMatch(/\/api\/ws\?last_event_id=100$/);

    await act(async () => {
      FakeWebSocket.last.serverOpen();
      FakeWebSocket.last.serverSend({ type: 'hello', server_time: new Date().toISOString(), last_event_id: 100 });
    });
    await waitFor(() => expect(text()).toContain('Conectado'));
  });

  it('mostra barra superior, estados dos cartões e avisos com texto (não só cor)', () => {
    const page = text();
    expect(page).toContain('Central de Aparelhos');
    expect(page).toContain('MODO SIMULADO');
    expect(page).toContain('Ambiente degradado');
    expect(page).toContain('2/10'); // instâncias online
    expect(page).toContain('Desatualizado'); // android-02 com frame.stale
    expect(page).toContain('Login necessário no QA Messenger'); // attention
    expect(page).toContain('observado'); // account_evidence
    expect(page).toContain('Aguardando o adb responder'); // booting + detalhe
    expect(page).toContain('Criar AVD'); // absent
    expect(page).toContain('Tentar novamente'); // error
    expect(page).toContain('Controle: IA');
    // miniatura só refaz o download quando o frame.id muda
    const thumb = document.querySelector('img[alt="Tela atual de android-01"]') as HTMLImageElement;
    expect(thumb.getAttribute('src')).toBe('/api/instances/android-01/frame?mode=thumb&f=frame-a1');
  });

  it('aplica eventos de forma idempotente e nunca descarta efêmeros (id nulo)', async () => {
    const ws = FakeWebSocket.last;
    const online = makeInstance(6, { state: 'online', frame: null });
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(101, 'instance.updated', { instance: online }, { instance_id: 'android-06' }) }));
    await waitFor(() => expect(text()).toContain('3/10'));

    // o mesmo id reentregue (com conteúdo diferente) precisa ser ignorado
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(101, 'instance.updated', { instance: makeInstance(6, { state: 'error' }) }) }));
    await flush(20);
    expect(text()).toContain('3/10');

    const frame = { id: 'frame-a2', ts: new Date().toISOString(), width: 1080, height: 2400, orientation: 'portrait', stale: false };
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(null, 'frame', { instance_id: 'android-01', frame }) }));
    await waitFor(() => {
      const thumb = document.querySelector('img[alt="Tela atual de android-01"]') as HTMLImageElement;
      expect(thumb.getAttribute('src')).toContain('f=frame-a2');
    });
  });

  it('hibernado: estado próprio, "Acordar" envia wake e não conta como online', async () => {
    const ws = FakeWebSocket.last;
    const hibernated = makeInstance(8, { state: 'hibernated', state_detail: 'hibernado (snapshot salvo)' });
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(102, 'instance.updated', { instance: hibernated }, { instance_id: 'android-08' }) }));
    const card = await waitFor(() => {
      const el = document.querySelector('article[aria-label="Aparelho android-08 — Hibernado"]');
      if (!el) throw new Error('cartão hibernado ausente');
      return el as HTMLElement;
    });
    expect(text(card)).toContain('Hibernado — acorda em segundos, sem ocupar RAM');
    expect(text()).toContain('3/10'); // continua 3 online
    expect(text(document.querySelector('header') as HTMLElement)).not.toContain('vagas'); // rodízio desligado
    expect(text(document.querySelector('[aria-label="Aparelhos por estado"]') as HTMLElement)).toContain('1 hibernado');

    await click(byRole('button', 'Acordar', card));
    await waitFor(() => expect(backend.callsTo('POST', /android-08\/actions\/wake$/)).toHaveLength(1));

    // aparelho online ganha "Hibernar" porque health.features.hibernation = true
    expect(allByRole('button', 'Hibernar android-01')).toHaveLength(1);
    expect(allByRole('button', 'Hibernar android-08')).toHaveLength(0);
  });

  it('rodízio: com auto_start_devices a barra mostra as vagas ao lado de ONLINE', async () => {
    const ws = FakeWebSocket.last;
    const settings = { ...makeSnapshot().settings, auto_start_devices: true, max_online_devices: 3 };
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(null, 'settings.updated', { settings }) }));
    await waitFor(() => expect(text()).toContain('3/10online3 vagas'));
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(null, 'settings.updated', { settings: makeSnapshot().settings }) }));
    await waitFor(() => expect(text()).not.toContain('3 vagas'));
  });

  it('chip da IA abre os modelos por função e o estado de receitas, fluxos e imagens', async () => {
    await click(byRole('button', /^Modelo de IA: simulador-local/));
    const pop = await waitFor(() => byRole('dialog', 'IA em uso'));
    const content = text(pop);
    for (const expected of ['Planejar', 'sim-planejador', 'Decidir', 'sim-decisor', 'Verificar', 'sim-verificador', 'Escalonamento', 'sim-escalonado']) {
      expect(content).toContain(expected);
    }
    expect(content).toContain('Reprodução (sem custo de modelo)');
    expect(content).toContain('Ligados');
    expect(content).toContain('Automático (só quando precisa)');
    await click(byRole('button', /^Modelo de IA: simulador-local/));
    await waitFor(() => expect(allByRole('dialog', 'IA em uso')).toHaveLength(0));
  });

  it('seleciona com caixa, Ctrl+clique e Shift+clique e envia a ação em lote', async () => {
    await click(byRole('checkbox', 'Selecionar android-01'));
    const card3 = document.querySelector('article[aria-label^="Aparelho android-03"]') as HTMLElement;
    await click(card3, { ctrlKey: true });
    const card6 = document.querySelector('article[aria-label^="Aparelho android-06"]') as HTMLElement;
    await click(card6, { shiftKey: true }); // intervalo 03..06
    await waitFor(() => expect(text()).toContain('Ação em 5 aparelhos'));
    expect(text()).toContain('5 de 10 selecionados');

    // a barra em lote oferece "Hibernar" (recurso ligado), mas não "Acordar" (nenhum hibernado na seleção)
    expect(allByRole('button', 'Hibernar', byRole('toolbar', /Ação em 5/))).toHaveLength(1);
    expect(allByRole('button', 'Acordar', byRole('toolbar', /Ação em 5/))).toHaveLength(0);
    await click(byRole('button', 'Parar', byRole('toolbar', /Ação em 5/)));
    await waitFor(() => expect(backend.callsTo('POST', /bulk$/)).toHaveLength(1));
    // O lote passou a mandar `idempotency_key` dentro de `params` (item 1.3): sem ela, um segundo clique — ou o
    // reenvio de uma requisição que o navegador achou perdida — abriria uma segunda leva de comandos nos mesmos
    // aparelhos. É de lá que o backend a lê, prefixando por instância (api.py:1225). A chave é gerada a cada
    // clique, então o teste confere a forma, não o valor.
    const lote = backend.callsTo('POST', /bulk$/)[0]?.body as
      { ids: string[]; action: string; params?: { idempotency_key?: string } } | undefined;
    expect(lote).toMatchObject({
      ids: ['android-01', 'android-03', 'android-04', 'android-05', 'android-06'], action: 'stop',
    });
    expect(lote?.params?.idempotency_key).toMatch(/^bulk:stop:/);
    // o motivo das rejeitadas aparece para o usuário
    await waitFor(() => expect(text()).toContain('android-01: já está online'));
  });

  it('comando: botões explicam por que estão indisponíveis', async () => {
    await click(byRole('button', 'Limpar', document.querySelector('[aria-labelledby="command-title"]') as HTMLElement));
    await waitFor(() => expect(text()).toContain('Selecione ao menos um aparelho'));
    const exec = byRole('button', /^Executar/);
    expect(exec.getAttribute('aria-disabled')).toBe('true');
    await click(exec);
    expect(runPosts()).toHaveLength(0);
  });

  it('comando: mostra a estimativa de custo quando o texto casa um fluxo conhecido (item 7.7)', async () => {
    backend.on('GET', /^\/api\/flows\/match$/, (call) =>
      call.query.get('command') === 'enviar oi'
        ? json({ flow_id: 'f-oi', name: 'Enviar oi', command_template: 'enviar oi', package: null,
                 target_version: null, steps_total: 3, steps_with_recipe: 1, ai_cost: 'parcial', estimated_usd: 0.12 })
        : json(null));
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'enviar oi');
    await waitFor(() => expect(text()).toContain('estimativa: US$ 0.12 por aparelho'));
    expect(text()).toContain('2 etapas sem IA');
  });

  it('comando: reutiliza a idempotency_key nas novas tentativas e só troca após sucesso', async () => {
    await click(byRole('checkbox', 'Selecionar android-01'));
    await click(byRole('checkbox', 'Selecionar android-02'));
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'Abra o app e envie a mensagem');

    backend.on('POST', /^\/api\/runs$/, () => apiError(503, 'ai_unavailable', 'Provedor de IA fora do ar'));
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(text()).toContain('Provedor de IA fora do ar'));
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(runPosts()).toHaveLength(2));

    backend.on('POST', /^\/api\/runs$/, () => json({ ...makeRun(), deduplicated: true }));
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(text()).toContain('Execução já existente — nenhuma duplicata criada'));

    const posts = runPosts().map((c) => c.body as { idempotency_key: string; mode: string; instance_ids: string[]; command: string });
    expect(posts).toHaveLength(3);
    expect(posts[0]?.idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
    expect(posts[1]?.idempotency_key).toBe(posts[0]?.idempotency_key);
    expect(posts[2]?.idempotency_key).toBe(posts[0]?.idempotency_key);
    expect(posts[2]).toMatchObject({ mode: 'execute', instance_ids: ['android-01', 'android-02'], command: 'Abra o app e envie a mensagem' });

    // "Planejar" é outra intenção → outra chave; e, depois do sucesso, a de "Executar" também já girou
    await flush(2100); // espera o intervalo de proteção contra clique duplo
    await click(byRole('button', /^Planejar/));
    await waitFor(() => expect(runPosts()).toHaveLength(4));
    const plan = runPosts()[3]?.body as { idempotency_key: string; mode: string };
    expect(plan.mode).toBe('plan');
    expect(plan.idempotency_key).not.toBe(posts[0]?.idempotency_key);
  }, 15_000);

  it('comando: distribuir entre servidores mostra a prévia e manda distribute em vez de aparelhos', async () => {
    backend.on('GET', /^\/api\/runs\/distribution$/, (call) => json({
      requested: Number(call.query.get('count')),
      picks: [
        { instance_id: 'android-09', server_id: 'worker-lan-01', server_name: 'Notebook da LAN', needs_start: false },
        { instance_id: 'android-10', server_id: 'worker-lan-01', server_name: 'Notebook da LAN', needs_start: true },
        { instance_id: 'android-01', server_id: 'central', server_name: 'central (este servidor)', needs_start: false },
      ],
      per_server: { 'Notebook da LAN': 2, 'central (este servidor)': 1 }, missing: 0, reasons: [],
    }));
    backend.on('POST', /^\/api\/runs$/, () => json(makeRun()));
    await flush(2100); // o teste anterior termina com uma execução criada: espera o intervalo contra clique duplo
    await click(byRole('button', /Distribuir entre servidores/));
    await setValue(byRole('textbox', 'Aparelhos') as HTMLInputElement, '3');
    await waitFor(() => expect(text()).toContain('em Notebook da LAN'));
    expect(text()).toContain('1 precisa ligar');
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'Abra o app distribuído');
    const antes = runPosts().length;
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(runPosts()).toHaveLength(antes + 1));
    const corpo = runPosts()[antes]?.body as { instance_ids: string[]; distribute?: { count: number; app_id: string } };
    expect(corpo.instance_ids).toEqual([]);
    expect(corpo.distribute?.count).toBe(3);
    expect(corpo.distribute?.app_id).toBeTruthy();
    // Volta ao modo de seleção: a escolha fica guardada no navegador e os próximos testes marcam aparelhos.
    await click(byRole('button', /Aparelhos marcados/));
    await flush(2100);
  }, 15_000);

  it('execução: cabeçalho, contadores e todas as abas renderizam a partir do RunDetail', async () => {
    await waitFor(() => expect(text()).toContain('2 aparelhos solicitados · 2 utilizados'));
    const area = document.getElementById('execucao') as HTMLElement;
    expect(text(area)).toContain('SIMULADO');
    expect(text(area)).toContain('Bloqueio (aguardando usuário)');
    expect(text(area)).toContain('1 objetivo(s) precisam de você');

    await click(byRole('tab', /^Plano/, area));
    await waitFor(() => expect(text(area)).toContain('efeito externo — sem repetição automática'));
    expect(text(area)).toContain('depende de:');
    expect(text(area)).toContain('Critérios de sucesso');

    await click(byRole('tab', /^Por aparelho/, area));
    await waitFor(() => expect(text(area)).toContain('Faça login com a conta de teste'));
    expect(text(area)).toContain('Assumir controle');
    expect(text(area)).toContain('Abandonar');

    await click(byRole('tab', /^Linha do tempo/, area));
    await waitFor(() => expect(text(area)).toContain('Execução iniciada'));

    await click(byRole('tab', /^Evidências/, area));
    await waitFor(() => expect(text(area)).toContain('conteúdo ocultado'));
    expect((area.querySelector('img[src="/api/evidence/1"]'))).not.toBeNull();

    await click(byRole('tab', /^Decisões/, area));
    await waitFor(() => expect(text(area)).toContain('pedir ajuda ao usuário'));

    await click(byRole('tab', /^Relatório/, area));
    await waitFor(() => expect(text(area)).toContain('Confirmação de leitura pelo destinatário'));
    expect(text(area)).toContain('Copiar Markdown');
    expect(text(area)).toContain('Mensagem enviada');
  });

  it('execução: eventos mantêm o detalhe vivo e "Cancelar" pede confirmação', async () => {
    const ws = FakeWebSocket.last;
    const area = document.getElementById('execucao') as HTMLElement;
    await click(byRole('tab', /^Por aparelho/, area));
    const objective = { ...makeRunDetail().objectives[1], status: 'succeeded', needs: null, blocked_reason: null };
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(110, 'objective.updated', { objective }, { run_id: RUN_ID, instance_id: 'android-02' }) }));
    await waitFor(() => expect(text(area)).not.toContain('Faça login com a conta de teste'));

    await click(byRole('button', /^Cancelar/, area));
    const dialog = await waitFor(() => byRole('dialog', /Cancelar a execução/));
    expect(backend.callsTo('POST', /cancel$/)).toHaveLength(0); // nada acontece sem confirmar
    await click(byRole('button', 'Cancelar execução', dialog));
    await waitFor(() => expect(backend.callsTo('POST', /cancel$/)).toHaveLength(1));
    await waitFor(() => expect(text(area)).toContain('Cancelando'));
  });

  it('foco: avisa o backend, assume o controle e converte cliques em pixels do aparelho', async () => {
    const ws = FakeWebSocket.last;
    await click(byRole('button', 'Abrir android-01 na visão de foco'));
    const panel = await waitFor(() => byRole('dialog', /Visão de foco: android-01/));
    // O foco vai no `watch` (contrato C2), que substituiu a mensagem `focus`; sem IntersectionObserver no jsdom,
    // todos os cartões contam como visíveis na grade.
    await waitFor(() => expect(ws.sent).toContainEqual(expect.objectContaining({ type: 'watch', focus: 'android-01', ttl_s: 20 })));
    expect(ws.sent.some((m) => (m as { type?: string }).type === 'focus')).toBe(false);
    await waitFor(() => expect(backend.callsTo('GET', /android-01\/frame$/).some((c) => c.query.get('mode') === 'full')).toBe(true));
    expect(text(panel)).toContain('Controle: IA');
    // O texto sai do <Screen> só depois que o frame chega: no runner do CI isso passa da primeira leitura (B13).
    await waitFor(() => expect(text(panel)).toContain('Somente visualização'));

    const box = panel.querySelector('[role="img"][aria-label^="Tela ao vivo"]') as HTMLElement;
    const rect = { left: 0, top: 0, width: 1000, height: 1200, right: 1000, bottom: 1200, x: 0, y: 0, toJSON: () => ({}) };
    box.getBoundingClientRect = () => rect as DOMRect; // imagem contida ocupa x ∈ [230, 770]

    // sem o controle, clicar na tela não envia nada
    await pointer(box, 'pointerdown', 500, 600);
    await pointer(box, 'pointerup', 500, 600);
    expect(backend.callsTo('POST', /input$/)).toHaveLength(0);

    await click(byRole('button', /^Assumir controle/, panel));
    await waitFor(() => expect(text(panel)).toContain('Controle: Você'));
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(120, 'control.changed', { instance_id: 'android-01', control: 'user', pending: false }, { instance_id: 'android-01' }) }));

    // toque: usa o X-Frame-Id do frame EXIBIDO e as dimensões do FrameInfo
    await pointer(box, 'pointerdown', 500, 600);
    await pointer(box, 'pointerup', 500, 600);
    await waitFor(() => expect(backend.callsTo('POST', /input$/)).toHaveLength(1));
    const shownFrameId = `full-${frameSeq}`;
    expect(backend.callsTo('POST', /input$/)[0]?.body).toEqual({ type: 'tap', x: 540, y: 1200, lease_id: 'lease-1', frame_id: shownFrameId });

    // margem (pillarbox) é rejeitada
    await pointer(box, 'pointerdown', 100, 600);
    await pointer(box, 'pointerup', 100, 600);
    await flush(20);
    expect(backend.callsTo('POST', /input$/)).toHaveLength(1);

    // arrastar = swipe com duration_ms
    await pointer(box, 'pointerdown', 500, 1000);
    await pointer(box, 'pointermove', 500, 700);
    await pointer(box, 'pointerup', 500, 400);
    await waitFor(() => expect(backend.callsTo('POST', /input$/)).toHaveLength(2));
    expect(backend.callsTo('POST', /input$/)[1]?.body).toMatchObject({ type: 'swipe', x: 540, y: 2000, x2: 540, y2: 800, duration_ms: expect.any(Number) });

    // tecla do Android e texto
    await click(byRole('button', 'Voltar', panel));
    await waitFor(() => expect(backend.callsTo('POST', /input$/)).toHaveLength(3));
    expect(backend.callsTo('POST', /input$/)[2]?.body).toMatchObject({ type: 'key', key: 'back', lease_id: 'lease-1' });

    // 409 stale_frame: explica e busca um frame novo
    const framesBefore = backend.callsTo('GET', /android-01\/frame$/).length;
    backend.on('POST', /^\/api\/instances\/[^/]+\/input$/, () => apiError(409, 'stale_frame', 'Frame antigo'));
    await pointer(box, 'pointerdown', 500, 600);
    await pointer(box, 'pointerup', 500, 600);
    await waitFor(() => expect(text()).toContain('A tela mudou — aguarde o novo frame e tente de novo'));
    await waitFor(() => expect(backend.callsTo('GET', /android-01\/frame$/).length).toBeGreaterThan(framesBefore));

    await click(byRole('button', /^Devolver à IA/, panel));
    await waitFor(() => expect(backend.callsTo('POST', /release$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /release$/)[0]?.body).toEqual({ lease_id: 'lease-1' });

    await click(byRole('button', /^Fechar/, panel));
    await waitFor(() => expect(allByRole('dialog', /Visão de foco/)).toHaveLength(0));
    await waitFor(() => expect(ws.sent[ws.sent.length - 1]).toMatchObject({ type: 'watch', focus: null }));
  });

  it('foco: pedido de controle "pending" espera a IA e só libera a interação após control.changed', async () => {
    const ws = FakeWebSocket.last;
    backend.on('POST', /^\/api\/instances\/android-02\/control\/take$/, () => json({ status: 'pending', lease_id: 'lease-2' }));
    backend.on('POST', /^\/api\/instances\/[^/]+\/input$/, () => json({ ok: true }));
    const inputsBefore = backend.callsTo('POST', /android-02\/input$/).length;

    await click(byRole('button', 'Abrir android-02 na visão de foco'));
    const panel = await waitFor(() => byRole('dialog', /Visão de foco: android-02/));
    // frame.stale continua sinalizado na visão de foco; o aviso só existe depois que o frame chega (B13).
    await waitFor(() => expect(text(panel)).toContain('Desatualizado'));
    await click(byRole('button', /^Assumir controle/, panel));
    await waitFor(() => expect(text(panel)).toContain('Aguardando a IA concluir a ação atual…'));

    // enquanto pendente, o controle manual nem aparece (só o motivo, uma vez) e nada chega ao aparelho
    expect(allByRole('button', /^Voltar/, panel)).toHaveLength(0);
    expect(panel.querySelector('input[aria-label="Texto para digitar no aparelho"]')).toBeNull();
    expect(text(panel)).toContain('Pedido de controle enviado: aguardando a IA concluir a ação atual.');
    expect(backend.callsTo('POST', /android-02\/input$/)).toHaveLength(inputsBefore);

    // o evento não traz lease: vale o lease_id guardado da resposta "pending"
    await act(async () => ws.serverSend({ type: 'event', event: makeEvent(130, 'control.changed', { instance_id: 'android-02', control: 'user', pending: false }, { instance_id: 'android-02' }) }));
    await waitFor(() => expect(text(panel)).toContain('Controle: Você'));
    await click(byRole('button', /^Início/, panel));
    await waitFor(() => expect(backend.callsTo('POST', /android-02\/input$/)).toHaveLength(inputsBefore + 1));
    expect(backend.callsTo('POST', /android-02\/input$/).at(-1)?.body).toMatchObject({ type: 'key', key: 'home', lease_id: 'lease-2' });

    // not_controller: explica e derruba o lease local
    backend.on('POST', /^\/api\/instances\/android-02\/input$/, () => apiError(409, 'not_controller', 'O controle voltou para a IA'));
    await click(byRole('button', /^Início/, panel));
    await waitFor(() => expect(text()).toContain('Você não está mais com o controle deste aparelho'));
    // sem o lease, a barra do controle manual sai da tela: não há tecla "desabilitada" para clicar à toa
    await waitFor(() => expect(allByRole('button', /^Início/, panel)).toHaveLength(0));

    await click(byRole('button', /^Fechar/, panel));
    await waitFor(() => expect(allByRole('dialog', /Visão de foco/)).toHaveLength(0));
  });

  it('reconexão: marca os dados como possivelmente desatualizados, refaz snapshot + WS e não cria execuções', async () => {
    const postsBefore = runPosts().length;
    const socketsBefore = FakeWebSocket.instances.length;
    snapshotLastEventId = 250;
    await act(async () => FakeWebSocket.last.serverClose());
    await waitFor(() => expect(text()).toContain('Reconectando'));
    expect(text()).toContain('podem estar desatualizados');

    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(socketsBefore + 1), 5000);
    expect(snapshotCalls()).toBe(2);
    expect(FakeWebSocket.last.url).toMatch(/last_event_id=250$/);
    await act(async () => {
      FakeWebSocket.last.serverOpen();
      FakeWebSocket.last.serverSend({ type: 'hello', server_time: new Date().toISOString(), last_event_id: 250 });
    });
    await waitFor(() => expect(text()).not.toContain('podem estar desatualizados'));
    expect(runPosts()).toHaveLength(postsBefore);
  }, 10_000);

  it('resync: refaz o snapshot e abre um WS novo', async () => {
    const socketsBefore = FakeWebSocket.instances.length;
    const snapsBefore = snapshotCalls();
    await act(async () => FakeWebSocket.last.serverSend({ type: 'resync' }));
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(socketsBefore + 1));
    expect(snapshotCalls()).toBe(snapsBefore + 1);
  });

  it('Configuração, Diagnóstico e Execuções renderizam com os dados do backend', async () => {
    await goTo('#/configuracao');
    await waitFor(() => expect(text()).toContain('Novo aplicativo'));
    expect(text()).toContain('com.poc.qamessenger');
    expect(text()).toContain('embutido');
    const builtinDelete = allByRole('button', /^Excluir/)[0] as HTMLElement;
    expect(builtinDelete.getAttribute('aria-disabled')).toBe('true'); // app embutido não pode ser excluído

    await click(byRole('tab', /^Instâncias e contas/));
    await waitFor(() => expect(text()).toContain('Aplicar app a todas'));
    await click(byRole('tab', /^IA$/));
    await waitFor(() => expect(text()).toContain('nunca no navegador'));
    // Limites → Por servidor: um cartão por máquina, cada um salva sozinho.
    const servidor = (id: string, host: boolean, slots: number) => ({
      worker_id: id, name: host ? `${id} (este servidor)` : 'Notebook da LAN', is_host: host, connected: true,
      maintenance: false, declared: { max_slots: slots, boot_parallelism: 1, max_working: null, min_free_ram_mb: 4096 },
      decided: { max_slots: null, boot_parallelism: null, max_working: null, min_free_ram_mb: null },
      effective: { max_slots: slots, boot_parallelism: 1, max_working: null, min_free_ram_mb: 4096 },
      locked: host ? { min_free_ram_mb: 'Guarda do boot deste servidor.' } : {}, online: 2, working: 0, devices: 3,
      cpu_percent: 20, cpu_count: 12, ram_free_mb: 40_000, ram_total_mb: 64_000,
    });
    backend.on('GET', /^\/api\/servers\/limits$/, () => json([servidor('central', true, 3), servidor('worker-lan-01', false, 6)]));
    backend.on('PUT', /^\/api\/servers\/worker-lan-01\/limits$/, (c) => {
      const base = servidor('worker-lan-01', false, 6);
      const body = c.body as Record<string, number>;
      return json({ ...base, decided: { ...base.decided, ...body }, effective: { ...base.effective, ...body } });
    });
    await click(byRole('tab', /^Limites/));
    await waitFor(() => expect(text()).toContain('Teto geral de aparelhos trabalhando'));
    await waitFor(() => expect(text()).toContain('Notebook da LAN'));
    const cartao = byRole('group', 'Limites de Notebook da LAN');
    const trabalhando = Array.from(cartao.querySelectorAll('input'))[2] as HTMLInputElement;
    await setValue(trabalhando, '3');
    const salvarCartao = Array.from(cartao.querySelectorAll('button')).find((b) => b.textContent?.includes('Salvar')) as HTMLElement;
    await click(salvarCartao);
    await waitFor(() => expect(backend.callsTo('PUT', /servers\/worker-lan-01\/limits$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /servers\/worker-lan-01\/limits$/)[0]?.body).toEqual({ max_working: 3 });
    await waitFor(() => expect(text()).toContain('Limites de Notebook da LAN salvos'));

    // --- Limites: valida no cliente e envia só o que mudou ---
    backend.on('PUT', /^\/api\/settings$/, (c) => json({ ...makeSnapshot().settings, ...(c.body as object) }));
    const maxDevices = byRole('textbox', 'Teto geral de aparelhos trabalhando') as HTMLInputElement;
    // 64, não 10: o teto de 10 era do CÓDIGO e apertava sozinho com 14 aparelhos de tarefa mais um segundo
    // worker (item 4.2). Quem limita de verdade passou a ser a vaga de cada máquina — `max_online_devices` aqui,
    // `max_slots` de cada worker —; este número é só o teto de validação do campo. O que o teste guarda é a
    // recusa no cliente antes de enviar, não o valor.
    await setValue(maxDevices, '99');
    await waitFor(() => expect(text()).toContain('O máximo é 64.'));
    await click(byRole('button', /^Salvar limites/));
    expect(backend.callsTo('PUT', /settings$/)).toHaveLength(0);
    await setValue(maxDevices, '6');
    await click(byRole('button', /^Salvar limites/));
    await waitFor(() => expect(backend.callsTo('PUT', /settings$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /settings$/)[0]?.body).toEqual({ max_active_devices: 6 });
    await waitFor(() => expect(text()).toContain('Limites salvos'));

    // --- Rodízio de aparelhos: o interruptor vai no PUT do parque; as vagas são do cartão de cada servidor ---
    expect(text()).toContain('Rodízio de aparelhos');
    expect(text()).toContain('0 = só desliga para ceder vaga');
    expect(text()).toContain('hibernação ligada');
    expect(text()).toContain('system-images;android-35;google_apis;x86_64');
    const autoStart = Array.from(document.querySelectorAll('label')).find((l) => l.textContent === 'Ligar aparelhos sob demanda')?.querySelector('input') as HTMLInputElement;
    expect(autoStart.checked).toBe(false);
    await click(autoStart);
    await click(byRole('button', /^Salvar limites/));
    await waitFor(() => expect(backend.callsTo('PUT', /settings$/)).toHaveLength(2));
    expect(backend.callsTo('PUT', /settings$/)[1]?.body).toEqual({ auto_start_devices: true });

    // --- Prévia dos aparelhos (v0.20): sob demanda é o padrão; "sempre" volta ao laço antigo sem reiniciar ---
    const preview = byRole('combobox', 'Prévia dos aparelhos') as HTMLSelectElement;
    expect(preview.value).toBe('on_demand');
    expect(Array.from(preview.options).map((o) => o.textContent)).toEqual(['Sob demanda (padrão)', 'Sempre (modo antigo)']);
    await setValue(preview, 'always');
    await click(byRole('button', /^Salvar limites/));
    await waitFor(() => expect(backend.callsTo('PUT', /settings$/)).toHaveLength(3));
    expect(backend.callsTo('PUT', /settings$/)[2]?.body).toEqual({ preview_mode: 'always' });

    // --- Fluxos e receitas ---
    await click(byRole('tab', /^Fluxos e receitas/));
    await waitFor(() => expect(text()).toContain('Enviar mensagem de teste'));
    const panel = byRole('tabpanel', /.*/);
    const marks = Array.from(panel.querySelectorAll('mark')).map((m) => m.textContent);
    expect(marks).toEqual(['{recipient}', '{message_template}']); // marcadores destacados no comando-modelo
    expect(text(panel)).toContain('7 usos');
    expect(text(panel)).toContain('último uso: nunca');
    expect(text(panel)).toContain('QA Messenger'); // app da receita resolvido pelo pacote
    expect(text(panel)).toContain('1.4.2');
    expect(text(panel)).toContain('Acertos: 24 / falhas: 1');
    expect(text(panel)).toContain('12/15 (80%)'); // concordância em modo sombra só onde total > 0
    expect(text(panel)).toContain('Quarentena');
    expect(text(panel)).toContain('Substituída');

    const flowSwitch = byRole('switch', 'Fluxo “Enviar mensagem de teste” ativo', panel);
    expect(flowSwitch.getAttribute('aria-checked')).toBe('true');
    await click(flowSwitch);
    await waitFor(() => expect(backend.callsTo('PUT', /flows\/enviar-mensagem$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /flows\//)[0]?.body).toEqual({ status: 'disabled' });
    await waitFor(() => expect(byRole('switch', 'Fluxo “Enviar mensagem de teste” ativo', panel).getAttribute('aria-checked')).toBe('false'));

    await click(byRole('button', 'Pôr em quarentena a receita send v2', panel));
    await waitFor(() => expect(backend.callsTo('PUT', /recipes\/11$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /recipes\/11$/)[0]?.body).toEqual({ status: 'quarantined' });
    await waitFor(() => expect(allByRole('button', 'Reativar a receita send v2', panel)).toHaveLength(1));
    await click(byRole('button', 'Reativar a receita open_app v1', panel));
    await waitFor(() => expect(backend.callsTo('PUT', /recipes\/12$/)[0]?.body).toEqual({ status: 'active' }));
    // receita substituída não pode ser reativada
    const superseded = allByRole('button', /^Reativar/, panel).find((b) => b.getAttribute('aria-disabled') === 'true');
    expect(superseded).toBeDefined();

    // excluir pede confirmação antes do DELETE
    await click(byRole('button', 'Excluir o fluxo Abrir Configurações', panel));
    const confirmFlow = await waitFor(() => byRole('dialog', /Excluir o fluxo/));
    expect(backend.callsTo('DELETE', /flows/)).toHaveLength(0);
    await click(byRole('button', 'Excluir fluxo', confirmFlow));
    await waitFor(() => expect(backend.callsTo('DELETE', /flows\/abrir-config$/)).toHaveLength(1));
    await waitFor(() => expect(text(panel)).not.toContain('Abrir Configurações'));

    await click(byRole('button', 'Excluir a receita open_app v1', panel));
    const confirmRecipe = await waitFor(() => byRole('dialog', /Excluir a receita/));
    await click(byRole('button', 'Excluir receita', confirmRecipe));
    await waitFor(() => expect(backend.callsTo('DELETE', /recipes\/12$/)).toHaveLength(1));
    await waitFor(() => expect(text(panel)).not.toContain('com.exemplo.desconhecido'));

    // listas vazias explicam o que são
    backend.on('GET', /^\/api\/flows$/, () => json([]));
    backend.on('GET', /^\/api\/recipes$/, () => json([]));
    await click(byRole('button', /^Atualizar/, panel));
    await waitFor(() => expect(text(panel)).toContain('Nenhum fluxo salvo ainda'));
    expect(text(panel)).toContain('Nenhuma receita aprendida ainda');
    expect(text(panel)).toContain('A IA aprende o caminho uma vez; as próximas execuções repetem por seletores, sem custo de modelo. Se a tela mudar, a IA assume só aquela etapa.');

    // --- Instâncias e contas: PUT só com o campo alterado ---
    backend.on('PUT', /^\/api\/instances\/android-07$/, (c) => json(makeInstance(7, c.body as object)));
    await click(byRole('tab', /^Instâncias e contas/));
    // Item 11.9: os campos ficam num painel que abre ao clicar no cartão — não mais numa linha de tabela sempre aberta.
    await click(byRole('button', /Editar android-07/));
    await setValue(byRole('textbox', 'Rótulo da conta') as HTMLInputElement, 'qa-novo-07');
    await click(byRole('button', /^Fechar$/));
    await click(byRole('button', /^Salvar alterações \(1\)/));
    await waitFor(() => expect(backend.callsTo('PUT', /android-07$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /android-07$/)[0]?.body).toEqual({ account_label: 'qa-novo-07' });

    // --- Aplicativos: valida e cria ---
    backend.on('POST', /^\/api\/apps$/, (c) => json({ id: 'novo', builtin: false, ...(c.body as object) }));
    await click(byRole('tab', /^Aplicativos/));
    await click(byRole('button', /^Novo aplicativo/));
    const editor = await waitFor(() => byRole('dialog', /Novo aplicativo/));
    await click(byRole('button', /^Salvar/, editor));
    await waitFor(() => expect(text(editor)).toContain('Dê um nome ao aplicativo.'));
    await setValue(byRole('textbox', 'Nome', editor) as HTMLInputElement, 'Loja');
    await setValue(byRole('textbox', 'Pacote Android', editor) as HTMLInputElement, 'com.poc.loja');
    await click(byRole('button', /^Salvar/, editor));
    await waitFor(() => expect(backend.callsTo('POST', /apps$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /apps$/)[0]?.body).toEqual({
      name: 'Loja', package: 'com.poc.loja', activity: null, apk_path: null, nav_hints: null, known_selectors: null,
    });
    await waitFor(() => expect(text()).toContain('com.poc.loja'));

    await goTo('#/diagnostico');
    await waitFor(() => expect(text()).toContain('Reexecutar diagnóstico'));
    // 11.7: os detalhes técnicos nascem recolhidos (problemas e azulejos vêm primeiro); abre como a pessoa abriria
    for (const id of ['diag-ferramentas', 'diag-outros']) {
      await waitFor(() => { if (!document.getElementById(id)) throw new Error(`seção ${id} ainda não apareceu`); });
      await act(async () => {
        const d = document.getElementById(id) as HTMLDetailsElement;
        d.open = true;
        d.dispatchEvent(new Event('toggle'));
      });
    }
    await waitFor(() => expect(text()).toContain('adb.exe'));
    expect(text()).toContain('Ausente'); // appium.found = false
    expect(text()).toContain('Surprise field'); // chave desconhecida cai na árvore genérica
    // custo de IA dos últimos 7 dias — modo simulado: sem preço, mas com a fatia de etapas por receita
    const usage = await waitFor(() => {
      const el = document.querySelector('[aria-label="Custo de IA — últimos 7 dias"]') as HTMLElement | null;
      if (!el || !text(el).includes('Etapas por receita')) throw new Error('resumo de custo ainda carregando');
      return el;
    });
    expect(backend.callsTo('GET', /usage$/).some((c) => c.query.get('days') === '7' && !c.query.has('run_id'))).toBe(true);
    expect(text(usage)).toContain('US$ total');
    expect(text(usage)).toContain('US$ por aparelho-comando');
    expect(text(usage)).toContain('sem preço');
    expect(text(usage)).not.toContain('US$ 0,00');
    expect(text(usage)).toContain('50% (6 de 12)');

    await goTo('#/execucoes');
    await waitFor(() => expect(text()).toContain('Recentes'));
    expect(text()).toContain('r-0001');

    await goTo('#/painel');
    await waitFor(() => expect(text()).toContain('Aparelhos'));
  });
});
