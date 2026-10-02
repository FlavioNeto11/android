import { create } from 'zustand';
import { hashDaRota, hashDe, parseHash, TELAS, type Rota, type Tela } from '../lib/rotas';
import { isBoolean, isString, isStringArray, loadJson, saveJson } from '../lib/storage';

/**
 * Estado de UI e roteamento. A URL (hash, contrato em `lib/rotas.ts`) é a fonte da verdade de ONDE a pessoa está:
 * tela, objeto aberto (persona, execução, aplicativo), guia e aparelho em Foco. O store só espelha a rota e oferece
 * as ações que a mudam; quem lê usa `rota`, `view`, `focusInstanceId` e `selectedRunId` como antes.
 *
 * Histórico: abrir um objeto ou o Foco EMPILHA (Voltar do navegador fecha); trocar de guia, canonizar um link e
 * seleção automática SUBSTITUEM (nada de entradas inúteis). Fechar o Foco ou voltar à lista usa `history.back()`
 * quando a entrada anterior é exatamente o destino, e substitui (ou empilha, nos botões "voltar à lista") quando
 * não é — link colado, recarga de página.
 */

/** O nome interno da tela é o mesmo da URL: `'perfis'` (nome antigo) virou `'personas'` em todo lugar. */
export type View = Tela;

export const VIEWS: readonly View[] = TELAS;

export function isView(v: unknown): v is View {
  return typeof v === 'string' && (VIEWS as readonly string[]).includes(v);
}

export function viewFromHash(hash: string): View | null {
  return parseHash(hash)?.tela ?? null;
}

export function hashForView(view: View): string {
  return hashDe(view);
}

/** Parâmetro global do aparelho aberto no Foco (ver `lib/rotas.ts`). */
export const PARAM_FOCO = 'foco';

export type ModoHistorico = 'push' | 'replace';

/** O que gravamos em `history.state` ao empilhar: de onde viemos, para saber se "voltar" é seguro. */
interface EstadoEmpilhado {
  cda: 1;
  de: string;
}

function ehEmpilhado(v: unknown): v is EstadoEmpilhado {
  return !!v && typeof v === 'object' && (v as { cda?: unknown }).cda === 1 && typeof (v as { de?: unknown }).de === 'string';
}

/**
 * Pura (testada): dá para chegar ao destino com `history.back()`? Só se a entrada atual foi empilhada por nós e a
 * anterior (`de`) é o destino — pelo hash exato, ou pelo critério `aceita` (ex.: "a lista, com qualquer filtro").
 */
export function podeVoltarPara(state: unknown, alvo: string, aceita?: (de: Rota) => boolean): boolean {
  if (!ehEmpilhado(state)) return false;
  if (state.de === alvo) return true;
  if (!aceita) return false;
  const de = parseHash(state.de);
  return !!de && aceita(de);
}

export interface Destino {
  tela: Tela;
  segmentos?: readonly string[];
  /** Query COMPLETA da tela de destino. `foco` é levado junto sozinho, a menos que venha aqui (`undefined` tira). */
  query?: Record<string, string | undefined>;
}

interface UiStore {
  /** Rota atual, já canônica (`#/perfis` vira `personas`). */
  rota: Rota;
  view: View;
  selectedIds: string[];
  /** Âncora do Shift+clique (último cartão alternado). */
  selectionAnchor: string | null;
  selectedRunId: string | null;
  /** Espelho de `?foco=` — o aparelho aberto no painel de Foco. */
  focusInstanceId: string | null;
  /** Texto a ser colocado no campo de comando (ex.: "Editar comando" de uma execução). */
  commandDraftRequest: { text: string; nonce: number } | null;
  /** Pedido de "Novo pedido" (tela Pedidos): o Comando abre o painel "Repetir ou acompanhar" e o foco vai ao texto. */
  novoPedidoRequest: number | null;
  /** Menu lateral recolhido para só ícones (telas largas; lembrado no navegador). */
  menuRecolhido: boolean;
  /** Gaveta do menu aberta (telas estreitas, abaixo de 1024 px). */
  menuAberto: boolean;

