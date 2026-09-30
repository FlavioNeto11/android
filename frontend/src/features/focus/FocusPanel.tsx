import { ArrowLeft, Bot, Hand, LoaderCircle, Minus, X, type LucideIcon } from 'lucide-react';
import { useCallback, useRef, useState, useSyncExternalStore } from 'react';
import { api, toApiError } from '../../api/client';
import { useOperationalContext } from '../devices/OperationalContextCard';
import { TrainingBar } from '../training/TrainingBar';
import type { ManualInput } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { KvList, KvRow } from '../../components/JsonTree';
import { StatusBadge } from '../../components/StatusBadge';
import { toneClass } from '../../components/tone';
import { cx } from '../../lib/format';
import type { Gesture } from '../../lib/gesture';
import { INSTANCE_STATE, metaOf, type Tone } from '../../lib/status';
import { useAppStore } from '../../store/app';
import { useControlStore, userHasControl } from '../../store/control';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { comandoAbertoDe, useBusyStore } from '../devices/actions';
import { CommandHistory, CommandSummary } from '../devices/CommandTrail';
import { focusActionGroups, serverHintOf } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import { FocusActions, type ManualKey } from './FocusActions';
import {
  AccountsSection, AppsSection, HealthSection, IdentitySection, PersonasSection, ServerSection, TaskSection,
} from './FocusInfoSections';
import styles from './Focus.module.css';
import { FocusSection } from './FocusSection';
import { HierarchyList } from './HierarchyList';
import { Drawer } from './Drawer';
import { Screen, type ScreenHandle, type ShownFrame } from './Screen';

type InputPayload = Omit<ManualInput, 'lease_id' | 'frame_id'>;

/** O mesmo limiar do `@media` em Focus.module.css: abaixo dele o painel é tela cheia e "Voltar" é a saída. */
const TELA_ESTREITA = '(max-width: 720px)';

function consultaTelaEstreita(): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(TELA_ESTREITA) : null;
}

function assinarTelaEstreita(avisar: () => void): () => void {
  const mq = consultaTelaEstreita();
  if (!mq) return () => undefined;
  mq.addEventListener('change', avisar);
  return () => mq.removeEventListener('change', avisar);
}

/** `false` onde não há `matchMedia` (SSR, jsdom sem stub): o painel fica como no desktop. */
function useTelaEstreita(): boolean {
  return useSyncExternalStore(assinarTelaEstreita, () => consultaTelaEstreita()?.matches ?? false, () => false);
}

/** O botão que abre o Foco no cartão do aparelho: para onde o teclado volta quando o drawer fecha. */
const cartaoDe = (id: string): string => `[data-instance-card="${CSS.escape(id)}"] button[aria-label^="Abrir"]`;

