import { Ban, Bot, Check, ChevronRight, Hand, History, Hourglass, RotateCcw, ScrollText, Smartphone, Zap } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Action, Attempt, Objective, Resolution, RunDetail, Step } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { SeloEtapaExploratoria } from '../../components/SeloDeExploracao';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { CodeBlock, KvList, KvRow } from '../../components/JsonTree';
import { PacotesAceitos } from '../../components/PacotesAceitos';
import { ProgressBar } from '../../components/ProgressBar';
import { StatusBadge } from '../../components/StatusBadge';
import { cx, formatInt, ratio } from '../../lib/format';
import {
  ACTION_STATUS, ATTEMPT_STATUS, DELIVERY_LEVEL, OBJECTIVE_STATUS, POSTCONDITION_KIND, STEP_STATUS, aiWaitMeta,
  drivenByMeta, isAiBlocked, metaOf, pendingWaitMeta, slotWaitDetail,
} from '../../lib/status';
import { formatClock, formatDuration, formatSpan, parseTs, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { serverHintOf } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import { useUiStore } from '../../store/ui';
import { useSessionStore } from '../../store/session';
import { type Voto, votoDoItem } from '../aprendizado/model';
import { AtalhoParaEnsinar, EnsinarACorrigir, primeiraEtapaAEnsinar } from './EnsinarACorrigir';
import { FeedbackItem, useFeedbackDaExecucao } from './FeedbackItem';
import {
  appLabel, attemptsByStep, currentSteps, etapaAConfirmar, headlineStep, isBlocked, previousVersionSteps,
  printParaConfirmar,
} from './model';
import { SideEffectFlag } from './PlanTab';
import { type Confirmacao, resolveObjective } from './runActions';
import styles from './Runs.module.css';
import { fonteDoEfeitoRepetido, fraseDoEfeitoRepetido } from './resumo';

/**
 * ONDE o objetivo rodou, do jeito que ficou GRAVADO — não do jeito que o parque está agora (#176).
 *
 * `null` quando nada foi fotografado: objetivo materializado antes deste registro, ou que nunca chegou a ser
 * despachado. Dizer "não registrado" é o honesto; supor a máquina local seria inventar o histórico.
 */
export function ondeRodou(o: Pick<Objective, 'worker_id' | 'hosted_by' | 'device_serial'>):
    { servidor: string; serial: string | null } | null {
  const servidor = o.worker_id ?? o.hosted_by;
  if (!servidor && !o.device_serial) return null;
  return { servidor: servidor ?? '—', serial: o.device_serial ?? null };
}

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
  // D2 (ADR-054): os votos já dados, lidos uma vez por execução e repartidos entre os itens.
  const feedback = useFeedbackDaExecucao(detail.id);
  const operador = useSessionStore((s) => s.operator);
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
        <ObjectiveRow key={o.id} detail={detail} objective={o} attempts={attempts} voto={votoDoItem(feedback, o.id, operador)}
                      open={open.has(o.id)} onToggle={() => toggle(o.id)} />
      ))}
    </div>
  );
}

interface ObjectiveRowProps {
  detail: RunDetail;
  objective: Objective;
  attempts: Map<string, Attempt[]>;
  /** O voto do D2 que ESTA pessoa já deu neste item. */
  voto: Voto | null;
  open: boolean;
  onToggle: () => void;
}

