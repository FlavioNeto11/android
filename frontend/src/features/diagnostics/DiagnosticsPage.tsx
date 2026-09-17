import { Check, CircleHelp, Cpu, Gauge, RefreshCw, Server, ServerCrash, TriangleAlert, Wrench, X, Zap } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import appStyles from '../../App.module.css';
import { api, hintForError, toApiError } from '../../api/client';
import type { Diagnostics } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardHeader } from '../../components/Card';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { CodeBlock, JsonTree } from '../../components/JsonTree';
import { RecordTable } from '../../components/RecordTable';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { isRecord } from '../../lib/format';
import { EVENT_LEVEL, HEALTH_STATUS, metaOf } from '../../lib/status';
import { formatClock, formatDateTime } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Diagnostics.module.css';
import { accelerationOk, parseTools } from './parse';

const KNOWN_KEYS = ['collected_at', 'host', 'tools', 'acceleration', 'capacity', 'measurements'] as const;

const LABELS: Record<string, string> = {
  os: 'Sistema operacional', os_version: 'Versão do SO', platform: 'Plataforma', hostname: 'Nome da máquina', arch: 'Arquitetura',
  cpu: 'Processador', cpu_model: 'Processador', cpu_cores: 'Núcleos', cores: 'Núcleos', physical_cores: 'Núcleos físicos',
  logical_cores: 'Núcleos lógicos', cpu_count: 'CPUs lógicas', ram_gb: 'Memória (GB)', mem_total_gb: 'Memória total (GB)',
  mem_available_gb: 'Memória livre (GB)', disk_free_gb: 'Disco livre (GB)', disk_total_gb: 'Disco total (GB)', python: 'Python',
  python_version: 'Versão do Python', node: 'Node.js', hypervisor: 'Hipervisor', accel: 'Aceleração', available: 'Disponível',
  enabled: 'Ativada', detail: 'Detalhe', details: 'Detalhes', virtualization: 'Virtualização', vtx: 'VT-x/AMD-V', whpx: 'WHPX',
  haxm: 'HAXM', aehd: 'AEHD', recommended_max: 'Máximo recomendado', max_recommended_devices: 'Máximo recomendado de aparelhos',
  estimated_max_devices: 'Estimativa de aparelhos simultâneos', per_device_ram_mb: 'RAM por aparelho (MB)', limiting_factor: 'Fator limitante',
  notes: 'Observações', devices: 'Aparelhos', instances: 'Instâncias', boot_seconds: 'Boot (s)', boot_s: 'Boot (s)',
  cpu_percent: 'CPU (%)', mem_used_percent: 'Memória usada (%)', rss_mb: 'RAM do emulador (MB)', ts: 'Horário', label: 'Medição',
  name: 'Nome', value: 'Valor', unit: 'Unidade', ok: 'OK', result: 'Resultado', duration_s: 'Duração (s)', error: 'Erro',
};

