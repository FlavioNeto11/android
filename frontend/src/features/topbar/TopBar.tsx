import {
  Server,
  Activity,
  Bot,
  Check,
  Cpu,
  FlaskConical,
  Hand,
  LayoutGrid,
  ListChecks,
  LogOut,
  MemoryStick,
  MonitorSmartphone,
  Package,
  RefreshCw,
  Settings as SettingsIcon,
  ShieldAlert,
  Smartphone,
  Stethoscope,
  UserRound,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import type { AiStatus, Health } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { KvList, KvRow } from '../../components/JsonTree';
import { Popover } from '../../components/Popover';
import { Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { toneClass } from '../../components/tone';
import { Tooltip } from '../../components/Tooltip';
import { aiFeatureRows, aiModelRows } from '../../lib/aiLabels';
import { cx, formatDecimal, formatInt } from '../../lib/format';
import { CONN_STATUS, HEALTH_STATUS, isRunActive, metaOf } from '../../lib/status';
import { useAppStore } from '../../store/app';
import { reconnectNow } from '../../store/live';
import { useSessionStore } from '../../store/session';
import { hashForView, useUiStore, type View } from '../../store/ui';
import styles from './TopBar.module.css';

const NAV: { view: View; label: string; icon: LucideIcon }[] = [
  { view: 'painel', label: 'Painel', icon: LayoutGrid },
  { view: 'perfis', label: 'Personas', icon: UserRound },
  { view: 'aplicativos', label: 'Aplicativos', icon: Package },
  { view: 'execucoes', label: 'Execuções', icon: ListChecks },
  { view: 'infraestrutura', label: 'Infraestrutura', icon: Server },
  { view: 'configuracao', label: 'Configuração', icon: SettingsIcon },
  { view: 'diagnostico', label: 'Diagnóstico', icon: Stethoscope },
];

export const EXTERNAL_DATA_NOTICE = 'Screenshots e textos das telas são enviados ao provedor externo de IA';

/** De que lado de uma faixa rolável ainda há conteúdo escondido. */
export type Transbordo = '' | 'inicio' | 'fim' | 'ambos';

/** Pura, para o teste. Tolerância de 1 px: as medidas do navegador chegam arredondadas. */
export function transbordoDe(m: { scrollLeft: number; clientWidth: number; scrollWidth: number }): Transbordo {
  const antes = m.scrollLeft > 1;
  const depois = m.scrollLeft + m.clientWidth < m.scrollWidth - 1;
  return antes && depois ? 'ambos' : depois ? 'fim' : antes ? 'inicio' : '';
}

/** Em janela estreita a navegação rola de lado e "Configuração" e "Diagnóstico" sumiam sem pista (P2.5). O CSS
 *  desenha um gradiente na borda que ainda tem seções; aqui só se mede. No jsdom tudo mede zero: sem gradiente. */
function useTransbordoHorizontal(ref: RefObject<HTMLElement | null>): Transbordo {
  const [estado, setEstado] = useState<Transbordo>('');
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const medir = () => setEstado(transbordoDe(el));
    medir();
    el.addEventListener('scroll', medir, { passive: true });
    window.addEventListener('resize', medir);
    // A faixa muda de largura sem `resize` da janela: fonte que termina de carregar, rótulo que troca.
    const observador = typeof ResizeObserver === 'function' ? new ResizeObserver(medir) : null;
    observador?.observe(el);
    return () => {
      el.removeEventListener('scroll', medir);
      window.removeEventListener('resize', medir);
      observador?.disconnect();
    };
  }, [ref]);
  return estado;
}

export function TopBar() {
  const view = useUiStore((s) => s.view);
  const navRef = useRef<HTMLElement>(null);
  const transborda = useTransbordoHorizontal(navRef);
  return (
    <header className={styles.bar}>
      <div className={styles.lead}>
        <a href={hashForView('painel')} className={styles.brand} aria-label="Central de Aparelhos — ir para o Painel">
          <span className={styles.brandMark}><MonitorSmartphone size={16} aria-hidden /></span>
          <span className={styles.brandName}>Central de Aparelhos</span>
        </a>
        {/* O gradiente fica no embrulho: um pseudo-elemento na própria faixa rolaria junto com o conteúdo. */}
        <div className={styles.navWrap} data-transborda={transborda || undefined}>
          <nav ref={navRef} className={styles.nav} aria-label="Seções">
            {NAV.map(({ view: v, label, icon: Icon }) => (
              <a key={v} href={hashForView(v)} className={styles.navLink} aria-current={view === v ? 'page' : undefined}>
                <Icon size={15} aria-hidden />
                {label}
              </a>
            ))}
          </nav>
        </div>
      </div>
      <div className={styles.status}>
        <HealthPill />
        <Counters />
        <AiBadge />
        <ConnectionIndicator />
        <OperadorAtual />
      </div>
    </header>
  );
}

