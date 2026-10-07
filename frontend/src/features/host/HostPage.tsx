import { FlaskConical, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Banner } from '../../components/Banner';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatDecimal, formatGb, formatInt, formatMb, formatPercent, plural } from '../../lib/format';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import { formatClock } from '../../lib/time';
import { apiHost, JANELAS_EM_HORAS, type AmostraDoHost, type AmostrasDoHost, type JanelaEmHoras } from './contratoDoHost';
import { defasagemEmMinutos, LIMITE_DE_DEFASAGEM_MIN, pressaoPorAparelho, processosNoTopo, resumoDaJanela, trechosDaSerie } from './resumo';
import styles from './Host.module.css';

/**
 * 31.180: o painel do host. Só leitura: CPU, memória, disco, os processos que mais pesam e os avisos de pressão por aparelho, na
 * janela de 1, 6 ou 24 h, relida a cada minuto (o amostrador grava uma linha por minuto). Lê `GET /api/host/amostras?horas=`
 * (proposta em `contratoDoHost.ts`); até a rota existir, lê um exemplo fixo e AVISA. Sem nome de máquina em lugar nenhum.
 */

export const PERIODO_DA_RELEITURA_MS = 60_000;

const rotuloDaJanela = (h: number): string => (h === 1 ? 'Última hora' : `Últimas ${h} horas`);

