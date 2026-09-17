import { act } from 'react';
import { vi } from 'vitest';

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
      return check();
    } catch (e) {
      lastError = e;
    }
    if (Date.now() - start > timeoutMs) throw lastError;
    await flush(15);
  }
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
