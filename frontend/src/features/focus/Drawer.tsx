import { useEffect, type KeyboardEvent, type ReactNode, type RefObject } from 'react';
import styles from './Focus.module.css';

const FOCAVEIS =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), '
  + 'textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/**
 * O corpo de um `<details>` fechado não recebe foco (só o `<summary>` dele). O Chromium ainda devolve retângulos para esse
 * corpo, então `getClientRects` não basta para saber.
 */
function dentroDeDetalhesFechado(el: HTMLElement, raiz: HTMLElement): boolean {
  for (let no: HTMLElement | null = el; no && no !== raiz; no = no.parentElement) {
    const pai = no.parentElement;
    if (pai instanceof HTMLDetailsElement && !pai.open && no.tagName !== 'SUMMARY') return true;
  }
  return false;
}

/**
 * O que o Tab alcança dentro do drawer, na ordem do documento. Conteúdo de `<details>` fechado e elementos ocultos
 * não entram (a Hierarquia é a última seção e fica fechada: sem este filtro "o último" seria um botão invisível e o
 * Tab escaparia do drawer). Sem layout (jsdom) `getClientRects` vem vazio para tudo: aí vale a lista inteira.
 */
export function elementosFocaveis(raiz: HTMLElement): HTMLElement[] {
  const todos = [...raiz.querySelectorAll<HTMLElement>(FOCAVEIS)]
    .filter((el) => !el.hidden && el.getAttribute('aria-hidden') !== 'true' && !dentroDeDetalhesFechado(el, raiz));
  const visiveis = todos.filter((el) => el.getClientRects().length > 0);
  return visiveis.length > 0 ? visiveis : todos;
}

interface DrawerProps {
  panelRef: RefObject<HTMLElement | null>;
  ariaLabel: string;
  onClose: () => void;
  /** Para onde o teclado volta ao fechar, quando o elemento que abriu o drawer já não existe. */
  restoreSelector: string;
  children: ReactNode;
}

/**
 * A casca do Foco: um drawer lateral SOBREPOSTO ao conteúdo (não reorganiza a grade), modal de verdade.
 *  - `aria-modal` e o teclado preso dentro (Tab e Shift+Tab dão a volta);
 *  - Esc e clique no fundo escurecido fecham; Esc dentro de um popover, de uma caixa de confirmação ou de um campo
 *    de texto fecha SÓ aquilo;
 *  - ao fechar, o teclado volta para quem abriu (o botão do cartão), para a pessoa não ter de procurar onde estava.
 * Abaixo de 720 px de janela o painel é tela cheia (Focus.module.css) e o fundo nem aparece.
 */
export function Drawer({ panelRef, ariaLabel, onClose, restoreSelector, children }: DrawerProps) {
  useEffect(() => {
    const ativo = document.activeElement;
    const origem = ativo instanceof HTMLElement && ativo !== document.body ? ativo : null;
    // O painel recebe o foco ao abrir (sem anel próprio): o Esc passa a funcionar sem a pessoa ter de clicar nele.
    panelRef.current?.focus();
    return () => {
      const alvo = origem?.isConnected ? origem : document.querySelector<HTMLElement>(restoreSelector);
      alvo?.focus?.({ preventScroll: true });
    };
    // Só na montagem e na desmontagem: o painel é remontado por aparelho (`key`), não reage a props.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const aoTeclar = (e: KeyboardEvent<HTMLElement>) => {
    const painel = panelRef.current;
    if (!painel || e.defaultPrevented) return;
    const alvo = e.target as HTMLElement;
    if (e.key === 'Escape') {
      if (alvo.closest('dialog')) return;
      // Popover e afins são `div[role=dialog]` em portal: o Esc deles é deles. O painel também é `role=dialog`.
      const interno = alvo.closest('[role="dialog"]');
      if (interno && interno !== painel) return;
      if (alvo instanceof HTMLInputElement || alvo instanceof HTMLTextAreaElement) return;
      onClose();
      return;
    }
    // Um Tab que nasce num portal (popover aberto) não é deste painel.
    if (e.key !== 'Tab' || !painel.contains(alvo)) return;
    const lista = elementosFocaveis(painel);
    const primeiro = lista[0];
    const ultimo = lista[lista.length - 1];
    if (!primeiro || !ultimo) {
      e.preventDefault();
      painel.focus();
    } else if (!e.shiftKey && alvo === ultimo) {
      e.preventDefault();
      primeiro.focus();
    } else if (e.shiftKey && (alvo === primeiro || alvo === painel)) {
      e.preventDefault();
      ultimo.focus();
    }
  };

  return (
    <>
      {/* O fundo só existe para o clique fora: não é foco nem conteúdo. */}
      <div className={styles.scrim} aria-hidden onClick={onClose} data-drawer-scrim />
      <aside
        ref={panelRef}
        tabIndex={-1}
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        onKeyDown={aoTeclar}
      >
        {children}
      </aside>
    </>
  );
}
