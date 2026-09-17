import type { ReactNode } from 'react';
import { humanizeKey, isRecord, scalarToText } from '../lib/format';
import { JsonTree } from './JsonTree';
import styles from './RecordTable.module.css';

interface RecordTableProps {
  rows: unknown[];
  /** Rótulos em pt-BR para colunas conhecidas; as demais são "humanizadas". */
  labels?: Record<string, string>;
  /** Colunas que devem vir primeiro, nesta ordem. */
  priority?: readonly string[];
  /** Personaliza células conhecidas; devolva `undefined` para cair na renderização padrão. */
  renderCell?: (column: string, value: unknown, row: Record<string, unknown>) => ReactNode | undefined;
  rowKey?: string;
  caption?: string;
}

/**
 * Tabela defensiva para listas de objetos de formato livre (medições do diagnóstico, relatório…):
 * as colunas são a união das chaves encontradas. Se algum item não for objeto, cai para a árvore genérica.
 */
export function RecordTable({ rows, labels, priority = [], renderCell, rowKey, caption }: RecordTableProps) {
  const records = rows.filter(isRecord);
  if (records.length === 0 || records.length !== rows.length) return <JsonTree value={rows} labels={labels} />;

  const keys = new Set<string>();
  for (const r of records) for (const k of Object.keys(r)) keys.add(k);
  const columns = Array.from(keys).sort((a, b) => {
    const ia = priority.indexOf(a);
    const ib = priority.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });

  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>{columns.map((c) => <th key={c} scope="col">{labels?.[c] ?? humanizeKey(c)}</th>)}</tr>
        </thead>
        <tbody>
          {records.map((r, i) => {
            const keyValue = rowKey ? r[rowKey] : undefined;
            return (
              <tr key={typeof keyValue === 'string' || typeof keyValue === 'number' ? `${keyValue}-${i}` : i}>
                {columns.map((c) => {
                  const custom = renderCell?.(c, r[c], r);
                  return (
                    <td key={c}>
                      {custom !== undefined ? custom : r[c] === null || typeof r[c] !== 'object' ? scalarToText(r[c]) : <JsonTree value={r[c]} labels={labels} depth={1} />}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