  /** Vai para outro lugar do portal (monta o hash por `hashDe`). */
  navegar: (destino: Destino, modo?: ModoHistorico) => void;
  /** Troca só parâmetros da rota atual (guia, filtro), preservando os demais. Por padrão substitui. */
  trocarQuery: (parcial: Record<string, string | undefined>, modo?: ModoHistorico) => void;
  /** Volta ao destino pelo histórico quando a entrada anterior é ele; senão vai até ele pelo `modo`. */
  voltarPara: (destino: Destino, modo: ModoHistorico, aceita?: (de: Rota) => boolean) => void;
  setView: (view: View) => void;
  toggleSelected: (id: string) => void;
  selectRange: (toId: string, order: readonly string[]) => void;
  setSelection: (ids: readonly string[]) => void;
  clearSelection: () => void;
  pruneSelection: (validIds: readonly string[]) => void;
  /** Seleciona a execução (seleção automática ou de outra tela). Na tela Execuções, substitui o hash. */
  selectRun: (id: string | null) => void;
  /** Abre a execução na tela Execuções, EMPILHANDO (é o clique da pessoa; Voltar retorna). */
  abrirExecucao: (id: string) => void;
  openFocus: (id: string) => void;
  closeFocus: () => void;
  requestCommandDraft: (text: string) => void;
  /** Leva ao Comando (`#/painel`) com o painel "Repetir ou acompanhar" aberto, sem mexer no texto já escrito. */
  abrirNovoPedido: () => void;
  /** O Comando já atendeu o pedido acima. */
  novoPedidoAtendido: () => void;
  /** Vai para Personas e abre esta pessoa (numa guia, se dita). O Foco fica como está. */
  openPersona: (id: string, tab?: string) => void;
  setMenuRecolhido: (v: boolean) => void;
  setMenuAberto: (v: boolean) => void;
}

/** Elemento que tinha o foco quando a visão de foco foi aberta (não é estado de UI, só uma referência). */
let focusOpener: HTMLElement | null = null;
/** Último hash aplicado ao store: `hashchange` e `popstate` chegam os dois na travessia, e o teste ainda dispara à mão. */
let ultimoAplicado: string | null = null;

const temJanela = typeof window !== 'undefined';

function semLegado(r: Rota): Rota {
  return { tela: r.tela, segmentos: r.segmentos, query: r.query };
}

/** `'perfis'` gravado no navegador antes do nome novo continua abrindo Personas. */
function viewSalva(): View | null {
  const v = loadJson('view', isString);
  if (v === 'perfis') return 'personas';
  return isView(v) ? v : null;
}

function rotaInicial(): Rota {
  const r = temJanela ? parseHash(window.location.hash) : null;
  if (r) return semLegado(r); // o hash é a fonte da verdade; o localStorage só semeia um hash vazio
  return { tela: (temJanela ? viewSalva() : null) ?? 'painel', segmentos: [], query: {} };
}

function runDaRota(r: Rota): string | null {
  return r.tela === 'execucoes' ? r.segmentos[0] ?? null : null;
}

export function menuRecolhidoInicial(): boolean {
  if (!temJanela) return false;
  // Sem preferência: recolhido onde a largura é disputada (1024–1279 px, com o Foco aberto sobra pouco ao conteúdo).
  return loadJson('menuRecolhido', isBoolean) ?? window.innerWidth < 1280;
}

const inicial = rotaInicial();

/** Hash do destino, levando `foco` junto quando o destino não diz nada sobre ele. */
function hashDoDestino(d: Destino, focoAtual: string | null): string {
  const query: Record<string, string | undefined> = { ...(d.query ?? {}) };
  if (!(PARAM_FOCO in query) && focoAtual) query[PARAM_FOCO] = focoAtual;
  return hashDe(d.tela, { segmentos: d.segmentos, query });
}

