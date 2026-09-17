import { LoaderCircle } from 'lucide-react';
import { cx } from '../lib/format';
import ui from './ui.module.css';

interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** Nome acessível: diga O QUE é ligado/desligado (ex.: "Fluxo “Enviar mensagem” ativo"). */
  label: string;
  /** Texto visível ao lado do interruptor para cada posição — o estado nunca é comunicado só por cor. */
  onText?: string;
  offText?: string;
  /** Requisição em voo: mostra o spinner e ignora cliques. */
  busy?: boolean;
  disabled?: boolean;
}

/** Interruptor liga/desliga (`role="switch"`): Espaço/Enter alternam, como em qualquer botão. */
export function Switch({ checked, onChange, label, onText = 'Ativo', offText = 'Desativado', busy, disabled }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-busy={busy || undefined}
      disabled={disabled}
      className={cx(ui.switch, checked && ui.switchOn)}
      onClick={() => {
        if (!busy) onChange(!checked);
      }}
    >
      <span className={ui.switchTrack} aria-hidden>
        <span className={ui.switchThumb}>{busy ? <LoaderCircle size={10} className="spin" /> : null}</span>
      </span>
      <span className={ui.switchText}>{checked ? onText : offText}</span>
    </button>
  );
}
