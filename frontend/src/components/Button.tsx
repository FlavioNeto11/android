import { LoaderCircle, type LucideIcon } from 'lucide-react';
import { forwardRef, type ButtonHTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import { cx } from '../lib/format';
import { Tooltip } from './Tooltip';
import ui from './ui.module.css';

export type ButtonVariant = 'secondary' | 'primary' | 'ghost' | 'outline' | 'danger' | 'dangerGhost';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: ButtonVariant;
  size?: 'sm' | 'md' | 'lg';
  icon?: LucideIcon;
  /** Mostra um spinner e bloqueia o clique (requisição em voo). */
  loading?: boolean;
  /**
   * Quando preenchido, o botão fica indisponível MAS continua focável (aria-disabled) e explica o motivo
   * em um tooltip — um botão apagado sem explicação não ajuda ninguém.
   */
  disabledReason?: string | null;
  /** Botão só com ícone: `label` vira aria-label + tooltip. */
  iconOnly?: boolean;
  /**
   * O nome acessível. Com `iconOnly`, também o tooltip. Sem `iconOnly` e com texto visível (`children`), vira o
   * `aria-label` (30.67: antes ele era ignorado e o leitor de tela ouvia só "Não enviar este", sem dizer de qual item).
   * Deve CONTER o texto visível (WCAG 2.5.3, rótulo no nome): quem fala o que vê precisa acionar o botão.
   */
  label?: string;
  block?: boolean;
  children?: ReactNode;
}

const VARIANT: Record<ButtonVariant, string | undefined> = {
  secondary: undefined,
  primary: ui.btnPrimary,
  ghost: ui.btnGhost,
  outline: ui.btnOutline,
  danger: ui.btnDanger,
  dangerGhost: ui.btnDangerGhost,
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon: Icon, loading, disabledReason, iconOnly, label, block, children, className, onClick, disabled, type, ...rest },
  ref,
) {
  const blocked = !!disabledReason || !!loading;
  const iconSize = size === 'sm' ? 13 : size === 'lg' ? 17 : 15;
  const handleClick = (e: MouseEvent<HTMLButtonElement>) => {
    if (blocked) {
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    onClick?.(e);
  };
  const button = (
    <button
      ref={ref}
      type={type ?? 'button'}
      className={cx(ui.btn, VARIANT[variant], size === 'sm' && ui.btnSm, size === 'lg' && ui.btnLg, iconOnly && ui.btnIconOnly, block && ui.btnBlock, className)}
      aria-label={iconOnly || (label && children != null)
        ? (disabledReason ? `${label ?? ''} — indisponível: ${disabledReason}` : label)
        : rest['aria-label']}
      aria-disabled={blocked || undefined}
      aria-busy={loading || undefined}
      disabled={disabled}
      onClick={handleClick}
      {...rest}
    >
      {loading ? <LoaderCircle size={iconSize} className="spin" aria-hidden /> : Icon ? <Icon size={iconSize} aria-hidden /> : null}
      {iconOnly ? null : children ?? label}
      {/* O motivo entra no nome acessível: leitores de tela não dependem do tooltip visual. */}
      {!iconOnly && disabledReason ? <span className="sr-only"> — indisponível: {disabledReason}</span> : null}
    </button>
  );
  const tip = disabledReason || (iconOnly ? label : null);
  return tip ? <Tooltip content={tip}>{button}</Tooltip> : button;
});
