import { ClipboardCheck, ClipboardCopy, FileText, RefreshCw, ServerCrash, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { RunReport, RunSummary } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { CodeBlock, JsonTree } from '../../components/JsonTree';
import { RecordTable } from '../../components/RecordTable';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { copyText, humanizeKey, isRecord, scalarToText } from '../../lib/format';
import { DELIVERY_LEVEL, OBJECTIVE_STATUS, isRunTerminal, metaOf } from '../../lib/status';
import { toast } from '../../store/toasts';
import styles from './Runs.module.css';

const TOTAL_LABELS: Record<string, string> = {
  total: 'Total', objectives: 'Objetivos', instances: 'Instâncias', instances_requested: 'Solicitadas', instances_used: 'Utilizadas',
  succeeded: 'Sucesso', failed: 'Falha', waiting_user: 'Bloqueio', uncertain: 'Incerto', cancelled: 'Cancelado',
  running: 'Em andamento', pending: 'Pendente', duration_s: 'Duração (s)', ai_calls: 'Chamadas de IA',
  ai_input_tokens: 'Tokens de entrada', ai_output_tokens: 'Tokens de saída',
};

const COLUMN_LABELS: Record<string, string> = {
  instance_id: 'Instância', worker_id: 'Servidor', device_serial: 'Serial',
  status: 'Situação', result: 'Resultado', summary: 'Resumo', detail: 'Detalhe', status_detail: 'Detalhe',
  delivery_level: 'Entrega', steps_done: 'Etapas concluídas', steps_total: 'Etapas', duration_s: 'Duração (s)', error: 'Erro',
  attempts: 'Tentativas', needs: 'Pendência', effects: 'Efeitos externos', account: 'Conta', evidence: 'Evidência',
};

// Servidor e serial logo depois da instância: o id lógico sozinho não identifica aparelho nenhum depois que o
// vínculo muda de máquina, e era essa a pergunta sem resposta no relatório antigo (#176).
const COLUMN_PRIORITY = ['instance_id', 'worker_id', 'device_serial', 'status', 'result', 'summary', 'delivery_level',
                         'detail', 'status_detail', 'error', 'needs'];

export function ReportTab({ run }: { run: RunSummary }) {
  const [report, setReport] = useState<RunReport | null>(null);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const token = useRef(0);
  const terminal = isRunTerminal(run.status);

  const load = useCallback(async () => {
    const my = ++token.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.runReport(run.id);
      if (my !== token.current) return;
      setReport(isRecord(res) ? (res as RunReport) : {});
    } catch (e) {
      if (my !== token.current) return;
      const err = toApiError(e);
      setReport(null);
      setError({ message: err.message, hint: hintForError(err) });
    } finally {
      if (my === token.current) setLoading(false);
    }
  }, [run.id]);

  // Recarrega ao abrir a aba e quando a execução muda de situação (ex.: acabou de terminar).
  useEffect(() => {
    void load();
    return () => {
      token.current += 1;
    };
  }, [load, run.status]);

  if (loading && !report) {
    return (
      <LoadingRegion label="Gerando o relatório…" className={styles.stack}>
        <Skeleton width={260} height={18} />
        <Skeleton height={64} radius={8} />
        <Skeleton height={120} radius={8} />
      </LoadingRegion>
    );
  }

  if (error && !report) {
    return (
      <EmptyState
        icon={terminal ? ServerCrash : FileText}
        tone={terminal ? 'danger' : undefined}
        compact
        title={terminal ? 'Não foi possível gerar o relatório' : 'Relatório ainda indisponível'}
        hint={terminal ? error.hint : 'O relatório final costuma ficar pronto quando a execução termina. Você pode tentar de novo a qualquer momento.'}
        actions={<Button variant="outline" icon={RefreshCw} onClick={() => void load()}>Tentar de novo</Button>}
      >
        {error.message}
      </EmptyState>
    );
  }

  const markdown = typeof report?.markdown === 'string' ? report.markdown : null;
  const totals = report?.totals;
  const perInstance = Array.isArray(report?.per_instance) ? (report.per_instance as unknown[]) : null;
  const untested = Array.isArray(report?.untested) ? (report.untested as unknown[]) : null;

  const copy = async () => {
    if (!markdown) return;
    const ok = await copyText(markdown);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
      toast({ tone: 'success', title: 'Relatório copiado em Markdown' });
    } else {
      toast({ tone: 'danger', title: 'Não foi possível copiar', hint: 'Abra “Ver Markdown”, selecione o texto e copie manualmente.' });
    }
  };

  return (
    <div className={styles.stack}>
      <div className={styles.toolbar} style={{ marginBottom: 0 }}>
        {!terminal ? (
          <Banner tone="info" icon={TriangleAlert} compact role="status">Execução ainda em andamento: este relatório é parcial.</Banner>
        ) : null}
        <div className={styles.toolbarRight}>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void load()}>Atualizar</Button>
          <Button size="sm" variant="outline" icon={copied ? ClipboardCheck : ClipboardCopy} disabledReason={markdown ? null : 'O backend não enviou a versão em Markdown.'} onClick={() => void copy()}>
            {copied ? 'Copiado' : 'Copiar Markdown'}
          </Button>
        </div>
      </div>

      <section>
        <h3 className={styles.subTitle}>Totais</h3>
        {isRecord(totals) ? <TotalsTiles totals={totals} /> : totals !== undefined ? <JsonTree value={totals} /> : <p className={styles.muted}>O relatório não trouxe totais.</p>}
      </section>

      <section>
        <h3 className={styles.subTitle}>Resultado por instância</h3>
        {perInstance && perInstance.length > 0 ? <PerInstanceTable rows={perInstance} /> : <p className={styles.muted}>Nenhum resultado por instância no relatório.</p>}
      </section>

      <section>
        <h3 className={styles.subTitle}>Itens não testados</h3>
        {untested && untested.length > 0 ? (
          <ul className={styles.untested}>
            {untested.map((u, i) => (
              <li key={i}>
                <TriangleAlert size={13} aria-hidden />
                <span>{typeof u === 'string' ? u : <JsonTree value={u} />}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.muted}>O relatório não aponta itens sem teste.</p>
        )}
      </section>

      {markdown ? (
        <Disclosure summary="Ver Markdown">
          {() => <CodeBlock value={markdown} />}
        </Disclosure>
      ) : null}
      {report && Object.keys(report).some((k) => !['run', 'totals', 'per_instance', 'untested', 'markdown'].includes(k)) ? (
        <Disclosure summary="Outros dados do relatório">
          {() => <JsonTree value={report} omit={['run', 'totals', 'per_instance', 'untested', 'markdown']} />}
        </Disclosure>
      ) : null}
    </div>
  );
}

