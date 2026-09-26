import { useEffect, useState, type RefObject } from 'react';
import { usePreviewStore } from '../../store/preview';

function observerAvailable(): boolean {
  return typeof IntersectionObserver !== 'undefined';
}

/**
 * O cartão está na viewport? Registra a resposta no store da prévia (vira o `watch.grid` do WebSocket) e a devolve
 * para a miniatura decidir se busca imagem.
 *
 * Um observador por cartão, criado no efeito: um observador global compartilhado nasceria com o
 * `IntersectionObserver` que existisse no primeiro uso, e a grade tem só dezenas de cartões.
 * Sem `IntersectionObserver` (navegador antigo, jsdom) tudo conta como visível — o comportamento de antes da
 * prévia sob demanda, que é o seguro: mostra a tela e custa o que sempre custou.
 */
export function usePreviewVisible(id: string, ref: RefObject<Element | null>): boolean {
  const [inView, setInView] = useState(() => !observerAvailable());
  const setVisible = usePreviewStore((s) => s.setVisible);

  useEffect(() => {
    const el = ref.current;
    if (!el || !observerAvailable()) {
      setInView(true);
      setVisible(id, true);
      return () => setVisible(id, false);
    }
    const io = new IntersectionObserver((entries) => {
      // Várias entradas do mesmo alvo numa rajada: vale a mais recente.
      const last = entries[entries.length - 1];
      if (!last) return;
      setInView(last.isIntersecting);
      setVisible(id, last.isIntersecting);
    });
    io.observe(el);
    return () => {
      io.disconnect();
      setVisible(id, false);
    };
  }, [id, ref, setVisible]);

  return inView;
}
