import { act } from 'react';
import { expect, onTestFailed, vi } from 'vitest';
import { esquecerLeituraDosPendentes } from '../features/aprendizado/api';

/** Backend falso: responde às rotas do contrato e registra tudo o que o frontend pediu. */
export interface RecordedCall {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

export type Handler = (call: RecordedCall) => Response | Promise<Response>;

export function json(data: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

export function apiError(status: number, code: string, message: string): Response {
  return json({ detail: { code, message } }, status);
}

/**
 * Modo do harness que acha o teste que corre contra a tela de antes (item 29.104): com `ATRASO_DO_FETCH_MS=N`, cada
 * resposta do backend falso demora de 0 a N ms, como no runner carregado do CI. Desligado por padrão. O sorteio é
 * semeado (`SEMENTE_DO_ATRASO`, ou a hora se faltar) e, dentro de um teste, depende só da semente e do nome dele: o
 * teste que falhar diz a semente, e a rodada se repete com ela.
 */
const ATRASO_MAXIMO_MS = Number(process.env.ATRASO_DO_FETCH_MS ?? 0);
// Cada `install()` abre uma geração. Com o atraso, a resposta de uma geração já substituída nunca chega: o teste que
// acabou com pedido em voo não escreve na store global do teste seguinte, como uma página fechada (29.104).
let geracaoDoBackend = 0;
const SEMENTE_DO_ATRASO = process.env.SEMENTE_DO_ATRASO ?? String(Date.now());

function sorteioDoAtraso(): (() => number) | null {
  if (!(ATRASO_MAXIMO_MS > 0)) return null;
  const teste = expect.getState().currentTestName ?? '';
  try {
    onTestFailed(() => {
      console.warn(`[harness] falhou com o fetch falso atrasado: ATRASO_DO_FETCH_MS=${ATRASO_MAXIMO_MS} SEMENTE_DO_ATRASO=${SEMENTE_DO_ATRASO} (${teste})`);
    });
  } catch {
    console.warn(`[harness] fetch falso atrasado fora de um teste: SEMENTE_DO_ATRASO=${SEMENTE_DO_ATRASO}`);
  }
  // FNV-1a da semente com o nome do teste, e o mulberry32 por cima: a sequência não depende de quais testes rodaram antes.
  let h = 2166136261;
  for (const c of `${SEMENTE_DO_ATRASO}|${teste}`) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  let a = h >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return Math.floor((((t ^ (t >>> 14)) >>> 0) / 4294967296) * (ATRASO_MAXIMO_MS + 1));
  };
}

export class FakeBackend {
  calls: RecordedCall[] = [];
  private handlers: { method: string; pattern: RegExp; handler: Handler }[] = [];

  /** Rotas registradas por último têm prioridade (permite sobrescrever o padrão em um teste). */
  on(method: string, pattern: RegExp, handler: Handler): this {
    this.handlers.unshift({ method, pattern, handler });
    return this;
  }

  callsTo(method: string, pattern: RegExp): RecordedCall[] {
    return this.calls.filter((c) => c.method === method && pattern.test(c.path));
  }

  install(): void {
    esquecerLeituraDosPendentes();          // a leitura dividida da fila não atravessa testes
    const atraso = sorteioDoAtraso();
    const geracao = ++geracaoDoBackend;
    const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://localhost');
      const method = (init?.method ?? 'GET').toUpperCase();
      let body: unknown = undefined;
      if (typeof init?.body === 'string') {
        try {
          body = JSON.parse(init.body);
        } catch {
          body = init.body;
        }
      }
      const call: RecordedCall = { method, path: url.pathname, query: url.searchParams, body };
      this.calls.push(call);
      const ms = atraso?.();
      if (ms) {
        await new Promise((r) => setTimeout(r, ms));
        if (geracao !== geracaoDoBackend) return new Promise<Response>(() => undefined);
      }
      const match = this.handlers.find((h) => h.method === method && h.pattern.test(url.pathname));
      if (!match) return apiError(404, 'not_found', `Rota não simulada: ${method} ${url.pathname}`);
      return match.handler(call);
    };
    vi.stubGlobal('fetch', vi.fn(fetchImpl));
  }
}

/** WebSocket falso: o teste faz o papel do servidor. */
export class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = FakeWebSocket.CONNECTING;
  sent: unknown[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  static get last(): FakeWebSocket {
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    if (!ws) throw new Error('Nenhum WebSocket foi aberto');
    return ws;
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }

  // ---- lado "servidor" ----
  serverOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({});
  }

  serverSend(msg: unknown): void {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }

  serverClose(code = 1006, reason = ''): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
}

export function installBrowserStubs(): void {
  vi.stubGlobal('WebSocket', FakeWebSocket);
  let blobSeq = 0;
  URL.createObjectURL = () => `blob:fake-${++blobSeq}`;
  URL.revokeObjectURL = () => undefined;
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
}

