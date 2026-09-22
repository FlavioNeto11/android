import { Ban, Check, ChevronRight, Hand, History, Hourglass, RotateCcw, ScrollText, Smartphone, Zap } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Action, Attempt, Objective, Resolution, RunDetail, Step } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { CodeBlock, KvList, KvRow } from '../../components/JsonTree';
import { ProgressBar } from '../../components/ProgressBar';
import { StatusBadge } from '../../components/StatusBadge';
import { cx, formatInt, ratio } from '../../lib/format';
import {
  ACTION_STATUS, ATTEMPT_STATUS, DELIVERY_LEVEL, OBJECTIVE_STATUS, POSTCONDITION_KIND, STEP_STATUS, drivenByMeta, metaOf,
  slotWaitDetail,
} from '../../lib/status';
import { formatClock, formatDuration, formatSpan, parseTs, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { serverHintOf } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import { useUiStore } from '../../store/ui';
import { attemptsByStep, currentSteps, headlineStep, isBlocked, previousVersionSteps } from './model';
import { SideEffectFlag } from './PlanTab';
import { resolveObjective } from './runActions';
import styles from './Runs.module.css';

function Duration({ start, end }: { start: string | null; end: string | null }) {
  const now = useNow();
  return <>{formatDuration(start, end, now)}</>;
}

function StaticDuration({ start, end }: { start: string | null; end: string | null }) {
  const s = parseTs(start);
  const e = parseTs(end);
  if (s === null || e === null) return <>—</>;
  return <>{formatSpan(Math.max(0, e - s))}</>;
}

export function InstancesTab({ detail }: { detail: RunDetail }) {
  const objectives = useMemo(
    () => detail.objectives.slice().sort((a, b) => a.instance_id.localeCompare(b.instance_id)),
    [detail.objectives],
  );
  const attempts = useMemo(() => attemptsByStep(detail), [detail]);
  // Bloqueados começam abertos: é onde o usuário precisa agir.
  const [open, setOpen] = useState<Set<string>>(() => new Set(detail.objectives.filter(isBlocked).map((o) => o.id)));
  // …e um objetivo que FICA bloqueado depois também se abre sozinho (uma vez; o usuário pode recolher).
  const seenBlocked = useRef<Set<string>>(new Set(detail.objectives.filter(isBlocked).map((o) => o.id)));
  useEffect(() => {
    const fresh = detail.objectives.filter((o) => isBlocked(o) && !seenBlocked.current.has(o.id)).map((o) => o.id);
    if (fresh.length === 0) return;
    for (const id of fresh) seenBlocked.current.add(id);
    setOpen((prev) => new Set([...prev, ...fresh]));
  }, [detail.objectives]);

  if (objectives.length === 0) {
    return (
      <EmptyState
        icon={Smartphone}
        compact
        title="Ainda não há objetivos por instância"
        hint={detail.status === 'planned' ? 'Eles são criados quando você clica em “Iniciar execução”.' : 'Os objetivos aparecem assim que o planejamento termina.'}
      />
    );
  }

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className={styles.objList}>
      {objectives.map((o) => (
        <ObjectiveRow key={o.id} detail={detail} objective={o} attempts={attempts} open={open.has(o.id)} onToggle={() => toggle(o.id)} />
      ))}
    </div>
  );
}

interface ObjectiveRowProps {
  detail: RunDetail;
  objective: Objective;
  attempts: Map<string, Attempt[]>;
  open: boolean;
  onToggle: () => void;
}