// ---- Quem está operando --------------------------------------------------------------------------

/** O nome que está sendo gravado em cada ação, visível o tempo todo — e a saída. Sem isto, ninguém saberia
 *  com qual nome está assinando o que faz no parque. */
function OperadorAtual() {
  const operator = useSessionStore((s) => s.operator);
  const busy = useSessionStore((s) => s.busy);
  if (!operator) return null;
  return (
    <Popover
      label={`Sessão de ${operator}. Abrir opções`}
      title="Sessão"
      align="end"
      triggerClassName={styles.pillBtn}
      trigger={<><UserRound size={14} aria-hidden />{operator}</>}
    >
      {() => (
        <>
          <p style={{ marginTop: 0, color: 'var(--text-2)' }}>
            Cada comando, aprovação e decisão à mão fica gravado com <strong>{operator}</strong>.
          </p>
          <Button size="sm" icon={LogOut} loading={busy}
                  onClick={() => void useSessionStore.getState().signOut()}>Sair</Button>
        </>
      )}
    </Popover>
  );
}

// ---- Saúde do ambiente ---------------------------------------------------------------------------

function HealthPill() {
  const health = useAppStore((s) => s.health);
  const hydrated = useAppStore((s) => s.hydrated);
  const setView = useUiStore((s) => s.setView);

  if (!health) {
    return hydrated ? null : <Skeleton width={118} height={28} radius={999} />;
  }
  const meta = metaOf(HEALTH_STATUS, health.status);
  const Icon = meta.icon;
  const problems = health.problems ?? [];

  return (
    <Popover
      label={`${meta.label}. ${problems.length > 0 ? `${problems.length} problema(s). ` : ''}Abrir detalhes do ambiente`}
      title="Saúde do ambiente"
      align="start"
      triggerClassName={cx(styles.pillBtn, toneClass(meta.tone))}
      trigger={
        <>
          <Icon size={14} aria-hidden />
          {meta.label}
          {problems.length > 0 ? <span className={styles.pillCount}>{problems.length}</span> : null}
        </>
      }
    >
      {(close) => (
        <>
          <StatusBadge meta={meta} />
          {problems.length === 0 ? (
            <p style={{ marginTop: 8, color: 'var(--text-2)' }}>Nenhum problema detectado. SDK, Appium e IA responderam normalmente.</p>
          ) : (
            <ul className={styles.healthList}>
              {problems.map((p, i) => (
                <li key={`${p.code}-${i}`} className={styles.problem}>
                  <p className={styles.problemMsg}>{p.message}</p>
                  {p.hint ? <p className={styles.problemHint}>O que fazer: {p.hint}</p> : null}
                  <p className={cx(styles.problemCode, 'mono')}>{p.code}</p>
                </li>
              ))}
            </ul>
          )}
          <div className={styles.healthFacts}>
            <Fact ok={!!health.sdk?.found}>
              {health.sdk?.found
                ? `Android SDK encontrado${health.sdk.emulator_version ? ` · emulador ${health.sdk.emulator_version}` : ''}${health.sdk.accel ? ` · aceleração ${health.sdk.accel}` : ''}`
                : 'Android SDK não encontrado'}
            </Fact>
            <Fact ok={!!health.appium?.running}>
              {health.appium?.running ? `Appium em execução (porta ${health.appium.port})` : `Appium parado${health.appium?.detail ? ` — ${health.appium.detail}` : ''}`}
            </Fact>
            <Fact ok={!!health.ai && (health.ai.configured || health.ai.simulated)}>
              {health.ai?.simulated ? 'IA em modo simulado' : health.ai?.configured ? `IA configurada${health.ai.model ? ` (${health.ai.model})` : ''}` : 'IA não configurada'}
            </Fact>
          </div>
          <div className={styles.healthFooter}>
            <span>Backend v{health.version}</span>
            <Button
              size="sm"
              variant="outline"
              icon={Stethoscope}
              onClick={() => {
                close();
                setView('diagnostico');
              }}
            >
              Abrir Diagnóstico
            </Button>
          </div>
        </>
      )}
    </Popover>
  );
}

