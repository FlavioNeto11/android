import { clamp01, cx } from '../lib/format';
import type { Tone } from '../lib/status';
import { toneClass } from './tone';
import ui from './ui.module.css';

interface ProgressBarProps {
  /** 0..1 */
  value: number;
  /** Nome acessível, ex.: "Progresso das etapas". */
  label: string;
  /** Texto visível ao lado da barra, ex.: "3/7". */
  text?: string;
  tone?: Tone;
  thick?: boolean;
  className?: string;
}

export function ProgressBar({ value, label, text, tone = 'accent', thick, className }: ProgressBarProps) {
  const v = clamp01(value);
  const pct = Math.round(v * 100);
  return (
    <div className={cx(ui.progress, thick && ui.progressThick, toneClass(tone), className)}>
      <div
        className={ui.progressTrack}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={text ? `${text} (${pct}%)` : `${pct}%`}
      >
        <div className={ui.progressFill} style={{ width: `${pct}%` }} />
      </div>
      {text ? <span className={ui.progressLabel}>{text}</span> : null}
    </div>
  );
}

export interface StackedSegment {
  key: string;
  label: string;
  value: number;
  tone: Tone;
  /** Hachura: segunda pista visual além da cor para os segmentos que pedem atenção. */
  hatch?: boolean;
}

/** Barra empilhada (decorativa: os números exatos ficam nos contadores ao lado, com ícone e texto). */
export function StackedBar({ segments, label }: { segments: StackedSegment[]; label: string }) {
  const total = segments.reduce((acc, s) => acc + Math.max(0, s.value), 0);
  const text = segments.filter((s) => s.value > 0).map((s) => `${s.label}: ${s.value}`).join(', ');
  return (
    <div className={ui.stacked} role="img" aria-label={`${label}. ${text || 'Sem dados'}`}>
      {total > 0
        ? segments
            .filter((s) => s.value > 0)
            .map((s) => (
              <span
                key={s.key}
                className={cx(ui.stackedSeg, toneClass(s.tone), s.hatch && ui.stackedHatch)}
                style={{ flexGrow: s.value, flexBasis: 0 }}
                title={`${s.label}: ${s.value}`}
              />
            ))
        : null}
    </div>
  );
}
