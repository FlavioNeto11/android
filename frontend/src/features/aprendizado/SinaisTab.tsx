import { FlaskConical, MessageSquareText, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { LoadErrorBanner, LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import type { Tone } from '../../lib/status';
import { formatDateTime, formatQuando } from '../../lib/time';
import { useUiStore } from '../../store/ui';
import { apiAprendizado } from './api';
import { type Polaridade, SINAL_KINDS, type Sinal, rotuloDaFalha, rotuloDoMotivo, rotuloDoSinal } from './model';
import styles from './Aprendizado.module.css';

const JANELAS = [7, 14, 30] as const;

const POLARIDADE: Record<Polaridade, { label: string; tone: Tone }> = {
  positive: { label: 'positivo', tone: 'success' },
  negative: { label: 'negativo', tone: 'danger' },
  neutral: { label: 'neutro', tone: 'neutral' },
};

/** Quem registrou o sinal, em português: o painel e o sistema são atores, não nomes. */
export function quemRegistrou(por: string): string {
  if (!por) return 'autor desconhecido';
  if (por === 'panel' || por === 'painel') return 'pelo painel';
  if (por === 'sistema' || por === 'system') return 'pelo sistema';
  return `por ${por}`;
}

function LinhaDeSinal({ s }: { s: Sinal }) {
  const pol = s.polarity ? POLARIDADE[s.polarity] : null;
  const onde = [s.app_package, s.capability].filter(Boolean).join(' › ');
  const abrirExecucao = (runId: string) => {
    useUiStore.getState().selectRun(runId);
    useUiStore.getState().setView('execucoes');
  };
  return (
    <li className={styles.item} data-item={`sinal:${s.id ?? s.source_ref}`}>
      <div className={styles.itemHead}>
        <span className={styles.itemTitulo}>{rotuloDoSinal(s.kind)}</span>
        {pol ? <Badge tone={pol.tone} size="sm">{pol.label}</Badge> : null}
        {s.verdict ? <Badge tone={s.verdict === 'certo' ? 'success' : 'danger'} size="sm">deu {s.verdict}</Badge> : null}
        {s.simulated ? <Badge tone="warning" size="sm" icon={FlaskConical} title="Execução simulada: nunca desliga nem promove nada real">simulado</Badge> : null}
      </div>
      <div className={styles.itemMeta}>
        {s.created_at ? <span title={formatDateTime(s.created_at)}>{formatQuando(s.created_at)}</span> : null}
        <span>{quemRegistrou(s.created_by)}</span>
        {onde ? <span className={styles.mono}>{onde}</span> : null}
        {s.reason ? <span>Motivo: {rotuloDoMotivo(s.reason)}</span> : null}
        {s.failure_kind ? <span>Falha: {rotuloDaFalha(s.failure_kind)}</span> : null}
        {s.run_id ? (
          <span>Execução: <button type="button" className={styles.linkBtn} onClick={() => abrirExecucao(s.run_id ?? '')}>{s.run_id}</button></span>
        ) : null}
      </div>
      {/* A nota já chega redigida; a que parecia credencial nem foi gravada. */}
      {s.note ? <p className={styles.secaoLead}>“{s.note}”</p> : null}
    </li>
  );
}

/**
 * Os sinais (ADR-054, D2): o botão "Deu certo / Deu errado" e os gestos que a pessoa já faz — confirmar à mão,
 * repetir, abandonar, cancelar, tomar o controle, responder, decidir uma aprovação. Nada aqui pergunta nada a ninguém.
 */
export function SinaisTab() {
  const [dias, setDias] = useState<number>(14);
  const [kind, setKind] = useState('');
  const [sinais, setSinais] = useState<Sinal[] | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const vez = useRef(0);

  const carregar = useCallback(async () => {
    const minha = ++vez.current;
    setCarregando(true);
    try {
      const lista = await apiAprendizado.sinais({ dias, kind });
      if (minha !== vez.current) return;
      setSinais(lista);
      setErro(null);
    } catch (e) {
      if (minha === vez.current) setErro(toLoadError(e));
    } finally {
      if (minha === vez.current) setCarregando(false);
    }
  }, [dias, kind]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  return (
    <section className={styles.secao} aria-label="Sinais">
      <div className={styles.toolbar}>
        <Field label="Janela" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={String(dias)} onChange={(e) => setDias(Number(e.target.value))}>
              {JANELAS.map((d) => <option key={d} value={d}>{d} dias</option>)}
            </Select>
          )}
        </Field>
        <Field label="Tipo de sinal" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">Todos</option>
              {SINAL_KINDS.map((k) => <option key={k} value={k}>{rotuloDoSinal(k)}</option>)}
            </Select>
          )}
        </Field>
        <div className={styles.toolbarFim}>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>Atualizar</Button>
        </div>
      </div>
      {erro && sinais ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}
      {!sinais ? (
        erro ? <LoadErrorState what="os sinais" error={erro} onRetry={() => void carregar()} /> : (
          <LoadingRegion label="Carregando os sinais…" className={styles.secao}>
            <Skeleton height={56} radius={8} />
            <Skeleton height={56} radius={8} />
          </LoadingRegion>
        )
      ) : sinais.length === 0 ? (
        <EmptyState icon={MessageSquareText} compact title="Nenhum sinal nesta janela">
          Os votos e os gestos (confirmar à mão, repetir, tomar o controle…) aparecem aqui quando acontecem.
        </EmptyState>
      ) : (
        <ul className={styles.lista} aria-label="Sinais recentes">
          {sinais.map((s, i) => <LinhaDeSinal key={`${s.id ?? s.source_ref}-${i}`} s={s} />)}
        </ul>
      )}
    </section>
  );
}
