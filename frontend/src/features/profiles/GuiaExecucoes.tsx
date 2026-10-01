import { ListChecks } from 'lucide-react';
import { api } from '../../api/client';
import type { RunSummary } from '../../api/types';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { StatusBadge } from '../../components/StatusBadge';
import { RUN_STATUS, metaOf } from '../../lib/status';
import { Carregando, useLista, useVersaoAoVivo } from './detalheComum';
import type { Pessoa } from './pessoa';
import styles from './Profiles.module.css';

// ---------------------------------------------------------------- execuções
export function AbaExecucoes({ profile }: { profile: Pessoa }) {
  const versao = useVersaoAoVivo(profile);
  const [runs] = useLista<RunSummary>(() => api.listProfileRuns(profile.id), [profile.id, versao]);
  if (runs === null) return <Carregando />;
  if (runs.length === 0) {
    return (
      <EmptyState icon={ListChecks} title="Sem execuções" hint="Comandos executados por esta persona aparecem aqui.">
        Nenhuma execução ainda.
      </EmptyState>
    );
  }
  return (
    <Card>
      <CardHeader title="Execuções desta persona" />
      <CardBody>
        <ul className={styles.list}>
          {runs.map((r) => (
            <li key={r.id}>
              <StatusBadge meta={metaOf(RUN_STATUS, r.status)} size="sm" /> {r.created_at} — {r.command}
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}
