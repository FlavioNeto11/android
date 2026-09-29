import {
  Activity, Check, CircleHelp, Cpu, Gauge, RefreshCw, Server, ServerCrash, Smartphone, TriangleAlert, Wrench, X, Zap,
  Wallet,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import appStyles from '../../App.module.css';
import { api, hintForError, toApiError } from '../../api/client';
import type { Diagnostics, EventRecord } from '../../api/types';
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
import { toneClass } from '../../components/tone';
import { cx, isRecord } from '../../lib/format';
import { EVENT_LEVEL, HEALTH_STATUS, metaOf } from '../../lib/status';
import { isBoolean, loadJson, saveJson } from '../../lib/storage';
import { formatClock, formatDateTime } from '../../lib/time';
import { selectInstanceList, useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { useUsage } from '../usage/useUsage';
import styles from './Diagnostics.module.css';
import { MeasurementsChart } from './MeasurementsChart';
import { OutrosDados } from './OutrosDados';
import { accelerationOk, diskFreeGb, estimatedMaxDevices, parseBootMeasurements, parseTools } from './parse';
import { buildDecisionTiles, type DecisionTile, type DecisionTileKey } from './tiles';
import { UsageWeekCard } from './UsageWeekCard';

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
  kind: 'Tipo', instance_id: 'Aparelho', clock_skew_before_after_s: 'Desvio de relógio antes/depois (s)', online_after: 'Online depois de',
  image: 'Imagem', saved: 'Salvo', save_seconds: 'Salvar (s)', wake_seconds: 'Acordar (s)', mem_free_gb: 'Memória livre (GB)',
};

const TILE_ICON: Record<DecisionTileKey, typeof Activity> = {
  health: Activity, cost: Gauge, balance: Wallet, devices: Smartphone, machine: Cpu, acceleration: Zap,
};

/** Rola até a seção correspondente; abre a seção se ela estiver recolhida (Disclosure nativo, sem JS extra). */
function jumpTo(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el instanceof HTMLDetailsElement) el.open = true;
  // jsdom (testes) não implementa scrollIntoView.
  el.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
}

const SECTION_STORAGE_PREFIX = 'diagnostics.section.';

function useSectionOpen(key: string, defaultValue: boolean): [boolean, (open: boolean) => void] {
  const [open, setOpenState] = useState(() => loadJson(SECTION_STORAGE_PREFIX + key, isBoolean) ?? defaultValue);
  const setOpen = useCallback((v: boolean) => {
    setOpenState(v);
    saveJson(SECTION_STORAGE_PREFIX + key, v);
  }, [key]);
  return [open, setOpen];
}

const EVENT_LEVELS = ['all', 'error', 'warn', 'info'] as const;
type EventLevelFilter = (typeof EVENT_LEVELS)[number];
const EVENT_LEVEL_LABEL: Record<EventLevelFilter, string> = { all: 'Todos', error: 'Erro', warn: 'Aviso', info: 'Info' };
const EVENTS_PAGE = 50;

function DecisionTileButton({ tile }: { tile: DecisionTile }) {
  const Icon = TILE_ICON[tile.key];
  return (
    <button type="button" className={cx(styles.tile, toneClass(tile.tone))} onClick={() => jumpTo(tile.anchor)}>
      <span className={styles.tileTitle}><Icon size={12} aria-hidden style={{ verticalAlign: '-1px', marginRight: 4 }} />{tile.title}</span>
      <span className={styles.tileValue}>{tile.value}</span>
      <span className={styles.tileSub}>{tile.sub}</span>
    </button>
  );
}

