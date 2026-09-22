import {
  AppWindow, CircleDashed, Clock, Eye, Hand, Hourglass, ImageOff, LoaderCircle, Maximize2, Moon, OctagonAlert, PowerOff,
  Store, TriangleAlert, User,
} from 'lucide-react';
import { memo, useState, type MouseEvent } from 'react';
import { frameUrl } from '../../api/client';
import type { Instance, Settings } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Checkbox } from '../../components/Field';
import { ProgressBar } from '../../components/ProgressBar';
import type { InstagramProfile } from '../../api/types';
import { StatusBadge } from '../../components/StatusBadge';
import { cx, ratio } from '../../lib/format';
import { CONTROL_OWNER, INSTANCE_STATE, SESSION_STATUS, STEP_STATUS, metaOf } from '../../lib/status';
import { ageMs, formatAgoCoarse, useNow } from '../../lib/time';
import { selectSlotWait, useAppStore } from '../../store/app';
import { useControlStore, userHasControl } from '../../store/control';
import { ACTION_META, comandoAbertoDe, motivoDoComando, runInstanceAction, useBusyStore } from './actions';
import { CommandSummary } from './CommandTrail';
import { canHibernate, noFrameTitle, primaryActionFor, serverHintOf, type ServerHint } from './deviceState';
import { ServerBadge } from './ServerBadge';
import styles from './Devices.module.css';

interface DeviceCardProps {
  instance: Instance;
  appName: string | null;
  /** Perfil do Instagram vinculado a este aparelho, quando existe: mostra a conta e o estado da sessão. */
  profile?: InstagramProfile | null;
  selected: boolean;
  focused: boolean;
  onToggle: (id: string) => void;
  onRange: (id: string) => void;
  onOpen: (id: string) => void;
}

/** Idade máxima aceitável de um frame (mesma regra do backend: limite configurado ou 2,5× o intervalo de captura). */
export function frameStaleLimitMs(settings: Settings | null, focused: boolean): number {
  const interval = (focused ? settings?.capture_focus_interval_s : settings?.capture_grid_interval_s) ?? 5;
  return Math.max(settings?.frame_max_age_ms ?? 6000, interval * 2500);
}

/**
 * Frame "desatualizado": o backend marcou `stale`, a instância está online sem frame, ou — conferido no
 * cliente, porque um backend que parou de capturar não tem como avisar — o frame passou da idade máxima.
 */
export function isFrameStale(instance: Pick<Instance, 'state' | 'frame'>, nowMs?: number, limitMs?: number): boolean {
  if (instance.state !== 'online') return false;
  if (!instance.frame || instance.frame.stale) return true;
  if (nowMs === undefined || limitMs === undefined) return false;
  const age = ageMs(instance.frame.ts, nowMs);
  return age !== null && age > limitMs;
}

/** Versão reativa: reavalia a cada segundo com o relógio do servidor. */
export function useFrameStale(instance: Pick<Instance, 'state' | 'frame'>, focused: boolean): boolean {
  const now = useNow();
  const settings = useAppStore((s) => s.settings);
  return isFrameStale(instance, now, frameStaleLimitMs(settings, focused));
}

function FrameAge({ ts }: { ts: string | null }) {
  const now = useNow();
  return <>{ts ? formatAgoCoarse(ts, now) : 'sem frame'}</>;
}

function Thumb({ instance, server, onOpen }: { instance: Instance; server: ServerHint | null; onOpen: () => void }) {
  const { id, state, frame } = instance;
  const [failedFrame, setFailedFrame] = useState<string | null>(null);
  const stale = useFrameStale(instance, false);

  if (state !== 'online') {
    const meta = INSTANCE_STATE[state] ?? INSTANCE_STATE.error;
    const Icon =
      state === 'absent' ? CircleDashed
      : state === 'stopped' ? PowerOff
      : state === 'hibernated' ? Moon
      : state === 'error' ? OctagonAlert
      : LoaderCircle;
    const title = noFrameTitle(state, instance.kind, server);
    // Título e detalhe agora saem da MESMA verdade (o processo no worker), então às vezes coincidem. Repetir a
    // frase em duas linhas não informa nada — some com a segunda quando ela só ecoa a primeira.
    const detalhe = instance.state_detail;
    const ecoa = !!detalhe && detalhe.trim().toLowerCase().replace(/\.$/, '') === title.toLowerCase();
    return (
      <div className={cx(styles.thumbBtn, styles.thumbStatic)}>
        <div className={styles.placeholder} style={state === 'error' ? { color: 'var(--danger-text)' } : undefined}>
          <Icon size={26} className={meta.spin ? 'spin' : undefined} aria-hidden />
          <span className={styles.placeholderTitle}>{title}</span>
          {detalhe && !ecoa ? <span className={styles.placeholderDetail}>{detalhe}</span> : null}
        </div>
      </div>
    );
  }

  const showImage = !!frame && failedFrame !== frame.id;
  return (
    <button type="button" className={styles.thumbBtn} onClick={onOpen} aria-label={`Abrir ${id} na visão de foco`}>
      {showImage && frame ? (
        <img
          className={cx(styles.thumbImg, stale && styles.thumbImgStale)}
          src={frameUrl(id, 'thumb', frame.id)}
          alt={`Tela atual de ${id}`}
          draggable={false}
          decoding="async"
          onError={() => setFailedFrame(frame.id)}
        />
      ) : (
        <div className={styles.placeholder}>
          <ImageOff size={24} aria-hidden />
          <span className={styles.placeholderTitle}>{frame ? 'Imagem indisponível' : 'Aguardando o primeiro frame'}</span>
          <span>{frame ? 'O backend não entregou este frame.' : 'A captura começa assim que a automação estiver pronta.'}</span>
        </div>
      )}
      {stale ? (
        <div className={styles.staleOverlay}>
          <span className={styles.staleTag}><TriangleAlert size={13} aria-hidden /> Desatualizado</span>
          <span className={styles.staleAge}>{frame ? <>último frame <FrameAge ts={frame.ts} /></> : 'nenhum frame recebido'}</span>
        </div>
      ) : null}
    </button>
  );
}

