import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
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
  // Lado efetivo: `align` é a preferência; se o painel sair da tela, vira para o outro lado. Medido no Foco do
  // android-06 (viewport 1024): "Instalar app" abria alinhado à esquerda e cortava "(promovida)" na borda direita.
  const [lado, setLado] = useState(align);

  useLayoutEffect(() => {
    if (!open) return;
    const el = panelRef.current;
    if (!el) return;
    if (lado !== align) return;          // vira uma vez só por abertura: sem espaço dos dois lados, não oscila
    const r = el.getBoundingClientRect();
    if (align === 'start' && r.right > window.innerWidth - 8) setLado('end');
    else if (align === 'end' && r.left < 8) setLado('start');
  }, [open, lado, align]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (hostRef.current && !hostRef.current.contains(e.target as Node)) setOpen(false);
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
        onClick={() => {
          setLado(align);
          setOpen((v) => !v);
        }}
      >
        {trigger}
      </button>
      {open ? (
        <div ref={panelRef} id={panelId} role="dialog" aria-label={title ?? label} data-side={lado}
             className={cx(styles.popoverPanel, lado === 'end' ? styles.popoverEnd : styles.popoverStart)}>
          {title ? <h3 className={styles.popoverTitle}>{title}</h3> : null}
          {typeof children === 'function' ? children(close) : children}
        </div>
      ) : null}
    </span>
  );
}
