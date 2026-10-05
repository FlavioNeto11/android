import { create } from 'zustand';

/**
 * Ponte entre o Modo treinamento (que sabe se há gravação) e o Foco (que envia as entradas e vê as recusas).
 * Vive só em memória: é estado da tela, não do servidor. As recusas (409 `stale_frame`) só contam enquanto a gravação
 * é da própria pessoa; ao terminar, o contador zera.
 */
interface TrainingState {
  gravando: Record<string, boolean>;
  recusadas: Record<string, number>;
  definirGravando: (instanceId: string, gravando: boolean) => void;
  registrarRecusa: (instanceId: string) => void;
}

export const useTrainingStore = create<TrainingState>((set) => ({
  gravando: {},
  recusadas: {},
  definirGravando: (instanceId, gravando) => set((s) => {
    if (!!s.gravando[instanceId] === gravando && (gravando || !s.recusadas[instanceId])) return s;
    const recusadas = { ...s.recusadas };
    if (!gravando) delete recusadas[instanceId];
    return { gravando: { ...s.gravando, [instanceId]: gravando }, recusadas };
  }),
  registrarRecusa: (instanceId) => set((s) => (
    s.gravando[instanceId] ? { recusadas: { ...s.recusadas, [instanceId]: (s.recusadas[instanceId] ?? 0) + 1 } } : s
  )),
}));
