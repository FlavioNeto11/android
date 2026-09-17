import { useCallback, useEffect, useRef, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { UsageQuery, UsageReport } from '../../api/types';
import { isRecord } from '../../lib/format';

export interface UsageState {
  report: UsageReport | null;
  error: { message: string; hint: string } | null;
  loading: boolean;
  reload: () => void;
}

/**
 * `GET /api/usage` para uma execução ou para os últimos N dias. Carrega ao montar, quando o escopo muda e
 * sempre que `refreshKey` mudar (ex.: a execução terminou). Respostas atrasadas de um escopo antigo são ignoradas.
 */
export function useUsage(scope: UsageQuery, refreshKey: unknown = null): UsageState {
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState<UsageState['error']>(null);
  const [loading, setLoading] = useState(true);
  const token = useRef(0);
  // Primitivos como dependência: o chamador pode passar um objeto novo a cada render sem disparar recargas.
  const runId = 'run_id' in scope ? scope.run_id : null;
  const days = 'days' in scope ? scope.days : null;

  const load = useCallback(async () => {
    const my = ++token.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.usage(runId !== null ? { run_id: runId } : { days: days ?? 7 });
      if (my !== token.current) return;
      if (!isRecord(res) || !Array.isArray(res.groups)) throw new Error('O backend devolveu um relatório de custo em formato inesperado.');
      setReport(res);
    } catch (e) {
      if (my !== token.current) return;
      const err = toApiError(e);
      setError({ message: err.message, hint: hintForError(err) });
    } finally {
      if (my === token.current) setLoading(false);
    }
  }, [runId, days]);

  useEffect(() => {
    void load();
    return () => {
      token.current += 1;
    };
  }, [load, refreshKey]);

  return { report, error, loading, reload: () => void load() };
}
