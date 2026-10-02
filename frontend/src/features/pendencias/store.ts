import { create } from 'zustand';
import { api } from '../../api/client';
import type { PedidoView } from '../../api/pedidos';
import type { Approval, PersonaDTO } from '../../api/types';
import { apiAprendizado } from '../aprendizado/api';
import { useContagemDoAprendizado } from '../aprendizado/contagem';
import type { EntradaDoLivro } from '../aprendizado/model';
import { apiPedidos } from '../pedidos/api';

/**
 * As fontes da caixa de pendências que vêm do servidor por leitura (as execuções já estão no snapshot ao vivo).
 * Um store à parte, como o `contagem.ts` do Aprendizado: não vem do WebSocket, é relido de tempos em tempos e depois
 * de cada decisão. Falha de leitura NÃO zera o que já se sabe e não mostra erro na barra (não é lugar de alarme); a
 * página da caixa diz que a leitura falhou (`falhou`).
 *
 * A contagem "Para aprovar" do menu (Aprendizado) sai desta mesma leitura: uma chamada a cada minuto, não duas. As
 * personas (`GET /personas`, a mesma leitura da tela Personas) trazem as sessões que pedem uma pessoa (RF-03).
 */
export interface FalhasDeLeitura { aprendizado: boolean; aprovacoes: boolean; personas: boolean; pedidos: boolean }

/** Os pedidos `aguardando_pessoa` (emenda à ADR-062): páginas de 200 até acabar o cursor, sem janela (regra 2 da ADR-062). */
async function lerPedidosAguardando(): Promise<PedidoView[]> {
  const todos: PedidoView[] = [];
  let cursor: string | undefined;
  for (let pagina = 0; pagina < 10; pagina++) {
    const r = await apiPedidos.listar({ estado: 'aguardando_pessoa', limit: 200, cursor });
    todos.push(...(Array.isArray(r?.items) ? r.items : []));
    if (!r?.proximo_cursor) break;
    cursor = r.proximo_cursor;
  }
  return todos;
}

interface PendenciasStore {
  aprendizado: EntradaDoLivro[] | null;
  aprovacoes: Approval[] | null;
  personas: PersonaDTO[] | null;
  pedidos: PedidoView[] | null;
  /** A última leitura de alguma das fontes falhou: a lista pode estar incompleta. */
  falhou: boolean;
  /** Qual fonte falhou na última leitura (B8, rodada 2): o número daquela origem vira "n+" ou "?" em vez de parecer certo. */
  falhas: FalhasDeLeitura;
  atualizar: () => Promise<void>;
}

let emVoo: Promise<void> | null = null;

export const usePendenciasStore = create<PendenciasStore>((set) => ({
  aprendizado: null,
  aprovacoes: null,
  personas: null,
  pedidos: null,
  falhou: false,
  falhas: { aprendizado: false, aprovacoes: false, personas: false, pedidos: false },
  atualizar: () => {
    emVoo ??= (async () => {
      try {
        const [fila, aprovacoes, personas, pedidos] = await Promise.allSettled([
          apiAprendizado.pendentes(), api.listApprovals('pending'), api.listPersonas(), lerPedidosAguardando(),
        ]);
        const falhas: FalhasDeLeitura = {
          aprendizado: fila.status === 'rejected', aprovacoes: aprovacoes.status === 'rejected', personas: personas.status === 'rejected',
          pedidos: pedidos.status === 'rejected',
        };
        const parcial: Partial<PendenciasStore> = {
          falhou: falhas.aprendizado || falhas.aprovacoes || falhas.personas || falhas.pedidos, falhas,
        };
        if (fila.status === 'fulfilled' && Array.isArray(fila.value?.itens)) {
          parcial.aprendizado = fila.value.itens;
          // O selo do Aprendizado no menu e a caixa leem a mesma fila.
          useContagemDoAprendizado.setState({
            pendentes: typeof fila.value.total === 'number' ? fila.value.total : fila.value.itens.length,
          });
        }
        if (aprovacoes.status === 'fulfilled' && Array.isArray(aprovacoes.value)) parcial.aprovacoes = aprovacoes.value;
        if (personas.status === 'fulfilled' && Array.isArray(personas.value)) parcial.personas = personas.value;
        if (pedidos.status === 'fulfilled') parcial.pedidos = pedidos.value;
        set(parcial);
      } finally {
        emVoo = null;
      }
    })();
    return emVoo;
  },
}));
