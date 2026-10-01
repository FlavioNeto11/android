import { useSyncExternalStore } from 'react';

/**
 * A faixa de largura do cabeçalho (rodada 2 da revisão de UX, tarefa 10):
 *  - `celular` < 768 px: uma linha só (menu, marca, semáforo, "Resumo"); o resto mora no painel "Resumo";
 *  - `tablet` 768 a 1023 px: duas linhas, com CPU, RAM e custos agrupados no chip "Recursos";
 *  - `largo` ≥ 1024 px: a régua inteira, como sempre.
 * Os limiares são os mesmos das regras `@media` de `TopBar.module.css` (a lógica decide O QUE existe; o CSS, como se
 * parece). Sem `matchMedia` (jsdom sem dublê, SSR) vale `largo`: o cabeçalho completo.
 */
export type FaixaDoCabecalho = 'celular' | 'tablet' | 'largo';

export const LIMITE_CELULAR = '(max-width: 767px)';
export const LIMITE_TABLET = '(max-width: 1023px)';

function consulta(q: string): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(q) : null;
}

function assinar(avisar: () => void): () => void {
  const lista = [consulta(LIMITE_CELULAR), consulta(LIMITE_TABLET)].filter((m): m is MediaQueryList => m !== null);
  for (const m of lista) m.addEventListener('change', avisar);
  return () => {
    for (const m of lista) m.removeEventListener('change', avisar);
  };
}

function atual(): FaixaDoCabecalho {
  if (consulta(LIMITE_CELULAR)?.matches) return 'celular';
  if (consulta(LIMITE_TABLET)?.matches) return 'tablet';
  return 'largo';
}

export function useFaixaDoCabecalho(): FaixaDoCabecalho {
  return useSyncExternalStore(assinar, atual, () => 'largo');
}
