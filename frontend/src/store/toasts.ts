import { create } from 'zustand';
import { hintForError, toApiError } from '../api/client';
import { localId } from '../lib/ids';

export type ToastTone = 'info' | 'success' | 'warning' | 'danger';

export interface Toast {
  id: string;
  /** Toasts com a mesma `key` substituem o anterior (evita pilhas de erros repetidos). */
  key: string | null;
  tone: ToastTone;
  title: string;
  message: string | null;
  hint: string | null;
  details: string[] | null;
  durationMs: number; // 0 = fica até ser fechado
  createdAt: number;
}

export interface ToastInput {
  tone: ToastTone;
  title: string;
  message?: string | null;
  hint?: string | null;
  details?: string[] | null;
  key?: string | null;
  durationMs?: number;
}

interface ToastStore {
  toasts: Toast[];
  push: (input: ToastInput) => string;
  dismiss: (id: string) => void;
}

const DEFAULT_DURATION: Record<ToastTone, number> = { info: 5000, success: 4000, warning: 9000, danger: 12000 };
const MAX_TOASTS = 5;

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (input) => {
    const id = localId('toast');
    const toast: Toast = {
      id,
      key: input.key ?? null,
      tone: input.tone,
      title: input.title,
      message: input.message ?? null,
      hint: input.hint ?? null,
      details: input.details ?? null,
      durationMs: input.durationMs ?? DEFAULT_DURATION[input.tone],
      createdAt: Date.now(),
    };
    set((s) => {
      const rest = toast.key ? s.toasts.filter((t) => t.key !== toast.key) : s.toasts;
      const next = [...rest, toast];
      return { toasts: next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next };
    });
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export function toast(input: ToastInput): string {
  return useToastStore.getState().push(input);
}

/**
 * Caminho único para erros de API: título com o contexto ("Não foi possível iniciar android-03"),
 * mensagem do backend (`detail.message`) e uma dica de próximo passo.
 */
export function toastError(context: string, error: unknown, opts: { key?: string; hint?: string } = {}): void {
  const e = toApiError(error);
  toast({
    tone: 'danger',
    title: context,
    message: e.message,
    hint: opts.hint ?? hintForError(e),
    key: opts.key ?? null,
  });
}
