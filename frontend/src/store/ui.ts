import { create } from 'zustand';
import { isString, isStringArray, loadJson, saveJson } from '../lib/storage';

export type View = 'painel' | 'perfis' | 'execucoes' | 'configuracao' | 'diagnostico';

export const VIEWS: readonly View[] = ['painel', 'perfis', 'execucoes', 'configuracao', 'diagnostico'];

export function isView(v: unknown): v is View {
  return typeof v === 'string' && (VIEWS as readonly string[]).includes(v);
}

export function viewFromHash(hash: string): View | null {
  const m = /^#\/?([a-z]+)/.exec(hash);
  return m && isView(m[1]) ? m[1] : null;
}

export function hashForView(view: View): string {
  return `#/${view}`;
}

interface UiStore {
  view: View;
  selectedIds: string[];
  /** Âncora do Shift+clique (último cartão alternado). */
  selectionAnchor: string | null;
  selectedRunId: string | null;
  focusInstanceId: string | null;
  /** Texto a ser colocado no campo de comando (ex.: "Editar comando" de uma execução). */
  commandDraftRequest: { text: string; nonce: number } | null;

  setView: (view: View) => void;
  toggleSelected: (id: string) => void;
  selectRange: (toId: string, order: readonly string[]) => void;
  setSelection: (ids: readonly string[]) => void;
  clearSelection: () => void;
  pruneSelection: (validIds: readonly string[]) => void;
  selectRun: (id: string | null) => void;
  openFocus: (id: string) => void;
  closeFocus: () => void;
  requestCommandDraft: (text: string) => void;
}

/** Elemento que tinha o foco quando a visão de foco foi aberta (não é estado de UI, só uma referência). */
let focusOpener: HTMLElement | null = null;

function initialView(): View {
  const fromHash = typeof window !== 'undefined' ? viewFromHash(window.location.hash) : null;
  if (fromHash) return fromHash; // o hash é a fonte da verdade; o localStorage só semeia um hash vazio
  return loadJson('view', isView) ?? 'painel';
}

export const useUiStore = create<UiStore>((set, get) => ({
  view: typeof window !== 'undefined' ? initialView() : 'painel',
  selectedIds: typeof window !== 'undefined' ? loadJson('selectedInstances', isStringArray) ?? [] : [],
  selectionAnchor: null,
  selectedRunId: typeof window !== 'undefined' ? loadJson('selectedRun', isString) : null,
  focusInstanceId: null,
  commandDraftRequest: null,

  setView: (view) => {
    if (get().view !== view) set({ view });
    saveJson('view', view);
    if (typeof window !== 'undefined' && viewFromHash(window.location.hash) !== view) {
      window.location.hash = hashForView(view);
    }
  },

  toggleSelected: (id) => {
    const cur = get().selectedIds;
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    set({ selectedIds: next, selectionAnchor: id });
    saveJson('selectedInstances', next);
  },

  selectRange: (toId, order) => {
    const { selectionAnchor, selectedIds } = get();
    const from = selectionAnchor ? order.indexOf(selectionAnchor) : -1;
    const to = order.indexOf(toId);
    if (to === -1) return;
    if (from === -1) {
      get().toggleSelected(toId);
      return;
    }
    const [a, b] = from <= to ? [from, to] : [to, from];
    const range = order.slice(a, b + 1);
    const next = Array.from(new Set([...selectedIds, ...range]));
    set({ selectedIds: next });
    saveJson('selectedInstances', next);
  },

  setSelection: (ids) => {
    const next = Array.from(new Set(ids));
    set({ selectedIds: next, selectionAnchor: next[next.length - 1] ?? null });
    saveJson('selectedInstances', next);
  },

  clearSelection: () => {
    set({ selectedIds: [], selectionAnchor: null });
    saveJson('selectedInstances', []);
  },

  pruneSelection: (validIds) => {
    const valid = new Set(validIds);
    const cur = get().selectedIds;
    const next = cur.filter((id) => valid.has(id));
    if (next.length !== cur.length) {
      set({ selectedIds: next });
      saveJson('selectedInstances', next);
    }
  },

  selectRun: (id) => {
    if (get().selectedRunId === id) return;
    set({ selectedRunId: id });
    saveJson('selectedRun', id);
  },

  openFocus: (id) => {
    // Guarda quem abriu o painel para devolver o foco do teclado ao fechar.
    if (typeof document !== 'undefined' && get().focusInstanceId === null) {
      focusOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    set({ focusInstanceId: id });
  },
  closeFocus: () => {
    set({ focusInstanceId: null });
    const opener = focusOpener;
    focusOpener = null;
    if (opener && opener.isConnected) setTimeout(() => opener.focus(), 0);
  },

  requestCommandDraft: (text) => set({ commandDraftRequest: { text, nonce: Date.now() } }),
}));

/** Liga o hash da URL à visão atual. Devolve a função de limpeza. */
export function bindHashRouting(): () => void {
  const onHash = () => {
    const v = viewFromHash(window.location.hash);
    if (v && v !== useUiStore.getState().view) useUiStore.getState().setView(v);
  };
  window.addEventListener('hashchange', onHash);
  // Semeia o hash quando a página abre sem ele.
  if (!viewFromHash(window.location.hash)) {
    window.history.replaceState(null, '', hashForView(useUiStore.getState().view));
  }
  return () => window.removeEventListener('hashchange', onHash);
}
