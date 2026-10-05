import type { LucideIcon } from 'lucide-react';
import { X } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { cx } from '../lib/format';
import type { Tone } from '../lib/status';
import { Button } from './Button';
import styles from './overlay.module.css';
import { toneClass } from './tone';

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  icon?: LucideIcon;
  tone?: Tone;
  size?: 'sm' | 'md' | 'lg';
  footer?: ReactNode;
  children: ReactNode;
  /**
   * Enquanto houver motivo, o diálogo não fecha: o X fica indisponível COM o motivo, e o Esc e o clique fora não fazem
   * nada. Antes, quem bloqueava fechar ignorava o `onClose`, e o X parecia ativo sem dizer por que não fechava.
   */
  closeBlockedReason?: string | null;
}

/**
 * Modal sobre o <dialog> nativo: foco preso, Esc fecha, fundo inerte e top layer de graça.
 * Clique no backdrop também fecha.
 */
export function Dialog({ open, onClose, title, icon: Icon, tone, size = 'sm', footer, children, closeBlockedReason }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      try {
        el.showModal();
      } catch {
        el.setAttribute('open', '');
      }
    } else if (!open && el.open) {
      el.close();
    }
  }, [open]);

  if (!open) return null;
  const fechar = () => {
    if (!closeBlockedReason) onClose();
  };

  return (
    <dialog
      ref={ref}
      className={cx(styles.dialog, size === 'md' && styles.dialogMd, size === 'lg' && styles.dialogLg, tone && toneClass(tone))}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        fechar();
      }}
      onClose={onClose}
      onMouseDown={(e) => {
        if (e.target === ref.current) fechar(); // clique no backdrop
      }}
    >
      <header className={styles.dialogHeader}>
        {Icon ? <Icon size={18} className={styles.dialogIcon} aria-hidden /> : null}
        <h2 id={titleId} className={styles.dialogTitle}>{title}</h2>
        <Button variant="ghost" size="sm" icon={X} iconOnly label="Fechar" disabledReason={closeBlockedReason} onClick={onClose} />
      </header>
      <div className={styles.dialogBody}>{children}</div>
      {footer ? <footer className={styles.dialogFooter}>{footer}</footer> : null}
    </dialog>
  );
}
