import { formatClock } from '../../lib/time';
import type { MeasurementPoint } from './parse';
import styles from './Diagnostics.module.css';

interface MeasurementsChartProps {
  points: MeasurementPoint[];
}

const W = 640;
const H = 200;
const PAD_L = 36;
const PAD_R = 36;
const PAD_T = 12;
const PAD_B = 28;

/**
 * SVG simples, sem biblioteca: barras de tempo de boot (eixo esquerdo) + linha de memória livre (eixo
 * direito), uma amostra por medição. Item 11.7 (5): a tabela de 60 linhas de JSON virou uma leitura de
 * relance; a tabela completa continua disponível atrás de "ver tabela".
 */
export function MeasurementsChart({ points }: MeasurementsChartProps) {
  if (points.length === 0) return null;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const n = points.length;
  const bootMax = Math.max(1, ...points.map((p) => p.bootSeconds));
  const memValues = points.map((p) => p.memFreeGb).filter((v): v is number => v !== null);
  const memMax = memValues.length > 0 ? Math.max(1, ...memValues) : null;
  const bandW = innerW / n;
  const barW = Math.min(28, bandW * 0.55);

  const x = (i: number) => PAD_L + bandW * i + bandW / 2;
  const yBoot = (v: number) => PAD_T + innerH * (1 - v / bootMax);
  const yMem = (v: number) => (memMax === null ? null : PAD_T + innerH * (1 - v / memMax));

  const memPath = memMax === null ? null
    : points
        .map((p, i) => (p.memFreeGb === null ? null : `${i === 0 || points[i - 1]?.memFreeGb === null ? 'M' : 'L'}${x(i)},${yMem(p.memFreeGb)}`))
        .filter((s): s is string => s !== null)
        .join(' ');

  const summary = `${n} medição(ões) de boot; tempo de boot entre ${Math.min(...points.map((p) => p.bootSeconds)).toFixed(1)}s e `
    + `${bootMax.toFixed(1)}s`
    + (memMax !== null ? `; memória livre entre ${Math.min(...memValues).toFixed(1)} GB e ${memMax.toFixed(1)} GB` : '');

  return (
    <figure className={styles.chart} role="img" aria-label={`Gráfico de medições de capacidade. ${summary}.`}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="xMidYMid meet" aria-hidden>
        <line x1={PAD_L} y1={PAD_T} x2={PAD_L} y2={H - PAD_B} className={styles.chartAxis} />
        <line x1={PAD_L} y1={H - PAD_B} x2={W - PAD_R} y2={H - PAD_B} className={styles.chartAxis} />
        {memMax !== null ? <line x1={W - PAD_R} y1={PAD_T} x2={W - PAD_R} y2={H - PAD_B} className={styles.chartAxis} /> : null}
        {points.map((p, i) => (
          <rect
            key={`bar-${p.index}`}
            x={x(i) - barW / 2}
            y={yBoot(p.bootSeconds)}
            width={barW}
            height={H - PAD_B - yBoot(p.bootSeconds)}
            className={styles.chartBar}
          >
            <title>{`Medição ${i + 1}${p.ts ? ` (${formatClock(p.ts)})` : ''}: boot ${p.bootSeconds.toFixed(1)}s${p.memFreeGb !== null ? `, RAM livre ${p.memFreeGb.toFixed(1)} GB` : ''}`}</title>
          </rect>
        ))}
        {memPath ? <path d={memPath} className={styles.chartLine} fill="none" /> : null}
        {memMax !== null ? points.map((p, i) => (p.memFreeGb === null ? null : (
          <circle key={`dot-${p.index}`} cx={x(i)} cy={yMem(p.memFreeGb) ?? 0} r={3} className={styles.chartDot} />
        ))) : null}
        <text x={PAD_L - 6} y={PAD_T + 4} textAnchor="end" className={styles.chartLabel}>{bootMax.toFixed(0)}s</text>
        <text x={PAD_L - 6} y={H - PAD_B} textAnchor="end" className={styles.chartLabel}>0s</text>
        {memMax !== null ? <text x={W - PAD_R + 6} y={PAD_T + 4} textAnchor="start" className={styles.chartLabel}>{memMax.toFixed(0)} GB</text> : null}
      </svg>
      <figcaption className={styles.chartLegend}>
        <span className={styles.chartLegendItem}><i className={styles.chartSwatchBar} aria-hidden /> Tempo de boot (s)</span>
        {memMax !== null ? <span className={styles.chartLegendItem}><i className={styles.chartSwatchLine} aria-hidden /> Memória livre (GB)</span> : null}
      </figcaption>
    </figure>
  );
}
