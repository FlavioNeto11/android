import {
  AppWindow, CircleDashed, Clock, Eye, EyeOff, Hand, Hourglass, ImageOff, LoaderCircle, Maximize2, Moon, OctagonAlert, Pause,
  PowerOff, Store, TriangleAlert, User,
} from 'lucide-react';
import { memo, useEffect, useRef, useState, type MouseEvent } from 'react';
import { frameUrl, profileAvatarUrl } from '../../api/client';
import type { Instance, PersonaOnDevice, Settings } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Checkbox } from '../../components/Field';
import { ProgressBar } from '../../components/ProgressBar';
import { StatusBadge } from '../../components/StatusBadge';
import { cx, ratio } from '../../lib/format';
import { CONTROL_OWNER, INSTANCE_STATE, SESSION_STATUS, STEP_STATUS, metaOf } from '../../lib/status';
import { evidenciaLegivel } from '../../lib/rotulos';
import { ageMs, tempoRelativo, useNow } from '../../lib/time';
import { selectSlotWait, useAppStore } from '../../store/app';
import { useControlStore, userHasControl } from '../../store/control';
import { ACTION_META, comandoAbertoDe, motivoDoComando, runInstanceAction, useBusyStore } from './actions';
import { CommandSummary } from './CommandTrail';
import {
  MOTIVO_SERVIDOR_SEM_RESPOSTA, NO_FRAME_TITLE, canHibernate, noFrameTitle, primaryActionFor, serverHintOf, type ServerHint,
} from './deviceState';
import { ServerBadge } from './ServerBadge';
import { aparelhoDesconhecido, seloDoAparelho } from './selos';
import { PAUSED_LABEL, SENSITIVE_LABEL, isPreviewPaused, isScreenFailure, streamLabel } from './streamState';
import { usePreviewVisible } from './usePreviewVisible';
import styles from './Devices.module.css';

interface DeviceCardProps {
  instance: Instance;
  appName: string | null;
  /** As personas deste aparelho (N:N, v0.29), uma por vínculo: os avatares, a conta e a sessão AQUI. */
  personas?: readonly PersonaOnDevice[] | null;
  selected: boolean;
  focused: boolean;
  onToggle: (id: string) => void;
  onRange: (id: string) => void;
  onOpen: (id: string) => void;
  /**
   * Versão compacta (aparelho parado): cabeçalho, estado, a conta e o botão. Sem a miniatura e sem o bloco
   * "Emulador desligado", que ocupava a altura de um cartão inteiro para dizer o que o selo já diz.
   */
  compacto?: boolean;
}

/**
 * Prazo do frame com a IA no controle — o MESMO `FRAME_MAX_AGE_IA_S` do backend (`devices/manager.py`; um teste de
 * lá confere). A prévia cede a vez à IA e o frame chega uma vez por ciclo dela (árvore, modelo, ação, assentamento):
 * nas execuções reais de 28/09 no android-06, nunca menos de 5,8 s entre duas ações. Com o limite do foco, a imagem
 * ficava cinza com "Desatualizado" durante boa parte de toda execução.
 */
export const AI_FRAME_MAX_AGE_MS = 30_000;

/**
 * Idade máxima aceitável de um frame (mesma regra do backend: limite configurado ou 2,5× o intervalo de captura; com a
 * IA no controle, no mínimo o ritmo dela).
 */
export function frameStaleLimitMs(settings: Settings | null, focused: boolean, aiInControl = false): number {
  const interval = (focused ? settings?.capture_focus_interval_s : settings?.capture_grid_interval_s) ?? 5;
  const limit = Math.max(settings?.frame_max_age_ms ?? 6000, interval * 2500);
  return aiInControl ? Math.max(limit, AI_FRAME_MAX_AGE_MS) : limit;
}

/**
 * Frame "desatualizado": o backend marcou `stale`, a instância está online sem frame, ou — conferido no
 * cliente, porque um backend que parou de capturar não tem como avisar — o frame passou da idade máxima.
 * Prévia suspensa (`stream.status: paused`) nunca é desatualizada: o frame é velho porque ninguém pediu um novo,
 * e o backend marca `frame.stale` mesmo assim — por isso a pergunta vem antes. Marcador de tela sensível (C4)
 * também não: não há imagem para envelhecer, e a captura pausa enquanto a senha é digitada. Só a falha publicada
 * da tela (`capture_error`, `worker_offline`) continua aparecendo por cima dele.
 */
