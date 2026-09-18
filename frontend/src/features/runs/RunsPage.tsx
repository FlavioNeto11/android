import { Check, FlaskConical, Hand, ListChecks, RefreshCw, Smartphone, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import appStyles from '../../App.module.css';
import { api } from '../../api/client';
import type { RunSummary } from '../../api/types';
import { Button } from '../../components/Button';
import { Card, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { conteudoAoTopo } from '../../lib/scroll';
import { RUN_STATUS, metaOf } from '../../lib/status';
import { formatAgoCoarse, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import styles from './Runs.module.css';
import { RunView } from './RunView';

const HISTORY_LIMIT = 50;

export function RunsPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  const runs = useAppStore((s) => s.runs);
  const mergeRuns = useAppStore((s) => s.mergeRuns);
  const selectedRunId = useUiStore((s) => s.selectedRunId);
  const selectRun = useUiStore((s) => s.selectRun);
  const setView = useUiStore((s) => s.setView);
  const [loading, setLoading] = useState(false);

  // O snapshot traz só as ativas + 20 recentes; aqui buscamos um histórico um pouco maior.
  const loadHistory = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.listRuns(HISTORY_LIMIT);
      if (Array.isArray(list)) mergeRuns(list);
    } catch (e) {
      toastError('Não foi possível atualizar a lista de execuções', e, { key: 'runs-history' });
    } finally {
      setLoading(false);
    }
  }, [mergeRuns]);

  useEffect(() => {
    if (hydrateCount > 0) void loadHistory();
  }, [hydrateCount, loadHistory]);

  // Abre a mais recente quando nada está selecionado.
  useEffect(() => {
    if (!selectedRunId && runs.length > 0 && runs[0]) selectRun(runs[0].id);
  }, [selectedRunId, runs, selectRun]);

  // Execução nova, leitura do começo: sem isto, escolher outra com a página rolada abria no meio do relatório.
  useEffect(() => {
    conteudoAoTopo();
  }, [selectedRunId]);

  return (
    <div className={appStyles.page}>
      <div className={appStyles.pageHeader}>
        <div>
          <h1 className={appStyles.pageTitle}>Execuções</h1>
          <p className={appStyles.pageLead}>Histórico recente e detalhes completos de cada execução: plano, progresso por instância, linha do tempo, evidências e relatório.</p>
        </div>
      </div>

      <div className={styles.runsLayout}>
        <Card className={styles.runListCard} aria-label="Lista de execuções">
          <CardHeader
            title="Recentes"
            subtitle={hydrated ? `${runs.length} execução(ões)` : undefined}
            actions={<Button size="sm" variant="ghost" icon={RefreshCw} iconOnly label="Atualizar lista" loading={loading} onClick={() => void loadHistory()} />}
          />
          {!hydrated ? (
            <LoadingRegion label="Carregando execuções…" className={styles.runList}>
              {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} height={74} radius={8} />)}
            </LoadingRegion>
          ) : runs.length === 0 ? (
            <EmptyState
              icon={ListChecks}
              compact
              title="Nenhuma execução ainda"
              hint="Crie a primeira pelo campo de comando do Painel."
              actions={<Button variant="outline" onClick={() => setView('painel')}>Ir para o Painel</Button>}
            />
          ) : (
            <ul className={styles.runList}>
              {runs.map((r) => (
                <li key={r.id}>
                  <RunItem run={r} current={r.id === selectedRunId} onSelect={() => selectRun(r.id)} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <RunView />
      </div>
    </div>
  );
}

function Age({ ts }: { ts: string }) {
  const now = useNow();
  return <>{formatAgoCoarse(ts, now)}</>;
}

function RunItem({ run, current, onSelect }: { run: RunSummary; current: boolean; onSelect: () => void }) {
  const c = run.counts;
  const blocked = (c?.waiting_user ?? 0) + (c?.uncertain ?? 0);
  return (
    <button type="button" className={styles.runItem} aria-current={current ? 'true' : undefined} onClick={onSelect}>
      <span className={styles.runItemTop}>
        <span className={styles.shortId}>{run.short_id}</span>
        <StatusBadge meta={metaOf(RUN_STATUS, run.status)} size="sm" className={styles.runItemBadge} />
        <span className={styles.runItemAge}><Age ts={run.created_at} /></span>
      </span>
      <span className={styles.runItemCmd}>{run.command}</span>
      <span className={styles.runItemMeta}>
        <span><Smartphone size={11} aria-hidden /> {run.instances_used}/{run.instances_requested}</span>
        {c ? (
          <>
            <span className={c.succeeded > 0 ? styles.miniOk : undefined}><Check size={11} aria-hidden /> {c.succeeded}<span className="sr-only"> com sucesso</span></span>
            <span className={c.failed > 0 ? styles.miniBad : undefined}><X size={11} aria-hidden /> {c.failed}<span className="sr-only"> com falha</span></span>
            <span className={blocked > 0 ? styles.miniWarn : undefined}><Hand size={11} aria-hidden /> {blocked}<span className="sr-only"> bloqueados ou incertos</span></span>
          </>
        ) : null}
        {run.simulated ? <span className={styles.miniWarn}><FlaskConical size={11} aria-hidden /> simulado</span> : null}
      </span>
    </button>
  );
}
