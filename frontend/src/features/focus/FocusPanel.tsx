import { Bot, CornerDownLeft, Delete, Hand, LoaderCircle, Minus, Send, Store, X, type LucideIcon } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, toApiError } from '../../api/client';
import { InstallAppMenu } from '../devices/InstallAppMenu';
import { OpenAppMenu } from '../devices/OpenAppMenu';
import { OperationalContextCard } from '../devices/OperationalContextCard';
import { TrainingBar } from '../training/TrainingBar';
import type { InstanceAction, ManualInput } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { TextInput } from '../../components/Field';
import { KvList, KvRow } from '../../components/JsonTree';
import { StatusBadge } from '../../components/StatusBadge';
import { toneClass } from '../../components/tone';
import { cx, formatMb, formatPercent } from '../../lib/format';
import type { Gesture } from '../../lib/gesture';
import { AUTOMATION_STATE, INSTANCE_STATE, metaOf, type Tone } from '../../lib/status';
import { useAppStore } from '../../store/app';
import { useControlStore, userHasControl } from '../../store/control';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { ACTION_META, runInstanceAction, useBusyStore } from '../devices/actions';
import { CommandHistory } from '../devices/CommandTrail';
import { quickActionsFor, serverHintOf, unsupportedReason } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import styles from './Focus.module.css';
import { HierarchyList } from './HierarchyList';
import { Screen, type ScreenHandle, type ShownFrame } from './Screen';

type InputPayload = Omit<ManualInput, 'lease_id' | 'frame_id'>;

const labelOf = (a: InstanceAction): string => ACTION_META[a].label;