export function DiagnosticsPage() {
  const health = useAppStore((s) => s.health);
  const recentEvents = useAppStore((s) => s.recentEvents);
  const [data, setData] = useState<Diagnostics | null>(null);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const token = useRef(0);

  const load = useCallback(async (refresh: boolean) => {
    const my = ++token.current;
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const res = await api.diagnostics(refresh);
      if (my !== token.current) return;
      setData(isRecord(res) ? res : { valor: res });
      if (refresh) toast({ tone: 'success', title: 'Diagnóstico refeito' });
    } catch (e) {
      if (my !== token.current) return;
      const err = toApiError(e);
      setError({ message: err.message, hint: hintForError(err) });
      // Ação pedida pelo usuário: além do aviso fixo na página, avisa por toast.
      if (refresh) toastError('Não foi possível refazer o diagnóstico', err);
    } finally {
      if (my === token.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(false);
    return () => {
      token.current += 1;
    };
  }, [load]);

  const tools = data ? parseTools(data.tools) : null;
  const accelOk = data ? accelerationOk(data.acceleration) : null;
  const measurements = data && Array.isArray(data.measurements) ? (data.measurements as unknown[]) : null;
  const extras = data ? Object.keys(data).filter((k) => !(KNOWN_KEYS as readonly string[]).includes(k)) : [];
  const problems = health?.problems ?? [];

  return (
    <div className={appStyles.page}>
      <div className={appStyles.pageHeader}>
        <div>
          <h1 className={appStyles.pageTitle}>Diagnóstico</h1>
          <p className={appStyles.pageLead}>
            Verificação da máquina: ferramentas do Android SDK, aceleração de hardware e quantos emuladores ela aguenta.
            {data && typeof data.collected_at === 'string' ? ` Coletado em ${formatDateTime(data.collected_at)}.` : ''}
          </p>
        </div>
        <Button variant="primary" icon={RefreshCw} loading={refreshing} disabled={loading} onClick={() => void load(true)}>
          Reexecutar diagnóstico
        </Button>
      </div>

      {refreshing ? (
        <Banner tone="info" icon={Gauge} role="status" title="Refazendo o diagnóstico…">
          Isto pode levar de alguns segundos a poucos minutos: o backend consulta as ferramentas e mede a capacidade da máquina. Os resultados anteriores continuam abaixo.
        </Banner>
      ) : null}

      {health ? (
        <Card aria-label="Saúde do ambiente">
          <CardHeader title="Saúde do ambiente" subtitle={`Backend v${health.version}`} actions={<StatusBadge meta={metaOf(HEALTH_STATUS, health.status)} size="lg" />} />
          <div className={styles.body}>
            {problems.length === 0 ? (
              <p className={styles.meta}>Nenhum problema detectado pelo backend.</p>
            ) : (
              <div className={styles.problems}>
                {problems.map((p, i) => (
                  <Banner key={`${p.code}-${i}`} tone="warning" icon={TriangleAlert} title={p.message}>
                    {p.hint ? <p>O que fazer: {p.hint}</p> : null}
                    <p className="mono">{p.code}</p>
                  </Banner>
                ))}
              </div>
            )}
          </div>
        </Card>
      ) : null}

      {loading && !data ? (
        <LoadingRegion label="Carregando o diagnóstico…" className={styles.grid}>
          {Array.from({ length: 4 }, (_, i) => (
            <Card key={i} padded>
              <Skeleton width="40%" height={18} />
              <Skeleton height={14} style={{ marginTop: 14 }} />
              <Skeleton height={14} width="80%" style={{ marginTop: 8 }} />
              <Skeleton height={14} width="60%" style={{ marginTop: 8 }} />
            </Card>
          ))}
        </LoadingRegion>
      ) : error && !data ? (
        <Card>
          <EmptyState icon={ServerCrash} tone="danger" title="Não foi possível obter o diagnóstico" hint={error.hint} actions={<Button variant="outline" icon={RefreshCw} onClick={() => void load(false)}>Tentar de novo</Button>}>
            {error.message}
          </EmptyState>
        </Card>
      ) : data ? (
        <>
          {error ? <Banner tone="danger" icon={ServerCrash} title="A última tentativa falhou — mostrando o resultado anterior">{error.message} {error.hint}</Banner> : null}
          <div className={styles.grid}>
            <Card aria-label="Máquina">
              <CardHeader title={<><Server size={15} aria-hidden style={{ verticalAlign: '-2px', marginRight: 8 }} />Máquina</>} />
              <div className={styles.body}>
                {data.host !== undefined ? <JsonTree value={data.host} labels={LABELS} /> : <p className={styles.meta}>O backend não informou dados do host.</p>}
              </div>
            </Card>

            <Card aria-label="Aceleração">
              <CardHeader
                title={<><Zap size={15} aria-hidden style={{ verticalAlign: '-2px', marginRight: 8 }} />Aceleração / hipervisor</>}
                actions={accelOk === null ? undefined : accelOk ? <Badge tone="success" icon={Check}>Disponível</Badge> : <Badge tone="danger" icon={X}>Indisponível</Badge>}
              />
              <div className={styles.body}>
                {data.acceleration !== undefined ? <JsonTree value={data.acceleration} labels={LABELS} /> : <p className={styles.meta}>O backend não informou dados de aceleração.</p>}
                {accelOk === false ? (
                  <Banner tone="warning" icon={TriangleAlert} compact>Sem aceleração de hardware os emuladores ficam lentos demais. Ative a virtualização na BIOS e o WHPX/Hyper-V (Windows) ou o hipervisor do emulador.</Banner>
                ) : null}
              </div>
            </Card>

            <Card className={styles.full} aria-label="Ferramentas">
              <CardHeader title={<><Wrench size={15} aria-hidden style={{ verticalAlign: '-2px', marginRight: 8 }} />Ferramentas</>} subtitle="Dependências que o backend usa para criar, iniciar e automatizar os emuladores." />
              <div className={styles.body}>
                {tools && tools.length > 0 ? (
                  <div className={styles.tableWrap}>
                    <table className={styles.table}>
                      <thead>
                        <tr>
                          <th scope="col">Ferramenta</th>
                          <th scope="col">Situação</th>
                          <th scope="col">Versão</th>
                          <th scope="col">Caminho / detalhe</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tools.map((t) => (
                          <tr key={t.name}>
                            <td className={styles.toolName}>{t.name}</td>
                            <td>
                              {t.ok === true ? <Badge tone="success" icon={Check} size="sm">OK</Badge>
                                : t.ok === false ? <Badge tone="danger" icon={X} size="sm">Ausente</Badge>
                                : <Badge tone="muted" icon={CircleHelp} size="sm">Indeterminado</Badge>}
                            </td>
                            <td className="mono">{t.version ?? '—'}</td>
                            <td>
                              {t.detail ? <span className={styles.toolDetail}>{t.detail}</span> : <span className={styles.meta}>—</span>}
                              {isRecord(t.raw) ? (
                                <Disclosure bare summary="Detalhes técnicos">{() => <CodeBlock value={t.raw} />}</Disclosure>
                              ) : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : data.tools !== undefined ? (
                  <JsonTree value={data.tools} labels={LABELS} />
                ) : (
                  <p className={styles.meta}>O backend não informou a lista de ferramentas.</p>
                )}
              </div>
            </Card>

            <Card aria-label="Capacidade">
              <CardHeader title={<><Gauge size={15} aria-hidden style={{ verticalAlign: '-2px', marginRight: 8 }} />Capacidade</>} subtitle="Estimativa de quantos aparelhos cabem nesta máquina." />
              <div className={styles.body}>
                {data.capacity !== undefined ? <JsonTree value={data.capacity} labels={LABELS} /> : <p className={styles.meta}>Sem estimativa de capacidade.</p>}
              </div>
            </Card>

            <Card aria-label="Medições">
              <CardHeader title={<><Cpu size={15} aria-hidden style={{ verticalAlign: '-2px', marginRight: 8 }} />Medições de capacidade</>} subtitle="Amostras coletadas durante os testes de carga." />
              <div className={styles.body}>
                {measurements && measurements.length > 0 ? (
                  <RecordTable rows={measurements} labels={LABELS} priority={['ts', 'label', 'name', 'devices', 'instances', 'boot_seconds', 'cpu_percent', 'mem_used_percent']} caption="Medições de capacidade" />
                ) : data.measurements !== undefined && !Array.isArray(data.measurements) ? (
                  <JsonTree value={data.measurements} labels={LABELS} />
                ) : (
                  <p className={styles.meta}>Nenhuma medição registrada ainda. Use “Reexecutar diagnóstico” com alguns emuladores ligados.</p>
                )}
              </div>
            </Card>

            {extras.length > 0 ? (
              <Card className={styles.full} aria-label="Outros dados">
                <CardHeader title="Outros dados" subtitle="Campos adicionais enviados pelo backend." />
                <div className={styles.body}>
                  <JsonTree value={Object.fromEntries(extras.map((k) => [k, data[k]]))} labels={LABELS} />
                </div>
              </Card>
            ) : null}
          </div>

          <Disclosure summary="Detalhes técnicos — resposta completa (JSON)">{() => <CodeBlock value={data} />}</Disclosure>
        </>
      ) : null}

      <Disclosure summary="Eventos recentes do sistema" meta={`${recentEvents.length}`}>
        {() =>
          recentEvents.length === 0 ? (
            <p className={styles.meta}>Nenhum evento recebido nesta sessão.</p>
          ) : (
            <ol className={styles.events}>
              {recentEvents.slice().reverse().map((e) => {
                const meta = metaOf(EVENT_LEVEL, e.level);
                return (
                  <li key={e.id ?? `${e.ts}-${e.kind}`} className={styles.eventRow}>
                    <time className={styles.eventTime} dateTime={e.ts}>{formatClock(e.ts)}</time>
                    <span className={styles.eventKind}>{meta.label.toLowerCase()} · {e.kind}</span>
                    <span>{e.message}{e.instance_id ? ` (${e.instance_id})` : ''}</span>
                  </li>
                );
              })}
            </ol>
          )
        }
      </Disclosure>
    </div>
  );
}
