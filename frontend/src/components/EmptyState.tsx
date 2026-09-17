import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cx } from '../lib/format';
import type { Tone } from '../lib/status';
import { toneClass } from './tone';
import ui from './ui.module.css';

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  /** O que aconteceu. */
  children?: ReactNode;
  /** Próximo passo útil. */
  hint?: ReactNode;
  actions?: ReactNode;
  tone?: Tone;
  compact?: boolean;
}

export function EmptyState({ icon: Icon, title, children, hint, actions, tone, compact }: EmptyStateProps) {
  return (
    <div className={cx(ui.empty, compact && ui.emptyCompact, tone && toneClass(tone))}>
      <span className={ui.emptyIcon}><Icon size={20} aria-hidden /></span>
      <p className={ui.emptyTitle}>{title}</p>
      {children ? <p className={ui.emptyText}>{children}</p> : null}
      {hint ? <p className={ui.emptyHint}>{hint}</p> : null}
      {actions ? <div className={ui.emptyActions}>{actions}</div> : null}
    </div>
  );
}
