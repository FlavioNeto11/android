import { LoaderCircle } from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import { cx } from '../lib/format';
import ui from './ui.module.css';

interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  className?: string;
  style?: CSSProperties;
}

export function Skeleton({ width = '100%', height = 14, radius, className, style }: SkeletonProps) {
  return <span aria-hidden className={cx(ui.skeleton, className)} style={{ width, height, borderRadius: radius, ...style }} />;
}

/** Bloco "carregando" anunciado para leitores de tela, com esqueletos visuais dentro. */
export function LoadingRegion({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

export function Spinner({ size = 16, label }: { size?: number; label?: string }) {
  return (
    <span role={label ? 'status' : undefined} style={{ display: 'inline-flex' }}>
      <LoaderCircle size={size} className={cx('spin', ui.spinner)} aria-hidden />
      {label ? <span className="sr-only">{label}</span> : null}
    </span>
  );
}
