import { ChevronRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Badge } from '../../components/Badge';
import { cx } from '../../lib/format';
import type { Tone } from '../../lib/status';
import styles from './Focus.module.css';

/**
 * Uma seção da coluna lateral do Foco: título, selo de estado opcional e recolhível.
 *
 * `<details>` nativo pelo mesmo motivo do `Disclosure`: teclado e leitor de tela funcionam sem código extra. Os
 * filhos ficam montados mesmo com a seção fechada — o conteúdo é pequeno, a busca da página (Ctrl+F) acha o que
 * está recolhido, e os testes leem o texto sem precisar abrir cada seção.
 */
export function FocusSection({ title, badge, defaultOpen = true, open, onToggle, className, children }: {
  title: string;
  /** Selo ao lado do título (o semáforo do estado, uma contagem…). */
  badge?: { label: string; tone: Tone } | null;
  defaultOpen?: boolean;
  /** Controlado: para outra parte do painel poder abrir a seção ("Ver hierarquia" em Observação). */
  open?: boolean;
  onToggle?: (open: boolean) => void;
  className?: string;
  children: ReactNode;
}) {
  const [aberta, setAberta] = useState(defaultOpen);
  const estaAberta = open ?? aberta;
  return (
    <section className={cx(styles.section, className)} aria-label={title} data-focus-section={title}>
      <details
        className={styles.sectionDetails}
        open={estaAberta}
        onToggle={(e) => {
          const agora = e.currentTarget.open;
          if (agora === estaAberta) return;
          setAberta(agora);
          onToggle?.(agora);
        }}
      >
        <summary className={styles.sectionSummary}>
          <ChevronRight size={15} className={styles.sectionChevron} aria-hidden />
          <h3 className={styles.sectionTitle}>{title}</h3>
          {badge ? <Badge size="sm" tone={badge.tone} className={styles.sectionBadge}>{badge.label}</Badge> : null}
        </summary>
        <div className={styles.sectionBody}>{children}</div>
      </details>
    </section>
  );
}
