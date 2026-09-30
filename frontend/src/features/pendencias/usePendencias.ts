import { useEffect, useMemo } from 'react';
import { useAppStore } from '../../store/app';
import { intervaloVisivel } from '../../lib/polling';
import { useSessionStore } from '../../store/session';
import { montarPendencias, type Pendencia } from './modelo';
import { usePendenciasStore } from './store';

/** Releitura da caixa: só depois do login (sem sessão a leitura só colheria 401) e só com a aba visível. */
const A_CADA_MS = 60_000;

/**
 * A caixa de pendências: a lista e o total (a mesma conta) para o menu e para a página. `nomeDaPersona` só enriquece o
 * texto das aprovações; não muda a contagem.
 */
export function usePendencias(nomeDaPersona?: (id: string | null) => string | null): {
  itens: Pendencia[]; total: number; carregado: boolean; falhou: boolean;
} {
  const aprendizado = usePendenciasStore((s) => s.aprendizado);
  const aprovacoes = usePendenciasStore((s) => s.aprovacoes);
  const falhou = usePendenciasStore((s) => s.falhou);
  const execucoes = useAppStore((s) => s.runs);
  const itens = useMemo(
    () => montarPendencias({ aprendizado, aprovacoes, execucoes, nomeDaPersona }),
    [aprendizado, aprovacoes, execucoes, nomeDaPersona],
  );
  return { itens, total: itens.length, carregado: aprendizado !== null || aprovacoes !== null, falhou };
}

/** Quem monta o menu liga a releitura periódica (uma vez; a página só lê). */
export function useReleituraDasPendencias(): void {
  const operator = useSessionStore((s) => s.operator);
  useEffect(() => {
    if (!operator) return undefined;
    const atualizar = () => void usePendenciasStore.getState().atualizar();
    atualizar();
    return intervaloVisivel(atualizar, A_CADA_MS);
  }, [operator]);
}
