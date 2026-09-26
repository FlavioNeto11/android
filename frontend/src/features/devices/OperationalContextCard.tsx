/**
 * Servidor → Aparelho → Tela → Apps → Perfil/Conta → Sessão, numa vista só (`GET .../operational-context`).
 *
 * Para entender por que o android-06 não entrava no Instagram era preciso cruzar Parque, Aplicativos, Perfis e
 * Servidores. Aqui cada camada aparece com a SUA fonte e nenhuma é deduzida da outra — o cartão só mostra o que o
 * backend respondeu. Usado no Foco (a partir do aparelho) e no perfil (a partir da persona).
 */
import { RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { ConnectivityInfo, OperationalContext } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatAgoCoarse, useNow } from '../../lib/time';
import { SESSION_PHASE_LABEL } from '../profiles/sessionGate';

const PRESENCA: Record<OperationalContext['apps'][number]['presence'], { label: string; tone: 'success' | 'danger' | 'info' | 'neutral' }> = {
  installed: { label: 'instalado', tone: 'success' },
  absent: { label: 'ausente', tone: 'danger' },
  in_progress: { label: 'em operação', tone: 'info' },
  unknown: { label: 'não verificado', tone: 'neutral' },
};

const STREAM_TONE: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  live: 'success', stale: 'warning', no_frame: 'neutral', capture_error: 'danger', worker_offline: 'danger',
  device_offline: 'neutral', device_hibernated: 'neutral', paused: 'neutral',
};
const REDE_TONE: Record<ConnectivityInfo['state'], 'success' | 'warning' | 'danger' | 'neutral'> = {
  healthy: 'success', degraded: 'warning', unavailable: 'danger', unknown: 'neutral',
};

export function OperationalContextCard({ instanceId, profileId, refreshKey }: {
  instanceId?: string | null;
  profileId?: string | null;
  /** Muda quando o estado do aparelho muda: o cartão relê sozinho. */
  refreshKey?: string;
}) {
  const [ctx, setCtx] = useState<OperationalContext | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);
  const now = useNow();

  const carregar = useCallback(async () => {
    if (!instanceId && !profileId) return;
    setCarregando(true);
    try {
      setCtx(await (instanceId ? api.instanceContext(instanceId) : api.profileContext(profileId as string)));
      setErro(null);
    } catch (e) {
      setErro(e instanceof Error ? e.message : String(e));
    } finally {
      setCarregando(false);
    }
  }, [instanceId, profileId]);

  useEffect(() => { void carregar(); }, [carregar, refreshKey]);

  if (!instanceId && !profileId) return null;
  const quando = (ts: string | null) => (ts ? formatAgoCoarse(ts, now) : 'nunca');
  return (
    <section aria-label="Contexto operacional" data-testid="operational-context">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-md)' }}>Contexto operacional</h3>
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>
          Reler
        </Button>
      </div>
      {erro ? <p role="alert">{erro}</p> : null}
      {ctx ? (
        <dl style={{ display: 'grid', gridTemplateColumns: 'max-content minmax(0, 1fr)', gap: '4px 12px', margin: '8px 0 0' }}>
          <dt>Servidor</dt>
          <dd>
            {ctx.server ? <>{ctx.server.name}{ctx.server.local ? ' (este)' : ''}{' '}
              <Badge tone={ctx.server.connected ? 'success' : 'danger'}>{ctx.server.connected ? 'conectado' : 'desconectado'}</Badge></>
              : 'este servidor'}
          </dd>
          <dt>Aparelho</dt>
          <dd>{ctx.instance_id} · {ctx.device.state}{ctx.device.state_detail ? ` — ${ctx.device.state_detail}` : ''}</dd>
          <dt>Prontidão</dt>
          <dd data-testid="context-readiness">
            {ctx.readiness ? <><Badge tone={ctx.readiness.phase === 'ready' ? 'success' : ctx.readiness.phase === 'not_running' ? 'neutral' : 'warning'}>
              {ctx.readiness.phase}</Badge>{ctx.readiness.detail ? ` ${ctx.readiness.detail}` : ''}</> : '—'}
          </dd>
          <dt>Tela</dt>
          <dd>
            {ctx.stream ? <><Badge tone={STREAM_TONE[ctx.stream.status] ?? 'neutral'}>{ctx.stream.status}</Badge> {ctx.stream.detail}</> : '—'}
          </dd>
          <dt>Internet</dt>
          <dd data-testid="context-connectivity">
            {ctx.connectivity ? <><Badge tone={REDE_TONE[ctx.connectivity.state]}>{ctx.connectivity.state}</Badge>
              {' '}{ctx.connectivity.detail} · verificada {quando(ctx.connectivity.checked_at)}</> : '—'}
          </dd>
          <dt>Apps</dt>
          <dd>
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {ctx.apps.map((a) => (
                <li key={a.app_id}>
                  {a.name} <Badge tone={PRESENCA[a.presence].tone}>{PRESENCA[a.presence].label}</Badge>
                  {' '}instalada {a.installed_version_name ?? '—'} · promovida {a.promoted_version_name ?? '—'}
                  {' '}· verificado {quando(a.verified_at)}
                </li>
              ))}
            </ul>
          </dd>
          {ctx.profiles.length === 0 ? (<><dt>Perfil</dt><dd>nenhum perfil vinculado</dd></>) : ctx.profiles.map((p) => (
            <div key={p.profile_id} style={{ display: 'contents' }}>
              <dt>Perfil</dt>
              <dd>
                {p.display_name ?? p.username} (@{p.username}){p.persona_name ? ` · persona ${p.persona_name}` : ''}
                {' '}· senha {p.credential_configured ? 'guardada' : 'não guardada'}
              </dd>
              <dt>Sessão</dt>
              <dd>
                {p.session_actions ? (
                  <Badge tone={SESSION_PHASE_LABEL[p.session_actions.phase].tone}>
                    {SESSION_PHASE_LABEL[p.session_actions.phase].label}
                  </Badge>
                ) : null}
                {' '}{p.session.status} · verificada {quando(p.session.verified_at)}
                {p.session.detail ? ` — ${p.session.detail}` : ''}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}
