import { WifiOff } from 'lucide-react';
import { useEffect, useRef } from 'react';
import styles from './App.module.css';
import { Banner } from './components/Banner';
import { Button } from './components/Button';
import { ConfirmHost } from './components/Confirm';
import { Toasts } from './components/Toasts';
import { AprendizadoPage } from './features/aprendizado/AprendizadoPage';
import { DiagnosticsPage } from './features/diagnostics/DiagnosticsPage';
import { FocusPanel } from './features/focus/FocusPanel';
import { LoginPage } from './features/login/LoginPage';
import { PainelPage } from './features/painel/PainelPage';
import { ProfilesPage } from './features/profiles/ProfilesPage';
import { AppsPage } from './features/apps/AppsPage';
import { InfraPage } from './features/infra/InfraPage';
import { RunsPage } from './features/runs/RunsPage';
import { SettingsPage } from './features/settings/SettingsPage';
import { TopBar } from './features/topbar/TopBar';
import { formatAgoCoarse, useNow } from './lib/time';
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
      {/* O motivo já existia (`conn.lastError`: "ping sem pong", "código 1006", "ressincronização repetida") e era
          descartado aqui — o banner apareceu em produção sem nada no log do servidor, e não dava para saber qual dos
          três caminhos o produziu. */}
      {conn.lastError ? <>Motivo: {conn.lastError}. </> : null}
      <RetryCountdown at={conn.nextRetryAt} /> Reconectar não cria nem reinicia execuções.
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

  // Abrir ou fechar o painel de foco muda a largura do conteúdo (1270 → 660 px medidos): a grade troca de colunas,
  // a página dobra de altura e, com o mesmo deslocamento, o cartão clicado sumia da vista — era o "scroll que
  // quebra" do Painel. Depois que o layout assenta, o cartão do aparelho em foco (ou o que acabou de sair dele)
  // volta a ficar visível.
  const ultimoFoco = useRef<string | null>(null);
  useEffect(() => {
    const alvo = focusId ?? ultimoFoco.current;
    ultimoFoco.current = focusId;
    if (!alvo) return undefined;
    // O painel cresce em etapas (montagem, imagem, transição): reposiciona a cada mudança de tamanho do conteúdo
    // durante um instante, não uma vez só — uma correção única chegava antes do layout assentar.
    const reposicionar = () => {
      const card = document.querySelector(`[data-instance-card="${CSS.escape(alvo)}"]`);
      card?.scrollIntoView?.({ block: 'nearest' });
    };
    const quadro = requestAnimationFrame(reposicionar);
    const main = mainRef.current;
    const obs = typeof ResizeObserver !== 'undefined' && main ? new ResizeObserver(reposicionar) : null;
    if (obs && main) {
      obs.observe(main);
      // a largura muda no `main`, mas a ALTURA cresce no conteúdo (a grade vira uma coluna depois)
      const grade = document.querySelector(`[data-instance-card="${CSS.escape(alvo)}"]`)?.parentElement;
      if (grade) obs.observe(grade);
    }
    const fim = window.setTimeout(() => obs?.disconnect(), 2000);
    return () => {
      cancelAnimationFrame(quadro);
      window.clearTimeout(fim);
      obs?.disconnect();
    };
  }, [focusId]);

  // Ao trocar de seção, volta ao topo e atualiza o título da aba.
  useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
    const names = { painel: 'Painel', perfis: 'Personas', aplicativos: 'Aplicativos', execucoes: 'Execuções',
                    aprendizado: 'Aprendizado', infraestrutura: 'Infraestrutura', configuracao: 'Configuração',
                    diagnostico: 'Diagnóstico' } as const;
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
        <main ref={mainRef} id="conteudo" tabIndex={-1} className={styles.main}>
          <ConnectionBanner />
          <div className={stale ? styles.stale : undefined}>
            {view === 'painel' ? <PainelPage /> : null}
            {view === 'perfis' ? <ProfilesPage /> : null}
            {view === 'aplicativos' ? <AppsPage /> : null}
            {view === 'execucoes' ? <RunsPage /> : null}
            {view === 'aprendizado' ? <AprendizadoPage /> : null}
            {view === 'infraestrutura' ? <InfraPage /> : null}
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
