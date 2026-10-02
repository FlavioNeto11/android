import { useEffect, useMemo } from 'react';
import { useAppStore } from '../../store/app';
import { intervaloVisivel } from '../../lib/polling';
import { useSessionStore } from '../../store/session';
import { usePedidosStore } from '../pedidos/store';
import { montarPendencias, type Pendencia } from './modelo';
import { usePendenciasStore, type FalhasDeLeitura } from './store';

/** Releitura da caixa: só depois do login (sem sessão a leitura só colheria 401) e só com a aba visível. */
const A_CADA_MS = 60_000;

/**
 * A caixa de pendências: a lista e o total (a mesma conta) para o menu e para a página. `nomeDaPersona` só enriquece o
 * texto das aprovações; não muda a contagem.
 */
export function usePendencias(nomeDaPersona?: (id: string | null) => string | null): {
  itens: Pendencia[]; total: number; carregado: boolean; falhou: boolean; falhas: FalhasDeLeitura;
} {
  const aprendizado = usePendenciasStore((s) => s.aprendizado);
  const aprovacoes = usePendenciasStore((s) => s.aprovacoes);
  const personas = usePendenciasStore((s) => s.personas);
  const pedidos = usePendenciasStore((s) => s.pedidos);
  const falhou = usePendenciasStore((s) => s.falhou);
  const falhas = usePendenciasStore((s) => s.falhas);
  const execucoes = useAppStore((s) => s.runs);
  const itens = useMemo(
    () => montarPendencias({ aprendizado, aprovacoes, execucoes, personas, pedidos, nomeDaPersona }),
    [aprendizado, aprovacoes, execucoes, personas, pedidos, nomeDaPersona],
  );
  return {
    itens, total: itens.length, carregado: aprendizado !== null || aprovacoes !== null || personas !== null || pedidos !== null, falhou, falhas,
  };
}

/**
 * Quem monta o menu liga a releitura periódica (uma vez; a página só lê). O evento `session.needs_person` (a batida
 * `needsPersonEpoch`) também relê: uma sessão que passou a pedir pessoa não espera o próximo minuto para aparecer.
 */
export function useReleituraDasPendencias(): void {
  const operator = useSessionStore((s) => s.operator);
  const needsPersonEpoch = useAppStore((s) => s.needsPersonEpoch);
  // Um pedido que passou a esperar uma pessoa (evento `pedido.*`) não espera o próximo minuto para aparecer na caixa.
  const pedidosEpoch = usePedidosStore((s) => s.epoch);
  useEffect(() => {
    if (!operator) return undefined;
    const atualizar = () => void usePendenciasStore.getState().atualizar();
    atualizar();
    return intervaloVisivel(atualizar, A_CADA_MS);
  }, [operator, needsPersonEpoch, pedidosEpoch]);
}