export function DiagnosticsPage() {
  const health = useAppStore((s) => s.health);
  const settings = useAppStore((s) => s.settings);
  const metrics = useAppStore((s) => s.metrics);
  const instancesMap = useAppStore((s) => s.instances);
  const instanceOrder = useAppStore((s) => s.instanceOrder);
  const instances = useMemo(() => selectInstanceList({ instances: instancesMap, instanceOrder }), [instancesMap, instanceOrder]);
  const recentEvents = useAppStore((s) => s.recentEvents);
  const [data, setData] = useState<Diagnostics | null>(null);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const token = useRef(0);

  const usage = useUsage({ days: 7 });

  const [eventLevel, setEventLevel] = useState<EventLevelFilter>('all');
  const [eventsLimit, setEventsLimit] = useState(EVENTS_PAGE);

  const [openMachine, setOpenMachine] = useSectionOpen('maquina', false);
  const [openAccel, setOpenAccel] = useSectionOpen('aceleracao', false);
  const [openTools, setOpenTools] = useSectionOpen('ferramentas', false);
  const [openCapacity, setOpenCapacity] = useSectionOpen('capacidade', false);
  const [openExtras, setOpenExtras] = useSectionOpen('outros', false);

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
  const bootPoints = useMemo(() => parseBootMeasurements(measurements), [measurements]);
  const extras = data ? Object.keys(data).filter((k) => !(KNOWN_KEYS as readonly string[]).includes(k)) : [];
  const problems = health?.problems ?? [];

  const onlineDevices = instances.filter((i) => i.state === 'online').length;
  const spendToday = usage.report?.spend_today_usd ?? health?.ai?.spend_today_usd ?? null;
  const dailyLimit = settings?.ai_max_usd_per_day ?? null;

  const disk = data ? diskFreeGb(data.host) : null;

  const tiles = useMemo(() => buildDecisionTiles({
    healthStatus: health?.status ?? null,
    problemsCount: problems.length,
    spendTodayUsd: spendToday,
    dailyLimitUsd: dailyLimit,
    onlineDevices,
    maxOnlineDevices: settings?.max_online_devices ?? null,
    estimatedMaxDevices: data ? estimatedMaxDevices(data.capacity) : null,
    cpuPercent: metrics?.cpu_percent ?? null,
    memAvailableGb: metrics?.mem_available_gb ?? null,
    memTotalGb: metrics?.mem_total_gb ?? null,
    diskFreeGb: disk?.freeGb ?? null,
    diskTotalGb: disk?.totalGb ?? null,
    accelOk,
    balances: health?.ai?.balances ?? null,
  }), [health, problems.length, spendToday, dailyLimit, onlineDevices, settings, data, metrics, disk, accelOk]);

  const filteredEvents = useMemo(() => {
    const list: EventRecord[] = eventLevel === 'all' ? recentEvents.slice() : recentEvents.filter((e) => e.level === eventLevel);
    return list.reverse();
  }, [recentEvents, eventLevel]);
  const visibleEvents = filteredEvents.slice(0, eventsLimit);

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

      {/* Painel de decisão (item 11.7-1): responde "está tudo bem?" sem descer a página. */}
      <div className={styles.tiles} role="group" aria-label="Resumo de decisão">
        {tiles.map((t) => <DecisionTileButton key={t.key} tile={t} />)}
      </div>

      {/* Problemas primeiro (item 11.7-2): antes de qualquer tabela. */}
      <Card aria-label="Problemas" id="diag-problemas">
        <CardHeader
          title="Problemas"
          subtitle="O que o backend encontrou de errado, e o que fazer a respeito."
          actions={health ? <StatusBadge meta={metaOf(HEALTH_STATUS, health.status)} size="lg" /> : undefined}
        />
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

      <UsageWeekCard state={usage} />

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

          <Card aria-label="Medições de capacidade" id="diag-capacidade-medicoes">
            <CardHeader title={<><Cpu size={15} aria-hidden style={{ verticalAlign: '-2px', marginRight: 8 }} />Medições de capacidade</>} subtitle="Amostras coletadas durante os testes de carga: tempo de boot e memória livre." />
            <div className={styles.body}>
              {bootPoints.length > 0 ? (
                <>
                  <MeasurementsChart points={bootPoints} />
                  <Disclosure bare summary="Ver tabela completa">
                    {() => <RecordTable rows={measurements ?? []} labels={LABELS} priority={['ts', 'label', 'name', 'devices', 'instances', 'boot_seconds', 'cpu_percent', 'mem_used_percent']} caption="Medições de capacidade" />}
                  </Disclosure>
                </>
              ) : measurements && measurements.length > 0 ? (
                <RecordTable rows={measurements} labels={LABELS} priority={['ts', 'label', 'name', 'devices', 'instances', 'boot_seconds', 'cpu_percent', 'mem_used_percent']} caption="Medições de capacidade" />
              ) : data.measurements !== undefined && !Array.isArray(data.measurements) ? (
                <JsonTree value={data.measurements} labels={LABELS} />
              ) : (
                <p className={styles.meta}>Nenhuma medição registrada ainda. Use “Reexecutar diagnóstico” com alguns emuladores ligados.</p>
              )}
            </div>
          </Card>

          {/* Detalhes técnicos (item 11.7-5): recolhidos por padrão, com sumário de âncoras acima. */}
          <Card aria-label="Detalhes técnicos">
            <CardHeader title="Detalhes técnicos" subtitle="Máquina, aceleração, ferramentas, capacidade e outros dados enviados pelo backend — recolhidos por padrão." />
            <div className={styles.body}>
              <nav className={styles.anchorNav} aria-label="Ir para">
                <button type="button" className={styles.anchorLink} onClick={() => jumpTo('diag-maquina')}>Máquina</button>
                <button type="button" className={styles.anchorLink} onClick={() => jumpTo('diag-aceleracao')}>Aceleração</button>
                <button type="button" className={styles.anchorLink} onClick={() => jumpTo('diag-ferramentas')}>Ferramentas</button>
                <button type="button" className={styles.anchorLink} onClick={() => jumpTo('diag-capacidade')}>Capacidade</button>
                {extras.length > 0 ? <button type="button" className={styles.anchorLink} onClick={() => jumpTo('diag-outros')}>Outros dados</button> : null}
              </nav>

              <Disclosure id="diag-maquina" summary={<><Server size={14} aria-hidden style={{ verticalAlign: '-2px', marginRight: 6 }} />Máquina</>} defaultOpen={openMachine} onToggle={setOpenMachine}>
                {() => (data.host !== undefined ? <JsonTree value={data.host} labels={LABELS} /> : <p className={styles.meta}>O backend não informou dados do host.</p>)}
              </Disclosure>

              <Disclosure
                id="diag-aceleracao"
                summary={<><Zap size={14} aria-hidden style={{ verticalAlign: '-2px', marginRight: 6 }} />Aceleração / hipervisor</>}
                meta={accelOk === null ? undefined : accelOk ? <Badge tone="success" icon={Check} size="sm">Disponível</Badge> : <Badge tone="danger" icon={X} size="sm">Indisponível</Badge>}
                defaultOpen={openAccel}
                onToggle={setOpenAccel}
              >
                {() => (
                  <>
                    {data.acceleration !== undefined ? <JsonTree value={data.acceleration} labels={LABELS} /> : <p className={styles.meta}>O backend não informou dados de aceleração.</p>}
                    {accelOk === false ? (
                      <Banner tone="warning" icon={TriangleAlert} compact>Sem aceleração de hardware os emuladores ficam lentos demais. Ative a virtualização na BIOS e o WHPX/Hyper-V (Windows) ou o hipervisor do emulador.</Banner>
                    ) : null}
                  </>
                )}
              </Disclosure>

              <Disclosure id="diag-ferramentas" summary={<><Wrench size={14} aria-hidden style={{ verticalAlign: '-2px', marginRight: 6 }} />Ferramentas</>} meta={tools ? `${tools.filter((t) => t.ok).length}/${tools.length} ok` : undefined} defaultOpen={openTools} onToggle={setOpenTools}>
                {() => (
                  tools && tools.length > 0 ? (
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
                  )
                )}
              </Disclosure>

              <Disclosure id="diag-capacidade" summary={<><Gauge size={14} aria-hidden style={{ verticalAlign: '-2px', marginRight: 6 }} />Capacidade</>} defaultOpen={openCapacity} onToggle={setOpenCapacity}>
                {() => (data.capacity !== undefined ? <JsonTree value={data.capacity} labels={LABELS} /> : <p className={styles.meta}>Sem estimativa de capacidade.</p>)}
              </Disclosure>

              {extras.length > 0 ? (
                <Disclosure id="diag-outros" summary="Outros dados" meta={`${extras.length} ${extras.length === 1 ? 'seção' : 'seções'}`} defaultOpen={openExtras} onToggle={setOpenExtras}>
                  {() => <OutrosDados data={data} chaves={extras} labels={LABELS} />}
                </Disclosure>
              ) : null}
            </div>
          </Card>

          <Disclosure summary="Detalhes técnicos — resposta completa (JSON)">{() => <CodeBlock value={data} />}</Disclosure>
        </>
      ) : null}

      <Card aria-label="Eventos recentes do sistema">
        <CardHeader title="Eventos recentes do sistema" subtitle="O que aconteceu nesta sessão, do mais novo para o mais antigo." />
        <div className={styles.body}>
          <div className={styles.eventsToolbar} role="group" aria-label="Filtrar por nível">
            {EVENT_LEVELS.map((lvl) => (
              <button
                key={lvl}
                type="button"
                className={cx(styles.eventsFilterBtn, eventLevel === lvl && styles.eventsFilterBtnActive)}
                aria-pressed={eventLevel === lvl}
                onClick={() => { setEventLevel(lvl); setEventsLimit(EVENTS_PAGE); }}
              >
                {EVENT_LEVEL_LABEL[lvl]}
              </button>
            ))}
            <span className={styles.meta}>{filteredEvents.length} evento(s)</span>
          </div>
          {filteredEvents.length === 0 ? (
            <p className={styles.meta}>Nenhum evento recebido nesta sessão{eventLevel !== 'all' ? ` no nível "${EVENT_LEVEL_LABEL[eventLevel]}"` : ''}.</p>
          ) : (
            <>
              <ol className={styles.events}>
                {visibleEvents.map((e) => {
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
              {filteredEvents.length > eventsLimit ? (
                <Button variant="outline" size="sm" onClick={() => setEventsLimit((n) => n + EVENTS_PAGE)}>
                  Ver mais ({filteredEvents.length - eventsLimit} restante(s))
                </Button>
              ) : null}
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
