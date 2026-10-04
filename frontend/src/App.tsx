import { MapPinOff, WifiOff } from 'lucide-react';
import { useEffect, useRef } from 'react';
import styles from './App.module.css';
import { Banner } from './components/Banner';
import { Button } from './components/Button';
import { ConfirmHost } from './components/Confirm';
import { Toasts } from './components/Toasts';
import { AprendizadoPage } from './features/aprendizado/AprendizadoPage';
import { CanaisPage } from './features/canais/CanaisPage';
import { DiagnosticsPage } from './features/diagnostics/DiagnosticsPage';
import { seletorDoAparelho } from './features/focus/Drawer';
import { FocusPanel } from './features/focus/FocusPanel';
import { LoginPage } from './features/login/LoginPage';
import { PedidosPage } from './features/pedidos/PedidosPage';
import { PendenciasPage } from './features/pendencias/PendenciasPage';
import { PainelPage } from './features/painel/PainelPage';
import { ProfilesPage } from './features/profiles/ProfilesPage';
import { AppsPage } from './features/apps/AppsPage';
import { InfraPage } from './features/infra/InfraPage';
import { RunsPage } from './features/runs/RunsPage';
import { SettingsPage } from './features/settings/SettingsPage';
import { MenuLateral } from './features/topbar/MenuLateral';
import { TopBar } from './features/topbar/TopBar';
import { tempoRelativo, useNow } from './lib/time';
import { useAppStore } from './store/app';
import { reconnectNow, startLive } from './store/live';
import { useSessionStore } from './store/session';
import { bindHashRouting, useUiStore } from './store/ui';

function RetryCountdown({ at }: { at: number | null }) {
  useNow();
  if (!at) return <>Tentando reconectar agora…</>;
  const s = Math.max(0, Math.ceil((at - Date.now()) / 1000));
  return <>Nova tentativa automática em {s} s.</>;
}

function LastSeen({ at }: { at: number | null }) {
  useNow();
  if (!at) return null;
  return <> Última atualização em tempo real {tempoRelativo(new Date(at).toISOString(), Date.now())}.</>;
}

/** Com dados já carregados e o canal ao vivo fora do ar: avisa que a tela pode estar desatualizada. */
function ConnectionBanner() {
  const hydrated = useAppStore((s) => s.hydrated);
  const conn = useAppStore((s) => s.conn);
  if (!hydrated || conn.status === 'connected' || conn.status === 'connecting') return null;
  return (
    <Banner
      className={styles.connBanner}
      tone={conn.status === 'disconnected' ? 'danger' : 'warning'}
      icon={WifiOff}
      role="alert"
      title={conn.status === 'disconnected' ? 'Desconectado do servidor' : 'Reconectando ao servidor…'}
      actions={<Button size="sm" onClick={reconnectNow}>Reconectar agora</Button>}
    >
      Os dados abaixo são os últimos recebidos e <strong>podem estar desatualizados</strong>.<LastSeen at={conn.lastConnectedAt} />{' '}
      {/* O motivo já existia (`conn.lastError`: "ping sem pong", "código 1006", "ressincronização repetida") e era
          descartado aqui — o banner apareceu em produção sem nada no log do servidor, e não dava para saber qual dos
          três caminhos o produziu. */}
      {conn.lastError ? <>Motivo: {conn.lastError}. </> : null}
      <RetryCountdown at={conn.nextRetryAt} /> Reconectar não cria nem reinicia execuções.
    </Banner>
  );
}

/** 29.61: link antigo ou digitado errado (`#/runs`) não cai calado no Painel: diz que o endereço não existe e leva a ele. */
function RotaDesconhecidaBanner() {
  const hash = useUiStore((s) => s.rotaDesconhecida);
  const dispensar = useUiStore((s) => s.dispensarRotaDesconhecida);
  const navegar = useUiStore((s) => s.navegar);
  if (!hash) return null;
  return (
    <Banner
      className={styles.connBanner}
      tone="warning"
      icon={MapPinOff}
      role="alert"
      title="Este endereço não existe no painel"
      actions={(
        <>
          <Button size="sm" onClick={() => navegar({ tela: 'painel' })}>Ir para o Painel</Button>
          <Button size="sm" variant="outline" onClick={dispensar}>Dispensar</Button>
        </>
      )}
    >
      O link <code>{hash}</code> não leva a nenhuma tela (pode ser antigo ou ter sido digitado errado). Você continua na
      tela em que estava.
    </Banner>
  );
}

