import { useCallback, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cx } from '../lib/format';
import styles from './overlay.module.css';

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  placement?: 'top' | 'bottom';
  /** `inline-flex` (padrão) ou `block` para envolver elementos de largura total. */
  display?: 'inline-flex' | 'block' | 'flex';
  className?: string;
}

interface Pos {
  left: number;
  top: number;
  placement: 'top' | 'bottom';
}

/**
 * Tooltip acessível: aparece em hover E em foco de teclado, some com Esc, é ligado por `aria-describedby`.
 * Renderizado em portal com `position: fixed` para não ser cortado por contêineres com overflow; se o
 * gatilho estiver dentro de um <dialog> modal, o portal vai para dentro dele (top layer).
 */
export function Tooltip({ content, children, placement = 'top', display = 'inline-flex', className }: TooltipProps) {
  const id = useId();
  const hostRef = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback((delay: number) => {
    if (showTimer.current) clearTimeout(showTimer.current);
    showTimer.current = setTimeout(() => setOpen(true), delay);
  }, []);
  const hide = useCallback(() => {
    if (showTimer.current) clearTimeout(showTimer.current);
    showTimer.current = null;
    setOpen(false);
    setPos(null);
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    const host = hostRef.current;
    const tip = tipRef.current;
    if (!host || !tip) return;
    const anchor = (host.firstElementChild as HTMLElement | null) ?? host;
    const r = anchor.getBoundingClientRect();
    const t = tip.getBoundingClientRect();
    const margin = 8;
    let place = placement;
    if (place === 'top' && r.top - t.height - margin < 4) place = 'bottom';
    if (place === 'bottom' && r.bottom + t.height + margin > window.innerHeight - 4) place = 'top';
    const left = Math.min(Math.max(6, r.left + r.width / 2 - t.width / 2), window.innerWidth - t.width - 6);
    const top = place === 'top' ? r.top - t.height - margin : r.bottom + margin;
    setPos({ left, top, placement: place });
  }, [open, placement, content]);

  useLayoutEffect(() => () => {
    if (showTimer.current) clearTimeout(showTimer.current);
  }, []);

  const container = open ? (hostRef.current?.closest('dialog') ?? document.body) : null;

  return (
    <span
      ref={hostRef}
      className={cx(styles.tooltipHost, className)}
      style={{ display }}
      aria-describedby={open ? id : undefined}
      onMouseEnter={() => show(250)}
      onMouseLeave={hide}
      onFocus={() => show(0)}
      onBlur={hide}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) hide();
      }}
    >
      {children}
      {open && container
        ? createPortal(
            <span
              ref={tipRef}
              id={id}
              role="tooltip"
              className={styles.tooltip}
              style={pos ? { left: pos.left, top: pos.top, opacity: 1 } : { left: 0, top: 0, opacity: 0 }}
            >
              {content}
            </span>,
            container,
          )
        : null}
    </span>
  );
}