export async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
}

/** Repete a verificação até passar (ou estourar o tempo), deixando o React e as promises andarem. */
export async function waitFor<T>(check: () => T, timeoutMs = 4000): Promise<T> {
  const start = Date.now();
  let lastError: unknown;
  for (;;) {
    try {
      const r = check();
      // `waitFor(() => text().includes('x'))` devolvia `false` e passava NA HORA, sem esperar nem afirmar nada:
      // um booleano falso conta como "ainda não", como uma exceção, até o prazo.
      if (r === false) throw new Error(`waitFor: a condição continuou falsa — ${String(check).slice(0, 160)}`);
      return r;
    } catch (e) {
      lastError = e;
    }
    if (Date.now() - start > timeoutMs) throw lastError;
    await flush(15);
  }
}

/**
 * Espera um elemento aparecer e o devolve. O `waitFor(() => raiz.querySelector(…))` passava na hora com `null` (só
 * `false` e exceção são "ainda não"); aqui `null` também é, até o prazo. A catraca `src/test/esperas.test.ts` barra a
 * forma antiga.
 */
export async function esperarElemento<E extends Element = HTMLElement>(
  seletor: string,
  raiz: ParentNode = document,
  timeoutMs = 4000,
): Promise<E> {
  return waitFor(() => {
    const el = raiz.querySelector<E>(seletor);
    if (el == null) throw new Error(`esperarElemento: nada com "${seletor}" ainda`);
    return el;
  }, timeoutMs);
}

// ---- consultas e interações mínimas (sem dependências extras) ----

export function text(el: Element | Document = document): string {
  return (el instanceof Document ? el.body : el).textContent ?? '';
}

export function byRole(role: string, name: RegExp | string, root: ParentNode = document): HTMLElement {
  const found = allByRole(role, name, root);
  if (found.length === 0) throw new Error(`Nenhum elemento role=${role} com nome ${String(name)}`);
  return found[0] as HTMLElement;
}

const IMPLICIT: Record<string, string> = { button: 'button', a: 'link', textarea: 'textbox', select: 'combobox', dialog: 'dialog', nav: 'navigation' };

function roleOf(el: Element): string | null {
  const explicit = el.getAttribute('role');
  if (explicit) return explicit;
  const tag = el.tagName.toLowerCase();
  if (tag === 'input') {
    const type = (el as HTMLInputElement).type;
    return type === 'checkbox' ? 'checkbox' : 'textbox';
  }
  return IMPLICIT[tag] ?? null;
}

function nameOf(el: Element): string {
  const label = el.getAttribute('aria-label');
  if (label) return label;
  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) return labelledBy.split(' ').map((id) => document.getElementById(id)?.textContent ?? '').join(' ');
  if (el.id) {
    // Os ids vêm do useId() do React e têm caracteres que exigiriam escape em seletores CSS.
    const forLabel = Array.from(document.querySelectorAll('label')).find((l) => l.htmlFor === el.id);
    if (forLabel) return forLabel.textContent ?? '';
  }
  return (el.textContent ?? '').replace(/\s+/g, ' ').trim();
}

export function allByRole(role: string, name: RegExp | string, root: ParentNode = document): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (const el of Array.from(root.querySelectorAll('*'))) {
    if (roleOf(el) !== role) continue;
    const n = nameOf(el);
    if (typeof name === 'string' ? n === name : name.test(n)) out.push(el as HTMLElement);
  }
  return out;
}

export async function click(el: Element, init: MouseEventInit = {}): Promise<void> {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ...init }));
  });
}

/** jsdom não abre `<details>` (o `Disclosure`) com clique no `<summary>`: abre e avisa o React como o navegador
 *  faria. `scope` restringe quando a mesma legenda aparece em mais de um `Disclosure` na tela (ex.: uma por etapa). */
export async function openDetails(summaryText: RegExp, scope: ParentNode = document): Promise<HTMLDetailsElement> {
  const summary = Array.from(scope.querySelectorAll('summary')).find((s) => summaryText.test(s.textContent ?? ''));
  if (!summary) throw new Error(`Nenhum <summary> com ${String(summaryText)}`);
  const details = summary.parentElement as HTMLDetailsElement;
  await act(async () => {
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
  });
  return details;
}

/** Define o valor de um <input>/<textarea>/<select> controlado pelo React. */
export async function setValue(el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string): Promise<void> {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  await act(async () => {
    setter?.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}

export async function pointer(el: Element, type: 'pointerdown' | 'pointermove' | 'pointerup', x: number, y: number): Promise<void> {
  await act(async () => {
    const ev = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 });
    Object.defineProperty(ev, 'pointerId', { value: 1 });
    el.dispatchEvent(ev);
  });
}
