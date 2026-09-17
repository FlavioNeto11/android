import type { StatusMeta } from '../lib/status';
import { Badge, type BadgeProps } from './Badge';

interface StatusBadgeProps extends Pick<BadgeProps, 'size' | 'plain' | 'className'> {
  meta: StatusMeta;
  /** Substitui o rótulo padrão (ex.: "Controle: IA"). */
  label?: string;
  /** Prefixo lido por leitores de tela (ex.: "Estado"). */
  srPrefix?: string;
}

/** Status = ícone (forma) + texto + cor. Nunca só cor. */
export function StatusBadge({ meta, label, srPrefix, ...rest }: StatusBadgeProps) {
  return (
    <Badge tone={meta.tone} icon={meta.icon} spin={meta.spin} title={meta.description} {...rest}>
      {srPrefix ? <span className="sr-only">{srPrefix}: </span> : null}
      {label ?? meta.label}
    </Badge>
  );
}