function Fact({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <p className={styles.fact}>
      {ok ? <Check size={13} className={styles.factOk} aria-hidden /> : <X size={13} className={styles.factBad} aria-hidden />}
      <span className="sr-only">{ok ? 'OK: ' : 'Problema: '}</span>
      {children}
    </p>
  );
}

// ---- Contadores ----------------------------------------------------------------------------------

function Counters() {
  const hydrated = useAppStore((s) => s.hydrated);
  const instances = useAppStore((s) => s.instances);
  const runs = useAppStore((s) => s.runs);
  const metrics = useAppStore((s) => s.metrics);
  // Rodízio: com `auto_start_devices`, o que limita os aparelhos ligados são as vagas de RAM.
  const slots = useAppStore((s) => (s.settings?.auto_start_devices ? s.settings.max_online_devices : null));
  const setView = useUiStore((s) => s.setView);
  const selectRun = useUiStore((s) => s.selectRun);

  // O total é o que está cadastrado — o "10" fixo de antes fazia um parque de 4 aparelhos aparecer como "4/10".
  const { online, total } = useMemo(() => {
    const list = Object.values(instances);
    return { online: list.filter((i) => i.state === 'online').length, total: list.length };
  }, [instances]);

  const { active, blocked, firstBlocked } = useMemo(() => {
    let a = 0;
    let b = 0;
    let first: string | null = null;
    for (const r of runs) {
      if (isRunActive(r.status)) a += 1;
      const n = (r.counts?.waiting_user ?? 0) + (r.counts?.uncertain ?? 0);
      if (n > 0 && !first) first = r.id;
      b += n;
    }
    return { active: a, blocked: b, firstBlocked: first };
  }, [runs]);

  if (!hydrated) return <Skeleton width={360} height={36} radius={8} />;

  const memUsed = metrics ? Math.max(0, metrics.mem_total_gb - metrics.mem_available_gb) : null;

  return (
    <div className={styles.counters} role="group" aria-label="Indicadores">
      <Tooltip
        content={
          typeof slots === 'number'
            ? `Aparelhos online / cadastrados · rodízio ligado: até ${slots} aparelho(s) ligado(s) ao mesmo tempo (vagas de RAM); os demais ligam sob demanda.`
            : 'Aparelhos online / cadastrados'
        }
      >
        <div className={styles.counter}>
          <span className={styles.counterValue}>
            <Smartphone size={13} aria-hidden />
            {online}<span className={styles.counterDim}>/{total}</span>
            {typeof slots === 'number' ? <span className={cx(styles.counterDim, styles.counterSlots)}>{' '}· vagas {slots}</span> : null}
          </span>
          <span className={styles.counterLabel}>Online</span>
        </div>
      </Tooltip>
      <Tooltip content="Execuções ativas (planejando, em execução, pausadas ou cancelando). Clique para ver.">
        <button type="button" className={styles.counter} onClick={() => setView('execucoes')}>
          <span className={styles.counterValue}><Activity size={13} aria-hidden />{formatInt(active)}</span>
          <span className={styles.counterLabel}>Execuções</span>
        </button>
      </Tooltip>
      <Tooltip content="Tarefas bloqueadas: objetivos aguardando o usuário + objetivos incertos que pedem revisão. Clique para abrir.">
        <button
          type="button"
          className={cx(styles.counter, blocked > 0 && styles.counterAlert)}
          onClick={() => {
            if (firstBlocked) selectRun(firstBlocked);
            setView('execucoes');
          }}
        >
          <span className={styles.counterValue}><Hand size={13} aria-hidden />{formatInt(blocked)}</span>
          <span className={styles.counterLabel}>Bloqueadas</span>
        </button>
      </Tooltip>
      <Tooltip content="Uso de CPU da máquina host">
        <div className={cx(styles.counter, metrics && metrics.cpu_percent >= 90 && styles.counterHot)}>
          <span className={styles.counterValue}><Cpu size={13} aria-hidden />{metrics ? `${formatInt(metrics.cpu_percent)}%` : '—'}</span>
          <span className={styles.counterLabel}>CPU</span>
        </div>
      </Tooltip>
      <Tooltip content={metrics ? `Memória em uso: ${formatInt(metrics.mem_used_percent)}% (usada / total)` : 'Memória do host (sem dados ainda)'}>
        <div className={cx(styles.counter, metrics && metrics.mem_used_percent >= 92 && styles.counterHot)}>
          <span className={styles.counterValue}>
            <MemoryStick size={13} aria-hidden />
            {metrics && memUsed !== null ? (
              <>{formatDecimal(memUsed)}<span className={styles.counterDim}>/{formatInt(metrics.mem_total_gb)} GB</span></>
            ) : '—'}
          </span>
          <span className={styles.counterLabel}>RAM</span>
        </div>
      </Tooltip>
    </div>
  );
}