function ObjectiveRow({ detail, objective: o, attempts, voto, open, onToggle }: ObjectiveRowProps) {
  const steps = useMemo(() => currentSteps(detail, o), [detail, o]);
  const headline = headlineStep(steps);
  const aEnsinar = primeiraEtapaAEnsinar(steps);                 // 31.125: o atalho fica na faixa do aparelho, sem descer até a etapa
  const meta = metaOf(OBJECTIVE_STATUS, o.status);
  const bodyId = `obj-body-${o.id}`;
  const blocked = isBlocked(o);
  // Rodízio: o aparelho está desligado esperando uma vaga de RAM — isso importa mais que a próxima etapa.
  const slotWait = slotWaitDetail(o);
  // Item 7.3 (achado #68): vaga de IA / resposta do modelo, distintas de "Executando" — que antes cobria as duas
  // por igual, e a espera por vaga só se via num texto livre que qualquer ajuste de redação quebrava em silêncio.
  const waitMeta = aiWaitMeta(o);
  // v0.20: ainda não começou porque outro aparelho está aprendendo o caminho (receita) que este vai repetir.
  const pendingWait = pendingWaitMeta(o);
  // Seletor que devolve objeto NOVO a cada render faz o Zustand achar que o estado mudou sempre (#185): lê-se a
  // fatia crua e deriva-se com `useMemo`, como na Infraestrutura.
  const workers = useAppStore((st) => st.workers);
  const workerAgora = useAppStore((st) => st.instances[o.instance_id]?.worker_id ?? null);
  // ONDE rodou ganha de onde o aparelho está HOJE: o id lógico é um apelido que muda de máquina por
  // configuração, e mostrar o dono atual num objetivo de três dias atrás era afirmar o que não aconteceu.
  // Sem fotografia (execução anterior à migração 022), o dono atual é o melhor palpite — e só ele.
  const workerId = o.worker_id ?? workerAgora;
  const server = useMemo(() => serverHintOf({ id: o.instance_id, worker_id: workerId }, workers),
                         [o.instance_id, workerId, workers]);
  const onde = ondeRodou(o);
  // ADR-055: o Marcar como concluído cita o print da etapa parada (o servidor o exige quando ela tem efeito externo).
  const confirmacao = useMemo<Confirmacao>(() => ({
    print: printParaConfirmar(detail, o),
    efeitoExterno: etapaAConfirmar(detail, o)?.side_effect ?? false,
    efeitoComprovado: etapaAConfirmar(detail, o)?.result?.efeito_comprovado ?? false,
  }), [detail, o]);

  return (
    <section className={cx(styles.obj, blocked && styles.objBlocked)} aria-label={`Objetivo em ${o.instance_id}`}>
      <button type="button" className={styles.objHead} aria-expanded={open} aria-controls={bodyId} onClick={onToggle}>
        <ChevronRight size={14} className={styles.objChevron} aria-hidden />
        <span className={styles.objInstance}>{o.instance_id}</span>
        {/* Achado #61 / E5: de uma tarefa não dava para descobrir em que máquina ela roda. */}
        <ServerBadge server={server} estatico />
        <StatusBadge meta={meta} size="sm" />
        {/* 31.350 (B): o estado do passo tem trecho PRÓPRIO, que não encolhe: só o título trunca. No celular o "Falhou" ficava no
            mesmo trecho truncado do título e saía da área visível. */}
        <span className={cx(styles.objStep, headline && !slotWait && !pendingWait ? styles.objStepDuo : 'truncate', (slotWait || waitMeta || pendingWait) && styles.objWait)}
              title={slotWait ?? pendingWait?.description ?? waitMeta?.description ?? undefined}>
          {slotWait ? (
            <><Hourglass size={12} aria-hidden /> {slotWait}</>
          ) : pendingWait ? (
            <><pendingWait.icon size={12} aria-hidden /> {pendingWait.label}</>
          ) : headline ? (
            <>
              <span className={cx('truncate', styles.objStepTitulo)}>{headline.title}</span>
              <span className={cx(styles.muted, styles.objStepEstado)}>
                {'· '}
                {waitMeta ? (
                  <><waitMeta.icon size={12} aria-hidden style={{ verticalAlign: '-2px' }} /> {waitMeta.label}</>
                ) : metaOf(STEP_STATUS, headline.status).label}
              </span>
            </>
          ) : 'Sem etapas'}
        </span>
        <ProgressBar value={ratio(o.steps_done, o.steps_total)} label={`Etapas concluídas em ${o.instance_id}`} text={`${o.steps_done}/${o.steps_total}`} />
        <span className={styles.objTime}>{o.started_at ? <Duration start={o.started_at} end={o.finished_at} /> : '—'}</span>
      </button>

      {aEnsinar ? (
        <div className={styles.objAtalho}>
          <span className={styles.muted}>«{aEnsinar.title}» · {metaOf(STEP_STATUS, aEnsinar.status).label}</span>
          <AtalhoParaEnsinar detail={detail} step={aEnsinar} />
        </div>
      ) : null}

      {open ? (
        <div id={bodyId} className={styles.objBody}>
          {blocked ? <BlockedBox objective={o} confirmacao={confirmacao} /> : o.status_detail ? <p className={styles.muted} style={{ marginTop: 10 }}>{o.status_detail}</p> : null}

          {/* D2 (ADR-054): ao lado de Confirmar, Repetir e Abandonar, e também no item concluído ou que falhou.
              Nunca pergunta sozinho: votar é opcional. */}
          <FeedbackItem runId={detail.id} objectiveId={o.id} voto={voto} simulada={detail.simulated} />

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
            {steps.length > 0 ? <StepTable detail={detail} steps={steps} attempts={attempts} /> : <p className={styles.muted}>Nenhuma etapa registrada para a versão atual do plano.</p>}
          </div>

          <OlderVersions detail={detail} objective={o} attempts={attempts} />

          <Disclosure bare summary="Detalhes técnicos">
            <KvList>
              <KvRow label="Objetivo"><span className="mono">{o.id}</span></KvRow>
              {/* Achado #176: daqui não se descobria em que máquina, em que aparelho físico nem por qual backend
                  aquilo rodou — e o vínculo id lógico → aparelho muda por configuração. */}
              <KvRow label="Onde rodou">
                {onde ? (
                  <>
                    {server ? <ServerBadge server={server} /> : <span className="mono">{onde.servidor}</span>}
                    {onde.serial ? <> · <span className="mono">{onde.serial}</span></> : null}
                  </>
                ) : 'não registrado (execução anterior a este registro)'}
              </KvRow>
              {o.hosted_by ? <KvRow label="Backend que despachou"><span className="mono">{o.hosted_by}</span></KvRow> : null}
              {o.physical_id ? <KvRow label="Identidade física"><span className="mono">{o.physical_id}</span></KvRow> : null}
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

function BlockedBox({ objective: o, confirmacao }: { objective: Objective; confirmacao: Confirmacao }) {
  const openFocus = useUiStore((s) => s.openFocus);
  const take = useControlStore((s) => s.take);
  const hasLease = useControlStore((s) => !!s.leases[o.instance_id]);
  const [busy, setBusy] = useState<Resolution | null>(null);
  const uncertain = o.status === 'uncertain';
  // Item 7.3 (achado #93, ponto 4): a IA travando o item (chave ausente, sem crédito, recusa por política) é um
  // motivo DIFERENTE de política do perfil/limite/aprovação — mesmo painel de resolução, ícone e título próprios,
  // para a pessoa não ler "aguardando você" como se o problema fosse dela.
  const aiBlocked = isAiBlocked(o);

  const resolve = async (r: Resolution) => {
    if (busy) return;
    setBusy(r);
    try {
      await resolveObjective(o, r, confirmacao);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Banner
      tone="warning"
      icon={uncertain ? OBJECTIVE_STATUS.uncertain.icon : aiBlocked ? Bot : Hand}
      className={styles.blockedBox}
      role="alert"
      title={uncertain ? 'Resultado incerto — requer a sua revisão'
            : aiBlocked ? 'Bloqueado pela IA — aguardando você' : 'Bloqueado — aguardando você'}
    >
      {o.needs ? <p className={styles.needs}>{o.needs}</p> : null}
      {o.blocked_reason ? <p><span className={styles.muted}>Motivo: </span>{o.blocked_reason}</p> : null}
      {!o.needs && !o.blocked_reason && o.status_detail ? <p>{o.status_detail}</p> : null}
      {uncertain && confirmacao.efeitoComprovado ? (
        <p style={{ marginTop: 6 }}>
          <strong>O efeito já saiu e foi comprovado.</strong> Repetir o faria de novo, por isso não há “Tentar novamente”. Confira no aparelho o que ficou em aberto e então confirme (com o print) ou abandone.
        </p>
      ) : uncertain ? (
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
        {confirmacao.efeitoComprovado ? null : (
          <Button size="sm" icon={RotateCcw} loading={busy === 'retry'} disabled={busy !== null && busy !== 'retry'} onClick={() => void resolve('retry')}>
            Tentar novamente…
          </Button>
        )}
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
              <StepTable detail={detail} steps={g.steps} attempts={attempts} />
            </div>
          ))}
        </div>
      )}
    </Disclosure>
  );
}

function StepTable({ detail, steps, attempts }: { detail: RunDetail; steps: Step[]; attempts: Map<string, Attempt[]> }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const apps = useAppStore((st) => st.apps);
  const planAppId = detail.plan?.app_id ?? null;
  return (
    <div className={styles.stepTable}>
      {steps.map((s) => {
        const isOpen = open.has(s.id);
        const bodyId = `step-body-${s.id}`;
        const meta = metaOf(STEP_STATUS, s.status);
        const list = attempts.get(s.id) ?? [];
        // Item 24.6: etapa em app diferente do plano (comando entre apps, ADR-058) — visível já no cabeçalho, não
        // só no detalhe, do mesmo jeito que o Plano mostra (`PlanStepList`).
        const outroApp = s.app_id && s.app_id !== planAppId ? appLabel(apps, s.app_id) : null;
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
                <SeloEtapaExploratoria exploratoria={s.exploratoria} />
                {outroApp ? <Badge size="sm" tone="info" title="Esta etapa roda em outro app, não no app do plano.">app: {outroApp}</Badge> : null}
                {s.side_effect ? <SideEffectFlag /> : null}
              </span>
              <StatusBadge meta={meta} size="sm" plain />
              <span className={styles.stepCell} title="Tentativas usadas / máximo">{s.attempts}/{s.max_attempts} tent.</span>
              <span className={styles.stepCell}>{s.started_at ? <Duration start={s.started_at} end={s.finished_at} /> : '—'}</span>
            </button>
            {isOpen ? (
              <div id={bodyId} className={styles.stepBody}>
                <StepDetail detail={detail} step={s} attempts={list} />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function StepDetail({ detail, step: s, attempts }: { detail: RunDetail; step: Step; attempts: Attempt[] }) {
  const apps = useAppStore((st) => st.apps);
  const appDaEtapa = appLabel(apps, s.app_id ?? detail.plan?.app_id ?? null);
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
        {s.motivo_da_persona ? (
          <>
            <dt className={styles.noteKey}>Motivo da persona</dt>
            <dd className={styles.noteVal}>{s.motivo_da_persona}</dd>
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
        {/* 29.60: o efeito que saiu mais de uma vez (29.58). É o motivo do "incerto", e o número é o que a pessoa
            precisa para decidir se apaga as cópias no app. */}
        {s.result?.efeito_repetido ? (
          <>
            <dt className={styles.noteKey}>Efeito repetido</dt>
            <dd className={styles.noteVal}>
              {fraseDoEfeitoRepetido(s.result.efeito_repetido.copias)} ({fonteDoEfeitoRepetido(s.result.efeito_repetido.fonte)})
            </dd>
          </>
        ) : null}
      </dl>

      <PacotesAceitos pacotes={s.pacotes_aceitos} />

      {/* Plano 22.7: a etapa que a habilidade errou (falhou ou ficou sem prova) se corrige aqui, no ensino dela. */}
      {/* 31.111 F5: ensinar a corrigir a partir da falha, no aparelho da etapa (o treino nasce ligado a ela). */}
      <EnsinarACorrigir detail={detail} step={s} />

      {attempts.length === 0 ? (
        <p className={styles.muted}>Nenhuma tentativa registrada ainda.</p>
      ) : (
        attempts.map((a) => <AttemptBlock key={a.id} attempt={a} />)
      )}

      <Disclosure bare summary="Detalhes técnicos">
        <KvList>
          <KvRow label="Etapa"><span className="mono">{s.id}</span></KvRow>
          <KvRow label="App">{appDaEtapa ?? '—'}</KvRow>
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
