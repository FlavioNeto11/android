import { CircleX, TriangleAlert, TrendingUp } from 'lucide-react';
import type { UsageReport } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { balanceShortName } from '../../lib/aiBalance';
import { cx } from '../../lib/format';
import styles from './Usage.module.css';
import { NO_PRICE, errorsByKindText, formatUsd, usageRows, usageTotals, type UsageTotals } from './usage';

interface Tile {
  key: string;
  label: string;
  value: string;
}

/** Blocos de totais. `value === "sem preço"` ou "—" aparece esmaecido (não é um número). */
export function UsageTiles({ tiles }: { tiles: Tile[] }) {
  return (
    <dl className={styles.tiles}>
      {tiles.map((t) => (
        <div key={t.key} className={styles.tile}>
          <dt className={styles.tileLabel}>{t.label}</dt>
          <dd className={cx(styles.tileValue, (t.value === NO_PRICE || t.value === '—') && styles.tileValueMuted)}>{t.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Aviso quando há modelos sem preço: total ausente (tudo sem preço) ou parcial (mistura). */
export function UnpricedNotice({ totals }: { totals: UsageTotals }) {
  if (totals.pricing === 'priced') return null;
  const models = totals.unpricedModels.join(', ') || 'modelo não informado';
  return (
    <Banner tone={totals.pricing === 'partial' ? 'warning' : 'info'} icon={TriangleAlert} compact role="note">
      {totals.pricing === 'partial'
        ? <>Total parcial: {NO_PRICE} para <span className="mono">{models}</span> — o valor em US$ cobre só os modelos com preço configurado.</>
        : <>Modelo(s) {NO_PRICE}: <span className="mono">{models}</span>. As contagens de chamadas e tokens valem; o custo em US$ não é calculado.</>}
    </Banner>
  );
}

/**
 * Item 7.3 (achado #101): chamadas com erro, por TIPO — a contagem por linha da tabela já existia (`r.errors`),
 * mas não dizia SE era recusa, orçamento, crédito ou credencial, e casar isso exigia ler o log do backend.
 */
export function ErrorsByKindNotice({ report }: { report: Pick<UsageReport, 'errors_by_kind'> }) {
  const texto = errorsByKindText(report);
  if (!texto) return null;
  return (
    <Banner tone="warning" icon={CircleX} compact role="note" title="Chamadas de IA com erro no período">
      {texto}. Chamada com erro não tem custo a calcular — o total em US$ não inclui essas linhas.
    </Banner>
  );
}

/** Tabela função × modelo. */
export function UsageTable({ report, caption }: { report: UsageReport; caption: string }) {
  const rows = usageRows(report);
  if (rows.length === 0) {
    return <p className={styles.empty}>Nenhuma chamada de IA registrada{report.scope?.run_id ? ' nesta execução' : ' no período'}.</p>;
  }
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Função</th>
            <th scope="col">Modelo</th>
            <th scope="col" className={styles.num}>Chamadas</th>
            <th scope="col" className={styles.num}>Tokens novos</th>
            <th scope="col" className={styles.num}>Cache lido</th>
            <th scope="col" className={styles.num}>Saída</th>
            <th scope="col" className={styles.num}>Com imagem</th>
            <th scope="col" className={styles.num}>US$</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <th scope="row">{r.roleLabel}</th>
              <td>
                <span className={styles.model}>
                  {r.model}
                  {r.escalated ? <Badge tone="info" icon={TrendingUp} size="sm" title="Chamadas escalonadas para o modelo mais forte">escalonado</Badge> : null}
                </span>
              </td>
              <td className={styles.num}>
                {r.calls}
                {r.errors > 0 ? <span className={styles.errors} title="Chamadas que terminaram em erro">{r.errors} erro(s)</span> : null}
              </td>
              <td className={styles.num}>{r.fresh}</td>
              <td className={styles.num}>{r.cacheRead}</td>
              <td className={styles.num}>{r.output}</td>
              <td className={styles.num}>{r.withImage}</td>
              <td className={cx(styles.num, !r.priced && styles.noPrice)}>{r.usd}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Totais de uma execução: US$ total, chamadas e US$ por aparelho, etapas por receita × por IA. */
export function RunUsageTotals({ report }: { report: UsageReport }) {
  const t = usageTotals(report);
  return (
    <>
      <UsageTiles
        tiles={[
          { key: 'usd', label: t.pricing === 'partial' ? 'US$ total (parcial)' : 'US$ total', value: t.totalUsd },
          { key: 'calls', label: 'Chamadas por aparelho', value: t.callsPerObjective },
          { key: 'usdPer', label: 'US$ por aparelho', value: t.usdPerObjective },
          // De qual saldo o custo desta execução saiu (ADR-051).
          ...Object.entries(report.by_account ?? {})
            .filter(([, usd]) => usd > 0)
            .map(([conta, usd]) => ({ key: `conta-${conta}`, label: `US$ ${balanceShortName(conta)}`, value: formatUsd(usd) })),
          { key: 'driven', label: 'Etapas por receita × por IA', value: t.drivenBy },
        ]}
      />
      <UnpricedNotice totals={t} />
    </>
  );
}
