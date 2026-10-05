import { Coins, RefreshCw, ServerCrash } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { RunSummary } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { isRunSemTrabalho } from '../../lib/status';
import usageStyles from '../usage/Usage.module.css';
import { isUsageEmpty, usageTotals } from '../usage/usage';
import { RunUsageTotals, UsageTable } from '../usage/UsageView';
import { useUsage } from '../usage/useUsage';
import { ProjecaoDoPlano } from './ProjecaoDoPlano';

type RunRef = Pick<RunSummary, 'id' | 'status'>;

/**
 * "Custo de IA desta execução" — recolhido por padrão; o `GET /api/usage?run_id=` só acontece na primeira
 * abertura. Depois disso recarrega sozinho quando a execução termina, ou pelo botão "Atualizar".
 *
 * Logo abaixo do custo real, o normal medido para o plano (item 18.3, `GET …/projection`), com a janela efetiva:
 * a comparação que faltava para saber se o gasto está fora do normal. Também só é lido na primeira abertura.
 */
export function RunUsageCard({ run }: { run: RunRef }) {
  const [total, setTotal] = useState<string | null>(null);
  return (
    <Disclosure
      summary={<><Coins size={13} aria-hidden style={{ verticalAlign: '-2px', marginRight: 6 }} />Custo de IA desta execução</>}
      meta={total ?? undefined}
    >
      {() => (
        <div className={usageStyles.wrap}>
          <RunUsageBody run={run} onTotal={setTotal} />
          <ProjecaoDoPlano run={run} />
        </div>
      )}
    </Disclosure>
  );
}

function RunUsageBody({ run, onTotal }: { run: RunRef; onTotal: (text: string | null) => void }) {
  const terminal = isRunSemTrabalho(run.status);       // 29.93: o custo para de crescer na espera pela pessoa
  // `terminal` como chave de recarga: false → true = a execução acabou de terminar.
  const { report, error, loading, reload } = useUsage({ run_id: run.id }, terminal);

  useEffect(() => {
    onTotal(report ? usageTotals(report).totalUsd : null);
  }, [report, onTotal]);

  if (!report) {
    if (loading) {
      return (
        <LoadingRegion label="Calculando o custo de IA…" className={usageStyles.wrap}>
          <Skeleton width={320} height={44} radius={8} />
          <Skeleton height={96} radius={8} />
        </LoadingRegion>
      );
    }
    return (
      <EmptyState
        icon={ServerCrash}
        tone="danger"
        compact
        title="Não foi possível calcular o custo de IA"
        hint={error?.hint}
        actions={<Button variant="outline" icon={RefreshCw} onClick={reload}>Tentar de novo</Button>}
      >
        {error?.message}
      </EmptyState>
    );
  }

  return (
    <div className={usageStyles.wrap}>
      <div className={usageStyles.toolbar}>
        <span className={usageStyles.toolbarNote}>
          {terminal ? 'Execução encerrada: valores finais.' : 'Execução em andamento: valores parciais — atualize quando quiser.'}
        </span>
        <span className={usageStyles.toolbarSpacer} />
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={reload}>Atualizar</Button>
      </div>
      {error ? <Banner tone="warning" icon={ServerCrash} compact title="Mostrando o último valor carregado">{error.message} {error.hint}</Banner> : null}
      {isUsageEmpty(report) ? (
        <p className={usageStyles.empty}>Ainda não há chamadas de IA nem etapas concluídas nesta execução.</p>
      ) : (
        <>
          <RunUsageTotals report={report} />
          <UsageTable report={report} caption="Custo de IA desta execução, por função e modelo" />
        </>
      )}
    </div>
  );
}
