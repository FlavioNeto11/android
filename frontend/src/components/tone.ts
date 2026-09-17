import type { Tone } from '../lib/status';
import ui from './ui.module.css';

const TONE_CLASS: Record<Tone, string | undefined> = {
  neutral: ui.toneNeutral,
  muted: ui.toneMuted,
  info: ui.toneInfo,
  accent: ui.toneAccent,
  success: ui.toneSuccess,
  warning: ui.toneWarning,
  danger: ui.toneDanger,
};

export function toneClass(tone: Tone): string {
  return TONE_CLASS[tone] ?? '';
}