export function isFrameStale(instance: Pick<Instance, 'state' | 'frame' | 'stream'>, nowMs?: number, limitMs?: number): boolean {
  if (instance.state !== 'online') return false;
  if (isPreviewPaused(instance)) return false;
  if (instance.frame?.sensitive && !isScreenFailure(instance)) return false;
  if (!instance.frame || instance.frame.stale) return true;
  if (nowMs === undefined || limitMs === undefined) return false;
  const age = ageMs(instance.frame.ts, nowMs);
  return age !== null && age > limitMs;
}

/** Versão reativa: reavalia a cada segundo com o relógio do servidor; com a IA no controle, no prazo do ritmo dela. */
export function useFrameStale(instance: Pick<Instance, 'state' | 'frame' | 'stream'> & Partial<Pick<Instance, 'control'>>,
                              focused: boolean): boolean {
  const now = useNow();
  const settings = useAppStore((s) => s.settings);
  return isFrameStale(instance, now, frameStaleLimitMs(settings, focused, instance.control === 'ai'));
}

function FrameAge({ ts }: { ts: string | null }) {
  const now = useNow();
  return <>{ts ? tempoRelativo(ts, now) : 'sem imagem'}</>;
}

function Thumb({ instance, server, visible, onOpen }: { instance: Instance; server: ServerHint | null; visible: boolean; onOpen: () => void }) {
  const { id, state, frame } = instance;
  const [failedFrame, setFailedFrame] = useState<string | null>(null);
  // Frame que a miniatura mostra. Só acompanha o `frame.id` enquanto o cartão está na tela: fora dela a imagem
  // fica no último frame visto e nenhum GET /frame sai. Antes, cada evento `frame` trocava o `src` de TODAS as
  // miniaturas, vistas ou não. (Ajuste de estado durante a renderização: o padrão do React para estado derivado.)
  const sensitive = frame?.sensitive === true;
  const [shownFrameId, setShownFrameId] = useState<string | null>(visible && !sensitive ? frame?.id ?? null : null);
  const latestFrameId = frame?.id ?? null;
  // Marcador de tela sensível (C4): não há imagem a buscar (o GET daria 404), e a imagem anterior sai na hora —
  // visível ou não, ela pode ser justamente a tela que ficou sensível. Frame comum de volta, a imagem volta.
  if (sensitive) {
    if (shownFrameId !== null) setShownFrameId(null);
  } else if (visible && latestFrameId !== shownFrameId) {
    setShownFrameId(latestFrameId);
  }
  const stale = useFrameStale(instance, false);
  const staleInfo = streamLabel(instance, stale);
  const paused = isPreviewPaused(instance);

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

  const showImage = !!shownFrameId && failedFrame !== shownFrameId;
  return (
    <button type="button" className={styles.thumbBtn} onClick={onOpen} aria-label={`Abrir ${id} na visão de foco`}>
      {showImage && shownFrameId ? (
        <img
          className={cx(styles.thumbImg, stale && styles.thumbImgStale)}
          src={frameUrl(id, 'thumb', shownFrameId)}
          alt={`Tela atual de ${id}`}
          draggable={false}
          decoding="async"
          onError={() => setFailedFrame(shownFrameId)}
        />
      ) : !visible ? (
        // Fora da tela e nunca visto: nada a buscar nem a afirmar.
        <div className={styles.placeholder} aria-hidden />
      ) : sensitive ? (
        <div className={styles.placeholder} title={SENSITIVE_LABEL.hint}>
          <EyeOff size={24} aria-hidden />
          <span className={styles.placeholderTitle}>{SENSITIVE_LABEL.title}</span>
          <span>A imagem desta tela não sai do aparelho.</span>
        </div>
      ) : paused && !frame ? (
        <div className={styles.placeholder}>
          <Pause size={24} aria-hidden />
          <span className={styles.placeholderTitle}>{PAUSED_LABEL.title}</span>
          <span>O aparelho segue online; a imagem volta quando ele aparecer na tela.</span>
        </div>
      ) : (
        <div className={styles.placeholder}>
          <ImageOff size={24} aria-hidden />
          <span className={styles.placeholderTitle}>{frame ? 'Imagem indisponível' : 'Aguardando a primeira imagem'}</span>
          <span>{frame ? 'O servidor não entregou esta imagem.' : 'A captura começa assim que a automação estiver pronta.'}</span>
        </div>
      )}
      {/* Suspensa sobre o marcador (a loja fora de vista) repetiria "sem imagem" com a hora de uma tela que nunca
          teve imagem: o aviso de tela sensível já diz tudo. */}
      {paused && frame && !sensitive ? (
        <div className={styles.pausedOverlay}>
          <span className={styles.pausedTag} title={PAUSED_LABEL.hint}><Pause size={12} aria-hidden /> {PAUSED_LABEL.title}</span>
          <span className={styles.staleAge}>última imagem <FrameAge ts={frame.ts} /></span>
        </div>
      ) : null}
      {stale ? (
        <div className={styles.staleOverlay}>
          <span className={styles.staleTag} title={staleInfo?.hint}><TriangleAlert size={13} aria-hidden /> Desatualizado</span>
          <span className={styles.staleAge}>
            {staleInfo ? `${staleInfo.title} · ` : ''}
            {frame ? <>última imagem <FrameAge ts={frame.ts} /></> : 'nenhuma imagem recebida'}
          </span>
        </div>
      ) : null}
    </button>
  );
}

