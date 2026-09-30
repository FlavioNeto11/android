import { create } from 'zustand';
import { api } from '../../api/client';
import type { Approval, PersonaDTO } from '../../api/types';
import { apiAprendizado } from '../aprendizado/api';
import { useContagemDoAprendizado } from '../aprendizado/contagem';
import type { EntradaDoLivro } from '../aprendizado/model';

/**
 * As fontes da caixa de pendências que vêm do servidor por leitura (as execuções já estão no snapshot ao vivo).
 * Um store à parte, como o `contagem.ts` do Aprendizado: não vem do WebSocket, é relido de tempos em tempos e depois
 * de cada decisão. Falha de leitura NÃO zera o que já se sabe e não mostra erro na barra (não é lugar de alarme); a
 * página da caixa diz que a leitura falhou (`falhou`).
 *
 * A contagem "Para aprovar" do menu (Aprendizado) sai desta mesma leitura: uma chamada a cada minuto, não duas. As
 * personas (`GET /personas`, a mesma leitura da tela Personas) trazem as sessões que pedem uma pessoa (RF-03).
 */
interface PendenciasStore {
  aprendizado: EntradaDoLivro[] | null;
  aprovacoes: Approval[] | null;
  personas: PersonaDTO[] | null;
  /** A última leitura de alguma das fontes falhou: a lista pode estar incompleta. */
  falhou: boolean;
  atualizar: () => Promise<void>;
}

let emVoo: Promise<void> | null = null;

export const usePendenciasStore = create<PendenciasStore>((set) => ({
  aprendizado: null,
  aprovacoes: null,
  personas: null,
  falhou: false,
  atualizar: () => {
    emVoo ??= (async () => {
      try {
        const [fila, aprovacoes, personas] = await Promise.allSettled([
          apiAprendizado.pendentes(), api.listApprovals('pending'), api.listPersonas(),
        ]);
        const parcial: Partial<PendenciasStore> = {
          falhou: fila.status === 'rejected' || aprovacoes.status === 'rejected' || personas.status === 'rejected',
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
        set(parcial);
      } finally {
        emVoo = null;
      }
    })();
    return emVoo;
  },
}));
