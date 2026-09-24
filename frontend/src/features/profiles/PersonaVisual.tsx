/**
 * Item 11.6 — "Perfil como uma pessoa, não como formulário".
 *
 * Peças visuais reaproveitadas pela Visão geral e pela aba Persona: réguas marcam o valor certo dentro de
 * um espectro fixo (em vez de um <select> escondendo a escala), etiquetas para interesses, duas colunas
 * coloridas para "Diz" × "Nunca diz", balões de conversa para os exemplos e um medidor de completude que
 * aponta exatamente o que falta (a mesma lista que o backend calcula em `persona.voice_gaps`, para o aviso
 * nunca divergir do que o modelo realmente recebe).
 */
import type { ReactNode } from 'react';
import { Badge } from '../../components/Badge';
import { ProgressBar } from '../../components/ProgressBar';
import { cx } from '../../lib/format';
import styles from './Profiles.module.css';

/** Rótulo de cada traço de VOZ — a MESMA lista usada pelo aviso de campos faltando e pelo medidor de completude. */
export const ROTULO_DE_VOZ: Record<string, string> = {
  personality: 'Personalidade', tone: 'Tom', formality: 'Formalidade', typical_length: 'Tamanho típico',
  emojis: 'Emojis', slang: 'Gírias', humor: 'Humor', interests: 'Interesses',
  dm_style: 'Estilo em mensagem direta', comment_style: 'Estilo em comentário',
  with_known: 'Com quem já conhece', with_strangers: 'Com desconhecidos',
  common_phrases: 'Expressões comuns', forbidden_phrases: 'Expressões proibidas', examples: 'Exemplos',
};

export const FORMALITY_OPTIONS = [
  { value: 'informal', label: 'Informal' }, { value: 'neutro', label: 'Neutro' }, { value: 'formal', label: 'Formal' },
] as const;
export const LENGTH_OPTIONS = [
  { value: 'curta', label: 'Curta' }, { value: 'media', label: 'Média' }, { value: 'longa', label: 'Longa' },
] as const;
export const EMOJI_OPTIONS = [
  { value: 'nunca', label: 'Nunca' }, { value: 'raro', label: 'Raro' },
  { value: 'moderado', label: 'Moderado' }, { value: 'muito', label: 'Muito' },
] as const;

/** Uma régua visual: marca o valor atual entre paradas fixas, em vez de um formulário escondendo a escala. */
export function Ruler({ label, options, value, compact }: {
  label: string;
  options: readonly { value: string; label: string }[];
  value?: string | null;
  compact?: boolean;
}) {
  const ativo = options.find((o) => o.value === value);
  return (
    <div className={cx(styles.ruler, compact && styles.rulerCompact)}>
      <span className={styles.rulerLabel}>{label}</span>
      <ol className={styles.rulerTrack} aria-label={`${label}: ${ativo ? ativo.label : 'não definido'}`}>
        {options.map((opt) => {
          const marcado = opt.value === value;
          return (
            <li key={opt.value} className={cx(styles.rulerStop, marcado && styles.rulerStopActive)}
                data-active={marcado || undefined}>
              <span className={styles.rulerDot} aria-hidden />
              <span className={styles.rulerStopLabel}>{opt.label}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** Interesses (ou qualquer lista curta) como etiquetas, não como textarea de "um por linha". */
export function TagList({ items, empty }: { items: string[]; empty?: string }) {
  if (items.length === 0) return empty ? <p className={styles.muted}>{empty}</p> : null;
  return (
    <ul className={styles.tagList}>
      {items.map((it, i) => (
        <li key={`${it}-${i}`}><Badge>{it}</Badge></li>
      ))}
    </ul>
  );
}

/** "Diz" × "Nunca diz": expressões comuns e proibidas lado a lado, em colunas coloridas. */
export function PhraseColumns({ common, forbidden }: { common: string[]; forbidden: string[] }) {
  return (
    <div className={styles.phrasePair}>
      <div className={cx(styles.phraseCol, styles.phraseColSay)}>
        <h4>Diz</h4>
        {common.length === 0 ? <p className={styles.muted}>Nada registrado.</p> : (
          <ul>{common.map((f, i) => <li key={i}>“{f}”</li>)}</ul>
        )}
      </div>
      <div className={cx(styles.phraseCol, styles.phraseColNever)}>
        <h4>Nunca diz</h4>
        {forbidden.length === 0 ? <p className={styles.muted}>Nada registrado.</p> : (
          <ul>{forbidden.map((f, i) => <li key={i}>“{f}”</li>)}</ul>
        )}
      </div>
    </div>
  );
}

/** Exemplos de mensagens como balões de conversa — não uma lista de texto solto. */
export function ExampleBubbles({ examples }: { examples: string[] }) {
  if (examples.length === 0) return <p className={styles.muted}>Sem exemplos registrados.</p>;
  return (
    <div className={styles.bubbles}>
      {examples.map((ex, i) => <p key={i} className={styles.bubble}>{ex}</p>)}
    </div>
  );
}

/** Par lado a lado: "Com conhecidos" × "Com estranhos", "Em DM" × "Em comentário". */
export function PairColumns({ leftLabel, left, rightLabel, right }: {
  leftLabel: string; left: string | null | undefined; rightLabel: string; right: string | null | undefined;
}) {
  return (
    <div className={styles.pair}>
      <div className={styles.pairCol}>
        <h4>{leftLabel}</h4>
        <p>{left || <span className={styles.muted}>Não descrito.</span>}</p>
      </div>
      <div className={styles.pairCol}>
        <h4>{rightLabel}</h4>
        <p>{right || <span className={styles.muted}>Não descrito.</span>}</p>
      </div>
    </div>
  );
}

/** Medidor de completude: quantos dos campos de voz estão preenchidos, com a lista do que falta por extenso. */
export function CompletenessGauge({ total, missing }: { total: number; missing: string[] }) {
  const done = Math.max(0, total - missing.length);
  return (
    <div className={styles.gauge}>
      <ProgressBar value={total === 0 ? 0 : done / total} label="Completude da voz da persona"
                   text={`${done}/${total}`} tone={missing.length === 0 ? 'success' : 'warning'} />
      {missing.length > 0 ? (
        <p className={styles.muted}>falta preencher: {missing.map((c) => ROTULO_DE_VOZ[c] ?? c).join(', ')}</p>
      ) : (
        <p className={styles.muted}>Todos os campos de voz estão preenchidos.</p>
      )}
    </div>
  );
}

/** Um número grande com rótulo pequeno embaixo — cartão de identidade em vez de tabela. */
export function StatFigure({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div className={styles.statFigure}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
