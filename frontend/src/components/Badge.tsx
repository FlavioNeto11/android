import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cx } from '../lib/format';
import type { Tone } from '../lib/status';
import { toneClass } from './tone';
import ui from './ui.module.css';

export interface BadgeProps {
  tone?: Tone;
  icon?: LucideIcon;
  spin?: boolean;
  size?: 'sm' | 'md' | 'lg';
  /** Fundo sólido — reservado para avisos que não podem passar despercebidos (ex.: MODO SIMULADO). */
  solid?: boolean;
  /** Sem cápsula: só ícone + texto coloridos (para linhas densas de tabela). */
  plain?: boolean;
  title?: string;
  className?: string;
  children: ReactNode;
}

export function Badge({ tone = 'neutral', icon: Icon, spin, size = 'md', solid, plain, title, className, children }: BadgeProps) {
  const iconSize = size === 'sm' ? 11 : size === 'lg' ? 15 : 12.5;
  return (
    <span
      className={cx(ui.badge, toneClass(tone), size === 'sm' && ui.badgeSm, size === 'lg' && ui.badgeLg, solid && ui.badgeSolid, plain && ui.badgePlain, className)}
      title={title}
    >
      {Icon ? <Icon size={iconSize} className={spin ? 'spin' : undefined} aria-hidden /> : null}
      <span className={ui.badgeLabel}>{children}</span>
    </span>
  );
}