export function App() {
  const view = useUiStore((s) => s.view);
  const focusId = useUiStore((s) => s.focusInstanceId);
  const stale = useAppStore((s) => s.hydrated && s.conn.status !== 'connected' && s.conn.status !== 'connecting');
  const checked = useSessionStore((s) => s.checked);
  const operator = useSessionStore((s) => s.operator);
  const mainRef = useRef<HTMLElement>(null);

  // Perguntar "quem sou eu" vem ANTES de tudo: no loopback o 401 nunca chega, e sem esta pergunta ninguém
  // jamais se identificaria — a auditoria seguiria dizendo `panel` na máquina em que o parque de fato roda.
  useEffect(() => {
    void useSessionStore.getState().refresh();
  }, []);

  useEffect(() => bindHashRouting(), []);

  // O canal ao vivo só abre DEPOIS do login: sem sessão, snapshot e WebSocket só colheriam 401 em backoff.
  useEffect(() => {
    if (!operator) return undefined;
    return startLive();
  }, [operator]);

  // O Foco é um drawer SOBREPOSTO: abrir ou fechar não muda a largura do conteúdo nem reorganiza a grade. Sobra só o
  // caso do link colado (`?foco=`) para um aparelho fora da vista: o cartão dele vem para a tela, sem rolar se já estiver.
  useEffect(() => {
    if (!focusId) return undefined;
    const quadro = requestAnimationFrame(() => {
      document.querySelector(seletorDoAparelho(focusId))?.scrollIntoView?.({ block: 'nearest' });
    });
    return () => cancelAnimationFrame(quadro);
  }, [focusId]);

  // Ao trocar de seção, volta ao topo e atualiza o título da aba.
  useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
    const names = { painel: 'Painel', personas: 'Personas', aplicativos: 'Aplicativos', execucoes: 'Execuções',
                    pedidos: 'Pedidos', pendencias: 'Pendências', aprendizado: 'Aprendizado', infraestrutura: 'Infraestrutura', configuracao: 'Configuração',
                    diagnostico: 'Diagnóstico', canais: 'Canais' } as const;
    document.title = `${names[view]} · Central de Aparelhos`;
  }, [view]);

  if (!checked) return <div className={styles.app} aria-busy="true" />;
  if (!operator) return <LoginPage />;

  return (
    <div className={styles.app}>
      <button type="button" className={styles.skip} onClick={() => mainRef.current?.focus()}>
        Pular para o conteúdo
      </button>
      <TopBar />
      <div className={styles.body}>
        <MenuLateral />
        <main ref={mainRef} id="conteudo" tabIndex={-1} className={styles.main}>
          <ConnectionBanner />
          <RotaDesconhecidaBanner />
          <div className={stale ? styles.stale : undefined}>
            {view === 'painel' ? <PainelPage /> : null}
            {view === 'personas' ? <ProfilesPage /> : null}
            {view === 'aplicativos' ? <AppsPage /> : null}
            {view === 'execucoes' ? <RunsPage /> : null}
            {view === 'pedidos' ? <PedidosPage /> : null}
            {view === 'pendencias' ? <PendenciasPage /> : null}
            {view === 'aprendizado' ? <AprendizadoPage /> : null}
            {view === 'infraestrutura' ? <InfraPage /> : null}
            {view === 'configuracao' ? <SettingsPage /> : null}
            {view === 'diagnostico' ? <DiagnosticsPage /> : null}
            {view === 'canais' ? <CanaisPage /> : null}
          </div>
        </main>
        {focusId ? <FocusPanel key={focusId} instanceId={focusId} /> : null}
      </div>
      <Toasts />
      <ConfirmHost />
    </div>
  );
}