/** Quem mostrar primeiro: quem tem conta de cadastro (o @ diz mais que o nome), e cada persona uma vez só. */
export function personasDoCartao(lista: readonly PersonaOnDevice[] | null | undefined): PersonaOnDevice[] {
  const vistas = new Set<string>();
  return [...(lista ?? [])]
    .sort((a, b) => Number(!!b.username) - Number(!!a.username))
    .filter((p) => (vistas.has(p.profile_id) ? false : (vistas.add(p.profile_id), true)));
}

export const rotuloDaPersona = (p: PersonaOnDevice) => (p.username ? `@${p.username}` : p.name || p.display_name || p.profile_id);

function DeviceCardImpl({ instance, appName, personas: vinculadas, selected, focused, onToggle, onRange, onOpen, compacto = false }: DeviceCardProps) {
  const { id, state, current } = instance;
  const personas = personasDoCartao(vinculadas);
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
  const stateMeta = seloDoAparelho(instance, workers);
  // RF-40: com o servidor sem resposta, o verbo que o estado guardado sugere ("Iniciar") fica indisponível, com o motivo.
  const semServidor = aparelhoDesconhecido(instance, workers) ? MOTIVO_SERVIDOR_SEM_RESPOSTA : null;
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
  // Na versão compacta some o bloco grande da miniatura; o que ele dizia DE ÚTIL (a máquina não responde, o ADB caiu)
  // sobe para uma linha. "Emulador desligado" em si não sobe: é o que o selo "Parada" já diz.
  const tituloSemTela = compacto && state !== 'online' ? noFrameTitle(state, instance.kind, server) : null;
  // "Desligado de propósito" (aqui ou no worker) e "Hibernado" são o que o selo já diz; só o que é PROBLEMA sobe.
  const avisoDoServidor = state !== 'online' && tituloSemTela && tituloSemTela !== NO_FRAME_TITLE[state]
    && !/^(Emulador desligado|Hibernado)/.test(tituloSemTela) ? tituloSemTela : null;

  // Abrir o foco estreita o conteúdo (1270 → 786 px): a grade troca de colunas e o cartão clicado ia parar fora da
  // vista — o "scroll que quebra" do Painel. O próprio cartão em foco se põe à vista depois que o layout assenta
  // (duas passadas: o painel cresce em etapas).
  const cardRef = useRef<HTMLElement>(null);
  // O cartão na viewport entra no `watch.grid` (C2) e só então a miniatura busca imagem.
  const visible = usePreviewVisible(id, cardRef);
  useEffect(() => {
    if (!focused) return undefined;
    const pos = () => cardRef.current?.scrollIntoView?.({ block: 'nearest' });
    const t1 = window.setTimeout(pos, 80);
    const t2 = window.setTimeout(pos, 500);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [focused]);

  return (
    <div className={styles.cardWrap}>
      <article
        ref={cardRef}
        className={cx(styles.card, compacto && styles.cardCompacto, selected && styles.cardSelected, focused && styles.cardFocused, !!instance.attention && styles.cardAttention)}
        aria-label={`Aparelho ${id} — ${stateMeta.label}`}
        data-instance-card={id}
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

        {compacto ? null : <Thumb instance={instance} server={server} visible={visible} onOpen={() => onOpen(id)} />}

        <div className={styles.body}>
          {/* Sem dado, sem linha: "Sem rótulo de conta" e "Sem app associado" repetidos em todo cartão eram só ruído. */}
          {instance.account_label ? (
            <div className={styles.line}>
              <User size={13} aria-hidden />
              <span className="truncate" title={instance.account_label}>
                <span className="sr-only">Conta: </span>
                {instance.account_label}
              </span>
            </div>
          ) : null}
          {instance.account_evidence && !compacto ? (
            <div className={styles.observed}>
              <Eye size={11} aria-hidden />
              <span className={styles.observedTag}>Evidência</span>
              <span className="truncate" title={instance.account_evidence}>{evidenciaLegivel(instance.account_evidence)}</span>
            </div>
          ) : null}
          {appName && !compacto ? (
            <div className={styles.line}>
              <AppWindow size={13} aria-hidden />
              <span className="truncate" title={appName}>
                <span className="sr-only">App: </span>
                {appName}
              </span>
            </div>
          ) : null}
          {personas.length > 0 ? (
            // N personas (uma por app): os avatares de todas, a conta e a sessão AQUI da primeira, e "+N" para o resto.
            // A sessão vem por último e desce de linha quando não cabe: com ela antes, o @ ficava em "@mari…" (medido
            // no aceite visual da E2, cartão de ~240 px).
            <div className={cx(styles.line, styles.linhaPersonas)}>
              <span className={styles.avatares} aria-hidden>
                {personas.slice(0, 3).map((p) => (
                  <Avatar key={p.profile_id} src={profileAvatarUrl(p.profile_id)} name={p.name || p.profile_id} size={18} />
                ))}
              </span>
              <span className="truncate" title={personas.map((p) => `${p.name}${p.username ? ` (@${p.username})` : ''}`).join(' · ')}>
                <span className="sr-only">{personas.length > 1 ? 'Personas: ' : 'Persona: '}</span>
                {rotuloDaPersona(personas[0]!)}
              </span>
              {/* Fora do corte: com o @ longo, o "+N" era o primeiro a sumir nas reticências (visto no navegador). */}
              {personas.length > 1 ? <span className={styles.maisPersonas}>+{personas.length - 1}</span> : null}
              <StatusBadge meta={metaOf(SESSION_STATUS, personas[0]?.session?.status ?? 'unknown')} size="sm" srPrefix="Sessão" />
            </div>
          ) : null}

          {avisoDoServidor ? (
            <div className={cx(styles.line, styles.lineMuted)} title={instance.state_detail ?? undefined}>
              <TriangleAlert size={13} aria-hidden />
              <span>{avisoDoServidor}</span>
            </div>
          ) : null}

          {/* Só o que tem dado: "Controle: —" (ninguém no comando) e "Sem tarefa em andamento" não dizem nada. */}
          {instance.control !== 'none' || (state === 'online' && !compacto) ? (
            <div className={styles.metaRow}>
              {instance.control !== 'none' ? (
                <StatusBadge meta={controlMeta} size="sm" label={`Controle: ${mine ? 'Você' : controlMeta.label}`} />
              ) : <span />}
              {state === 'online' ? (
                <span className={styles.frameAge} title="Idade da última imagem recebida">
                  <Clock size={11} aria-hidden />
                  <span className="sr-only">Última imagem </span>
                  <FrameAge ts={instance.frame?.ts ?? null} />
                </span>
              ) : null}
            </div>
          ) : null}

          {current?.step_title || showSlotWait || current?.run_id ? (
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
                <span className={styles.stepIdle}>Aguardando a próxima etapa…</span>
              )}
            </div>
          ) : null}

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
              disabledReason={semServidor ?? motivoDoComando(comandoAberto, id, primary)}
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
              disabledReason={semServidor ?? motivoDoComando(comandoAberto, id, 'hibernate')}
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
