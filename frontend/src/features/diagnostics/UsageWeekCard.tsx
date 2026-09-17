import { Coins, RefreshCw, ServerCrash } from 'lucide-react';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardHeader } from '../../components/Card';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import usageStyles from '../usage/Usage.module.css';
import { isUsageEmpty, usageTotals } from '../usage/usage';
import { UnpricedNotice, UsageTable, UsageTiles } from '../usage/UsageView';
import { useUsage } from '../usage/useUsage';
import styles from './Diagnostics.module.css';

export const USAGE_DAYS = 7;

/** "Custo de IA — últimos 7 dias": resumo independente do diagnóstico (carrega e falha por conta própria). */
export function UsageWeekCard() {
  const { report, error, loading, reload } = useUsage({ days: USAGE_DAYS });
  const totals = report ? usageTotals(report) : null;

  return (
    <Card aria-label={`Custo de IA — últimos ${USAGE_DAYS} dias`}>
      <CardHeader
        title={<><Coins size={15} aria-hidden style={{ verticalAlign: '-2px', marginRight: 8 }} />Custo de IA — últimos {USAGE_DAYS} dias</>}
        subtitle="Quanto a IA custou e quanto do trabalho já roda por receita, sem chamada de modelo."
        actions={<Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={reload}>Atualizar</Button>}
      />
      <div className={styles.body}>
        {!report || !totals ? (
          loading ? (
            <LoadingRegion label="Calculando o custo de IA…">
              <Skeleton width={420} height={46} radius={8} />
            </LoadingRegion>
          ) : (
            <EmptyState
              icon={ServerCrash}
              tone="danger"
              compact
              title="Não foi possível calcular o custo de IA"
              hint={error?.hint}
              actions={<Button variant="outline" icon={RefreshCw} onClick={reload}>Tentar de novo</Button>}
            >
              {error?.message}
            </EmptyState>
          )
        ) : (
          <>
            {error ? <Banner tone="warning" icon={ServerCrash} compact title="Mostrando o último valor carregado">{error.message} {error.hint}</Banner> : null}
            {isUsageEmpty(report) ? (
              <p className={styles.meta}>Nenhuma chamada de IA nem etapa concluída nos últimos {USAGE_DAYS} dias.</p>
            ) : (
              <div className={usageStyles.wrap}>
                <UsageTiles
                  tiles={[
                    { key: 'usd', label: totals.pricing === 'partial' ? 'US$ total (parcial)' : 'US$ total', value: totals.totalUsd },
                    { key: 'usdPer', label: 'US$ por aparelho-comando', value: totals.usdPerObjective },
                    { key: 'share', label: 'Etapas por receita', value: totals.recipeShare },
                    { key: 'calls', label: 'Chamadas de IA', value: totals.calls },
                  ]}
                />
                <UnpricedNotice totals={totals} />
                <p className={styles.meta}>
                  {report.objectives_with_ai > 0
                    ? <>{totals.objectivesWithAi} aparelho-comando(s) usaram IA · {totals.callsPerObjective} chamada(s) de IA por aparelho-comando</>
                    : 'Nenhum aparelho-comando usou IA no período'}
                  {' '}· etapas concluídas: {totals.drivenBy}
                </p>
                <Disclosure bare summary="Ver por função e modelo">
                  {() => <UsageTable report={report} caption={`Custo de IA dos últimos ${USAGE_DAYS} dias, por função e modelo`} />}
                </Disclosure>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
