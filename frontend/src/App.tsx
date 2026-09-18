import { WifiOff } from 'lucide-react';
import { useEffect, useRef } from 'react';
import styles from './App.module.css';
import { Banner } from './components/Banner';
import { Button } from './components/Button';
import { ConfirmHost } from './components/Confirm';
import { Toasts } from './components/Toasts';
import { DiagnosticsPage } from './features/diagnostics/DiagnosticsPage';
import { FocusPanel } from './features/focus/FocusPanel';
import { PainelPage } from './features/painel/PainelPage';
import { ProfilesPage } from './features/profiles/ProfilesPage';
import { ReleasesPage } from './features/releases/ReleasesPage';
import { RunsPage } from './features/runs/RunsPage';
import { SettingsPage } from './features/settings/SettingsPage';
import { TopBar } from './features/topbar/TopBar';
import { formatAgoCoarse, useNow } from './lib/time';
import { useAppStore } from './store/app';
import { reconnectNow, startLive } from './store/live';
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
  return <> Última atualização em tempo real {formatAgoCoarse(new Date(at).toISOString(), Date.now())}.</>;
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
      title={conn.status === 'disconnected' ? 'Desconectado do backend' : 'Reconectando ao backend…'}
      actions={<Button size="sm" onClick={reconnectNow}>Reconectar agora</Button>}
    >
      Os dados abaixo são os últimos recebidos e <strong>podem estar desatualizados</strong>.<LastSeen at={conn.lastConnectedAt} />{' '}
      <RetryCountdown at={conn.nextRetryAt} /> Reconectar não cria nem reinicia execuções.
    </Banner>
  );
}

export function App() {
  const view = useUiStore((s) => s.view);
  const focusId = useUiStore((s) => s.focusInstanceId);
  const stale = useAppStore((s) => s.hydrated && s.conn.status !== 'connected' && s.conn.status !== 'connecting');
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const unbind = bindHashRouting();
    const stop = startLive();
    return () => {
      unbind();
      stop();
    };
  }, []);

  // Ao trocar de seção, volta ao topo e atualiza o título da aba.
  useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
    const names = { painel: 'Painel', perfis: 'Perfis', aplicativos: 'Aplicativos', execucoes: 'Execuções',
                    configuracao: 'Configuração',
                    diagnostico: 'Diagnóstico' } as const;
    document.title = `${names[view]} · Central de Aparelhos`;
  }, [view]);

  return (
    <div className={styles.app}>
      <button type="button" className={styles.skip} onClick={() => mainRef.current?.focus()}>
        Pular para o conteúdo
      </button>
      <TopBar />
      <div className={styles.body}>
        <main ref={mainRef} id="conteudo" tabIndex={-1} className={styles.main}>
          <ConnectionBanner />
          <div className={stale ? styles.stale : undefined}>
            {view === 'painel' ? <PainelPage /> : null}
            {view === 'perfis' ? <ProfilesPage /> : null}
            {view === 'aplicativos' ? <ReleasesPage /> : null}
            {view === 'execucoes' ? <RunsPage /> : null}
            {view === 'configuracao' ? <SettingsPage /> : null}
            {view === 'diagnostico' ? <DiagnosticsPage /> : null}
          </div>
        </main>
        {focusId ? <FocusPanel key={focusId} instanceId={focusId} /> : null}
      </div>
      <Toasts />
      <ConfirmHost />
    </div>
  );
}
