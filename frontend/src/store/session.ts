import { create } from 'zustand';
import { api, hintForError, onUnauthorized, toApiError } from '../api/client';
import { isString, loadJson, saveJson } from '../lib/storage';

/**
 * Quem está operando o painel.
 *
 * Por que isto existe, e não apenas "trate o 401": no loopback — que é como o parque roda hoje — 401 NUNCA
 * acontece, porque quem está na máquina passa sem credencial por desenho. Se o login só aparecesse no 401,
 * ninguém jamais se identificaria ali e toda a auditoria continuaria dizendo `panel`. Então a pergunta é
 * `GET /api/session`, feita antes de qualquer outra: ela responde "quem sou eu" E "esta origem exige a chave".
 *
 * O cookie é `HttpOnly`: este store não tem como lê-lo, e isso é o ponto — um XSS não leva a sessão embora. O
 * que guardamos aqui é só o NOME, para a próxima visita já vir preenchido.
 */

const NOME_LEMBRADO = 'painel.operador';

export interface SessionStore {
  /** Já perguntamos ao backend? Enquanto `false` o painel não decide nada — nem mostra login, nem carrega. */
  checked: boolean;
  operator: string | null;
  /** Esta origem exige a chave de acesso (`API_TOKEN`)? Falso no loopback. */
  tokenRequired: boolean;
  /** Último nome usado neste navegador, para o campo já vir preenchido. */
  lastName: string;
  busy: boolean;
  error: string | null;

  refresh: () => Promise<void>;
  signIn: (operator: string, token: string) => Promise<boolean>;
  signOut: () => Promise<void>;
  /** Chamado quando qualquer requisição toma 401: a sessão caiu e a tela de login volta. */
  markUnauthorized: () => void;
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  checked: false,
  operator: null,
  tokenRequired: false,
  lastName: loadJson(NOME_LEMBRADO, isString) ?? '',
  busy: false,
  error: null,

  refresh: async () => {
    try {
      const info = await api.session();
      set({ checked: true, operator: info.operator, tokenRequired: info.token_required, error: null });
    } catch (e) {
      const err = toApiError(e);
      // 401 aqui não deveria acontecer (a rota responde sem credencial), mas se acontecer o certo é a tela de
      // login — e não um painel vazio dizendo "tente novamente".
      set({ checked: true, operator: null, tokenRequired: err.status === 401 || get().tokenRequired,
            error: err.status === 0 ? `${err.message} ${hintForError(err)}` : null });
    }
  },

  signIn: async (operator, token) => {
    set({ busy: true, error: null });
    try {
      const info = await api.login(operator.trim(), token.trim() || undefined);
      saveJson(NOME_LEMBRADO, info.operator ?? operator.trim());
      set({ operator: info.operator, tokenRequired: info.token_required, busy: false, error: null,
            lastName: info.operator ?? operator.trim(), checked: true });
      return true;
    } catch (e) {
      const err = toApiError(e);
      // `invalid_credentials` prova que ESTA origem exige a chave, mesmo que a pergunta anterior tenha dito o
      // contrário: sem isto, a tela ficaria pedindo só o nome e o backend recusando, sem saída visível.
      set({ busy: false, tokenRequired: err.code === 'invalid_credentials' ? true : get().tokenRequired,
            error: `${err.message} ${hintForError(err)}`.trim() });
      return false;
    }
  },

  signOut: async () => {
    set({ busy: true });
    try {
      await api.logout();
    } catch {
      /* sair sempre termina em "não estou mais logado": erro de rede aqui não prende ninguém na tela */
    }
    set({ operator: null, busy: false, error: null });
    // Pergunta de novo o que ESTA origem exige. Sem isto, sair e entrar de novo de uma estação remota mostraria
    // a tela de login sem o campo da chave — e o login seria recusado sem que a pessoa pudesse fazer nada.
    await get().refresh();
  },

  markUnauthorized: () => {
    if (get().operator === null) return;
    set({ operator: null, tokenRequired: true, error: 'Sua sessão terminou. Entre de novo para continuar.' });
  },
}));

/** Assina o 401 global uma única vez. Fora do `create` para não reassinar a cada render. */
onUnauthorized(() => useSessionStore.getState().markUnauthorized());