function DeviceCardImpl({ instance, appName, profile, selected, focused, onToggle, onRange, onOpen }: DeviceCardProps) {
  const { id, state, current } = instance;
  const busyAction = useBusyStore((s) => s.busy[id]);
  // Um aparelho, uma operacao: enquanto houver comando aberto, os verbos de ciclo de vida ficam indisponiveis
  // COM o motivo. Antes o `busy` cobria so a duracao do POST, e o botao voltava a ficar clicavel durante um boot
  // remoto de ate 480 s -- o clique duplo que o aceite 9 proibe.
  const comandoAberto = useAppStore((s) => comandoAbertoDe(s.lastCommand[id]));
  // O comando que ainda age, ou o que acabou sem desfecho conhecido: os dois precisam ficar VISÍVEIS no cartão.
  // Um `uncertain` sumia junto com o toast e só voltava a existir no banco — o de 21/09 ficou um dia invisível.
  const ultimoComando = useAppStore((s) => s.lastCommand[id]);
  const comandoAMostrar = comandoAberto ?? (ultimoComando?.state === 'uncertain' ? ultimoComando : undefined);
  const lease = useControlStore((s) => s.leases[id]);
  const controlBusy = useControlStore((s) => !!s.busy[id]);
  const release = useControlStore((s) => s.release);
  const mine = userHasControl(instance, lease);
  const hibernation = useAppStore((s) => s.health?.features?.hibernation === true);
  const workers = useAppStore((s) => s.workers);
  const server = serverHintOf(instance, workers);
  // Rodízio: o aparelho está desligado porque o objetivo dele espera uma vaga de RAM.
  const slotWait = useAppStore((s) => selectSlotWait(s, id));
  const showSlotWait = !!slotWait && state !== 'online' && state !== 'booting';

  // A loja (Play Store) é ligada, desligada e aberta como qualquer aparelho, mas nunca é ALVO de comando: sem caixa
  // de seleção, e Ctrl/Shift+clique não fazem nada nela.
  const loja = instance.kind === 'store';
  const stateMeta = metaOf(INSTANCE_STATE, state);
  const controlMeta = metaOf(CONTROL_OWNER, instance.control);
  const stepMeta = current?.step_status ? metaOf(STEP_STATUS, current.step_status) : null;

  // Ctrl/Cmd+clique alterna; Shift+clique seleciona o intervalo — em qualquer ponto do cartão.
  const onClickCapture = (e: MouseEvent) => {
    if (!(e.ctrlKey || e.metaKey || e.shiftKey)) return;
    if (loja) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.shiftKey) onRange(id);
    else onToggle(id);
  };

  const primary = primaryActionFor(state, instance);

  return (
    <div className={styles.cardWrap}>
      <article
        className={cx(styles.card, selected && styles.cardSelected, focused && styles.cardFocused, !!instance.attention && styles.cardAttention)}
        aria-label={`Instância ${id} — ${stateMeta.label}`}
        onClickCapture={onClickCapture}
        onMouseDown={(e) => {
          if (e.shiftKey) e.preventDefault();
        }}
      >
        <div className={styles.head}>
          {loja ? (
            <Badge size="sm" tone="accent" icon={Store}
                   title="Aparelho-loja: guarda o aplicativo oficial da Play Store. Não executa tarefas.">Loja</Badge>
          ) : (
            <Checkbox checked={selected} onChange={() => onToggle(id)} aria-label={`Selecionar ${id}`} />
          )}
          <span className={styles.instId}>{id}</span>
          {/* Em que máquina este aparelho roda — o cartão não dizia, e as três realidades pareciam duas (#61). */}
          <ServerBadge server={server} />
          <span className={styles.headBadge}><StatusBadge meta={stateMeta} size="sm" srPrefix="Estado" /></span>
        </div>

        <Thumb instance={instance} server={server} onOpen={() => onOpen(id)} />

        <div className={styles.body}>
          <div className={cx(styles.line, !instance.account_label && styles.lineMuted)}>
            <User size={13} aria-hidden />
            <span className="truncate" title={instance.account_label ?? undefined}>
              <span className="sr-only">Conta: </span>
              {instance.account_label || 'Sem rótulo de conta'}
            </span>
          </div>
          {instance.account_evidence ? (
            <div className={styles.observed}>
              <Eye size={11} aria-hidden />
              <span className={styles.observedTag}>observado</span>
              <span className="truncate" title={instance.account_evidence}>{instance.account_evidence}</span>
            </div>
          ) : null}
          <div className={cx(styles.line, !appName && styles.lineMuted)}>
            <AppWindow size={13} aria-hidden />
            <span className="truncate" title={appName ?? undefined}>
              <span className="sr-only">App: </span>
              {appName ?? 'Sem app associado'}
            </span>
          </div>
          {profile ? (
            <div className={styles.line}>
              <StatusBadge meta={metaOf(SESSION_STATUS, profile.session.status)} size="sm" srPrefix="Sessão" />
              <span className="truncate" title={`Perfil @${profile.username}`}>@{profile.username}</span>
            </div>
          ) : null}

          <div className={styles.metaRow}>
            <StatusBadge
              meta={controlMeta}
              size="sm"
              label={instance.control === 'none' ? 'Controle: —' : `Controle: ${mine ? 'Você' : controlMeta.label}`}
            />
            {state === 'online' ? (
              <span className={styles.frameAge} title="Idade do último frame recebido">
                <Clock size={11} aria-hidden />
                <span className="sr-only">Último frame </span>
                <FrameAge ts={instance.frame?.ts ?? null} />
              </span>
            ) : null}
          </div>

          <div className={styles.step}>
            {current?.step_title ? (
              <>
                <span className={cx(styles.stepTitle, 'truncate')} title={current.step_title}>{current.step_title}</span>
                <div className={styles.metaRow}>
                  {stepMeta ? <StatusBadge meta={stepMeta} size="sm" plain /> : <span />}
                </div>
                <ProgressBar
                  value={ratio(current.steps_done, current.steps_total)}
                  label={`Etapas concluídas em ${id}`}
                  text={`${current.steps_done}/${current.steps_total}`}
                />
              </>
            ) : showSlotWait ? (
              <span className={cx(styles.stepIdle, styles.stepWait)} title={slotWait ?? undefined}>
                <Hourglass size={12} aria-hidden />
                <span><span className="sr-only">Tarefa na fila: </span>{slotWait}</span>
              </span>
            ) : (
              <span className={styles.stepIdle}>{current?.run_id ? 'Aguardando a próxima etapa…' : 'Sem tarefa em andamento'}</span>
            )}
          </div>

          {comandoAMostrar ? <CommandSummary cmd={comandoAMostrar} /> : null}

          {instance.control_pending ? (
            <Banner tone="info" icon={LoaderCircle} compact className={styles.attention} role="status">
              Aguardando a IA concluir a ação atual…
            </Banner>
          ) : null}
          {instance.attention ? (
            <Banner tone="warning" icon={Hand} compact className={styles.attention} role="status">
              {instance.attention}
            </Banner>
          ) : null}
        </div>

        <div className={styles.foot}>
          {primary ? (
            <Button
              size="sm"
              variant={state === 'error' ? 'outline' : 'secondary'}
              icon={ACTION_META[primary].icon}
              loading={busyAction === primary || comandoAberto?.verb === primary}
              disabledReason={motivoDoComando(comandoAberto, id, primary)}
              onClick={() => void runInstanceAction(id, primary)}
            >
              {state === 'error' ? 'Tentar novamente' : ACTION_META[primary].label}
            </Button>
          ) : null}
          {mine ? (
            <Button size="sm" variant="outline" loading={controlBusy} onClick={() => void release(id)}>
              Devolver à IA
            </Button>
          ) : null}
          <span className={styles.footSpacer} />
          {canHibernate(state, hibernation, instance) ? (
            <Button
              size="sm"
              variant="ghost"
              icon={ACTION_META.hibernate.icon}
              iconOnly
              label={`${ACTION_META.hibernate.label} ${id}`}
              loading={busyAction === 'hibernate' || comandoAberto?.verb === 'hibernate'}
              disabledReason={motivoDoComando(comandoAberto, id, 'hibernate')}
              onClick={() => void runInstanceAction(id, 'hibernate')}
            />
          ) : null}
          <Button size="sm" variant="ghost" icon={Maximize2} onClick={() => onOpen(id)} aria-label={`Abrir ${id}`}>
            Abrir
          </Button>
        </div>
      </article>
    </div>
  );
}

export const DeviceCard = memo(DeviceCardImpl);
