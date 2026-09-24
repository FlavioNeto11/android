import { useCallback, useState } from 'react';
import { isBoolean, loadJson, saveJson } from './storage';

/**
 * Sumário + seções recolhíveis (item 11.5): telas longas (Diagnóstico, Configuração) usam o mesmo par —
 * um link rola até a seção e a abre se estiver recolhida (`<details>` nativo), e o aberto/fechado de cada
 * seção fica lembrado no navegador para a próxima visita.
 */

/** Rola até `id` e abre a seção se ela for um `<details>` recolhido. */
export function jumpTo(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  if (el instanceof HTMLDetailsElement) el.open = true;
  // jsdom (testes) não implementa scrollIntoView.
  el.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
}

/** Estado aberto/fechado de uma seção, lembrado sob `storageKey`. `storageKey` deve ser estável entre renders. */
export function useSectionOpen(storageKey: string, defaultValue: boolean): [boolean, (open: boolean) => void] {
  const [open, setOpenState] = useState(() => loadJson(storageKey, isBoolean) ?? defaultValue);
  const setOpen = useCallback((v: boolean) => {
    setOpenState(v);
    saveJson(storageKey, v);
  }, [storageKey]);
  return [open, setOpen];
}
