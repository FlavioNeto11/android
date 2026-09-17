import { GitBranch } from 'lucide-react';
import { useMemo } from 'react';
import type { RunDetail } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { formatClock } from '../../lib/time';
import styles from './Runs.module.css';

export function DecisionsTab({ decisions }: { decisions: RunDetail['decisions'] }) {
  const ordered = useMemo(() => decisions.slice().sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0)), [decisions]);

  if (ordered.length === 0) {
    return (
      <EmptyState icon={GitBranch} compact title="Nenhuma decisão registrada" hint="Quando a IA escolhe entre caminhos (tentar de novo, revisar o plano, pedir ajuda), o motivo aparece aqui em linguagem simples." />
    );
  }

  return (
    <ol className={styles.decisions} aria-label="Decisões, da mais nova para a mais antiga">
      {ordered.map((d, i) => (
        <li key={`${d.ts}-${i}`} className={styles.decision}>
          <time className={styles.eventTime} dateTime={d.ts}>{formatClock(d.ts)}</time>
          <span className={styles.eventInst}>{d.instance_id ?? 'execução'}</span>
          <span>{d.text}</span>
        </li>
      ))}
    </ol>
  );
}
