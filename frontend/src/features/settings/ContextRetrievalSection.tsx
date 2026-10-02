import { CheckCircle2, CircleHelp, Search, ServerCrash, ShieldCheck, ShieldAlert, XCircle, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { ContextRetrievalStatus } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { KvList, KvRow } from '../../components/JsonTree';
import { PageSection } from '../../components/Page';
import { Skeleton } from '../../components/Skeleton';
import {
  cacheHitRate, contagens, fallbackLabel, modeLabel, ms, provas, sendReasonLabel, usd, veredito, visibilityLabel,
} from '../../lib/contextRetrieval';
import styles from './ContextRetrieval.module.css';

/**
 * Retrieval de contexto de código (ADR-063): só LEITURA do que o backend decide. Nada aqui liga o provedor remoto, muda
 * a configuração ou dispara consulta; o interruptor é `context_retrieval.enabled` no `config.yaml`. A leitura (`GET
 * /api/context-retrieval/status`) não consulta código, não vai à rede e nunca traz a chave, a pergunta ou caminhos.
 */
export function ContextRetrievalSection() {
  const [status, setStatus] = useState<ContextRetrievalStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await api.contextRetrievalStatus());
    } catch (e) {
      const err = toApiError(e);
      setError({ message: err.message, hint: hintForError(err) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <PageSection
      title={<><Search size={16} aria-hidden className={styles.inlineIcon} /> Retrieval de contexto</>}
      subtitle="Busca de trechos de código para preparar tarefas (ADR-063). Só leitura: este painel não liga o envio a provedor externo; isso é decisão de configuração."
      actions={<Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void load()}>Atualizar</Button>}
    >
      {error ? (
        <Banner tone="warning" icon={ServerCrash} compact title="Não foi possível consultar o retrieval de contexto">
          {error.message}{error.hint ? ` ${error.hint}` : ''}
        </Banner>
      ) : null}
      {!status && loading ? <Skeleton height={90} radius={8} /> : null}
      {status ? <Conteudo s={status} /> : null}
    </PageSection>
  );
}

function Conteudo({ s }: { s: ContextRetrievalStatus }) {
  const v = veredito(s);
  const remoto = s.mode === 'hybrid' || s.mode === 'shadow';
  const fallbacks = contagens(s.summary.fallbacks, fallbackLabel);
  const bloqueios = contagens(s.summary.privacy_blocks, sendReasonLabel);
  return (
    <>
      <Banner tone={v.tone} icon={v.tone === 'warning' ? ShieldAlert : ShieldCheck} compact title={v.titulo} role="status">
        {v.detalhe}
      </Banner>
      <div className={styles.grid}>
        <div className={styles.card}>
          <h3 className={styles.cardTitle}>Configuração</h3>
          <KvList>
            <KvRow label="Modo"><Badge tone={s.enabled ? 'info' : 'neutral'}>{modeLabel(s.mode)}</Badge></KvRow>
            <KvRow label="Interruptor">{s.enabled ? 'Ligado' : 'Desligado'}</KvRow>
            <KvRow label="Arquivos por pedido">{s.top_k}</KvRow>
            <KvRow label="Provedor">
              {s.provider.name === 'none' ? 'Nenhum' : `${s.provider.name}${s.provider.model ? ` · ${s.provider.model}` : ''}`}
            </KvRow>
            <KvRow label="Provedor disponível">
              {s.provider.available ? <Badge tone="success">Sim</Badge>
                : <Badge tone="neutral">Não{s.provider.unavailable_reason ? ` · ${sendReasonLabel(s.provider.unavailable_reason)}` : ''}</Badge>}
            </KvRow>
          </KvList>
        </div>

        <div className={styles.card}>
          <h3 className={styles.cardTitle}>Proveniência do envio externo</h3>
          <KvList>
            <KvRow label="Decisão">
              <Badge tone={s.external_send.allowed ? 'warning' : 'success'}>{s.external_send.allowed ? 'Permitido' : 'Bloqueado'}</Badge>
            </KvRow>
            <KvRow label="Motivo">{sendReasonLabel(s.external_send.reason)}</KvRow>
            <KvRow label="Classe do repositório">{s.external_send.repository_class}</KvRow>
            <KvRow label="Visibilidade">{visibilityLabel(s.external_send.visibility)}</KvRow>
            <KvRow label="Config pede envio remoto">{s.external_send.configured_for_remote ? 'Sim' : 'Não'}</KvRow>
          </KvList>
          {remoto ? (
            <ul className={styles.proofs} aria-label="Provas exigidas para enviar código público">
              {provas(s.external_send).map((p) => (
                <li key={p.nome}>
                  <span>{p.nome}</span>
                  {p.ok === true ? <Badge tone="success" icon={CheckCircle2} size="sm">Sim</Badge>
                    : p.ok === false ? <Badge tone="danger" icon={XCircle} size="sm">Não</Badge>
                      : <Badge tone="neutral" icon={CircleHelp} size="sm">Sem resposta</Badge>}
                </li>
              ))}
            </ul>
          ) : <p className={styles.muted}>As provas de proveniência só contam quando o modo usa um provedor remoto.</p>}
        </div>

        <div className={styles.card}>
          <h3 className={styles.cardTitle}>Orçamento por pedido</h3>
          <KvList>
            <KvRow label="Chamadas">{s.budget.max_calls}</KvRow>
            <KvRow label="Tempo limite">{ms(s.budget.timeout_ms)}</KvRow>
            <KvRow label="Custo máximo">{usd(s.budget.max_cost_usd)}</KvRow>
          </KvList>
        </div>

        <div className={styles.card}>
          <h3 className={styles.cardTitle}>Métricas recentes</h3>
          {s.summary.requests === 0 ? (
            <p className={styles.muted}>Nenhum pedido registrado ainda.</p>
          ) : (
            <KvList>
              <KvRow label="Pedidos">{s.summary.requests}</KvRow>
              <KvRow label="Acerto do cache">{cacheHitRate(s.summary.cache)}</KvRow>
              <KvRow label="Latência p50 · p95">{ms(s.summary.latency_ms.p50)} · {ms(s.summary.latency_ms.p95)}</KvRow>
              <KvRow label="Custo">{usd(s.summary.cost_usd)}</KvRow>
              <KvRow label="Tokens de entrada">{s.summary.input_tokens.toLocaleString('pt-BR')}</KvRow>
            </KvList>
          )}
          {fallbacks.length > 0 ? (
            <>
              <p className={styles.muted}>Voltou ao local por:</p>
              <ul className={styles.counts}>{fallbacks.map((f) => <li key={f.chave}><span>{f.rotulo}</span><strong>{f.n}</strong></li>)}</ul>
            </>
          ) : null}
          {bloqueios.length > 0 ? (
            <>
              <p className={styles.muted}>Bloqueios de privacidade:</p>
              <ul className={styles.counts}>{bloqueios.map((f) => <li key={f.chave}><span>{f.rotulo}</span><strong>{f.n}</strong></li>)}</ul>
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
