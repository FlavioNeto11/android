import { ChevronRight } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { cx } from '../lib/format';
import ui from './ui.module.css';

interface DisclosureProps {
  summary: ReactNode;
  /** Texto discreto alinhado à direita do resumo (contagens, horários…). */
  meta?: ReactNode;
  defaultOpen?: boolean;
  /**
   * Abre quando passa de falso a verdadeiro, depois de montado também; nunca fecha. É o `defaultOpen` para o que
   * depende de uma leitura que pode chegar depois do bloco (ex.: a saúde do Livro), sem desfazer o que a pessoa
   * fechou ou abriu (29.112).
   */
  openWhen?: boolean;
  /** Sem moldura — para uso dentro de cartões e linhas. */
  bare?: boolean;
  /** Avisado na primeira abertura (carregamento sob demanda). */
  onFirstOpen?: () => void;
  onToggle?: (open: boolean) => void;
  className?: string;
  /** Âncora: permite `document.getElementById(id)` rolar até aqui e abrir (ex.: sumário do diagnóstico). */
  id?: string;
  /** Conteúdo; se for função, só é montado depois de aberto (evita renderizar JSON grande à toa). */
  children: ReactNode | (() => ReactNode);
}

/** <details>/<summary> nativo: teclado e leitores de tela funcionam sem código extra. */
export function Disclosure({ summary, meta, defaultOpen = false, openWhen = false, bare, onFirstOpen, onToggle, className, id, children }: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen || openWhen);
  const [everOpened, setEverOpened] = useState(defaultOpen || openWhen);
  const pediaAntes = useRef(openWhen);

  useEffect(() => {
    const subiu = openWhen && !pediaAntes.current;
    pediaAntes.current = openWhen;
    if (!subiu) return;
    setOpen(true);
    if (!everOpened) {
      setEverOpened(true);
      onFirstOpen?.();
    }
    // Só a subida do `openWhen` importa: `everOpened` e `onFirstOpen` mudarem não reabre o que a pessoa fechou.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openWhen]);

  return (
    <details
      id={id}
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
