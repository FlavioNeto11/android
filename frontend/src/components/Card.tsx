import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from '../lib/format';
import ui from './ui.module.css';

interface CardProps extends HTMLAttributes<HTMLElement> {
  padded?: boolean;
  as?: 'section' | 'div' | 'article';
}

export function Card({ padded, as: Tag = 'section', className, children, ...rest }: CardProps) {
  return (
    <Tag className={cx(ui.card, padded && ui.cardPadded, className)} {...rest}>
      {children}
    </Tag>
  );
}

interface CardHeaderProps {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  titleId?: string;
  level?: 2 | 3;
}

export function CardHeader({ title, subtitle, actions, titleId, level = 2 }: CardHeaderProps) {
  const H = level === 2 ? 'h2' : 'h3';
  return (
    <header className={ui.cardHeader}>
      <div className={ui.cardHeaderText}>
        <H id={titleId} className={ui.cardTitle}>{title}</H>
        {subtitle ? <p className={ui.cardSubtitle}>{subtitle}</p> : null}
      </div>
      {actions ? <div className={ui.cardActions}>{actions}</div> : null}
    </header>
  );
}

export function CardBody({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx(ui.cardBody, className)} {...rest}>
      {children}
    </div>
  );
}