export function FocusPanel({ instanceId }: { instanceId: string }) {
  const instance = useAppStore((s) => s.instances[instanceId]);
  const appName = useAppStore((s) => {
    const appId = s.instances[instanceId]?.app_id;
    return appId ? s.apps.find((a) => a.id === appId)?.name ?? appId : null;
  });
  const closeFocus = useUiStore((s) => s.closeFocus);
  const lease = useControlStore((s) => s.leases[instanceId]);
  const controlBusy = useControlStore((s) => !!s.busy[instanceId]);
  const take = useControlStore((s) => s.take);
  const release = useControlStore((s) => s.release);
  const dropLease = useControlStore((s) => s.drop);
  const busyAction = useBusyStore((s) => s.busy[instanceId]);
  const hibernation = useAppStore((s) => s.health?.features?.hibernation === true);
  const workers = useAppStore((s) => s.workers);

  const screenRef = useRef<ScreenHandle>(null);
  const panelRef = useRef<HTMLElement>(null);
  const [sending, setSending] = useState(false);
  const [text, setText] = useState('');
  const [shown, setShown] = useState<ShownFrame | null>(null);
  const [highlight, setHighlight] = useState<[number, number, number, number] | null>(null);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  const mine = userHasControl(instance, lease);

  const sendInput = useCallback(
    async (payload: InputPayload, frame?: ShownFrame | null): Promise<boolean> => {
      const currentLease = useControlStore.getState().leases[instanceId];
      const displayed = frame ?? screenRef.current?.shown() ?? null;
      if (!currentLease) {
        toast({ tone: 'warning', title: 'Você não está no controle', hint: 'Clique em “Assumir controle” antes de interagir.' });
        return false;
      }
      if (!displayed) {
        toast({ tone: 'warning', title: 'Ainda não há frame na tela', hint: 'Aguarde a imagem carregar e tente de novo.' });
        return false;
      }
      setSending(true);
      try {
        // `frame_id` = frame que o usuário ESTÁ VENDO (o que gerou a imagem exibida), não o mais novo do store.
        await api.sendInput(instanceId, { ...payload, lease_id: currentLease.leaseId, frame_id: displayed.id });
        return true;
      } catch (e) {
        const err = toApiError(e);
        if (err.code === 'stale_frame' || err.code === 'frame_mismatch') {
          toast({ tone: 'warning', title: 'A tela mudou — aguarde o novo frame e tente de novo', message: 'Nada foi enviado ao aparelho.', key: `stale-${instanceId}` });
          screenRef.current?.refresh();
        } else if (err.code === 'not_controller') {
          dropLease(instanceId);
          toast({
            tone: 'danger',
            title: 'Você não está mais com o controle desta instância',
            message: err.message,
            hint: 'O controle pode ter expirado ou voltado para a IA. Clique em “Assumir controle” para continuar.',
            key: `ctl-${instanceId}`,
          });
        } else {
          toastError('A ação manual não foi aceita', err);
        }
        return false;
      } finally {
        setSending(false);
      }
    },
    [instanceId, dropLease],
  );

  const onGesture = useCallback(
    (g: Gesture, frame: ShownFrame) => {
      const payload: InputPayload =
        g.type === 'tap' ? { type: 'tap', x: g.x, y: g.y }
        : g.type === 'long_press' ? { type: 'long_press', x: g.x, y: g.y, duration_ms: g.duration_ms }
        : { type: 'swipe', x: g.x, y: g.y, x2: g.x2, y2: g.y2, duration_ms: g.duration_ms };
      void sendInput(payload, frame);
    },
    [sendInput],
  );

  const submitText = async (e: FormEvent) => {
    e.preventDefault();
    if (!text) return;
    if (await sendInput({ type: 'text', text })) setText('');
  };

  if (!instance) {
    return (
      <aside ref={panelRef} tabIndex={-1} className={styles.panel} role="dialog" aria-modal="false" aria-label={`Foco: ${instanceId}`}>
        <div className={styles.header}>
          <span className={styles.title}>{instanceId}</span>
          <span className={styles.headerSpacer} />
          <Button variant="ghost" icon={X} iconOnly label="Fechar visão de foco" onClick={closeFocus} />
        </div>
        <EmptyState icon={Minus} title="Instância não encontrada" hint="Ela pode ter sido removida do backend. Feche este painel e escolha outra instância.">
          O backend não lista mais {instanceId}.
        </EmptyState>
      </aside>
    );
  }

  const online = instance.state === 'online';
  const server = serverHintOf(instance, workers);
  const loja = instance.kind === 'store';
  const lockReason =
    instance.state === 'hibernated' ? 'A instância está hibernada: acorde-a para interagir.'
    : !online ? 'A instância precisa estar online.'
    : !mine ? 'Assuma o controle para interagir.'
    : null;

  // ---- faixa "quem controla" ----
  let owner: { tone: Tone; icon: LucideIcon; label: string; hint: string; spin?: boolean };
  if (mine) {
    owner = { tone: 'warning', icon: Hand, label: 'Você', hint: 'A IA está em espera nesta instância. Devolva o controle quando terminar.' };
  } else if (instance.control_pending || lease?.status === 'pending') {
    owner = { tone: 'info', icon: LoaderCircle, spin: true, label: instance.control === 'ai' ? 'IA' : '—', hint: 'Aguardando a IA concluir a ação atual…' };
  } else if (instance.control === 'ai') {
    owner = { tone: 'accent', icon: Bot, label: 'IA', hint: 'A IA está operando este aparelho. Você pode assumir a qualquer momento.' };
  } else if (instance.control === 'user') {
    owner = { tone: 'warning', icon: Hand, label: 'Usuário (outra sessão)', hint: 'O controle manual está ativo, mas não nesta aba. Clique em “Retomar controle” para interagir por aqui.' };
  } else {
    owner = { tone: 'muted', icon: Minus, label: 'Livre', hint: 'Ninguém está controlando. Assuma o controle para interagir manualmente.' };
  }
  const OwnerIcon = owner.icon;
  const pending = !mine && (instance.control_pending || lease?.status === 'pending');

  return (
    <aside
      ref={panelRef}
      tabIndex={-1}
      className={styles.panel}
      role="dialog"
      aria-modal="false"
      aria-label={`Visão de foco: ${instance.id}`}
      onKeyDown={(e) => {
        if (e.key !== 'Escape' || e.defaultPrevented) return;
        const t = e.target as HTMLElement;
        if (t.closest('dialog')) return;
        if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) return;
        closeFocus();
      }}
    >
      <div className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Foco</p>
          <h2 className={styles.title}>{instance.id}</h2>
        </div>
        {/* De uma tarefa em foco não dava para descobrir em que máquina ela roda (#61 / E5). */}
        <ServerBadge server={server} size="md" />
        <StatusBadge meta={metaOf(INSTANCE_STATE, instance.state)} srPrefix="Estado" />
        <span className={styles.headerSpacer} />
        <Button variant="ghost" icon={X} onClick={closeFocus}>Fechar</Button>
      </div>

      <div className={cx(styles.control, toneClass(owner.tone))} role="status" aria-live="polite">
        <span className={styles.controlIcon}><OwnerIcon size={19} className={owner.spin ? 'spin' : undefined} aria-hidden /></span>
        <div className={styles.controlText}>
          <p className={styles.controlOwner}>
            {pending ? 'Controle solicitado' : <>Controle: <strong>{owner.label}</strong></>}
            {instance.control === 'none' && !mine && !pending ? <span className="sr-only"> (livre)</span> : null}
          </p>
          <p className={styles.controlHint}>{owner.hint}</p>
        </div>
        <div className={styles.controlActions}>
          {mine ? (
            <Button variant="primary" icon={Bot} loading={controlBusy} onClick={() => void release(instance.id)}>Devolver à IA</Button>
          ) : (
            <Button
              variant="primary"
              icon={Hand}
              loading={controlBusy}
              disabledReason={
                instance.state === 'hibernated' ? 'A instância está hibernada: acorde-a antes de assumir o controle.'
                : !online ? 'A instância precisa estar online para assumir o controle.'
                : pending ? 'Pedido já enviado — aguardando a IA concluir a ação atual.'
                : null
              }
              onClick={() => void take(instance.id)}
            >
              {instance.control === 'user' ? 'Retomar controle' : 'Assumir controle'}
            </Button>
          )}
        </div>
      </div>

      <div className={styles.content}>
        <div className={styles.screenCol}>
          <Screen ref={screenRef} instance={instance} interactive={mine && online} busy={sending} onGesture={onGesture} highlight={highlight} onShownChange={setShown} />
        </div>

        <div className={styles.side}>
          {instance.attention ? (
            <Banner tone="warning" icon={Hand} role="alert" title="Atenção necessária">{instance.attention}</Banner>
          ) : null}

          {online && !loja ? <TrainingBar instance={instance} leaseId={lease?.leaseId ?? null} mine={mine} /> : null}

          <section className={styles.group} aria-label="Interação manual">
            <h3 className={styles.groupTitle}>Interação manual</h3>
            {lockReason ? <p className={styles.groupHint}>{lockReason}</p> : (
              <div className={styles.gestures}>
                <span><b>Clique</b> = toque · <b>segurar ≥ 0,6 s</b> = toque longo</span>
                <span><b>Arrastar</b> = deslizar (a duração acompanha o gesto)</span>
              </div>
            )}
            {/* Decisão 4 do plano (dono, 24/09): a loja aceita texto pelo painel como os outros aparelhos. Só a
                SENHA da conta Google não passa — o backend recusa com `store_password_blocked`. */}
            {loja ? (
              <p className={styles.groupHint}>
                <Store size={12} aria-hidden /> Na loja, a senha da conta Google é digitada direto na janela do emulador;
                os demais textos (busca, nomes) vão por aqui.
              </p>
            ) : null}
            <form className={styles.textRow} onSubmit={(e) => void submitText(e)}>
              <TextInput
                value={text}
                placeholder="Texto para digitar no aparelho"
                aria-label="Texto para digitar no aparelho"
                disabled={!!lockReason}
                onChange={(e) => setText(e.target.value)}
              />
              <Button type="submit" icon={Send} loading={sending && !!text} disabledReason={lockReason ?? (text ? null : 'Digite um texto para enviar.')}>
                Enviar texto
              </Button>
            </form>
            <div className={styles.keys} role="group" aria-label="Botões do Android">
              {(['back', 'home', 'recents'] as const).map((k) => (
                <Button key={k} icon={ACTION_META[k].icon} disabled={sending} disabledReason={lockReason} onClick={() => void sendInput({ type: 'key', key: k })}>
                  {ACTION_META[k].label}
                </Button>
              ))}
            </div>
            <div className={styles.keys2} role="group" aria-label="Teclas">
              <Button icon={CornerDownLeft} disabled={sending} disabledReason={lockReason} onClick={() => void sendInput({ type: 'key', key: 'enter' })}>Enter</Button>
              <Button icon={Delete} disabled={sending} disabledReason={lockReason} onClick={() => void sendInput({ type: 'key', key: 'delete' })}>Apagar</Button>
            </div>
          </section>

          <section className={styles.group} aria-label="Ações rápidas">
            <h3 className={styles.groupTitle}>Ações rápidas</h3>
            <div className={styles.quick}>
              {instance.state === 'absent' ? (
                <Button icon={ACTION_META.create.icon} loading={busyAction === 'create'}
                        disabledReason={unsupportedReason(instance, 'create', ACTION_META.create.label)}
                        onClick={() => void runInstanceAction(instance.id, 'create')}>
                  {ACTION_META.create.label}
                </Button>
              ) : null}
              {instance.state === 'hibernated' ? (
                <Button
                  variant="primary"
                  icon={ACTION_META.wake.icon}
                  loading={busyAction === 'wake'}
                  disabled={!!busyAction && busyAction !== 'wake'}
                  disabledReason={unsupportedReason(instance, 'wake', ACTION_META.wake.label)}
                  onClick={() => void runInstanceAction(instance.id, 'wake')}
                >
                  {ACTION_META.wake.label}
                </Button>
              ) : null}
              {quickActionsFor(instance, hibernation, labelOf).map(({ action, disabledReason }) => action === 'open_app' ? (
                <OpenAppMenu key={action} padrao={instance.app_id} disabledReason={disabledReason}
                             loading={busyAction === action} disabled={!!busyAction && busyAction !== action}
                             onPick={(appId) => void runInstanceAction(instance.id, action, { app_id: appId })} />
              ) : action === 'install_apk' ? (
                // Instalar escolhe o SEU app, independente de "Abrir app": nada é deduzido do controle vizinho.
                <InstallAppMenu key={action} padrao={instance.app_id} disabledReason={disabledReason}
                                loading={busyAction === action} disabled={!!busyAction && busyAction !== action}
                                onPick={(appId) => void runInstanceAction(instance.id, action, { app_id: appId })} />
              ) : (
                <Button
                  key={action}
                  icon={ACTION_META[action].icon}
                  loading={busyAction === action}
                  disabled={!!busyAction && busyAction !== action}
                  disabledReason={disabledReason}
                  onClick={() => void runInstanceAction(instance.id, action)}
                >
                  {ACTION_META[action].label}
                </Button>
              ))}
              <Button
                variant="dangerGhost"
                icon={ACTION_META.reset.icon}
                loading={busyAction === 'reset'}
                disabled={!!busyAction && busyAction !== 'reset'}
                disabledReason={unsupportedReason(instance, 'reset', ACTION_META.reset.label)
                  ?? (instance.state === 'absent' ? 'Não há AVD para resetar.' : null)}
                onClick={() => void runInstanceAction(instance.id, 'reset')}
              >
                Resetar dados…
              </Button>
            </div>
            <p className={styles.groupHint}>
              App: {appName ?? 'nenhum associado'} · Conta: {instance.account_label ?? '—'}
              {instance.account_evidence ? ` · observado: ${instance.account_evidence}` : ''}
            </p>
          </section>

          <section className={styles.group}>
            <OperationalContextCard instanceId={instance.id} refreshKey={`${instance.state}:${busyAction ?? ''}`} />
          </section>

          <CommandHistory instanceId={instance.id} />

          <Disclosure summary="Detalhes técnicos">
            <KvList>
              {/* O que o WORKER reporta ganha do que o central inventaria: num aparelho remoto o AVD e a porta
                  daqui eram de outra máquina, e apareciam como se fossem a verdade do aparelho (#61). */}
              {server ? (
                <>
                  <KvRow label="Servidor">
                    {server.name}
                    {server.enrolled ? (server.connected ? '' : ' — sem canal agora') : ' — não está inscrito'}
                  </KvRow>
                  <KvRow label="Processo no servidor">
                    {server.process ?? 'não reportado'}{server.detail ? ` — ${server.detail}` : ''}
                  </KvRow>
                </>
              ) : null}
              <KvRow label="AVD">
                <span className="mono">{server?.avd_name ?? instance.avd_name}</span>
                {server?.avd_name && server.avd_name !== instance.avd_name ? (
                  <span className={styles.groupHint}> (no servidor; aqui a instância se chama {instance.avd_name})</span>
                ) : null}
              </KvRow>
              <KvRow label="Serial"><span className="mono">{server?.serial ?? instance.serial}</span></KvRow>
              <KvRow label={server ? 'Porta do ADB no servidor' : 'Porta do console'}>
                <span className="mono">{server ? server.adb_port ?? '—' : instance.console_port}</span>
              </KvRow>
              <KvRow label="Portas">
                <span className="mono">system {instance.ports?.system} · mjpeg {instance.ports?.mjpeg} · chromedriver {instance.ports?.chromedriver}</span>
              </KvRow>
              <KvRow label="PID"><span className="mono">{instance.pid ?? '—'}</span></KvRow>
              <KvRow label="Tempo de boot">{instance.boot_seconds != null ? `${Math.round(instance.boot_seconds)} s` : '—'}</KvRow>
              <KvRow label="Automação">
                {metaOf(AUTOMATION_STATE, instance.automation?.state).label}
                {instance.automation?.detail ? ` — ${instance.automation.detail}` : ''}
              </KvRow>
              <KvRow label="Recursos">
                {instance.resources ? `${formatMb(instance.resources.rss_mb)} · CPU ${formatPercent(instance.resources.cpu_percent)}` : '—'}
              </KvRow>
              <KvRow label="Frame exibido"><span className="mono">{shown ? `${shown.id} (${shown.width}×${shown.height})` : '—'}</span></KvRow>
              <KvRow label="Frame mais novo">
                <span className="mono">{instance.frame ? `${instance.frame.id} · ${instance.frame.orientation === 'landscape' ? 'paisagem' : 'retrato'}` : '—'}</span>
              </KvRow>
              <KvRow label="Lease"><span className="mono">{lease ? `${lease.leaseId} (${lease.status === 'granted' ? 'concedido' : 'pendente'})` : '—'}</span></KvRow>
            </KvList>
          </Disclosure>

          <HierarchyList
            instanceId={instance.id}
            online={online}
            canTap={mine && online && !sending}
            onTap={(x, y) => void sendInput({ type: 'tap', x, y })}
            onHighlight={setHighlight}
          />
        </div>
      </div>
    </aside>
  );
}
