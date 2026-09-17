import { ArrowDownToLine, Clock, ServerCrash } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { EventRecord } from '../../api/types';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { Checkbox, Select } from '../../components/Field';
import { CodeBlock } from '../../components/JsonTree';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { toneClass } from '../../components/tone';
import ui from '../../components/ui.module.css';
import { cx } from '../../lib/format';
import { instanceShort } from '../../lib/ids';
import { EVENT_LEVEL, metaOf } from '../../lib/status';
import { formatClock } from '../../lib/time';
import { loadRunDetail } from '../../store/live';
import styles from './Runs.module.css';

type Level = EventRecord['level'];
const LEVELS: Level[] = ['info', 'warn', 'error'];
const PAGE = 300;

interface TimelineTabProps {
  events: EventRecord[];
  status: 'loading' | 'ready' | 'error';
  instanceIds: string[];
  runId: string;
}

export function TimelineTab({ events, status, instanceIds, runId }: TimelineTabProps) {
  const [instance, setInstance] = useState<string>('');
  const [levels, setLevels] = useState<Set<Level>>(() => new Set(LEVELS));
  const [follow, setFollow] = useState(true);
  /** Com "acompanhar" desligado, a lista congela neste id para não pular enquanto o usuário lê. */
  const [frozenAtId, setFrozenAtId] = useState<number | null>(null);
  const [limit, setLimit] = useState(PAGE);

  const lastId = events.length > 0 ? (events[events.length - 1]?.id ?? null) : null;

  useEffect(() => {
    if (follow) setFrozenAtId(null);
  }, [follow]);

  const instanceOptions = useMemo(() => {
    const ids = new Set(instanceIds);
    for (const e of events) if (e.instance_id) ids.add(e.instance_id);
    return Array.from(ids).sort();
  }, [instanceIds, events]);

  const { visible, hiddenNew, totalMatching } = useMemo(() => {
    const matches = (e: EventRecord) => (!instance || e.instance_id === instance) && levels.has(e.level);
    const shown: EventRecord[] = [];
    let pending = 0;
    let total = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (!e || !matches(e)) continue;
      if (frozenAtId !== null && typeof e.id === 'number' && e.id > frozenAtId) {
        pending += 1;
        continue;
      }
      total += 1;
      if (shown.length < limit) shown.push(e); // mais novos primeiro
    }
    return { visible: shown, hiddenNew: pending, totalMatching: total };
  }, [events, instance, levels, frozenAtId, limit]);

  const toggleLevel = (l: Level) =>
    setLevels((prev) => {
      const next = new Set(prev);
      if (next.has(l)) next.delete(l);
      else next.add(l);
      return next.size === 0 ? new Set(LEVELS) : next;
    });

  if (status === 'loading' && events.length === 0) {
    return (
      <LoadingRegion label="Carregando a linha do tempo…" className={styles.stack}>
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} height={18} />)}
      </LoadingRegion>
    );
  }
  if (status === 'error' && events.length === 0) {
    return (
      <EmptyState icon={ServerCrash} tone="danger" compact title="Não foi possível carregar a linha do tempo" hint="Eventos novos continuam chegando em tempo real enquanto a conexão estiver ativa." actions={<Button variant="outline" onClick={() => void loadRunDetail(runId, { silent: true })}>Tentar de novo</Button>}>
        O backend não devolveu o histórico de eventos desta execução.
      </EmptyState>
    );
  }

  return (
    <div>
      <div className={styles.toolbar}>
        <div className={styles.toolbarGroup}>
          <label className={styles.toolbarLabel} htmlFor="tl-instance">Instância</label>
          <Select id="tl-instance" small value={instance} onChange={(e) => setInstance(e.target.value)} style={{ width: 170 }}>
            <option value="">Todas</option>
            {instanceOptions.map((id) => <option key={id} value={id}>{id}</option>)}
          </Select>
        </div>
        <div className={styles.toolbarGroup} role="group" aria-label="Filtrar por nível">
          <span className={styles.toolbarLabel}>Nível</span>
          {LEVELS.map((l) => {
            const meta = EVENT_LEVEL[l];
            const Icon = meta.icon;
            return (
              <button key={l} type="button" className={ui.chip} aria-pressed={levels.has(l)} onClick={() => toggleLevel(l)}>
                <Icon size={12} aria-hidden /> {meta.label}
              </button>
            );
          })}
        </div>
        <div className={styles.toolbarRight}>
          <span className={styles.muted}>{totalMatching} evento(s)</span>
          <Checkbox
            checked={follow}
            onChange={(e) => {
              const on = e.target.checked;
              setFollow(on);
              if (!on) setFrozenAtId(lastId);
            }}
            label="Acompanhar em tempo real"
          />
        </div>
      </div>

      {hiddenNew > 0 ? (
        <div className={styles.newEvents}>
          <Button size="sm" variant="outline" icon={ArrowDownToLine} onClick={() => setFrozenAtId(lastId)}>
            Mostrar {hiddenNew} evento(s) novo(s)
          </Button>
        </div>
      ) : null}

      {visible.length === 0 ? (
        <EmptyState icon={Clock} compact title="Nenhum evento para estes filtros" hint="Ajuste a instância ou os níveis selecionados." />
      ) : (
        <ol className={styles.timeline} aria-label="Eventos, do mais novo para o mais antigo" aria-live={follow ? 'off' : undefined}>
          {visible.map((e) => <EventRow key={e.id ?? `${e.ts}-${e.kind}`} event={e} />)}
        </ol>
      )}
      {totalMatching > visible.length ? (
        <div className={styles.newEvents} style={{ marginTop: 10 }}>
          <Button size="sm" variant="ghost" onClick={() => setLimit((n) => n + PAGE)}>Mostrar mais antigos ({totalMatching - visible.length} restantes)</Button>
        </div>
      ) : null}
    </div>
  );
}

function EventRow({ event: e }: { event: EventRecord }) {
  const meta = metaOf(EVENT_LEVEL, e.level);
  const Icon = meta.icon;
  return (
    <li className={cx(styles.event, e.level === 'warn' && styles.eventWarn, e.level === 'error' && styles.eventError)}>
      <time className={styles.eventTime} dateTime={e.ts}>{formatClock(e.ts)}</time>
      <span className={cx(styles.eventLevel, toneClass(meta.tone))}>
        <Icon size={13} aria-hidden />
        <span className="sr-only">{meta.label}</span>
      </span>
      <span className={styles.eventInst} title={e.instance_id ?? undefined}>{e.instance_id ? instanceShort(e.instance_id) : '—'}</span>
      <div className={styles.eventMsg}>
        {e.message || e.kind}
        <span className={styles.eventKind}>{e.kind}</span>
        {e.data ? (
          <Disclosure bare summary="Detalhes técnicos">
            {() => <CodeBlock value={{ id: e.id, objective_id: e.objective_id, step_id: e.step_id, attempt_id: e.attempt_id, data: e.data }} />}
          </Disclosure>
        ) : null}
      </div>
    </li>
  );
}
