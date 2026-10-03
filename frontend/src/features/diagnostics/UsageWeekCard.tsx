import { Coins, RefreshCw, ServerCrash, TriangleAlert } from 'lucide-react';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardHeader } from '../../components/Card';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { balanceShortName } from '../../lib/aiBalance';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import usageStyles from '../usage/Usage.module.css';
import { byOriginText, formatUsd, isUsageEmpty, usageTotals } from '../usage/usage';
import { DrivenByNullNotice, ErrorsByKindNotice, StrongModelSection, UnpricedNotice, UsageTable, UsageTiles } from '../usage/UsageView';
import { useAppStore } from '../../store/app';
import type { UsageState } from '../usage/useUsage';
import styles from './Diagnostics.module.css';

export const USAGE_DAYS = 7;

/**
 * "Custo de IA — últimos 7 dias": mais visível na tela de diagnóstico (item 11.7-3), logo abaixo dos
 * azulejos de decisão. O relatório (`state`) vem de cima — o azulejo "Custo de IA hoje" usa a MESMA
 * chamada, em vez de o card buscar por conta própria e duplicar a requisição.
 */
export function UsageWeekCard({ state }: { state: UsageState }) {
  const { report, error, loading, reload } = state;
  const totals = report ? usageTotals(report) : null;
  // O nome do app da discordância do rejulgamento, como o catálogo o cadastrou; o id quando o app não está carregado.
  const apps = useAppStore((s) => s.apps);
  const appLabel = (appId: string): string => apps.find((a) => a.id === appId)?.name ?? appId;

  return (
    <Card aria-label={`Custo de IA — últimos ${USAGE_DAYS} dias`} id="diag-custo">
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
                    // Gasto de HOJE (item 7.2): é ele que o teto diário compara, e é o número que faltava para
                    // alguém perceber a conta subindo antes de o crédito acabar.
                    ...(report.spend_today_usd === null || report.spend_today_usd === undefined
                      ? []
                      : [{ key: 'today', label: 'US$ hoje (UTC)', value: formatUsd(report.spend_today_usd) }]),
                    // Por conta (ADR-051): de qual saldo o custo saiu. A Google (Gemini) aparece quando é usada.
                    ...Object.entries(report.by_account ?? {})
                      .filter(([, usd]) => usd > 0)
                      .map(([conta, usd]) => ({ key: `conta-${conta}`, label: `US$ ${balanceShortName(conta)}`, value: formatUsd(usd) })),
                    { key: 'usdPer', label: 'US$ por aparelho-comando', value: totals.usdPerObjective },
                    { key: 'share', label: 'Etapas por receita', value: totals.recipeShare },
                    { key: 'calls', label: 'Chamadas de IA', value: totals.calls },
                  ]}
                />
                {report.fallbacks?.length ? (
                  <Banner tone="warning" icon={TriangleAlert} compact role="note" title="Houve troca de modelo no período">
                    {report.fallbacks.map((f) => (
                      <div key={`${f.fallback}-${f.requested_model}-${f.model}`}>
                        {f.calls}× {f.fallback === 'refusal' ? 'recusa de' : `falha de ${f.fallback} em`}{' '}
                        <span className="mono">{f.requested_model ?? '—'}</span>; respondeu{' '}
                        <span className="mono">{f.model}</span> (cobrado na tarifa de <span className="mono">{f.model}</span>).
                      </div>
                    ))}
                  </Banner>
                ) : null}
                <UnpricedNotice totals={totals} />
                <ErrorsByKindNotice report={report} />
                <DrivenByNullNotice report={report} />
                <p className={styles.meta}>
                  {report.objectives_with_ai > 0
                    ? <>{totals.objectivesWithAi} aparelho-comando(s) usaram IA · {totals.callsPerObjective} chamada(s) de IA por aparelho-comando</>
                    : 'Nenhum aparelho-comando usou IA no período'}
                  {' '}· etapas concluídas: {totals.drivenBy}
                </p>
                {/* RA-10: de onde veio o custo (execução, curador, leitura…), na mesma base do total. */}
                {byOriginText(report) ? <p className={styles.meta}>Por origem: {byOriginText(report)}</p> : null}
                <Disclosure bare summary="Ver por função e modelo">
                  {() => <UsageTable report={report} caption={`Custo de IA dos últimos ${USAGE_DAYS} dias, por função e modelo`} />}
                </Disclosure>
                {/* RA-10 (31.16): por que subiu ao modelo forte, o rejulgamento, a cascata e a imagem. */}
                <StrongModelSection report={report} appLabel={appLabel} />
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
