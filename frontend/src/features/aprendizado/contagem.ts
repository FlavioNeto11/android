import { create } from 'zustand';
import { apiAprendizado } from './api';

/**
 * A contagem de "Para aprovar" que a barra do topo mostra (ADR-054). Um store à parte, e não no `useAppStore`: não vem
 * do snapshot nem do WebSocket — é uma leitura do livro, refeita de tempos em tempos e depois de cada decisão.
 *
 * Falha de leitura NÃO zera o número nem mostra erro: a barra do topo não é lugar de alarme, e um backend sem a rota
 * (antes da migração 055) só deixa a contagem de fora.
 */
interface ContagemStore {
  pendentes: number | null;
  atualizar: () => Promise<void>;
}

let emVoo: Promise<void> | null = null;

export const useContagemDoAprendizado = create<ContagemStore>((set) => ({
  pendentes: null,
  atualizar: () => {
    // Duas telas pedindo ao mesmo tempo (a barra e a página que acabou de aprovar) viram UMA leitura.
    emVoo ??= (async () => {
      try {
        const res = await apiAprendizado.pendentes();
        const n = typeof res?.total === 'number' ? res.total : Array.isArray(res?.itens) ? res.itens.length : null;
        if (n !== null) set({ pendentes: n });
      } catch {
        /* silencioso de propósito: ver o comentário do store */
      } finally {
        emVoo = null;
      }
    })();
    return emVoo;
  },
}));
