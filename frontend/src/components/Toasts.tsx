import { CircleCheck, CircleX, Info, TriangleAlert, X, type LucideIcon } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { cx } from '../lib/format';
import type { Tone } from '../lib/status';
import { useToastStore, type Toast, type ToastTone } from '../store/toasts';
import styles from './overlay.module.css';
import { toneClass } from './tone';

const ICON: Record<ToastTone, LucideIcon> = { info: Info, success: CircleCheck, warning: TriangleAlert, danger: CircleX };
const TONE: Record<ToastTone, Tone> = { info: 'info', success: 'success', warning: 'warning', danger: 'danger' };
const TONE_LABEL: Record<ToastTone, string> = { info: 'Informação', success: 'Sucesso', warning: 'Aviso', danger: 'Erro' };

function ToastItem({ toast }: { toast: Toast }) {
  const dismiss = useToastStore((s) => s.dismiss);
  const hovering = useRef(false);

  useEffect(() => {
    if (toast.durationMs <= 0) return;
    let timer: ReturnType<typeof setTimeout>;
    const arm = () => {
      timer = setTimeout(() => {
        if (hovering.current) arm(); // não some enquanto o ponteiro está em cima
        else dismiss(toast.id);
      }, toast.durationMs);
    };
    arm();
    return () => clearTimeout(timer);
  }, [toast.id, toast.durationMs, dismiss]);

  const Icon = ICON[toast.tone];
  return (
    <li
      className={cx(styles.toast, toneClass(TONE[toast.tone]))}
      role={toast.tone === 'danger' || toast.tone === 'warning' ? 'alert' : 'status'}
      onMouseEnter={() => (hovering.current = true)}
      onMouseLeave={() => (hovering.current = false)}
    >
      <Icon size={17} className={styles.toastIcon} aria-hidden />
      <div className={styles.toastBody}>
        <p className={styles.toastTitle}>
          <span className="sr-only">{TONE_LABEL[toast.tone]}: </span>
          {toast.title}
        </p>
        {toast.message ? <p className={styles.toastMessage}>{toast.message}</p> : null}
        {toast.details && toast.details.length > 0 ? (
          <ul className={styles.toastDetails}>
            {toast.details.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        ) : null}
        {toast.hint ? <p className={styles.toastHint}>{toast.hint}</p> : null}
      </div>
      <button type="button" className={styles.toastClose} aria-label="Fechar aviso" onClick={() => dismiss(toast.id)}>
        <X size={14} aria-hidden />
      </button>
    </li>
  );
}

/**
 * Região de toasts. Usa `popover="manual"` para entrar no top layer: assim os avisos continuam visíveis
 * e clicáveis mesmo com um <dialog> modal aberto. A cada toast novo o popover é reaberto para voltar ao
 * topo da pilha. Sem suporte a Popover API, cai para `position: fixed` comum.
 */
export function Toasts() {
  const toasts = useToastStore((s) => s.toasts);
  const ref = useRef<HTMLUListElement>(null);
  const lastId = toasts.length > 0 ? toasts[toasts.length - 1]?.id : null;

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof el.showPopover !== 'function') return;
    try {
      if (el.matches(':popover-open')) el.hidePopover();
      if (lastId) el.showPopover();
    } catch {
      /* fallback: continua como elemento fixo normal */
    }
  }, [lastId]);

  return (
    <ul ref={ref} className={styles.toastRegion} aria-label="Notificações" popover="manual">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} />
      ))}
    </ul>
  );
}
