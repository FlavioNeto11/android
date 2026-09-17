import { Check, CircleAlert, Minus } from 'lucide-react';
import {
  forwardRef, useEffect, useId, useRef,
  type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from 'react';
import { cx } from '../lib/format';
import ui from './ui.module.css';

interface FieldProps {
  label: string;
  /** Unidade ou observação curta à direita do rótulo ("segundos", "opcional"). */
  unit?: string;
  hint?: ReactNode;
  error?: string | null;
  /** Recebe os ids para ligar o controle ao rótulo, à dica e ao erro. */
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode;
  className?: string;
}

export function Field({ label, unit, hint, error, children, className }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') || undefined;
  return (
    <div className={cx(ui.field, className)}>
      <div className={ui.fieldLabelRow}>
        <label htmlFor={id} className={ui.fieldLabel}>{label}</label>
        {unit ? <span className={ui.fieldUnit}>{unit}</span> : null}
      </div>
      {children({ id, describedBy, invalid: !!error })}
      {error ? (
        <p id={errorId} className={ui.fieldError} role="alert">
          <CircleAlert size={12} aria-hidden /> {error}
        </p>
      ) : null}
      {hint ? <p id={hintId} className={ui.fieldHint}>{hint}</p> : null}
    </div>
  );
}

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  invalid?: boolean;
  mono?: boolean;
  small?: boolean;
}

export const TextInput = forwardRef<HTMLInputElement, InputProps>(function TextInput({ invalid, mono, small, className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      className={cx(ui.input, invalid && ui.inputInvalid, mono && ui.inputMono, small && ui.inputSm, className)}
      aria-invalid={invalid || undefined}
      spellCheck={false}
      autoComplete="off"
      {...rest}
    />
  );
});

interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
  mono?: boolean;
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea({ invalid, mono, className, ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      className={cx(ui.textarea, invalid && ui.inputInvalid, mono && ui.inputMono, className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
});

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
  small?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select({ invalid, small, className, children, ...rest }, ref) {
  return (
    <select ref={ref} className={cx(ui.select, invalid && ui.inputInvalid, small && ui.inputSm, className)} aria-invalid={invalid || undefined} {...rest}>
      {children}
    </select>
  );
});

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** Rótulo visível; se omitido, informe `aria-label`. */
  label?: ReactNode;
  indeterminate?: boolean;
}

export function Checkbox({ label, indeterminate, className, ...rest }: CheckboxProps) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = !!indeterminate;
  }, [indeterminate]);
  return (
    <label className={cx(ui.checkbox, className)}>
      <input ref={ref} type="checkbox" className={ui.checkboxInput} {...rest} />
      <span className={ui.checkboxBox} aria-hidden>
        {indeterminate ? <Minus size={12} strokeWidth={3.5} /> : <Check size={12} strokeWidth={3.5} />}
      </span>
      {label ? <span className={ui.checkboxLabel}>{label}</span> : null}
    </label>
  );
}
