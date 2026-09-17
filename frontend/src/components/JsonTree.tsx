import type { ReactNode } from 'react';
import { humanizeKey, isRecord, prettyJson, scalarToText } from '../lib/format';
import ui from './ui.module.css';

interface JsonTreeProps {
  value: unknown;
  /** Rótulos em pt-BR para chaves conhecidas; as demais são "humanizadas". */
  labels?: Record<string, string>;
  /** Chaves a omitir no primeiro nível (já renderizadas de outra forma). */
  omit?: readonly string[];
  depth?: number;
}

const MAX_DEPTH = 6;

function isScalar(v: unknown): boolean {
  return v === null || v === undefined || typeof v !== 'object';
}

/**
 * Renderização defensiva de payloads livres (diagnóstico, relatório): chaves conhecidas ganham rótulo,
 * o resto vira uma árvore chave/valor legível. Nunca lança — no pior caso mostra o JSON cru.
 */
export function JsonTree({ value, labels, omit, depth = 0 }: JsonTreeProps): ReactNode {
  if (isScalar(value)) return <span>{scalarToText(value)}</span>;
  if (depth >= MAX_DEPTH) return <pre className={ui.codeBlock}>{prettyJson(value)}</pre>;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span>—</span>;
    if (value.every(isScalar)) return <span>{value.map(scalarToText).join(', ')}</span>;
    return (
      <ul className={ui.kvList}>
        {value.map((item, i) => (
          <li key={i} className={ui.kvListItem}>
            <JsonTree value={item} labels={labels} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }

  if (isRecord(value)) {
    const entries = Object.entries(value).filter(([k]) => !(depth === 0 && omit?.includes(k)));
    if (entries.length === 0) return <span>—</span>;
    return (
      <dl className={ui.kv}>
        {entries.map(([k, v]) => (
          <KvRow key={k} label={labels?.[k] ?? humanizeKey(k)}>
            {isScalar(v) ? scalarToText(v) : (
              <div className={ui.kvNested}>
                <JsonTree value={v} labels={labels} depth={depth + 1} />
              </div>
            )}
          </KvRow>
        ))}
      </dl>
    );
  }
  return <pre className={ui.codeBlock}>{prettyJson(value)}</pre>;
}

export function KvRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className={ui.kvKey}>{label}</dt>
      <dd className={ui.kvVal}>{children}</dd>
    </>
  );
}

export function KvList({ children }: { children: ReactNode }) {
  return <dl className={ui.kv}>{children}</dl>;
}

export function CodeBlock({ value }: { value: unknown }) {
  return <pre className={ui.codeBlock}>{typeof value === 'string' ? value : prettyJson(value)}</pre>;
}