// ---- IA ------------------------------------------------------------------------------------------

function AiBadge() {
  const ai = useAppStore((s) => s.health?.ai ?? null);
  const features = useAppStore((s) => s.health?.features ?? null);
  if (!ai) return null;

  return (
    <div className={styles.ai}>
      {ai.simulated ? (
        <Tooltip content="Modo simulado de desenvolvimento: nenhuma IA real é chamada e os resultados são fictícios.">
          <Badge tone="warning" solid icon={FlaskConical} size="lg">MODO SIMULADO</Badge>
        </Tooltip>
      ) : null}
      {!ai.configured && !ai.simulated ? (
        <Tooltip content="Defina a chave do provedor de IA no arquivo .env do backend e reinicie o servidor. A chave nunca é informada pelo navegador.">
          <span tabIndex={0} style={{ display: 'inline-flex' }}>
            <Badge tone="danger" icon={ShieldAlert} size="lg">IA não configurada</Badge>
          </span>
        </Tooltip>
      ) : (
        <AiDetailsPopover ai={ai} features={features} />
      )}
      {ai.sends_data_externally ? (
        <Tooltip content={EXTERNAL_DATA_NOTICE}>
          <span className={styles.notice} tabIndex={0} role="img" aria-label={EXTERNAL_DATA_NOTICE}>
            <ShieldAlert size={15} aria-hidden />
          </span>
        </Tooltip>
      ) : null}
    </div>
  );
}

/** Chip do modelo: abre os modelos por função e o estado de receitas / fluxos / imagens. */
function AiDetailsPopover({ ai, features: healthFeatures }: { ai: AiStatus; features: Health['features'] | null }) {
  const models = aiModelRows(ai);
  const features = aiFeatureRows(ai, healthFeatures);
  const name = ai.model ?? ai.provider;
  return (
    <Popover
      label={`Modelo de IA: ${name}. Abrir modelos por função e recursos`}
      title="IA em uso"
      align="end"
      triggerClassName={cx(styles.pillBtn, styles.aiChip, toneClass('neutral'))}
      trigger={
        <>
          <Bot size={14} aria-hidden />
          <span className={styles.aiChipLabel}>{name}</span>
        </>
      }
    >
      <KvList>
        <KvRow label="Provedor"><span className="mono">{ai.provider}</span></KvRow>
        {ai.effort ? <KvRow label="Esforço">{ai.effort}</KvRow> : null}
        {models.length === 0 ? <KvRow label="Modelo"><span className="mono">{ai.model ?? '—'}</span></KvRow> : null}
      </KvList>
      {models.length > 0 ? (
        <>
          <h4 className={styles.aiGroupTitle}>Modelo por função</h4>
          <KvList>
            {models.map((m) => <KvRow key={m.key} label={m.label}><span className="mono">{m.value}</span></KvRow>)}
          </KvList>
        </>
      ) : null}
      {features.length > 0 ? (
        <>
          <h4 className={styles.aiGroupTitle}>Economia de IA</h4>
          <KvList>
            {features.map((f) => <KvRow key={f.key} label={f.label}>{f.value}</KvRow>)}
          </KvList>
        </>
      ) : (
        <p className={styles.aiNote}>O backend não informou o estado de receitas, fluxos e imagens.</p>
      )}
    </Popover>
  );
}

// ---- WebSocket -----------------------------------------------------------------------------------

function ConnectionIndicator() {
  const conn = useAppStore((s) => s.conn);
  const meta = CONN_STATUS[conn.status];
  const waiting = conn.status === 'reconnecting' || conn.status === 'disconnected';
  return (
    <div className={styles.conn} role="status" aria-live="polite">
      <Tooltip content={conn.lastError && waiting ? `Último erro: ${conn.lastError}` : 'Canal em tempo real (WebSocket) com o backend'}>
        <StatusBadge meta={meta} size="lg" srPrefix="Conexão" />
      </Tooltip>
      {waiting ? <Button size="sm" variant="ghost" icon={RefreshCw} iconOnly label="Reconectar agora" onClick={reconnectNow} /> : null}
    </div>
  );
}
