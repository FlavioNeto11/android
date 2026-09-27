/**
 * Servidor → Aparelho → Tela → Apps → Perfil/Conta → Sessão, numa vista só (`GET .../operational-context`).
 *
 * Para entender por que o android-06 não entrava no Instagram era preciso cruzar Parque, Aplicativos, Perfis e
 * Servidores. Aqui cada camada aparece com a SUA fonte e nenhuma é deduzida da outra — o cartão só mostra o que o
 * backend respondeu. Usado no Foco (a partir do aparelho) e no perfil (a partir da persona).
 */
import { CircleCheck, CircleHelp, CircleX, LoaderCircle, RefreshCw, ServerCrash } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { OperationalContext } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { StatusBadge } from '../../components/StatusBadge';
import { type LoadError, toLoadError } from '../../lib/loadError';
import {
  CONNECTIVITY_STATE, INSTANCE_STATE, READINESS_PHASE, SESSION_STATUS, STREAM_STATUS, metaOf, type StatusMeta,
} from '../../lib/status';
import { formatAgoCoarse, useNow } from '../../lib/time';
import { SESSION_PHASE_LABEL } from '../profiles/sessionGate';

const PRESENCA: Record<OperationalContext['apps'][number]['presence'], StatusMeta> = {
  installed: { label: 'instalado', tone: 'success', icon: CircleCheck },
  absent: { label: 'ausente', tone: 'danger', icon: CircleX },
  in_progress: { label: 'em operação', tone: 'info', icon: LoaderCircle, spin: true },
  unknown: { label: 'não verificado', tone: 'neutral', icon: CircleHelp },
};

/** Só este pedaço assina o relógio de 1 s: com `useNow()` no topo, o cartão inteiro re-renderizava a cada segundo. */
function Quando({ ts }: { ts: string | null }) {
  const now = useNow();
  return <>{ts ? formatAgoCoarse(ts, now) : 'nunca'}</>;
}

export function OperationalContextCard({ instanceId, profileId, refreshKey }: {
  instanceId?: string | null;
  profileId?: string | null;
  /** Muda quando o estado do aparelho muda: o cartão relê sozinho. */
  refreshKey?: string;
}) {
  const [ctx, setCtx] = useState<OperationalContext | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(false);

  const carregar = useCallback(async () => {
    if (!instanceId && !profileId) return;
    setCarregando(true);
    try {
      setCtx(await (instanceId ? api.instanceContext(instanceId) : api.profileContext(profileId as string)));
      setErro(null);
    } catch (e) {
      setErro(toLoadError(e));
    } finally {
      setCarregando(false);
    }
  }, [instanceId, profileId]);

  useEffect(() => { void carregar(); }, [carregar, refreshKey]);

  if (!instanceId && !profileId) return null;
  return (
    <section aria-label="Contexto operacional" data-testid="operational-context">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 'var(--fs-md)' }}>Contexto operacional</h3>
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>
          Reler
        </Button>
      </div>
      {erro ? (
        // "Reler" logo acima é o "tentar de novo"; o cartão de baixo fica com a última leitura, se houver.
        <Banner tone="danger" icon={ServerCrash} compact role="alert" title="Não foi possível ler o contexto">
          {erro.message} {erro.hint}
        </Banner>
      ) : null}
      {ctx ? (
        <dl style={{ display: 'grid', gridTemplateColumns: 'max-content minmax(0, 1fr)', gap: '4px 12px', margin: '8px 0 0' }}>
          <dt>Servidor</dt>
          <dd>
            {ctx.server ? <>{ctx.server.name}{ctx.server.local ? ' (este)' : ''}{' '}
              <Badge tone={ctx.server.connected ? 'success' : 'danger'}>{ctx.server.connected ? 'conectado' : 'desconectado'}</Badge></>
              : 'este servidor'}
          </dd>
          <dt>Aparelho</dt>
          <dd>
            {ctx.instance_id} <StatusBadge meta={metaOf(INSTANCE_STATE, ctx.device.state)} size="sm" srPrefix="Estado" />
            {ctx.device.state_detail ? ` — ${ctx.device.state_detail}` : ''}
          </dd>
          <dt>Prontidão</dt>
          <dd data-testid="context-readiness">
            {ctx.readiness ? <><StatusBadge meta={metaOf(READINESS_PHASE, ctx.readiness.phase)} size="sm" />
              {ctx.readiness.detail ? ` ${ctx.readiness.detail}` : ''}</> : '—'}
          </dd>
          <dt>Tela</dt>
          <dd>
            {ctx.stream ? <><StatusBadge meta={metaOf(STREAM_STATUS, ctx.stream.status)} size="sm" /> {ctx.stream.detail}</> : '—'}
          </dd>
          <dt>Internet</dt>
          <dd data-testid="context-connectivity">
            {ctx.connectivity ? <><StatusBadge meta={metaOf(CONNECTIVITY_STATE, ctx.connectivity.state)} size="sm" />
              {' '}{ctx.connectivity.detail} · verificada <Quando ts={ctx.connectivity.checked_at} /></> : '—'}
          </dd>
          <dt>Apps</dt>
          <dd>
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {ctx.apps.map((a) => (
                <li key={a.app_id}>
                  {a.name} <StatusBadge meta={PRESENCA[a.presence]} size="sm" />
                  {' '}instalada {a.installed_version_name ?? '—'} · promovida {a.promoted_version_name ?? '—'}
                  {' '}· verificado <Quando ts={a.verified_at} />
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
                {' '}<StatusBadge meta={metaOf(SESSION_STATUS, p.session.status)} size="sm" srPrefix="Sessão" />
                {' '}· verificada <Quando ts={p.session.verified_at} />
                {p.session.detail ? ` — ${p.session.detail}` : ''}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}
