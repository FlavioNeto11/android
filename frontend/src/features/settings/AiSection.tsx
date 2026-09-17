import { Bot, FlaskConical, KeyRound, RefreshCw, ServerCrash, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { AiStatus } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { KvList, KvRow } from '../../components/JsonTree';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { useAppStore } from '../../store/app';
import { EXTERNAL_DATA_NOTICE } from '../topbar/TopBar';
import styles from './Settings.module.css';

export function AiSection() {
  const fromHealth = useAppStore((s) => s.health?.ai ?? null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAi(await api.ai());
    } catch (e) {
      const err = toApiError(e);
      setError({ message: err.message, hint: hintForError(err) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Se GET /api/ai falhar, o status que veio no health continua útil (com o erro visível).
  const status = ai ?? fromHealth;

  if (loading && !status) {
    return (
      <LoadingRegion label="Consultando o status da IA…">
        <Skeleton width={280} height={18} />
        <Skeleton height={90} radius={8} style={{ marginTop: 12 }} />
      </LoadingRegion>
    );
  }
  if (!status) {
    return (
      <EmptyState icon={ServerCrash} tone="danger" title="Não foi possível consultar a IA" hint={error?.hint} actions={<Button variant="outline" icon={RefreshCw} onClick={() => void load()}>Tentar de novo</Button>}>
        {error?.message}
      </EmptyState>
    );
  }

  const usable = status.configured || status.simulated;

  return (
    <>
      <div className={styles.sectionIntro}>
        <p className={styles.sectionLead}>Status do provedor de IA usado para planejar e operar os aparelhos. Esta tela é somente leitura.</p>
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void load()}>Atualizar</Button>
      </div>

      {error ? <Banner tone="warning" icon={ServerCrash} compact title="Mostrando o último status conhecido">{error.message} {error.hint}</Banner> : null}

      {status.simulated ? (
        <Banner tone="warning" icon={FlaskConical} title="MODO SIMULADO" role="status">
          Nenhuma IA real é chamada: planos e resultados são fictícios, úteis só para testar o painel. Para usar a IA de verdade, configure a chave e desative o modo simulado no <span className="mono">.env</span> do backend.
        </Banner>
      ) : !status.configured ? (
        <Banner tone="danger" icon={ShieldAlert} title="IA não configurada" role="alert">
          Planejar e Executar ficam indisponíveis até a chave ser definida. O restante do painel (emuladores, controle manual, diagnóstico) continua funcionando.
        </Banner>
      ) : null}

      <div className={styles.aiGrid}>
        <div>
          <KvList>
            <KvRow label="Situação">
              {usable ? <Badge tone={status.simulated ? 'warning' : 'success'} icon={status.simulated ? FlaskConical : ShieldCheck}>{status.simulated ? 'Simulada' : 'Pronta para uso'}</Badge> : <Badge tone="danger" icon={ShieldAlert}>Indisponível</Badge>}
            </KvRow>
            <KvRow label="Provedor"><span className="mono">{status.provider}</span></KvRow>
            <KvRow label="Modelo"><span className="mono">{status.model ?? '—'}</span></KvRow>
            <KvRow label="Esforço de raciocínio">{status.effort ?? '—'}</KvRow>
            <KvRow label="Chave de API">{status.configured ? 'Presente no backend' : 'Ausente'}</KvRow>
            <KvRow label="Dados saem da máquina?">{status.sends_data_externally ? 'Sim' : 'Não'}</KvRow>
          </KvList>
          {status.notice ? <p style={{ marginTop: 12, color: 'var(--text-2)' }}>{status.notice}</p> : null}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {status.sends_data_externally ? (
            <Banner tone="warning" icon={ShieldAlert} title="Dados enviados para fora desta máquina">
              {EXTERNAL_DATA_NOTICE}. Não use contas pessoais nem dados reais nos emuladores: tudo o que aparece na tela pode ser enviado.
            </Banner>
          ) : (
            <Banner tone="success" icon={ShieldCheck} title="Nada sai desta máquina">No modo atual, screenshots e textos das telas ficam apenas no backend local.</Banner>
          )}
          <Banner tone="info" icon={KeyRound} title="Onde fica a chave">
            <p>A chave do provedor vive no arquivo <span className="mono">.env</span> do backend — <strong>nunca no navegador</strong>, e este painel não tem campo para ela.</p>
            <ol className={styles.steps} style={{ marginTop: 8 }}>
              <li>Abra o <span className="mono">.env</span> na pasta do backend.</li>
              <li>Defina a chave do provedor de IA (veja o <span className="mono">.env.example</span> para o nome da variável).</li>
              <li>Reinicie o backend e clique em “Atualizar”.</li>
            </ol>
          </Banner>
        </div>
      </div>
      <p style={{ color: 'var(--text-3)', fontSize: 'var(--fs-sm)' }}><Bot size={12} aria-hidden style={{ verticalAlign: '-1px' }} /> O modelo em uso também aparece na barra superior.</p>
    </>
  );
}
