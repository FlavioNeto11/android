import { useEffect } from 'react';
import { create } from 'zustand';
import type { AvisoDTO } from '../../api/pedidos';
import { intervaloVisivel } from '../../lib/polling';
import { onLiveEvent } from '../../store/live';
import { useSessionStore } from '../../store/session';
import { toast } from '../../store/toasts';
import { apiPedidos } from './api';

/**
 * O que a barra e o menu precisam saber dos pedidos sem abrir a tela: os avisos NÃO LIDOS da caixa de avisos
 * (informativos; o que espera uma pessoa mora nas Pendências, ADR-062) e uma batida (`epoch`) a cada evento `pedido.*`
 * para as telas relerem. Não vem do WebSocket nem do snapshot: é relido a cada minuto e a cada evento. Falha de leitura
 * não zera o que já se sabe (a barra não é lugar de alarme) e a tela da caixa diz que falhou.
 */
interface PedidosStore {
  epoch: number;
  avisos: AvisoDTO[] | null;
  /** Avisos não lidos da caixa. `null` = ainda sem leitura. */
  naoLidos: number | null;
  falhou: boolean;
  atualizar: () => Promise<void>;
  bater: () => void;
}

const LIMITE_DA_CAIXA = 50;
let emVoo: Promise<void> | null = null;

export const usePedidosStore = create<PedidosStore>((set) => ({
  epoch: 0,
  avisos: null,
  naoLidos: null,
  falhou: false,
  bater: () => set((s) => ({ epoch: s.epoch + 1 })),
  atualizar: () => {
    emVoo ??= (async () => {
      try {
        const r = await apiPedidos.avisos({ lido: 0, requer_pessoa: 0, limit: LIMITE_DA_CAIXA });
        const itens = Array.isArray(r?.items) ? r.items : [];
        // Lista completa: o tamanho dela é o que a pessoa vê. Só com mais páginas o total do backend é o que vale.
        const naoLidos = r?.proximo_cursor ? Math.max(r.nao_lidos ?? 0, itens.length) : itens.length;
        set({ avisos: itens, naoLidos, falhou: false });
      } catch {
        set({ falhou: true });
      } finally {
        emVoo = null;
      }
    })();
    return emVoo;
  },
}));

const A_CADA_MS = 60_000;
const ESPERA_DO_EVENTO_MS = 400;

/**
 * Quem monta o menu liga a releitura (uma vez): depois do login, a cada minuto com a aba visível e a cada evento
 * `pedido.*` (com uma espera curta, que junta a rajada). O aviso informativo que chega ao vivo vira um toast.
 */
export function useReleituraDosPedidos(): void {
  const operator = useSessionStore((s) => s.operator);
  useEffect(() => {
    if (!operator) return undefined;
    const atualizar = () => void usePedidosStore.getState().atualizar();
    let espera: ReturnType<typeof setTimeout> | null = null;
    atualizar();
    const parar = intervaloVisivel(atualizar, A_CADA_MS);
    const sair = onLiveEvent((ev) => {
      if (!ev.kind.startsWith('pedido.')) return;
      usePedidosStore.getState().bater();
      if (ev.kind === 'pedido.aviso') {
        const aviso = (ev.data as { aviso?: AvisoDTO } | null)?.aviso;
        if (aviso && !aviso.requer_pessoa) {
          toast({
            tone: aviso.nivel === 'error' ? 'danger' : aviso.nivel === 'warn' ? 'warning' : 'info',
            title: aviso.pedido_titulo ? `Pedido “${aviso.pedido_titulo}”` : 'Aviso de pedido',
            message: aviso.mensagem,
            key: `aviso-${aviso.id}`,
          });
        }
      }
      if (espera) clearTimeout(espera);
      espera = setTimeout(atualizar, ESPERA_DO_EVENTO_MS);
    });
    return () => {
      parar();
      sair();
      if (espera) clearTimeout(espera);
    };
  }, [operator]);
}
