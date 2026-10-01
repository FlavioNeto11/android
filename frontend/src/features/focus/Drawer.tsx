import { useEffect, useRef, type KeyboardEvent, type ReactNode, type RefObject } from 'react';
import { focarConteudo } from '../../lib/scroll';
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

/**
 * Os marcadores de um aparelho na grade do Painel: o cartão (visão Cartões) e a linha (visão Lista). Uma lista só, lida
 * pelo "clique fora", pela devolução do foco e pela rolagem até o aparelho: com o marcador só do cartão, na Lista a
 * linha contava como "fora" e o clique para trocar de aparelho ou marcar era engolido (RF-02 da revisão final).
 */
export const MARCADORES_DE_APARELHO = ['data-instance-card', 'data-instance-row'] as const;

/** Seletor do aparelho `id` na grade (cartão ou linha), com um descendente opcional (`button[aria-label^="Abrir"]`). */
export function seletorDoAparelho(id: string, dentro = ''): string {
  return MARCADORES_DE_APARELHO.map((m) => `[${m}="${CSS.escape(id)}"]${dentro ? ` ${dentro}` : ''}`).join(', ');
}

/**
 * O que conta como "clicar fora" do drawer: o conteúdo da página (o `main` do App), menos os aparelhos da grade.
 * Ficam de fora de propósito, e continuam clicáveis com o drawer aberto: o menu lateral (os links levam o `?foco=` junto
 * para a tela seguinte) e os cartões e linhas de aparelho (trocar de aparelho, marcar). Também o topo, os popovers e
 * os avisos.
 */
const AREA_DE_FORA = '#conteudo';
const APARELHO = MARCADORES_DE_APARELHO.map((m) => `[${m}]`).join(', ');

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
  const fecharRef = useRef(onClose);
  fecharRef.current = onClose;

  // Clique fora: o primeiro clique na página SÓ fecha (é engolido, como num fundo de modal) em vez de também agir
  // sobre o que estava embaixo. Isso também evita o pior caso: um clique que navega logo depois de um `history.back()`
  // ainda pendente reabriria o Foco. Captura, para chegar antes do botão clicado.
  useEffect(() => {
    const aoClicar = (e: MouseEvent) => {
      const alvo = e.target;
      const painel = panelRef.current;
      if (!(alvo instanceof Element) || !painel || painel.contains(alvo)) return;
      const fora = alvo.closest('[data-drawer-scrim]') !== null
        || (alvo.closest(AREA_DE_FORA) !== null && alvo.closest(APARELHO) === null);
      if (!fora) return;
      e.preventDefault();
      e.stopPropagation();
      fecharRef.current();
    };
    document.addEventListener('click', aoClicar, true);
    return () => document.removeEventListener('click', aoClicar, true);
  }, [panelRef]);

  useEffect(() => {
    const ativo = document.activeElement;
    const origem = ativo instanceof HTMLElement && ativo !== document.body ? ativo : null;
    // O painel recebe o foco ao abrir (sem anel próprio): o Esc passa a funcionar sem a pessoa ter de clicar nele.
    panelRef.current?.focus();
    return () => {
      const alvo = origem?.isConnected ? origem : document.querySelector<HTMLElement>(restoreSelector);
      // Nem quem abriu nem o botão do cartão existem mais (aparelho fora da lista, link colado numa tela sem cartões): o
      // teclado vai ao contêiner do conteúdo, e não cai no <body>, de onde a pessoa recomeçaria do topo da página.
      if (alvo?.focus) alvo.focus({ preventScroll: true });
      else focarConteudo();
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
      {/* Só escurece: não pega o ponteiro (menu e cartões seguem clicáveis); o clique fora é tratado acima. */}
      <div className={styles.scrim} aria-hidden data-drawer-scrim />
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
