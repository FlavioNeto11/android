import { ChevronRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { cx } from '../lib/format';
import ui from './ui.module.css';

interface DisclosureProps {
  summary: ReactNode;
  /** Texto discreto alinhado à direita do resumo (contagens, horários…). */
  meta?: ReactNode;
  defaultOpen?: boolean;
  /** Sem moldura — para uso dentro de cartões e linhas. */
  bare?: boolean;
  /** Avisado na primeira abertura (carregamento sob demanda). */
  onFirstOpen?: () => void;
  onToggle?: (open: boolean) => void;
  className?: string;
  /** Conteúdo; se for função, só é montado depois de aberto (evita renderizar JSON grande à toa). */
  children: ReactNode | (() => ReactNode);
}

/** <details>/<summary> nativo: teclado e leitores de tela funcionam sem código extra. */
export function Disclosure({ summary, meta, defaultOpen = false, bare, onFirstOpen, onToggle, className, children }: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [everOpened, setEverOpened] = useState(defaultOpen);

  return (
    <details
      className={cx(ui.disclosure, bare && ui.disclosureBare, className)}
      open={open}
      onToggle={(e) => {
        const isOpen = e.currentTarget.open;
        if (isOpen === open) return;
        setOpen(isOpen);
        if (isOpen && !everOpened) {
          setEverOpened(true);
          onFirstOpen?.();
        }
        onToggle?.(isOpen);
      }}
    >
      <summary className={ui.disclosureSummary}>
        <ChevronRight size={14} className={ui.disclosureChevron} aria-hidden />
        <span>{summary}</span>
        {meta ? <span className={ui.disclosureMeta}>{meta}</span> : null}
      </summary>
      <div className={ui.disclosureBody}>
        {typeof children === 'function' ? (everOpened ? children() : null) : children}
      </div>
    </details>
  );
}
