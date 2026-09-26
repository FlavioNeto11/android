import { create } from 'zustand';

/**
 * Quais miniaturas da grade estão NA TELA agora (contrato C2, `watch.grid`). Quem preenche é o próprio cartão,
 * pelo IntersectionObserver (`features/devices/usePreviewVisible.ts`); quem lê é o controlador ao vivo, que
 * transforma isto no `watch` do WebSocket. Aparelho fora da viewport não entra: é o que deixa o backend parar de
 * capturar prévia que ninguém está vendo.
 */
interface PreviewStore {
  visible: Record<string, true>;
  setVisible: (id: string, on: boolean) => void;
}

export const usePreviewStore = create<PreviewStore>((set) => ({
  visible: {},
  setVisible: (id, on) =>
    set((s) => {
      // Sem mudança, devolve o MESMO estado: os assinantes só acordam quando o conjunto muda de fato.
      if (on === (s.visible[id] === true)) return s;
      if (on) return { visible: { ...s.visible, [id]: true } };
      const { [id]: _gone, ...rest } = s.visible;
      return { visible: rest };
    }),
}));

/** Ids visíveis em ordem estável (o servidor não liga para a ordem; a comparação e os testes, sim). */
export function visibleGrid(visible: Record<string, true>): string[] {
  return Object.keys(visible).sort();
}
