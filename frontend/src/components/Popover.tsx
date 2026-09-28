import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cx } from '../lib/format';
import styles from './overlay.module.css';

interface PopoverProps {
  /** Conteúdo do botão gatilho. */
  trigger: ReactNode;
  triggerClassName?: string;
  /** Nome acessível do gatilho. */
  label: string;
  title?: string;
  align?: 'start' | 'end';
  children: ReactNode | ((close: () => void) => ReactNode);
}

/** Popover por clique: fecha com Esc, clique fora ou perda de foco; devolve o foco ao gatilho. */
export function Popover({ trigger, triggerClassName, label, title, align = 'start', children }: PopoverProps) {
  const [open, setOpen] = useState(false);
  const hostRef = useRef<HTMLSpanElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  // Posição FIXA, calculada a partir do gatilho e presa à tela. Medido no Foco do android-06 (viewport 1024): o
  // painel absoluto era cortado — primeiro pela borda direita ("(promovida)" sumia), depois de virar de lado, pelo
  // `overflow` da coluna de ações. Fixo, ele não pertence a nenhum contêiner que role ou corte.
  // E vai para o `body` por PORTAL (evolução 2): a página e o Foco viraram contêineres (`container-type`), e a
  // contenção de layout faz de um contêiner o bloco de contenção de `position: fixed` — o painel sairia deslocado
  // pela posição da página e recortado pela rolagem do `main` ou da coluna do Foco.
  const [pos, setPos] = useState<{ top: number; left: number; lado: 'start' | 'end' } | null>(null);

  const posicionar = useCallback(() => {
    const b = btnRef.current;
    const p = panelRef.current;
    if (!b || !p) return;
    const br = b.getBoundingClientRect();
    const { width: w, height: h } = p.getBoundingClientRect();
    const m = 8;
    const preferida = align === 'end' ? br.right - w : br.left;
    const left = Math.max(m, Math.min(preferida, window.innerWidth - w - m));
    const abaixo = br.bottom + m;
    const top = abaixo + h <= window.innerHeight - m ? abaixo
      : br.top - m - h >= m ? br.top - m - h : Math.max(m, window.innerHeight - h - m);
    setPos({ top, left, lado: left === preferida ? align : align === 'start' ? 'end' : 'start' });
  }, [align]);

  useLayoutEffect(() => {
    if (open) posicionar();
    else setPos(null);
  }, [open, posicionar]);

  useEffect(() => {
    if (!open) return;
    window.addEventListener('resize', posicionar);
    window.addEventListener('scroll', posicionar, true);     // captura: a rolagem de qualquer coluna move o gatilho
    return () => {
      window.removeEventListener('resize', posicionar);
      window.removeEventListener('scroll', posicionar, true);
    };
  }, [open, posicionar]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const alvo = e.target as Node;
      // O painel mora fora do host (portal): clique nele não é "clique fora".
      if (hostRef.current?.contains(alvo) || panelRef.current?.contains(alvo)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const close = () => setOpen(false);

  // No portal, o painel fica no fim do `body`: sem isto o Tab do gatilho pularia o conteúdo aberto.
  const tabParaDentro = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!open || e.key !== 'Tab' || e.shiftKey) return;
    const primeiro = panelRef.current?.querySelector<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])');
    if (!primeiro) return;
    e.preventDefault();
    primeiro.focus();
  };

  return (
    <span ref={hostRef} className={styles.popoverHost}>
      <button
        ref={btnRef}
        type="button"
        className={triggerClassName}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={tabParaDentro}
      >
        {trigger}
      </button>
      {open ? createPortal(
        <div ref={panelRef} id={panelId} role="dialog" aria-label={title ?? label} data-side={pos?.lado ?? align}
             className={cx(styles.popoverPanel, align === 'end' ? styles.popoverEnd : styles.popoverStart)}
             // Antes de medir, invisível no canto: evita um quadro no lugar errado.
             style={pos ? { position: 'fixed', top: pos.top, left: pos.left, right: 'auto' }
               : { position: 'fixed', top: 0, left: 0, visibility: 'hidden' }}>
          {title ? <h3 className={styles.popoverTitle}>{title}</h3> : null}
          {typeof children === 'function' ? children(close) : children}
        </div>,
        document.body,
      ) : null}
    </span>
  );
}