/** Grava o hash no histórico (sem `hashchange`: quem chama aplica ao store logo em seguida). */
function gravarHash(hash: string, modo: ModoHistorico): boolean {
  if (!temJanela) return false;
  const atual = window.location.hash;
  if (hash === atual) return false;
  if (modo === 'push') {
    const estado: EstadoEmpilhado = { cda: 1, de: atual };
    window.history.pushState(estado, '', hash);
  } else {
    // Substituir preserva o `state`: a entrada anterior continua a mesma, então o "de onde viemos" segue valendo.
    window.history.replaceState(window.history.state, '', hash);
  }
  return true;
}

export const useUiStore = create<UiStore>((set, get) => ({
  rota: inicial,
  view: inicial.tela,
  selectedIds: temJanela ? loadJson('selectedInstances', isStringArray) ?? [] : [],
  selectionAnchor: null,
  selectedRunId: runDaRota(inicial) ?? (temJanela ? loadJson('selectedRun', isString) : null),
  focusInstanceId: inicial.query[PARAM_FOCO] || null,
  commandDraftRequest: null,
  novoPedidoRequest: null,
  menuRecolhido: menuRecolhidoInicial(),
  menuAberto: false,

  navegar: (destino, modo = 'push') => {
    gravarHash(hashDoDestino(destino, get().focusInstanceId), modo);
    // Aplica mesmo sem mudança de hash: o store pode ter saído da URL (teste, estado semeado à mão).
    aplicarHash(true);
  },

  trocarQuery: (parcial, modo = 'replace') => {
    const { rota } = get();
    get().navegar({ tela: rota.tela, segmentos: rota.segmentos, query: { ...rota.query, ...parcial } }, modo);
  },

  voltarPara: (destino, modo, aceita) => {
    const alvo = hashDoDestino(destino, get().focusInstanceId);
    const estado: unknown = temJanela ? window.history.state : null;
    if (podeVoltarPara(estado, alvo, aceita) && ehEmpilhado(estado)) {
      // A tela responde já (a travessia do histórico é assíncrona); o `popstate` que chega depois com este mesmo
      // hash é ignorado pela idempotência.
      const anterior = parseHash(estado.de);
      if (anterior) {
        aplicarRota(semLegado(anterior));
        ultimoAplicado = estado.de;
      }
      window.history.back();
      return;
    }
    gravarHash(alvo, modo);
    aplicarHash(true);
  },

  setView: (view) => {
    if (get().view === view) return;
    const run = get().selectedRunId;
    // Ir para Execuções leva a execução selecionada no link: `selectRun(x); setView('execucoes')` (Infraestrutura,
    // Aprendizado, Aplicativos) já chega em `#/execucoes/x`.
    get().navegar({ tela: view, segmentos: view === 'execucoes' && run ? [run] : [] });
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
    if (get().selectedRunId !== id) {
      set({ selectedRunId: id });
      saveJson('selectedRun', id);
    }
    const { rota } = get();
    // Na tela Execuções o link acompanha a seleção, sem empilhar (seleção automática, execução recém-criada). A guia
    // é da execução anterior: sai do link.
    if (rota.tela === 'execucoes' && (rota.segmentos[0] ?? null) !== id) {
      get().navegar({ tela: 'execucoes', segmentos: id ? [id] : [], query: { ...rota.query, aba: undefined } }, 'replace');
    }
  },

  abrirExecucao: (id) => {
    const { rota } = get();
    if (get().selectedRunId !== id) {
      set({ selectedRunId: id });
      saveJson('selectedRun', id);
    }
    if (rota.tela === 'execucoes' && rota.segmentos[0] === id) return;
    get().navegar({ tela: 'execucoes', segmentos: [id], query: rota.tela === 'execucoes' ? { ...rota.query, aba: undefined } : {} });
  },

  openFocus: (id) => {
    const atual = get().focusInstanceId;
    if (atual === id) return;
    // Guarda quem abriu o painel para devolver o foco do teclado ao fechar.
    if (typeof document !== 'undefined' && atual === null) {
      focusOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    // Abrir empilha (Voltar fecha); trocar de aparelho com o painel aberto substitui (Voltar fecha de vez).
    get().trocarQuery({ [PARAM_FOCO]: id }, atual === null ? 'push' : 'replace');
  },

  closeFocus: () => {
    const { rota, focusInstanceId } = get();
    if (!focusInstanceId) return;
    // Fechar não empilha: volta à entrada de antes do Foco quando ela é exatamente esta tela sem o `foco`; senão
    // (link colado, recarga, guia trocada com o painel aberto) substitui a entrada atual.
    get().voltarPara({ tela: rota.tela, segmentos: rota.segmentos, query: { ...rota.query, [PARAM_FOCO]: undefined } }, 'replace');
  },

  requestCommandDraft: (text) => set({ commandDraftRequest: { text, nonce: Date.now() } }),

  abrirNovoPedido: () => {
    set({ novoPedidoRequest: Date.now() });
    get().navegar({ tela: 'painel' });
  },
  novoPedidoAtendido: () => set({ novoPedidoRequest: null }),

  openPersona: (id, tab) => {
    get().navegar({ tela: 'personas', segmentos: tab ? [id, tab] : [id] });
  },

  setMenuRecolhido: (v) => {
    set({ menuRecolhido: v });
    saveJson('menuRecolhido', v);
  },
  setMenuAberto: (v) => set({ menuAberto: v }),
}));

/** Devolve o teclado a quem abriu o Foco (fechar pelo botão, pelo Esc ou pelo Voltar do navegador). */
function devolverFocoDoTeclado(): void {
  const opener = focusOpener;
  focusOpener = null;
  if (opener && opener.isConnected) setTimeout(() => opener.focus(), 0);
}

/** Espelha a rota no store. Devolve a execução selecionada depois de aplicar. */
function aplicarRota(r: Rota): string | null {
  const prev = useUiStore.getState();
  const foco = r.query[PARAM_FOCO] || null;
  const run = runDaRota(r) ?? prev.selectedRunId;
  useUiStore.setState({ rota: r, view: r.tela, focusInstanceId: foco, selectedRunId: run, menuAberto: false });
  if (run !== prev.selectedRunId) saveJson('selectedRun', run);
  saveJson('view', r.tela);
  if (prev.focusInstanceId && !foco) devolverFocoDoTeclado();
  return run;
}

/**
 * Lê o hash atual e aplica ao store. Idempotente. Canoniza sem empilhar: nome antigo (`#/perfis`), hash desconhecido
 * (volta à rota atual) e `#/execucoes` sem id quando há execução selecionada.
 */
export function aplicarHash(forcar = false): void {
  if (!temJanela) return;
  const hash = window.location.hash;
  if (hash === ultimoAplicado && !forcar) return;
  const lida = parseHash(hash);
  if (!lida) {
    const atual = hashDaRota(useUiStore.getState().rota);
    ultimoAplicado = atual;
    window.history.replaceState(window.history.state, '', atual);
    return;
  }
  const r = semLegado(lida);
  if (lida.legado) window.history.replaceState(window.history.state, '', hashDaRota(r));
  const run = aplicarRota(r);

  // `#/execucoes` (menu) com uma execução já selecionada: o link passa a nomeá-la.
  if (r.tela === 'execucoes' && !r.segmentos[0] && run) {
    const canonico = hashDe('execucoes', { segmentos: [run], query: r.query });
    window.history.replaceState(window.history.state, '', canonico);
    useUiStore.setState({ rota: { ...r, segmentos: [run] } });
  }
  ultimoAplicado = window.location.hash;
}

/** Liga o hash da URL ao store (Voltar/Avançar, link colado, `<a href>`). Devolve a função de limpeza. */
export function bindHashRouting(): () => void {
  ultimoAplicado = null;
  // Semeia o hash quando a página abre sem ele (ou com um que não nomeia tela nenhuma).
  if (!parseHash(window.location.hash)) {
    window.history.replaceState(window.history.state, '', hashDaRota(useUiStore.getState().rota));
  }
  aplicarHash(true);
  window.addEventListener('hashchange', aoMudarHash);
  window.addEventListener('popstate', aoMudarHash);
  return () => {
    window.removeEventListener('hashchange', aoMudarHash);
    window.removeEventListener('popstate', aoMudarHash);
  };
}

/** Ouvinte estável (o evento não pode cair no parâmetro `forcar`). */
function aoMudarHash(): void {
  aplicarHash();
}
