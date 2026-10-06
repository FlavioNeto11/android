// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { ComandoRemoto, ComandoRemotoInterruptor, Worker } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import {
  FakeBackend, apiError, byRole, click, installBrowserStubs, json, openDetails, setValue, text, waitFor,
} from '../../test/harness';
import { TerminalDoWorker, motivoDeNaoExecutar, textoDoErro } from './TerminalDoWorker';
import { ApiError } from '../../api/client';

// 29.154, fatia 2: o terminal do worker na Infraestrutura. Prova SIMULADA (backend falso); o agente real é `not_run`.

const WORKER = {
  id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [], state: 'online',
  observed_state: 'online', maintenance: false, state_detail: null, connected: true, local: false,
  resources: {}, devices: [], enrolled_at: '2026-10-06T10:00:00Z', last_seen_at: '2026-10-06T10:00:00Z',
} as unknown as Worker;

function interruptor(over: Partial<ComandoRemotoInterruptor> = {}): ComandoRemotoInterruptor {
  return {
    worker_id: 'worker-lan-01', central_ativo: true, worker_ligado: true, agente_anuncia: true, negociado: true,
    e_o_central: false, ...over,
  };
}

function comando(over: Partial<ComandoRemoto> = {}): ComandoRemoto {
  return {
    id: 'exec-0001', worker_id: 'worker-lan-01', requested_by: 'Flavio', modo: 'linha', linha: 'Get-Date', pasta: null,
    timeout_s: 60, state: 'succeeded', exit_code: 0, truncated: false, duration_ms: 420, reason: null,
    created_at: '2026-10-06T15:00:00Z', dispatched_at: '2026-10-06T15:00:00Z', finished_at: '2026-10-06T15:00:01Z',
    ...over,
  };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function abrir(estado: ComandoRemotoInterruptor, historico: ComandoRemoto[] = []): Promise<void> {
  backend.on('GET', /\/workers\/worker-lan-01\/comando-remoto$/, () => json(estado));
  backend.on('GET', /\/workers\/worker-lan-01\/comandos$/, () => json({ items: historico }));
  await act(async () => {
    root.render(<><TerminalDoWorker worker={WORKER} /><ConfirmHost /></>);
  });
  await openDetails(/Comando remoto/);
  await waitFor(() => text().includes('canal '));
}

describe('o que impede o comando', () => {
  it('diz o primeiro interruptor desligado, na ordem central, worker, agente', () => {
    expect(motivoDeNaoExecutar(interruptor())).toBeNull();
    expect(motivoDeNaoExecutar(interruptor({ negociado: false, central_ativo: false, worker_ligado: false })))
      .toContain('comando_remoto.ativo');
    expect(motivoDeNaoExecutar(interruptor({ negociado: false, worker_ligado: false }))).toContain('interruptor deste worker');
    expect(motivoDeNaoExecutar(interruptor({ negociado: false, agente_anuncia: false }))).toContain('worker.yaml');
    expect(motivoDeNaoExecutar(interruptor({ negociado: false, e_o_central: true }))).toContain('central');
  });

  it('traduz as recusas do central em texto de gente', () => {
    expect(textoDoErro(new ApiError(401, 'sem_operador', 'x'))).toContain('usuário nomeado');
    expect(textoDoErro(new ApiError(422, 'linha_com_credencial', 'x'))).toContain('credencial');
    expect(textoDoErro(new ApiError(429, 'fila_cheia', 'x'))).toContain('fila');
    expect(textoDoErro(new ApiError(409, 'qualquer', 'texto do backend'))).toBe('texto do backend');
  });
});

describe('o terminal do worker', () => {
  it('nada é consultado antes de abrir, e desligado mostra o motivo e não deixa executar', async () => {
    backend.on('GET', /\/comando-remoto$/, () => json(interruptor({ negociado: false, worker_ligado: false })));
    backend.on('GET', /\/comandos$/, () => json({ items: [] }));
    await act(async () => { root.render(<TerminalDoWorker worker={WORKER} />); });
    expect(backend.calls).toHaveLength(0);

    await openDetails(/Comando remoto/);
    await waitFor(() => text().includes('worker desligado'));
    expect(text()).toContain('O interruptor deste worker está desligado');
    await setValue(document.querySelector('input[placeholder="Get-Date"]') as HTMLInputElement, 'Get-Date');
    await click(byRole('button', /^Executar — indisponível/));
    expect(backend.callsTo('POST', /comandos/)).toHaveLength(0);
  });

  it('executa, mostra a saída e avisa que o segredo é mascarado', async () => {
    backend.on('POST', /\/comandos$/, () => json({ id: 'exec-0001', worker_id: 'worker-lan-01', state: 'created', created_at: '2026-10-06T15:00:00Z' }, 202));
    backend.on('GET', /\/comandos\/exec-0001$/, () => json(comando({ stdout: 'terça-feira, 6 de outubro\n', stderr: '' })));
    await abrir(interruptor());

    await setValue(document.querySelector('input[placeholder="Get-Date"]') as HTMLInputElement, '  Get-Date  ');
    await click(byRole('button', /^Executar$/));
    await waitFor(() => backend.callsTo('POST', /\/comandos$/).length === 1);
    const pedido = backend.callsTo('POST', /\/comandos$/)[0]!.body as Record<string, unknown>;
    expect(pedido.linha).toBe('Get-Date');                          // aparado
    expect(pedido.timeout_s).toBe(60);
    expect(String(pedido.idempotency_key)).toMatch(/^painel-/);
    expect('pasta' in pedido).toBe(false);                          // vazia não vai

    await waitFor(() => text().includes('terça-feira, 6 de outubro'), 6000);   // consulta de 2 em 2 s
    expect(text()).toContain('código de saída 0');
    expect(text()).toContain('mascarados');
  });

  it('linha com credencial: a recusa vira texto e nenhuma consulta de saída começa', async () => {
    backend.on('POST', /\/comandos$/, () => apiError(422, 'linha_com_credencial', 'recusada'));
    await abrir(interruptor());

    await setValue(document.querySelector('input[placeholder="Get-Date"]') as HTMLInputElement, 'x');
    await click(byRole('button', /^Executar$/));
    await waitFor(() => text().includes('parece conter uma credencial'));
    expect(backend.callsTo('GET', /\/comandos\/exec/)).toHaveLength(0);
  });

  it('prazo fora de 1 a 600 s é barrado no formulário', async () => {
    await abrir(interruptor());
    await setValue(document.querySelector('input[placeholder="Get-Date"]') as HTMLInputElement, 'dir');
    await setValue(document.querySelector('input[type="number"]') as HTMLInputElement, '601');
    await click(byRole('button', /^Executar$/));
    await waitFor(() => text().includes('de 1 a 600 segundos'));
    expect(backend.callsTo('POST', /\/comandos$/)).toHaveLength(0);
  });

  it('cancela o comando em curso', async () => {
    backend.on('POST', /\/comandos$/, () => json({ id: 'exec-0001', worker_id: 'worker-lan-01', state: 'running', created_at: '2026-10-06T15:00:00Z' }, 202));
    backend.on('POST', /\/comandos\/exec-0001\/cancelar$/, () => json(comando({ state: 'cancelled', exit_code: null })));
    await abrir(interruptor());

    await setValue(document.querySelector('input[placeholder="Get-Date"]') as HTMLInputElement, 'Start-Sleep 50');
    await click(byRole('button', /^Executar$/));
    await waitFor(() => text().includes('rodando'));
    await click(byRole('button', /^Cancelar$/));
    await waitFor(() => text().includes('cancelado'));
    expect(backend.callsTo('POST', /cancelar$/)).toHaveLength(1);
  });

  it('o interruptor do worker pede confirmação para ligar e só então chama o PUT', async () => {
    backend.on('PUT', /\/comando-remoto$/, () => json(interruptor()));
    await abrir(interruptor({ negociado: false, worker_ligado: false }));

    await click(byRole('button', /Ligar neste worker/));
    await waitFor(() => text().includes('Ligar o comando remoto em'));
    expect(backend.callsTo('PUT', /comando-remoto/)).toHaveLength(0);
    await click(byRole('button', /^Ligar$/, byRole('dialog', /.+/)));
    await waitFor(() => backend.callsTo('PUT', /comando-remoto/).length === 1);
    expect(backend.callsTo('PUT', /comando-remoto/)[0]!.body).toEqual({ ligado: true });
  });

  it('o histórico lista os comandos e abrir um mostra a saída cortada com o aviso', async () => {
    backend.on('GET', /\/comandos\/exec-0002$/, () => json(comando({
      id: 'exec-0002', linha: 'Get-Process', stdout: 'começo… [cortados] …fim', truncated: true, state: 'failed', exit_code: 1,
    })));
    await abrir(interruptor(), [comando(), comando({ id: 'exec-0002', linha: 'Get-Process', state: 'failed', exit_code: 1 })]);

    expect(text()).toContain('Get-Date');
    expect(text()).toContain('Flavio');
    await click(byRole('button', /Abrir o comando Get-Process/));
    await waitFor(() => text().includes('começo… [cortados] …fim'));
    expect(text()).toContain('Saída cortada');
    expect(text()).toContain('código de saída 1');
  });

  it('sem sessão nomeada: diz o que fazer em vez de mostrar o código do backend', async () => {
    backend.on('GET', /\/comando-remoto$/, () => apiError(401, 'sem_operador', 'sem operador'));
    backend.on('GET', /\/comandos$/, () => apiError(401, 'sem_operador', 'sem operador'));
    await act(async () => { root.render(<TerminalDoWorker worker={WORKER} />); });
    await openDetails(/Comando remoto/);
    await waitFor(() => text().includes('Entre com o seu usuário nomeado'));
  });

  it('comando incerto avisa que o central não repete', async () => {
    backend.on('GET', /\/comandos\/exec-0003$/, () => json(comando({ id: 'exec-0003', state: 'uncertain', exit_code: null })));
    await abrir(interruptor(), [comando({ id: 'exec-0003', state: 'uncertain', exit_code: null })]);
    await click(byRole('button', /Abrir o comando Get-Date/));
    await waitFor(() => text().includes('não repete'));
  });
});

// 29.164: o estado do canal, a recusa e o que fazer a seguir, em português; nada de código nem de marcação crua na tela.
describe('29.164: o cartão em português', () => {
  const CRUS = /(timed_out|succeeded|dispatched|uncertain|rejected|cancelled|worker_sem_remote_exec|comando_remoto_desligado|fila_cheia|negociado|`)/;

  it('cada estado do canal diz em palavras o que está certo, o que falta e o que fazer, sem crase nem código', async () => {
    const casos: [Partial<ComandoRemotoInterruptor>, RegExp][] = [
      [{ negociado: true }, /canal pronto/],
      [{ negociado: false, central_ativo: false, worker_ligado: false, agente_anuncia: false }, /desligado no central[\s\S]*reinicia a tarefa farm-central/],
      [{ negociado: false, worker_ligado: false }, /use "Ligar neste worker"/],
      [{ negociado: false, agente_anuncia: false }, /não oferece o comando remoto[\s\S]*reinicie o agente/],
      [{ negociado: false }, /canal com o agente ainda não ficou pronto/],
    ];
    for (const [estado, esperado] of casos) {
      await act(async () => root.unmount());
      container.remove();
      backend = new FakeBackend();
      backend.install();
      container = document.createElement('div');
      document.body.append(container);
      root = createRoot(container);
      await abrir(interruptor(estado));
      const visivel = text();
      if (estado.negociado) expect(visivel).toMatch(esperado);
      else expect(`${visivel} ${motivoDeNaoExecutar(interruptor(estado))}`).toMatch(esperado);
      expect(visivel).not.toMatch(CRUS);
      expect(visivel).toContain('Não é um terminal');
    }
  });

  it.each([
    ['failed', /terminou com erro[\s\S]*execute de novo/, /^falhou$/],
    ['timed_out', /passou do prazo[\s\S]*aumente o prazo/, /estourou o prazo/],
    ['cancelled', /cancelado antes de terminar[\s\S]*Confira lá/, /cancelado/],
    ['rejected', /recusou o comando e nada rodou[\s\S]*peça de novo/, /recusado/],
  ] as const)('o estado %s mostra o rótulo em português e o que fazer, sem o código', async (estado, passo, rotulo) => {
    const c = comando({ id: 'exec-9', state: estado, exit_code: null, reason: estado === 'rejected' ? 'o central reiniciou antes de despachar' : null });
    backend.on('GET', /\/comandos\/exec-9$/, () => json(c));
    await abrir(interruptor(), [c]);
    await click(byRole('button', /Abrir o comando Get-Date/));
    await waitFor(() => passo.test(text()));
    expect(text()).not.toContain(estado);
    expect(Array.from(container.querySelectorAll('span,div')).some((e) => !e.children.length && rotulo.test(e.textContent ?? ''))).toBe(true);
    if (estado === 'rejected') expect(text()).toContain('o central reiniciou antes de despachar');
  });

  it('o que roda bem não ganha passo a seguir, e as recusas do central falam em português', async () => {
    const c = comando({ id: 'exec-1' });
    backend.on('GET', /\/comandos\/exec-1$/, () => json(c));
    await abrir(interruptor(), [c]);
    await click(byRole('button', /Abrir o comando Get-Date/));
    await waitFor(() => text().includes('código de saída 0'));
    expect(text()).not.toContain('corrija a linha');
    for (const codigo of ['sem_operador', 'linha_com_credencial', 'comando_remoto_desligado', 'worker_sem_remote_exec', 'limite_por_minuto', 'fila_cheia', 'central_fora']) {
      expect(textoDoErro(new ApiError(409, codigo, 'texto cru do backend'))).not.toMatch(/texto cru|_/);
    }
  });
});