function ObjectiveRow({ detail, objective: o, attempts, open, onToggle }: ObjectiveRowProps) {
  const steps = useMemo(() => currentSteps(detail, o), [detail, o]);
  const headline = headlineStep(steps);
  const meta = metaOf(OBJECTIVE_STATUS, o.status);
  const bodyId = `obj-body-${o.id}`;
  const blocked = isBlocked(o);
  // Rodízio: o aparelho está desligado esperando uma vaga de RAM — isso importa mais que a próxima etapa.
  const slotWait = slotWaitDetail(o);
  // Seletor que devolve objeto NOVO a cada render faz o Zustand achar que o estado mudou sempre (#185): lê-se a
  // fatia crua e deriva-se com `useMemo`, como na Infraestrutura.
  const workers = useAppStore((st) => st.workers);
  const workerId = useAppStore((st) => st.instances[o.instance_id]?.worker_id ?? null);
  const server = useMemo(() => serverHintOf({ id: o.instance_id, worker_id: workerId }, workers),
                         [o.instance_id, workerId, workers]);

  return (
    <section className={cx(styles.obj, blocked && styles.objBlocked)} aria-label={`Objetivo em ${o.instance_id}`}>
      <button type="button" className={styles.objHead} aria-expanded={open} aria-controls={bodyId} onClick={onToggle}>
        <ChevronRight size={14} className={styles.objChevron} aria-hidden />
        <span className={styles.objInstance}>{o.instance_id}</span>
        {/* Achado #61 / E5: de uma tarefa não dava para descobrir em que máquina ela roda. */}
        <ServerBadge server={server} estatico />
        <StatusBadge meta={meta} size="sm" />
        <span className={cx(styles.objStep, 'truncate', slotWait && styles.objWait)} title={slotWait ?? undefined}>
          {slotWait ? (
            <><Hourglass size={12} aria-hidden /> {slotWait}</>
          ) : headline ? (
            <>{headline.title} · <span className={styles.muted}>{metaOf(STEP_STATUS, headline.status).label}</span></>
          ) : 'Sem etapas'}
        </span>
        <ProgressBar value={ratio(o.steps_done, o.steps_total)} label={`Etapas concluídas em ${o.instance_id}`} text={`${o.steps_done}/${o.steps_total}`} />
        <span className={styles.objTime}>{o.started_at ? <Duration start={o.started_at} end={o.finished_at} /> : '—'}</span>
      </button>

      {open ? (
        <div id={bodyId} className={styles.objBody}>
          {blocked ? <BlockedBox objective={o} /> : o.status_detail ? <p className={styles.muted} style={{ marginTop: 10 }}>{o.status_detail}</p> : null}

          {o.delivery_level && o.delivery_level !== 'none' ? (
            <p><span className={styles.muted}>Nível de entrega observado: </span><StatusBadge meta={metaOf(DELIVERY_LEVEL, o.delivery_level)} size="sm" /></p>
          ) : null}

          {o.effects?.length > 0 ? (
            <div>
              <h4 className={styles.subTitle}>Efeitos externos já realizados</h4>
              <ul className={styles.effects}>
                {o.effects.map((e, i) => (
                  <li key={i}><Zap size={11} aria-hidden style={{ color: 'var(--warning-text)', flex: 'none' }} /> {e}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div>
            <h4 className={styles.subTitle}>Etapas — plano v{o.plan_version}</h4>
            {steps.length > 0 ? <StepTable steps={steps} attempts={attempts} /> : <p className={styles.muted}>Nenhuma etapa registrada para a versão atual do plano.</p>}
          </div>

          <OlderVersions detail={detail} objective={o} attempts={attempts} />

          <Disclosure bare summary="Detalhes técnicos">
            <KvList>
              <KvRow label="Objetivo"><span className="mono">{o.id}</span></KvRow>
              <KvRow label="Versão do plano">v{o.plan_version}</KvRow>
              <KvRow label="Chamadas de IA">{formatInt(o.ai_calls)}</KvRow>
              <KvRow label="Tokens (entrada / saída)">{formatInt(o.ai_input_tokens)} / {formatInt(o.ai_output_tokens)}</KvRow>
              <KvRow label="Início / fim">{formatClock(o.started_at)} → {formatClock(o.finished_at)}</KvRow>
              {Object.entries(o.parameters ?? {}).map(([k, v]) => (
                <KvRow key={k} label={`Parâmetro: ${k}`}><span className="mono">{v}</span></KvRow>
              ))}
            </KvList>
          </Disclosure>
        </div>
      ) : null}
    </section>
  );
}

function BlockedBox({ objective: o }: { objective: Objective }) {
  const openFocus = useUiStore((s) => s.openFocus);
  const take = useControlStore((s) => s.take);
  const hasLease = useControlStore((s) => !!s.leases[o.instance_id]);
  const [busy, setBusy] = useState<Resolution | null>(null);
  const uncertain = o.status === 'uncertain';

  const resolve = async (r: Resolution) => {
    if (busy) return;
    setBusy(r);
    try {
      await resolveObjective(o, r);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Banner
      tone="warning"
      icon={uncertain ? OBJECTIVE_STATUS.uncertain.icon : Hand}
      className={styles.blockedBox}
      role="alert"
      title={uncertain ? 'Resultado incerto — requer a sua revisão' : 'Bloqueado — aguardando você'}
    >
      {o.needs ? <p className={styles.needs}>{o.needs}</p> : null}
      {o.blocked_reason ? <p><span className={styles.muted}>Motivo: </span>{o.blocked_reason}</p> : null}
      {!o.needs && !o.blocked_reason && o.status_detail ? <p>{o.status_detail}</p> : null}
      {uncertain ? (
        <p style={{ marginTop: 6 }}>
          <strong>Nada será reenviado automaticamente.</strong> Uma ação com efeito externo pode ou não ter acontecido. Abra o aparelho, confira o que de fato ocorreu e então escolha abaixo.
        </p>
      ) : (
        <p style={{ marginTop: 6 }}>Assuma o controle para resolver direto no aparelho (login, captcha, permissão…) e depois diga como seguir.</p>
      )}
      <div className={styles.blockedActions}>
        <Button
          size="sm"
          variant="primary"
          icon={Hand}
          onClick={() => {
            openFocus(o.instance_id);
            if (!hasLease) void take(o.instance_id);
          }}
        >
          Assumir controle
        </Button>
        <Button size="sm" icon={Check} loading={busy === 'confirm_done'} disabled={busy !== null && busy !== 'confirm_done'} onClick={() => void resolve('confirm_done')}>
          Marcar como concluído…
        </Button>
        <Button size="sm" icon={RotateCcw} loading={busy === 'retry'} disabled={busy !== null && busy !== 'retry'} onClick={() => void resolve('retry')}>
          Tentar novamente…
        </Button>
        <Button size="sm" variant="dangerGhost" icon={Ban} loading={busy === 'abandon'} disabled={busy !== null && busy !== 'abandon'} onClick={() => void resolve('abandon')}>
          Abandonar…
        </Button>
      </div>
    </Banner>
  );
}

function OlderVersions({ detail, objective, attempts }: { detail: RunDetail; objective: Objective; attempts: Map<string, Attempt[]> }) {
  const older = useMemo(() => previousVersionSteps(detail, objective), [detail, objective]);
  const reasons = useMemo(
    () => new Map(detail.plan_versions.filter((v) => v.objective_id === objective.id).map((v) => [v.version, v.reason])),
    [detail.plan_versions, objective.id],
  );
  if (older.length === 0) return null;
  return (
    <Disclosure bare summary={<><History size={12} aria-hidden style={{ verticalAlign: '-2px' }} /> Versões anteriores do plano</>} meta={`${older.length}`}>
      {() => (
        <div className={styles.objList}>
          {reasons.get(objective.plan_version) ? (
            <p className={styles.muted}>Motivo da revisão para v{objective.plan_version}: {reasons.get(objective.plan_version)}</p>
          ) : null}
          {older.map((g) => (
            <div key={g.version}>
              <p className={styles.muted}>Plano v{g.version}{reasons.get(g.version) ? ` — ${reasons.get(g.version)}` : ''}</p>
              <StepTable steps={g.steps} attempts={attempts} />
            </div>
          ))}
        </div>
      )}
    </Disclosure>
  );
}

function StepTable({ steps, attempts }: { steps: Step[]; attempts: Map<string, Attempt[]> }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  return (
    <div className={styles.stepTable}>
      {steps.map((s) => {
        const isOpen = open.has(s.id);
        const bodyId = `step-body-${s.id}`;
        const meta = metaOf(STEP_STATUS, s.status);
        const list = attempts.get(s.id) ?? [];
        return (
          <div key={s.id} className={styles.stepRow}>
            <button
              type="button"
              className={styles.stepHead}
              aria-expanded={isOpen}
              aria-controls={bodyId}
              onClick={() =>
                setOpen((prev) => {
                  const next = new Set(prev);
                  if (next.has(s.id)) next.delete(s.id);
                  else next.add(s.id);
                  return next;
                })
              }
            >
              <ChevronRight size={13} className={styles.objChevron} aria-hidden />
              <span className={styles.stepSeq}>{s.seq}</span>
              <span className={styles.stepName}>
                <span className="truncate">{s.title}</span>
                <DrivenByBadge drivenBy={s.driven_by} />
                {s.side_effect ? <SideEffectFlag /> : null}
              </span>
              <StatusBadge meta={meta} size="sm" plain />
              <span className={styles.stepCell} title="Tentativas usadas / máximo">{s.attempts}/{s.max_attempts} tent.</span>
              <span className={styles.stepCell}>{s.started_at ? <Duration start={s.started_at} end={s.finished_at} /> : '—'}</span>
            </button>
            {isOpen ? (
              <div id={bodyId} className={styles.stepBody}>
                <StepDetail step={s} attempts={list} />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function StepDetail({ step: s, attempts }: { step: Step; attempts: Attempt[] }) {
  return (
    <>
      <dl className={styles.noteGrid}>
        <dt className={styles.noteKey}>Objetivo da etapa</dt>
        <dd className={styles.noteVal}>{s.goal}</dd>
        {s.status_detail ? (
          <>
            <dt className={styles.noteKey}>Situação</dt>
            <dd className={styles.noteVal}>{s.status_detail}</dd>
          </>
        ) : null}
        {s.next_retry_at ? (
          <>
            <dt className={styles.noteKey}>Próxima tentativa</dt>
            <dd className={styles.noteVal}>{formatClock(s.next_retry_at)}</dd>
          </>
        ) : null}
        {s.result ? (
          <>
            <dt className={styles.noteKey}>Resultado</dt>
            <dd className={styles.noteVal}>
              {s.result.verified ? 'Verificado' : 'Não verificado'}
              {s.result.delivery_level ? ` · ${metaOf(DELIVERY_LEVEL, s.result.delivery_level).label}` : ''}
              {s.result.evidence_text ? <> — “{s.result.evidence_text}”</> : null}
            </dd>
          </>
        ) : null}
      </dl>

      {attempts.length === 0 ? (
        <p className={styles.muted}>Nenhuma tentativa registrada ainda.</p>
      ) : (
        attempts.map((a) => <AttemptBlock key={a.id} attempt={a} />)
      )}

      <Disclosure bare summary="Detalhes técnicos">
        <KvList>
          <KvRow label="Etapa"><span className="mono">{s.id}</span></KvRow>
          <KvRow label="Depende de">{s.depends_on.length > 0 ? <span className="mono">{s.depends_on.join(', ')}</span> : '—'}</KvRow>
          <KvRow label="Pré-condição">{s.precondition ?? '—'}</KvRow>
          <KvRow label="Verificação de sucesso">
            {POSTCONDITION_KIND[s.postcondition?.kind] ?? s.postcondition?.kind ?? '—'}
            {s.postcondition?.value ? <> · <span className="mono">{s.postcondition.value}</span></> : null}
          </KvRow>
          <KvRow label="Tempo limite">{s.timeout_s} s</KvRow>
        </KvList>
      </Disclosure>
    </>
  );
}

function AttemptBlock({ attempt: a }: { attempt: Attempt }) {
  const meta = metaOf(ATTEMPT_STATUS, a.status);
  return (
    <div className={styles.attempt}>
      <div className={styles.attemptHead}>
        <span className={styles.attemptTitle}>Tentativa {a.number}</span>
        <StatusBadge meta={meta} size="sm" />
        <span className={styles.muted}>
          {formatClock(a.started_at)} · {a.finished_at ? <StaticDuration start={a.started_at} end={a.finished_at} /> : <Duration start={a.started_at} end={null} />}
        </span>
      </div>
      <div className={styles.attemptBody}>
        {a.error || a.recovery || a.observed_result ? (
          <dl className={styles.noteGrid}>
            {a.error ? (
              <>
                <dt className={styles.noteKey}>Erro original</dt>
                <dd className={cx(styles.noteVal, styles.noteErr)}>{a.error}</dd>
              </>
            ) : null}
            {a.recovery ? (
              <>
                <dt className={styles.noteKey}>Recuperação tentada</dt>
                <dd className={styles.noteVal}>{a.recovery}</dd>
              </>
            ) : null}
            {a.observed_result ? (
              <>
                <dt className={styles.noteKey}>Resultado observado</dt>
                <dd className={styles.noteVal}>{a.observed_result}</dd>
              </>
            ) : null}
          </dl>
        ) : null}

        {a.actions.length === 0 ? (
          <p className={styles.muted}>Nenhuma ação registrada nesta tentativa.</p>
        ) : (
          <ol className={styles.actions} aria-label={`Ações da tentativa ${a.number}`}>
            {a.actions.map((act) => <ActionRow key={act.id} action={act} />)}
          </ol>
        )}
      </div>
    </div>
  );
}

/** Selo por etapa a partir de `driven_by`: "Receita" (sem IA), "Receita + IA" ou "IA". Nada enquanto for `null`. */
export function DrivenByBadge({ drivenBy }: { drivenBy: Step['driven_by'] | undefined }) {
  const meta = drivenByMeta(drivenBy);
  if (!meta) return null;
  return (
    <Badge tone={meta.tone} icon={meta.icon} size="sm" title={meta.description} className={styles.flagBadge}>
      <span className="sr-only">Conduzida por: </span>{meta.label}
    </Badge>
  );
}

/** Selo por ação quando `source === 'recipe'` (reproduzida da receita, sem chamada de modelo). */
export function RecipeActionBadge() {
  return (
    <Badge tone="success" icon={ScrollText} size="sm" title="Ação reproduzida da receita: nenhuma chamada de modelo." className={styles.flagBadge}>
      receita
    </Badge>
  );
}

function ActionRow({ action: act }: { action: Action }) {
  const meta = metaOf(ACTION_STATUS, act.status);
  const fromRecipe = act.source === 'recipe';
  return (
    <li>
      <div className={styles.action}>
        <span className={styles.actionSeq}>{act.seq}</span>
        <span className={styles.actionTool} title={act.tool}>{act.tool}</span>
        <span className={styles.actionWhy}>
          {fromRecipe ? <><RecipeActionBadge />{' '}</> : null}
          {act.rationale ?? <span className={styles.muted}>{fromRecipe ? 'Passo gravado na receita' : 'Sem justificativa registrada'}</span>}
          {act.side_effect ? <> <SideEffectFlag /></> : null}
          {act.error ? <span className={styles.actionErr}>{act.error}</span> : null}
        </span>
        <StatusBadge meta={meta} size="sm" plain />
        <span className={styles.stepCell}>{act.done_at ? <StaticDuration start={act.intent_at} end={act.done_at} /> : '…'}</span>
      </div>
      <Disclosure bare summary="Detalhes técnicos (argumentos e resultado)">
        {() => (
          <div className={styles.objList}>
            <CodeBlock value={{ args: act.args, result: act.result }} />
          </div>
        )}
      </Disclosure>
    </li>
  );
}
