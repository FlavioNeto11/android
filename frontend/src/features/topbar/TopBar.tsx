import {
  Activity,
  Bot,
  Cpu,
  FlaskConical,
  Hand,
  Info,
  LogOut,
  Menu as MenuIcon,
  MemoryStick,
  MonitorSmartphone,
  RefreshCw,
  ShieldAlert,
  Smartphone,
  UserRound,
  Wallet,
} from 'lucide-react';
import type { AiStatus, Health } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { KvList, KvRow } from '../../components/JsonTree';
import {
  balanceOfRole, balanceShortName, balanceStateLabel, balanceTone, balanceUsage, balanceUsdLabel, headerBalances,
} from '../../lib/aiBalance';
import { Popover } from '../../components/Popover';
import { Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { toneClass } from '../../components/tone';
import { Tooltip } from '../../components/Tooltip';
import { aiFeatureRows, aiModelRows } from '../../lib/aiLabels';
import { cx, formatDecimal, formatInt } from '../../lib/format';
import { CONN_STATUS } from '../../lib/status';
import { useAppStore } from '../../store/app';
import { reconnectNow } from '../../store/live';
import { useSessionStore } from '../../store/session';
import { hashForView, useUiStore } from '../../store/ui';
import { DESTINO_EM_ANDAMENTO, useContagemDeAparelhos, useExecucoesEmAndamento } from '../../store/metricas';
import { usePendencias } from '../pendencias/usePendencias';
import { SaudeAmbiente } from './SaudeAmbiente';
import { ID_BOTAO_MENU, ID_MENU } from './MenuLateral';
import styles from './TopBar.module.css';

export const EXTERNAL_DATA_NOTICE = 'Capturas e textos das telas são enviados ao provedor externo de IA';

export function TopBar() {
  const menuAberto = useUiStore((s) => s.menuAberto);
  const setMenuAberto = useUiStore((s) => s.setMenuAberto);
  return (
    <header className={styles.bar}>
      {/* Duas faixas de propósito: em cima, a marca e com quem se está (IA, conexão, operador); embaixo, como está o
          parque. As seções moram no menu lateral (MenuLateral.tsx): na faixa de cima elas não cabiam abaixo de
          ~1500 px e sumiam sem pista. */}
      <div className={styles.row}>
        {/* Abaixo de 1024 px o menu lateral vira gaveta, e este botão a abre. */}
        <button
          type="button"
          id={ID_BOTAO_MENU}
          className={styles.menuBtn}
          aria-controls={ID_MENU}
          aria-expanded={menuAberto}
          onClick={() => setMenuAberto(!menuAberto)}
        >
          <MenuIcon size={20} aria-hidden />
          <span className="sr-only">Menu</span>
        </button>
        <a href={hashForView('painel')} className={styles.brand} aria-label="Central de Aparelhos — ir para o Painel">
          <span className={styles.brandMark}><MonitorSmartphone size={16} aria-hidden /></span>
          <span className={styles.brandName}>Central de Aparelhos</span>
        </a>
        <div className={styles.tools}>
          <AiBadge />
          <ConnectionIndicator />
          <OperadorAtual />
        </div>
      </div>
      {/* Três grupos, da esquerda para a direita: Saúde, Capacidade e Custos. O rótulo de cada um é texto discreto; o
          que pede ação (semáforo, contador "aguardando você", saldo baixo) é o único que ganha cor. */}
      <div className={styles.strip}>
        <div className={styles.grupo} role="group" aria-label="Saúde">
          <span className={styles.grupoRotulo} aria-hidden>Saúde</span>
          <SaudeAmbiente />
        </div>
        <div className={styles.grupo} role="group" aria-label="Capacidade">
          <span className={styles.grupoRotulo} aria-hidden>Capacidade</span>
          <Counters />
        </div>
        <Custos />
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
      trigger={<><UserRound size={14} aria-hidden /><span className={styles.opName}>{operator}</span></>}
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
// O semáforo mora em `SaudeAmbiente.tsx` (revisão de UX, tarefa 02): nível e motivos de `store/metricas`.

// ---- Contadores ----------------------------------------------------------------------------------

function Counters() {
  const hydrated = useAppStore((s) => s.hydrated);
  const metrics = useAppStore((s) => s.metrics);
  // Rodízio: com `auto_start_devices`, o que limita os aparelhos ligados são as vagas de RAM — de cada servidor.
  const rodizio = useAppStore((s) => !!s.settings?.auto_start_devices);
  const navegar = useUiStore((s) => s.navegar);

  // Fonte única (`store/metricas`, tarefa 02 da revisão de UX): o total é o de aparelhos de TAREFA, o mesmo de
  // "N de T selecionados"; a loja fica à parte e o aparelho de servidor fora do ar não conta como online.
  const { online, total, desconhecidos, loja } = useContagemDeAparelhos();
  // D1 (decisões da revisão de UX): o MESMO total da caixa de Pendências e do selo do menu, e o clique leva a ela.
  const aguardando = usePendencias().total;
  // A mesma conta do chip "Em andamento" de Execuções, e o clique abre a lista já nesse filtro (RF-05).
  const active = useExecucoesEmAndamento();

  if (!hydrated) return <Skeleton width={420} height={24} radius={6} />;

  const memUsed = metrics ? Math.max(0, metrics.mem_total_gb - metrics.mem_available_gb) : null;

  // Cada indicador é uma linha só (ícone · valor · rótulo): empilhar valor e rótulo numa caixa de 36 px dobrava a
  // altura da faixa e o "· vagas N" espremido junto do valor lia como "3 /15 · vagas 4".
  return (
    <div className={styles.counters} role="group" aria-label="Indicadores">
      {/* As vagas saíram daqui: o número era só a vaga do central, posta ao lado do online do parque inteiro. Elas
          são por servidor, e a Infraestrutura é a dona; acima da capacidade vira motivo no semáforo. */}
      <Tooltip
        content={[
          'Aparelhos de tarefa online / cadastrados',
          loja ? 'a loja fica à parte (não recebe tarefa)' : null,
          desconhecidos > 0 ? `${desconhecidos} em estado desconhecido (servidor fora do ar)` : null,
          rodizio ? 'rodízio ligado: cada servidor liga até as vagas dele (em Infraestrutura)' : null,
        ].filter(Boolean).join(' · ')}
      >
        <div className={styles.counter}>
          <Smartphone size={14} aria-hidden />
          <span className={styles.counterValue}>{online}<span className={styles.counterDim}>/{total}</span></span>
          <span className={styles.counterLabel}>online</span>
        </div>
      </Tooltip>
      <Tooltip content="Execuções em andamento (planejando, em execução, pausadas ou cancelando). Um plano pronto que ainda não foi executado não conta. Clique para ver a lista filtrada.">
        <button type="button" className={cx(styles.counter, active > 0 && styles.counterLive)} onClick={() => navegar(DESTINO_EM_ANDAMENTO)}>
          <Activity size={14} aria-hidden />
          <span className={styles.counterValue}>{formatInt(active)}</span>
          <span className={styles.counterLabel}>{active === 1 ? 'execução' : 'execuções'}</span>
        </button>
      </Tooltip>
      {/* Era "N bloqueadas", lido como personas bloqueadas. Hoje é o total da caixa de Pendências (D1). */}
      <Tooltip content="Pendências: o que depende de você (aprovações do Aprendizado e das personas, contas que pedem intervenção e execuções paradas pedindo informação). O mesmo número da caixa de Pendências. Clique para abrir.">
        <button
          type="button"
          className={cx(styles.counter, aguardando > 0 && styles.counterAlert)}
          onClick={() => navegar({ tela: 'pendencias' })}
        >
          <Hand size={14} aria-hidden />
          <span className={styles.counterValue}>{formatInt(aguardando)}</span>
          <span className={styles.counterLabel}>aguardando você</span>
        </button>
      </Tooltip>
      <Tooltip content="Uso de CPU do servidor central">
        <div className={cx(styles.counter, metrics && metrics.cpu_percent >= 90 && styles.counterHot)}>
          <Cpu size={14} aria-hidden />
          <span className={styles.counterLabel}>CPU</span>
          <span className={styles.counterValue}>{metrics ? `${formatInt(metrics.cpu_percent)}%` : '—'}</span>
          {metrics ? <Medidor pct={metrics.cpu_percent} alto={75} critico={90} /> : null}
        </div>
      </Tooltip>
      <Tooltip content={metrics ? `Memória em uso: ${formatInt(metrics.mem_used_percent)}% (usada / total)` : 'Memória do servidor central (sem dados ainda)'}>
        <div className={cx(styles.counter, metrics && metrics.mem_used_percent >= 92 && styles.counterHot)}>
          <MemoryStick size={14} aria-hidden />
          <span className={styles.counterLabel}>RAM</span>
          <span className={styles.counterValue}>
            {metrics && memUsed !== null ? (
              <>{formatDecimal(memUsed)}<span className={styles.counterDim}>/{formatInt(metrics.mem_total_gb)} GB</span></>
            ) : '—'}
          </span>
          {metrics ? <Medidor pct={metrics.mem_used_percent} alto={80} critico={92} /> : null}
        </div>
      </Tooltip>
    </div>
  );
}

/** Barra fina de ocupação. Decorativa: o número ao lado já diz o valor, e a cor só reforça o limiar. */
function Medidor({ pct, alto, critico }: { pct: number; alto: number; critico: number }) {
  const nivel = pct >= critico ? 'critico' : pct >= alto ? 'alto' : undefined;
  return (
    <span className={styles.meter} data-nivel={nivel} aria-hidden>
      <span className={styles.meterFill} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </span>
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
        <Tooltip content="Defina a chave do provedor de IA no arquivo .env do servidor e reinicie-o. A chave nunca é informada pelo navegador.">
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

/**
 * Saldo estimado de cada conta de IA em uso (ADR-051). Só aparece a conta que paga alguma função (ou que está
 * barrada): o chip é alerta, não enfeite — verde discreto quando tudo vai bem, cor quando pede ação.
 */
function Custos() {
  const balances = useAppStore((s) => s.health?.ai?.balances);
  const list = headerBalances(balances);
  if (list.length === 0) return null;
  const emOutraMoeda = list.some((b) => b.currency !== 'USD');
  return (
    <div className={styles.grupo} role="group" aria-label="Custos: saldo das contas de IA">
      <span className={styles.grupoRotulo} aria-hidden>Custos</span>
      <div className={styles.balances}>
        {list.map((b) => (
          <Tooltip key={b.account} content={`${b.label}: ${b.message} Usada por: ${balanceUsage(b)}. Detalhes em Configuração › IA.`}>
            <span tabIndex={0} className={styles.balanceChip} data-tone={balanceTone(b)}>
              <Wallet size={14} aria-hidden />
              <span>{balanceShortName(b.account)}</span>
              <strong>{balanceUsdLabel(b)}</strong>
            </span>
          </Tooltip>
        ))}
      </div>
      <Tooltip content={`Valores estimados: o saldo que você informou menos o consumo medido desde então. Todos em US$${emOutraMoeda ? '; a conta em outra moeda aparece convertida pelo câmbio configurado' : ''}.`}>
        <span className={styles.estimado} tabIndex={0} role="img" aria-label="Valores estimados, em US$">
          <Info size={14} aria-hidden />
        </span>
      </Tooltip>
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
            {models.map((m) => {
              // Quem paga esta função (ADR-051): o modelo sozinho não diz de qual saldo o custo sai.
              const conta = balanceOfRole(ai.balances, m.key);
              return (
                <KvRow key={m.key} label={m.label}>
                  <span className="mono">{m.value}</span>
                  {conta ? <span className={styles.aiAccount} data-tone={balanceTone(conta)}> · {balanceShortName(conta.account)}</span> : null}
                </KvRow>
              );
            })}
          </KvList>
        </>
      ) : null}
      {ai.balances && ai.balances.length > 0 ? (
        <>
          <h4 className={styles.aiGroupTitle}>Saldo das contas (estimado)</h4>
          <KvList>
            {ai.balances.map((b) => (
              <KvRow key={b.account} label={balanceShortName(b.account)}>
                <span className={styles.aiAccount} data-tone={balanceTone(b)}>
                  {b.estimated_balance === null ? 'sem leitura' : balanceUsdLabel(b)}
                  {b.state !== 'ok' ? ` · ${balanceStateLabel(b.state)}` : ''}
                </span>
                <br /><small className={styles.aiNote}>{balanceUsage(b)}</small>
              </KvRow>
            ))}
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
        <p className={styles.aiNote}>O servidor não informou o estado de receitas, fluxos e imagens.</p>
      )}
    </Popover>
  );
}

// ---- WebSocket -----------------------------------------------------------------------------------

function ConnectionIndicator() {
  const conn = useAppStore((s) => s.conn);
  const meta = CONN_STATUS[conn.status];
  const waiting = conn.status === 'reconnecting' || conn.status === 'disconnected';
  // Sem pílula: "Ambiente OK" e "Conectado" eram dois selos verdes lado a lado. Com a conexão boa, texto discreto;
  // com problema, a cor do tom volta (e o ConnectionBanner no conteúdo explica).
  return (
    <div className={cx(styles.conn, waiting && styles.connWaiting)} role="status" aria-live="polite">
      <Tooltip content={conn.lastError && waiting ? `Último erro: ${conn.lastError}` : 'Canal em tempo real com o servidor'}>
        <StatusBadge meta={meta} plain srPrefix="Conexão" />
      </Tooltip>
      {waiting ? <Button size="sm" variant="ghost" icon={RefreshCw} iconOnly label="Reconectar agora" onClick={reconnectNow} /> : null}
    </div>
  );
}
