import { Server, Shuffle } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import { api } from '../../api/client';
import type { DistributionPreview } from '../../api/types';
import { TextInput } from '../../components/Field';
import { plural } from '../../lib/format';
import styles from './CommandPanel.module.css';

export const DIST_MIN = 1;
export const DIST_MAX = 64;

/** Quantos aparelhos, validado: `null` quando o texto não é um número aceito. */
export function parseCount(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= DIST_MIN && n <= DIST_MAX ? n : null;
}

/** Prévia da distribuição, relida quando app/quantidade mudam e a cada 15 s enquanto o modo está ligado. */
export function useDistributionPreview(enabled: boolean, count: number | null, appId: string): {
  preview: DistributionPreview | null; loading: boolean;
} {
  const [preview, setPreview] = useState<DistributionPreview | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!enabled || count === null || !appId) {
      setPreview(null);
      return;
    }
    const ctrl = new AbortController();
    const buscar = () => {
      setLoading(true);
      api.previewDistribution(count, appId, ctrl.signal)
        .then(setPreview)
        .catch(() => { if (!ctrl.signal.aborted) setPreview(null); })
        .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    };
    const t = setTimeout(buscar, 300);
    const i = setInterval(buscar, 15_000);
    return () => {
      clearTimeout(t);
      clearInterval(i);
      ctrl.abort();
    };
  }, [enabled, count, appId]);
  return { preview, loading };
}

interface Props {
  apps: { id: string; name: string }[];
  appId: string;
  countText: string;
  preview: DistributionPreview | null;
  loading: boolean;
  onApp: (id: string) => void;
  onCount: (text: string) => void;
}

/** "Distribuir entre servidores": o backend escolhe os aparelhos pela carga de cada máquina. */
export function DistributeTarget({ apps, appId, countText, preview, loading, onApp, onCount }: Props) {
  const countId = useId();
  const appFieldId = useId();
  const count = parseCount(countText);
  const precisaLigar = preview ? preview.picks.filter((p) => p.needs_start).length : 0;
  return (
    <div className={styles.distribute}>
      <div className={styles.distributeInputs}>
        <label htmlFor={countId} className={styles.distributeLabel}>Aparelhos</label>
        <TextInput id={countId} small inputMode="numeric" className={styles.distributeCount} value={countText}
                   invalid={count === null} onChange={(e) => onCount(e.target.value)} />
        <label htmlFor={appFieldId} className={styles.distributeLabel}>do app</label>
        <select id={appFieldId} className={styles.distributeSelect} value={appId} onChange={(e) => onApp(e.target.value)}>
          {apps.length === 0 ? <option value="">nenhum app cadastrado</option> : null}
          {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </div>
      <p className={styles.distributePreview} aria-live="polite">
        <Shuffle size={13} aria-hidden />
        {count === null ? `Informe de ${DIST_MIN} a ${DIST_MAX} aparelhos.`
          : !preview ? (loading ? 'Calculando a distribuição…' : 'Sem prévia agora.')
          : preview.picks.length === 0 ? `Nenhum aparelho disponível${preview.reasons[0] ? `: ${preview.reasons[0]}` : ''}.`
          : (
            <>
              {Object.entries(preview.per_server).map(([nome, n], i) => (
                <span key={nome} className={styles.distributeServer} title={nome}>
                  {i > 0 ? ' · ' : ''}<Server size={12} aria-hidden /> <strong>{n}</strong> em {nome}
                </span>
              ))}
              {precisaLigar > 0 ? <span className={styles.distributeNote}> ({plural(precisaLigar, 'precisa ligar', 'precisam ligar')})</span> : null}
              {preview.missing > 0 ? (
                <span className={styles.distributeWarn}>
                  {' '}— faltam {preview.missing}: {preview.reasons[0] ?? 'sem aparelho livre'}. Executar usa os {preview.picks.length} disponíveis.
                </span>
              ) : null}
            </>
          )}
      </p>
    </div>
  );
}
