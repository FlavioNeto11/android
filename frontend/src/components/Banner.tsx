import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cx } from '../lib/format';
import type { Tone } from '../lib/status';
import { toneClass } from './tone';
import ui from './ui.module.css';

interface BannerProps {
  tone: Tone;
  icon: LucideIcon;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
  /** `alert` interrompe leitores de tela; use só para o que exige ação. */
  role?: 'alert' | 'status' | 'note';
  className?: string;
}

export function Banner({ tone, icon: Icon, title, children, actions, compact, role = 'note', className }: BannerProps) {
  return (
    <div className={cx(ui.banner, toneClass(tone), compact && ui.bannerCompact, className)} role={role}>
      <Icon size={compact ? 14 : 16} className={ui.bannerIcon} aria-hidden />
      <div className={ui.bannerBody}>
        {title ? <p className={ui.bannerTitle}>{title}</p> : null}
        {children ? <div className={ui.bannerText}>{children}</div> : null}
      </div>
      {actions ? <div className={ui.bannerActions}>{actions}</div> : null}
    </div>
  );
}
