import { Check, FlaskConical, Hand, ListChecks, RefreshCw, Smartphone, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import appStyles from '../../App.module.css';
import { api } from '../../api/client';
import type { RunSummary } from '../../api/types';
import { Button } from '../../components/Button';
import { Card, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { conteudoAoTopo } from '../../lib/scroll';
import { RUN_STATUS, metaOf } from '../../lib/status';
import { formatAgoCoarse, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import styles from './Runs.module.css';
import { RunView } from './RunView';

const HISTORY_LIMIT = 50;

export function RunsPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  const runs = useAppStore((s) => s.runs);
  const mergeRuns = useAppStore((s) => s.mergeRuns);
  const instances = useAppStore((s) => s.instances);
  // Só para dar NOME ao servidor no filtro: a execução guarda o worker_id, e id cru não diz qual máquina é.
  const workers = useAppStore((s) => s.workers);
  const selectedRunId = useUiStore((s) => s.selectedRunId);
  const selectRun = useUiStore((s) => s.selectRun);
  const abrirExecucao = useUiStore((s) => s.abrirExecucao);
  const setView = useUiStore((s) => s.setView);
  const [loading, setLoading] = useState(false);
  // Filtro por ONDE rodou. Com ele ligado a lista deixa de ser "o que o store tem" e passa a ser a resposta do
  // servidor: o store recebe execuções ao vivo pelo WebSocket, e misturá-las com um recorte mentiria sobre o filtro.
  const [filtro, setFiltro] = useState<{ instancia: string; servidor: string }>({ instancia: '', servidor: '' });
  const [filtradas, setFiltradas] = useState<RunSummary[] | null>(null);
  const [total, setTotal] = useState(0);
  const [carregadas, setCarregadas] = useState(0);
  const filtrando = !!(filtro.instancia || filtro.servidor);

  // Servidores conhecidos pelo parque: a lista sai dos aparelhos, sem uma chamada só para isso.
  const servidores = useMemo(
    () => Array.from(new Set(Object.values(instances).map((i) => i.worker_id).filter((w): w is string => !!w))).sort(),
    [instances],
  );

  // O snapshot traz só as ativas + 20 recentes; aqui buscamos um histórico um pouco maior — e, do segundo
  // "Carregar mais" em diante, as páginas seguintes. Sem isto as execuções antigas não tinham caminho nenhum.
  const loadHistory = useCallback(async (opts: { mais?: boolean } = {}) => {
    setLoading(true);
    try {
      const offset = opts.mais ? carregadas : 0;
      const page = await api.listRuns(HISTORY_LIMIT, offset, filtro.instancia || undefined, filtro.servidor || undefined);
      const lista = Array.isArray(page?.runs) ? page.runs : [];
      mergeRuns(lista);                               // o detalhe de qualquer execução da página fica disponível
      setTotal(typeof page?.total === 'number' ? page.total : lista.length);
      setCarregadas(offset + lista.length);
      setFiltradas(filtrando ? (prev) => (opts.mais ? [...(prev ?? []), ...lista] : lista) : null);
    } catch (e) {
      toastError('Não foi possível atualizar a lista de execuções', e, { key: 'runs-history' });
    } finally {
      setLoading(false);
    }
  }, [mergeRuns, carregadas, filtro.instancia, filtro.servidor, filtrando]);

  useEffect(() => {
    if (hydrateCount > 0) void loadHistory();
    // Trocar o filtro recomeça a paginação do zero, de propósito.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrateCount, filtro.instancia, filtro.servidor]);

  const lista = filtrando ? (filtradas ?? []) : runs;
  const temMais = carregadas < total;

  // Abre a mais recente quando nada está selecionado (substitui o link, não empilha: não foi a pessoa que escolheu).
  useEffect(() => {
    if (!selectedRunId && lista.length > 0 && lista[0]) selectRun(lista[0].id);
  }, [selectedRunId, lista, selectRun]);

  // Execução nova, leitura do começo: sem isto, escolher outra com a página rolada abria no meio do relatório.
  useEffect(() => {
    conteudoAoTopo();
  }, [selectedRunId]);

  return (
    <div className={appStyles.page}>
      <div className={appStyles.pageHeader}>
        <div>
          <h1 className={appStyles.pageTitle}>Execuções</h1>
          <p className={appStyles.pageLead}>Histórico recente e detalhes completos de cada execução: plano, progresso por aparelho, linha do tempo, evidências e relatório.</p>
        </div>
      </div>

      <div className={styles.runsLayout}>
        <Card className={styles.runListCard} aria-label="Lista de execuções">
          <CardHeader
            title="Recentes"
            subtitle={hydrated ? `${lista.length} de ${Math.max(total, lista.length)} execução(ões)` : undefined}
            actions={<Button size="sm" variant="ghost" icon={RefreshCw} iconOnly label="Atualizar lista" loading={loading} onClick={() => void loadHistory()} />}
          />
          {/* Achado #176: de uma execução não se descobria onde ela rodou, e antigas não tinham como ser achadas. */}
          <div className={styles.runFilters}>
            <label>
              <span className="sr-only">Filtrar por aparelho</span>
              <Select small value={filtro.instancia} aria-label="Filtrar por aparelho"
                      onChange={(e) => setFiltro((f) => ({ ...f, instancia: e.target.value }))}>
                <option value="">Todos os aparelhos</option>
                {Object.keys(instances).sort().map((id) => <option key={id} value={id}>{id}</option>)}
              </Select>
            </label>
            <label>
              <span className="sr-only">Filtrar por servidor</span>
              <Select small value={filtro.servidor} aria-label="Filtrar por servidor"
                      onChange={(e) => setFiltro((f) => ({ ...f, servidor: e.target.value }))}>
                <option value="">Todos os servidores</option>
                {servidores.map((w) => <option key={w} value={w}>{workers[w]?.name ?? w}</option>)}
              </Select>
            </label>
          </div>
          {!hydrated ? (
            <LoadingRegion label="Carregando execuções…" className={styles.runList}>
              {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} height={74} radius={8} />)}
            </LoadingRegion>
          ) : lista.length === 0 ? (
            <EmptyState
              icon={ListChecks}
              compact
              title={filtrando ? 'Nenhuma execução com esse filtro' : 'Nenhuma execução ainda'}
              hint={filtrando ? 'Esse aparelho ou servidor não aparece em nenhuma execução registrada.'
                              : 'Crie a primeira pelo campo de comando do Painel.'}
              actions={filtrando
                ? <Button variant="outline" onClick={() => setFiltro({ instancia: '', servidor: '' })}>Limpar filtro</Button>
                : <Button variant="outline" onClick={() => setView('painel')}>Ir para o Painel</Button>}
            />
          ) : (
            <>
              <ul className={styles.runList}>
                {lista.map((r) => (
                  <li key={r.id}>
                    <RunItem run={r} current={r.id === selectedRunId} onSelect={() => abrirExecucao(r.id)} />
                  </li>
                ))}
              </ul>
              {temMais ? (
                <Button size="sm" variant="outline" block loading={loading} onClick={() => void loadHistory({ mais: true })}>
                  Carregar mais ({Math.max(total - carregadas, 0)} restantes)
                </Button>
              ) : null}
            </>
          )}
        </Card>

        <RunView />
      </div>
    </div>
  );
}

function Age({ ts }: { ts: string }) {
  const now = useNow();
  return <>{formatAgoCoarse(ts, now)}</>;
}

function RunItem({ run, current, onSelect }: { run: RunSummary; current: boolean; onSelect: () => void }) {
  const c = run.counts;
  const blocked = (c?.waiting_user ?? 0) + (c?.uncertain ?? 0);
  return (
    <button type="button" className={styles.runItem} aria-current={current ? 'true' : undefined} onClick={onSelect}>
      <span className={styles.runItemTop}>
        <span className={styles.shortId}>{run.short_id}</span>
        <StatusBadge meta={metaOf(RUN_STATUS, run.status)} size="sm" className={styles.runItemBadge} />
        <span className={styles.runItemAge}><Age ts={run.created_at} /></span>
      </span>
      <span className={styles.runItemCmd}>{run.command}</span>
      <span className={styles.runItemMeta}>
        <span><Smartphone size={11} aria-hidden /> {run.instances_used}/{run.instances_requested}</span>
        {c ? (
          <>
            <span className={c.succeeded > 0 ? styles.miniOk : undefined}><Check size={11} aria-hidden /> {c.succeeded}<span className="sr-only"> com sucesso</span></span>
            <span className={c.failed > 0 ? styles.miniBad : undefined}><X size={11} aria-hidden /> {c.failed}<span className="sr-only"> com falha</span></span>
            <span className={blocked > 0 ? styles.miniWarn : undefined}><Hand size={11} aria-hidden /> {blocked}<span className="sr-only"> bloqueados ou incertos</span></span>
          </>
        ) : null}
        {run.simulated ? <span className={styles.miniWarn}><FlaskConical size={11} aria-hidden /> simulado</span> : null}
      </span>
    </button>
  );
}
