import { CircleX, ImageOff, Image as ImageIcon, TriangleAlert, TrendingUp } from 'lucide-react';
import type { UsageReport } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Disclosure } from '../../components/Disclosure';
import { balanceShortName } from '../../lib/aiBalance';
import { cx, formatInt } from '../../lib/format';
import styles from './Usage.module.css';
import {
  NO_PRICE, cascadeText, errorsByKindText, escalationRows, formatUsd, imageReasonRows, rejudgeByAppRows, rejudgeText,
  stepsDrivenByNull, usageRows, usageTotals, type UsageTotals,
} from './usage';

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

/**
 * RA-10 (31.16): etapas com decisão de IA que terminaram sem dizer quem as conduziu (`driven_by` nulo). O aceite do
 * RA-10 é zero; a contagem "por receita × por IA" soma essas etapas como IA, e o aviso diz o que essa soma supõe.
 */
export function DrivenByNullNotice({ report }: { report: Pick<UsageReport, 'steps_driven_by_null'> }) {
  const n = stepsDrivenByNull(report);
  if (n === 0) return null;
  return (
    <Banner tone="warning" icon={TriangleAlert} compact role="note" title="Etapas sem registro de quem decidiu">
      {formatInt(n)} etapa(s) com decisão de IA terminaram sem dizer se foram conduzidas por receita ou pela IA. A
      contagem “por receita × por IA” as soma como IA; o esperado é nenhuma.
    </Banner>
  );
}

/**
 * RA-10 (31.16): por que as chamadas subiram ao modelo forte, quanto custou conferir o verificador (e quanto o forte
 * DISCORDOU do barato), a cascata do bloqueio e por que a imagem foi junto. Some quando o período não tem nada disso
 * (ou o servidor é anterior ao adendo v0.75).
 */
export function StrongModelSection({ report, appLabel }: { report: UsageReport; appLabel?: (appId: string) => string }) {
  const escalations = escalationRows(report);
  const rejudge = rejudgeText(report);
  const cascade = cascadeText(report);
  const byApp = rejudgeByAppRows(report);
  const images = imageReasonRows(report);
  if (escalations.length === 0 && !rejudge && !cascade && images.length === 0) return null;
  const nomeDoApp = (appId: string): string => (appId === '*' ? 'etapa sem app' : appLabel?.(appId) ?? appId);
  return (
    <section className={styles.section} aria-labelledby="usage-modelo-forte">
      <h3 id="usage-modelo-forte" className={styles.sectionTitle}>Modelo forte e conferência</h3>
      {escalations.length > 0 || rejudge || cascade ? (
        <dl className={styles.facts}>
          {escalations.length > 0 ? (
            <div className={styles.fact}>
              <dt className={styles.factLabel}>Subiu ao modelo forte, por motivo</dt>
              <dd className={styles.factValue}>
                <ul className={styles.factList}>
                  {escalations.map((e) => (
                    <li key={e.key}>
                      <span>{e.label}</span>
                      <span className={styles.factNums}>{e.calls} · {e.usd}</span>
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          ) : null}
          {rejudge ? (
            <div className={styles.fact}>
              <dt className={styles.factLabel}>Rejulgamento do verificador</dt>
              <dd className={styles.factValue}>{rejudge}</dd>
            </div>
          ) : null}
          {cascade ? (
            <div className={styles.fact}>
              <dt className={styles.factLabel}>Cascata do bloqueio</dt>
              <dd className={styles.factValue}>{cascade}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
      {byApp.length > 0 ? (
        <Disclosure bare summary="Discordância do rejulgamento, por app">
          {() => (
            <div className={styles.tableWrap}>
              <table className={cx(styles.table, styles.tableWrapLabels)}>
                <caption className="sr-only">Quanto o modelo forte desfez o veredito do barato, por app</caption>
                <thead>
                  <tr>
                    <th scope="col">App</th>
                    <th scope="col" className={styles.num}>Julgadas</th>
                    <th scope="col" className={styles.num}>Discordância</th>
                  </tr>
                </thead>
                <tbody>
                  {byApp.map((r) => (
                    <tr key={r.key}>
                      <th scope="row">{nomeDoApp(r.appId)}</th>
                      <td className={styles.num}>{r.judged}</td>
                      <td className={styles.num}>{r.rate} ({r.disagreements})</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Disclosure>
      ) : null}
      {images.length > 0 ? (
        <Disclosure bare summary="Imagem: por que foi junto (ou não)">
          {() => (
            <div className={styles.tableWrap}>
              <table className={cx(styles.table, styles.tableWrapLabels)}>
                <caption className="sr-only">Por que a imagem da tela foi, ou não, junto com a chamada</caption>
                <thead>
                  <tr>
                    <th scope="col">Motivo</th>
                    <th scope="col">Imagem</th>
                    <th scope="col" className={styles.num}>Com imagem</th>
                  </tr>
                </thead>
                <tbody>
                  {images.map((r) => (
                    <tr key={r.key}>
                      <th scope="row">{r.label}</th>
                      <td>
                        {r.sends === null ? '—' : (
                          <Badge tone={r.sends ? 'info' : 'neutral'} icon={r.sends ? ImageIcon : ImageOff} size="sm">
                            {r.sends ? 'vai' : 'não vai'}
                          </Badge>
                        )}
                      </td>
                      <td className={styles.num}>{r.withImage} de {r.calls}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Disclosure>
      ) : null}
    </section>
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