function TotalsTiles({ totals }: { totals: Record<string, unknown> }) {
  const scalars = Object.entries(totals).filter(([, v]) => v === null || typeof v !== 'object');
  const nested = Object.entries(totals).filter(([, v]) => v !== null && typeof v === 'object');
  return (
    <>
      <div className={styles.tiles}>
        {scalars.map(([k, v]) => (
          <div key={k} className={styles.tile}>
            <div className={styles.tileValue}>{scalarToText(v)}</div>
            <div className={styles.tileLabel}>{TOTAL_LABELS[k] ?? humanizeKey(k)}</div>
          </div>
        ))}
      </div>
      {nested.length > 0 ? (
        <div style={{ marginTop: 12 }}>
          <JsonTree value={Object.fromEntries(nested)} labels={TOTAL_LABELS} />
        </div>
      ) : null}
    </>
  );
}

function PerInstanceTable({ rows }: { rows: unknown[] }) {
  return (
    <RecordTable
      rows={rows}
      labels={COLUMN_LABELS}
      priority={COLUMN_PRIORITY}
      rowKey="instance_id"
      caption="Resultado por instância"
      renderCell={(column, value) => {
        if (column === 'status' && typeof value === 'string') return <StatusBadge meta={metaOf(OBJECTIVE_STATUS, value)} size="sm" />;
        if (column === 'delivery_level' && typeof value === 'string') return <StatusBadge meta={metaOf(DELIVERY_LEVEL, value)} size="sm" plain />;
        if (column === 'instance_id' && typeof value === 'string') return <span className="mono">{value}</span>;
        // Onde rodou: sem fotografia, "não registrado" — nunca a máquina local por omissão.
        if ((column === 'worker_id' || column === 'device_serial') && value == null) return <span className={styles.muted}>não registrado</span>;
        if ((column === 'worker_id' || column === 'device_serial') && typeof value === 'string') return <span className="mono">{value}</span>;
        return undefined;
      }}
    />
  );
}