function Serie({ titulo, amostras, valor, max, formato }: {
  titulo: string; amostras: readonly AmostraDoHost[]; valor: (a: AmostraDoHost) => number | null; max?: number; formato: (n: number | null) => string;
}) {
  const L = 300;
  const A = 56;
  const valores = amostras.map(valor);
  const trechos = trechosDaSerie(valores, L, A, max);
  const medidos = valores.filter((v): v is number => v !== null);
  const resumo = medidos.length ? `menor ${formato(Math.min(...medidos))}, maior ${formato(Math.max(...medidos))}` : 'sem medida na janela';
  return (
    <figure className={styles.serie}>
      <figcaption className={styles.serieTitulo}>{titulo} <span className={styles.mudo}>({resumo})</span></figcaption>
      <svg viewBox={`0 0 ${L} ${A}`} preserveAspectRatio="none" role="img" aria-label={`${titulo}: ${resumo}`} className={styles.grafico}>
        <line x1="0" y1={A - 0.5} x2={L} y2={A - 0.5} className={styles.base} />
        {trechos.map((t, i) => (
          t.length === 1
            ? <circle key={i} cx={t[0]!.x} cy={t[0]!.y} r="1.5" className={styles.linha} />
            : <polyline key={i} points={t.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" className={styles.linha} vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
    </figure>
  );
}

function Corpo({ dado, horas }: { dado: AmostrasDoHost; horas: number }) {
  const { amostras } = dado;
  const r = useMemo(() => resumoDaJanela(amostras), [amostras]);
  const processos = useMemo(() => processosNoTopo(amostras), [amostras]);
  const pressao = useMemo(() => pressaoPorAparelho(amostras), [amostras]);
  const atraso = defasagemEmMinutos(r.ultima?.ts_utc, Date.now());
  if (!r.ultima) {
    return <EmptyState icon={TriangleAlert} title="Nenhuma amostra do host na janela" hint="O amostrador do host grava uma linha por minuto; sem linha, ele está parado ou o central ainda não o lê." />;
  }
  const u = r.ultima;
  const celulas: [string, string][] = [
    ['CPU agora', formatPercent(u.cpu_host_pct)],
    [`CPU média na janela`, formatPercent(r.cpu.media)],
    ['Pico de CPU', r.cpu.pico === null ? '—' : `${formatPercent(r.cpu.pico)} às ${formatClock(r.cpu.picoEm)}`],
    ['RAM livre agora', formatMb(u.ram_livre_mb)],
    ['Menor RAM livre na janela', formatMb(r.ramLivreMinimaMb)],
    ['Disco livre', formatGb(u.disco_livre_gb)],
    ['Núcleos usados pelas VMs', formatDecimal(u.vm_convidado_nucleos)],
    ['Memória da VM do WSL', formatMb(u.vmmem_ws_mb)],
    ['CPU dos emuladores (% do host)', formatPercent(r.qemuPct)],
  ];
  return (
    <>
      {atraso !== null && atraso > LIMITE_DE_DEFASAGEM_MIN ? (
        <Banner tone="warning" icon={TriangleAlert} compact role="status" title="A última amostra é antiga">
          A amostra mais nova tem {plural(atraso, 'minuto', 'minutos')} (às {formatClock(u.ts_utc)}). O amostrador grava uma por minuto: ele pode ter parado.
        </Banner>
      ) : null}
      <dl className={styles.celulas} aria-label="Medidas do host">
        {celulas.map(([rotulo, valor]) => (
          <div key={rotulo} className={styles.celula}>
            <dd className={styles.celulaValor}>{valor}</dd>
            <dt className={styles.celulaRotulo}>{rotulo}</dt>
          </div>
        ))}
      </dl>
      <section aria-labelledby="host-series" className={styles.secao}>
        <h2 id="host-series" className={styles.subtitulo}>{rotuloDaJanela(horas)}</h2>
        <div className={styles.series}>
          <Serie titulo="CPU do host (%)" amostras={amostras} valor={(a) => a.cpu_host_pct} max={100} formato={formatPercent} />
          <Serie titulo="CPU dos emuladores (% do host)" amostras={amostras} valor={(a) => a.qemu_host_pct} max={100} formato={formatPercent} />
          <Serie titulo="RAM livre" amostras={amostras} valor={(a) => a.ram_livre_mb} formato={formatMb} />
          <Serie titulo="Núcleos usados pelas VMs" amostras={amostras} valor={(a) => a.vm_convidado_nucleos} formato={formatDecimal} />
        </div>
      </section>
      <section aria-labelledby="host-processos" className={styles.secao}>
        <h2 id="host-processos" className={styles.subtitulo}>Processos que mais pesaram</h2>
        {processos.length === 0 ? <p className={styles.mudo}>Nenhum processo no topo nesta janela.</p> : (
          <div className={styles.rolagem}>
            <table className={styles.tabela}>
              <caption className="sr-only">Processos com mais CPU na janela: média por minuto e maior valor de um minuto, em % do host.</caption>
              <thead><tr><th scope="col">Processo</th><th scope="col">Média</th><th scope="col">Pico em 1 minuto</th></tr></thead>
              <tbody>
                {processos.map((p) => (
                  <tr key={p.nome} data-processo={p.nome}>
                    <th scope="row" className={styles.nome}>{p.nome}</th>
                    <td>{formatDecimal(p.mediaPct)}%</td>
                    <td>{formatDecimal(p.picoPct)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className={styles.mudo}>Só o nome do processo e a CPU; os emuladores (qemu) têm a série própria acima.</p>
      </section>
      <section aria-labelledby="host-pressao" className={styles.secao}>
        <h2 id="host-pressao" className={styles.subtitulo}>Avisos de pressão por aparelho</h2>
        {pressao.length === 0 ? (
          <p className={styles.mudo} role="status">Nenhum aviso de pressão na janela. Vazio também vale quando o amostrador não consegue medir (banco indisponível).</p>
        ) : (
          <div className={styles.rolagem}>
            <table className={styles.tabela}>
              <caption className="sr-only">Avisos “Convidado sob pressão de CPU” por aparelho na janela.</caption>
              <thead><tr><th scope="col">Aparelho</th><th scope="col">Avisos</th><th scope="col">Minutos com aviso</th><th scope="col">Último</th></tr></thead>
              <tbody>
                {pressao.map((p) => (
                  <tr key={p.instanceId} data-aparelho={p.instanceId}>
                    <th scope="row" className={styles.nome}>{p.instanceId}</th>
                    <td>{formatInt(p.avisos)}</td>
                    <td>{formatInt(p.minutos)}</td>
                    <td>{formatClock(p.ultimoEm)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {dado.falhas > 0 ? <p className={styles.mudo}>{plural(dado.falhas, 'linha de falha do amostrador ficou de fora', 'linhas de falha do amostrador ficaram de fora')} dos números.</p> : null}
    </>
  );
}

export function HostPage() {
  useEffect(() => { document.title = 'Host · Central de Aparelhos'; }, []);
  const [horas, setHoras] = useState<JanelaEmHoras>(1);
  const [dado, setDado] = useState<AmostrasDoHost | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [lidoEm, setLidoEm] = useState<string | null>(null);
  const [vez, setVez] = useState(0);

  // A janela e a releitura de cada minuto. A releitura não pisca o esqueleto: os números antigos ficam até os novos chegarem.
  useEffect(() => {
    const ctl = new AbortController();
    setCarregando(true);
    apiHost.amostras(horas, ctl.signal)
      .then((d) => { setDado(d); setErro(null); setLidoEm(new Date().toISOString()); })
      .catch((e: unknown) => { if (!ctl.signal.aborted) setErro(toLoadError(e)); })
      .finally(() => { if (!ctl.signal.aborted) setCarregando(false); });
    return () => ctl.abort();
  }, [horas, vez]);
  useEffect(() => {
    const t = setInterval(() => setVez((n) => n + 1), PERIODO_DA_RELEITURA_MS);
    return () => clearInterval(t);
  }, []);
  const recarregar = useCallback(() => setVez((n) => n + 1), []);

  return (
    <Page title="Host" lead="Como a máquina central está: CPU, memória, disco, os processos que mais pesam e os avisos de pressão por aparelho. Só leitura; relida a cada minuto.">
      <div className={styles.filtros}>
        <Field label="Janela">
          {({ id }) => (
            <Select id={id} small value={String(horas)} onChange={(e) => setHoras(JANELAS_EM_HORAS.find((h) => String(h) === e.target.value) ?? 1)}>
              {JANELAS_EM_HORAS.map((h) => <option key={h} value={String(h)}>{rotuloDaJanela(h)}</option>)}
            </Select>
          )}
        </Field>
        <p className={styles.mudo} role="status">{lidoEm ? `Lido às ${formatClock(lidoEm)}; a próxima leitura é em até 1 minuto.` : 'Lendo…'}</p>
      </div>
      {erro && dado ? <LoadErrorBanner error={erro} onRetry={recarregar} /> : null}
      {dado?.exemplo ? (
        <Banner tone="info" icon={FlaskConical} title="Dados de exemplo" role="status">
          O central ainda não oferece as amostras do host: esta tela lê um exemplo fixo, inventado. Nada aqui veio da máquina. Quando a rota responder, a mesma tela passa a ler o real.
        </Banner>
      ) : null}
      {carregando && !dado ? <LoadingRegion label="Lendo as amostras do host"><Skeleton height={160} /></LoadingRegion> : null}
      {erro && !dado ? <LoadErrorState what="as amostras do host" error={erro} onRetry={recarregar} /> : null}
      {dado ? <Corpo dado={dado} horas={horas} /> : null}
    </Page>
  );
}