export function FocusPanel({ instanceId }: { instanceId: string }) {
  const instance = useAppStore((s) => s.instances[instanceId]);
  const defaultApp = useAppStore((s) => {
    const appId = s.instances[instanceId]?.app_id;
    return appId ? s.apps.find((a) => a.id === appId) ?? null : null;
  });
  const closeFocus = useUiStore((s) => s.closeFocus);
  const lease = useControlStore((s) => s.leases[instanceId]);
  const controlBusy = useControlStore((s) => !!s.busy[instanceId]);
  const take = useControlStore((s) => s.take);
  const release = useControlStore((s) => s.release);
  const dropLease = useControlStore((s) => s.drop);
  const busyAction = useBusyStore((s) => s.busy[instanceId]);
  // Um aparelho, uma operação — a mesma leitura do cartão: o comando que ainda age bloqueia os verbos de ciclo de
  // vida; o que acabou sem desfecho (`uncertain`) continua visível no topo até alguém decidir.
  const openCmd = useAppStore((s) => comandoAbertoDe(s.lastCommand[instanceId]));
  const ultimoComando = useAppStore((s) => s.lastCommand[instanceId]);
  const comandoAMostrar = openCmd ?? (ultimoComando?.state === 'uncertain' ? ultimoComando : undefined);
  const hibernation = useAppStore((s) => s.health?.features?.hibernation === true);
  const workers = useAppStore((s) => s.workers);
  const telaEstreita = useTelaEstreita();
  const contexto = useOperationalContext(instanceId, null, `${instance?.state ?? ''}:${busyAction ?? ''}`);

  const screenRef = useRef<ScreenHandle>(null);
  const panelRef = useRef<HTMLElement>(null);
  const [sending, setSending] = useState(false);
  const [shown, setShown] = useState<ShownFrame | null>(null);
  const [highlight, setHighlight] = useState<[number, number, number, number] | null>(null);
  const [hierarquiaAberta, setHierarquiaAberta] = useState(false);

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
            title: 'Você não está mais com o controle deste aparelho',
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

  const mostrarHierarquia = () => {
    setHierarquiaAberta(true);
    // Depois de abrir, a seção vem para a vista: ela é a última da coluna e costuma estar fora da tela.
    window.setTimeout(() => {
      panelRef.current?.querySelector('[data-focus-section="Hierarquia"]')?.scrollIntoView?.({ block: 'nearest' });
    }, 0);
  };

  if (!instance) {
    return (
      <Drawer panelRef={panelRef} ariaLabel={`Foco: ${instanceId}`} onClose={closeFocus} restoreSelector={cartaoDe(instanceId)}>
        <div className={styles.header}>
          {telaEstreita ? <Button variant="ghost" icon={ArrowLeft} onClick={closeFocus}>Voltar</Button> : null}
          <span className={styles.title}>{instanceId}</span>
          <span className={styles.headerSpacer} />
          <Button variant="ghost" icon={X} iconOnly label="Fechar visão de foco" onClick={closeFocus} />
        </div>
        <EmptyState icon={Minus} title="Aparelho não encontrado" hint="Ele pode ter sido removido do backend. Feche este painel e escolha outro aparelho.">
          O backend não lista mais {instanceId}.
        </EmptyState>
      </Drawer>
    );
  }

  const online = instance.state === 'online';
  const server = serverHintOf(instance, workers);
  const loja = instance.kind === 'store';
  const groups = focusActionGroups(instance, hibernation, openCmd);
  const verifyReason = groups.apps.find((i) => i.action === 'verify_app')?.disabledReason ?? null;

  // ---- faixa "quem controla" ----
  let owner: { tone: Tone; icon: LucideIcon; label: string; hint: string; spin?: boolean };
  if (mine) {
    owner = { tone: 'warning', icon: Hand, label: 'Você', hint: 'A IA está em espera neste aparelho. Devolva o controle quando terminar.' };
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
    <Drawer panelRef={panelRef} ariaLabel={`Visão de foco: ${instance.id}`} onClose={closeFocus} restoreSelector={cartaoDe(instance.id)}>
      <div className={styles.header}>
        {/* Em tela cheia (celular) não há painel ao lado para onde "fechar": a saída é "Voltar", no canto onde
            o polegar espera. No desktop continua "Fechar" à direita. */}
        {telaEstreita ? <Button variant="ghost" icon={ArrowLeft} onClick={closeFocus}>Voltar</Button> : null}
        <div>
          <p className={styles.eyebrow}>Foco</p>
          <h2 className={styles.title}>{instance.id}</h2>
        </div>
        {/* De uma tarefa em foco não dava para descobrir em que máquina ela roda (#61 / E5). */}
        <ServerBadge server={server} size="md" />
        <StatusBadge meta={metaOf(INSTANCE_STATE, instance.state)} srPrefix="Estado" />
        <span className={styles.headerSpacer} />
        {telaEstreita ? null : <Button variant="ghost" icon={X} onClick={closeFocus}>Fechar</Button>}
      </div>

      {/* O comando que ainda age (ou que acabou sem desfecho) fica no topo, como no cartão: é ele que explica por
          que os botões de ciclo de vida estão bloqueados. */}
      {comandoAMostrar ? (
        <div className={styles.commandBar}><CommandSummary cmd={comandoAMostrar} /></div>
      ) : null}

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
                instance.state === 'hibernated' ? 'O aparelho está hibernado: acorde-o antes de assumir o controle.'
                : !online ? 'O aparelho precisa estar online para assumir o controle.'
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

          <IdentitySection instance={instance} server={server} appName={defaultApp?.name ?? instance.app_id} />
          <HealthSection instance={instance} />
          <ServerSection instance={instance} server={server} />
          <TaskSection instance={instance} openCmd={openCmd} />
          {/* "Sessão" deixou de ser um bloco à parte: a sessão é de uma conta num aparelho, então aparece na persona
              (a do perfil) e em cada conta (a da conta), com o portão de Conectar/Verificar. */}
          <PersonasSection contexto={contexto} instanceId={instanceId} />
          <AccountsSection contexto={contexto} instance={instance} />
          <AppsSection contexto={contexto} instance={instance} verifyReason={verifyReason} />

          <FocusActions
            instance={instance}
            groups={groups}
            openCmd={openCmd}
            busyAction={busyAction}
            mine={mine}
            pending={!!pending}
            sending={sending}
            defaultApp={defaultApp}
            contextLoading={contexto.carregando}
            onKey={(key: ManualKey) => void sendInput({ type: 'key', key })}
            onText={(text) => sendInput({ type: 'text', text })}
            onRefreshFrame={() => screenRef.current?.refresh()}
            onReloadContext={() => void contexto.carregar()}
            onShowHierarchy={mostrarHierarquia}
          />

          <FocusSection title="Comandos recentes" defaultOpen={false}>
            <CommandHistory instanceId={instance.id} semTitulo />
          </FocusSection>

          <FocusSection title="Detalhes técnicos" defaultOpen={false}>
            <KvList>
              {/* O que o WORKER reporta ganha do que o central inventaria: num aparelho remoto o AVD e a porta
                  daqui eram de outra máquina, e apareciam como se fossem a verdade do aparelho (#61). */}
              <KvRow label="AVD">
                <span className="mono">{server?.avd_name ?? instance.avd_name}</span>
                {server?.avd_name && server.avd_name !== instance.avd_name ? (
                  <span className={styles.groupHint}> (no servidor; aqui o aparelho se chama {instance.avd_name})</span>
                ) : null}
              </KvRow>
              <KvRow label="Serial"><span className="mono">{server?.serial ?? instance.serial}</span></KvRow>
              <KvRow label={server ? 'Porta do ADB no servidor' : 'Porta do console'}>
                <span className="mono">{server ? server.adb_port ?? '—' : instance.console_port}</span>
              </KvRow>
              {instance.tunnel_port ? (
                <KvRow label="Túnel (porta local)"><span className="mono">{instance.tunnel_port} → {instance.remote_adb_port ?? '—'}</span></KvRow>
              ) : null}
              <KvRow label="Portas">
                <span className="mono">system {instance.ports?.system} · mjpeg {instance.ports?.mjpeg} · chromedriver {instance.ports?.chromedriver}</span>
              </KvRow>
              <KvRow label="PID"><span className="mono">{instance.pid ?? '—'}</span></KvRow>
              <KvRow label="Tempo de boot">{instance.boot_seconds != null ? `${Math.round(instance.boot_seconds)} s` : '—'}</KvRow>
              {instance.system_image || instance.api_level ? (
                <KvRow label="Imagem do sistema">
                  <span className="mono">{instance.system_image ?? '—'}{instance.api_level ? ` · API ${instance.api_level}` : ''}</span>
                </KvRow>
              ) : null}
              {instance.abis?.length ? <KvRow label="ABIs"><span className="mono">{instance.abis.join(', ')}</span></KvRow> : null}
              {instance.play_store != null ? <KvRow label="Play Store">{instance.play_store ? 'sim' : 'não'}</KvRow> : null}
              <KvRow label="Frame exibido"><span className="mono">{shown ? `${shown.id} (${shown.width}×${shown.height})` : '—'}</span></KvRow>
              <KvRow label="Frame mais novo">
                <span className="mono">{instance.frame ? `${instance.frame.id} · ${instance.frame.orientation === 'landscape' ? 'paisagem' : 'retrato'}` : '—'}</span>
              </KvRow>
              <KvRow label="Lease"><span className="mono">{lease ? `${lease.leaseId} (${lease.status === 'granted' ? 'concedido' : 'pendente'})` : '—'}</span></KvRow>
            </KvList>
          </FocusSection>

          <FocusSection title="Hierarquia" defaultOpen={false} open={hierarquiaAberta} onToggle={setHierarquiaAberta}>
            <HierarchyList
              instanceId={instance.id}
              online={online}
              active={hierarquiaAberta}
              canTap={mine && online && !sending}
              onTap={(x, y) => void sendInput({ type: 'tap', x, y })}
              onHighlight={setHighlight}
            />
          </FocusSection>
        </div>
      </div>
    </Drawer>
  );
}
